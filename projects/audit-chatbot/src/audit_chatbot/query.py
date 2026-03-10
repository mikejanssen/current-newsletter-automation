from __future__ import annotations

import argparse
import re
from pathlib import Path

from audit_chatbot.db import connect

RISK_PATTERNS = [
    r"material weakness",
    r"significant deficien",
    r"going concern",
    r"qualified opinion",
    r"adverse opinion",
    r"disclaimer of opinion",
    r"noncompliance",
    r"questioned costs",
    r"line of credit",
    r"litigation",
    r"debt",
    r"deficit",
]

STRICT_PATTERNS = {
    r"material weakness",
    r"significant deficien",
    r"going concern",
    r"qualified opinion",
    r"adverse opinion",
    r"disclaimer of opinion",
    r"noncompliance",
    r"questioned costs",
}

FINANCING_SOFT_PATTERNS = {
    r"debt",
    r"line of credit",
    r"deficit",
}

FINANCING_STRESS_MARKERS = [
    "default",
    "in default",
    "covenant",
    "waiver",
    "forbearance",
    "liquidity",
    "refinanc",
    "maturity wall",
    "going concern",
    "substantial doubt",
    "noncompliance",
    "cross-default",
]

DEFICIT_STRESS_MARKERS = [
    "accumulated deficit",
    "unrestricted net assets deficit",
    "negative unrestricted net assets",
    "negative cash flow",
    "cash flows (used in) operating activities",
    "insufficient cash",
    "liquidity",
    "going concern",
    "substantial doubt",
    "debt covenant",
    "default",
    "forbearance",
    "unable to",
]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Query audit-chatbot index")
    p.add_argument("command", choices=["summary", "risks", "docs", "search", "risks-all"])
    p.add_argument("query", nargs="*", help="Station name/id or search phrase")
    p.add_argument("--db", default="output/audit-chatbot.db")
    p.add_argument("--limit", type=int, default=12)
    risk_mode = p.add_mutually_exclusive_group()
    risk_mode.add_argument(
        "--strict", action="store_true", help="Show only higher-confidence risk signals"
    )
    risk_mode.add_argument(
        "--watchlist",
        action="store_true",
        help="Include softer/boilerplate-style mentions for manual review",
    )
    p.add_argument(
        "--explain",
        action="store_true",
        help="Print longer context excerpts for each risk signal",
    )
    p.add_argument(
        "--path-date",
        default="",
        help="Only consider docs with file paths containing this date (YYYY-MM-DD)",
    )
    p.add_argument("--out", default="", help="Write markdown output for risks-all")
    p.add_argument("--json-out", default="", help="Write JSON output for risks-all")
    return p.parse_args(argv)


def resolve_station_ids(conn, station_query: str) -> list[str]:
    q = station_query.strip().lower()
    rows = conn.execute(
        """
        SELECT DISTINCT canonical_station_id
        FROM docs
        WHERE lower(station_id) = ?
           OR lower(station_name) = ?
           OR lower(station_name) LIKE ?
           OR lower(station_id) LIKE ?
           OR lower(canonical_station_id) = ?
           OR lower(canonical_station_name) = ?
           OR lower(canonical_station_name) LIKE ?
        ORDER BY canonical_station_name
        """,
        (q, q, f"%{q}%", f"%{q}%", q, q, f"%{q}%"),
    ).fetchall()
    return [r["canonical_station_id"] for r in rows]


def docs_for_station(conn, station_ids: list[str], limit: int):
    if not station_ids:
        return []
    placeholders = ",".join(["?"] * len(station_ids))
    return conn.execute(
        f"""
        SELECT canonical_station_id, canonical_station_name, title, file_path, file_mtime
        FROM docs
        WHERE canonical_station_id IN ({placeholders})
        ORDER BY file_mtime DESC
        LIMIT ?
        """,
        (*station_ids, limit),
    ).fetchall()


def all_station_ids(conn) -> list[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT canonical_station_id
        FROM docs
        ORDER BY canonical_station_name
        """
    ).fetchall()
    return [r["canonical_station_id"] for r in rows]


def print_docs(rows) -> None:
    if not rows:
        print("No matching documents.")
        return
    for r in rows:
        print(f"- {r['canonical_station_name']} ({r['canonical_station_id']}): {r['title']}")
        print(f"  source: `{r['file_path']}`")


def print_summary(rows) -> None:
    if not rows:
        print("No matching documents.")
        return
    station = rows[0]["canonical_station_name"]
    print(f"Summary for {station} ({rows[0]['canonical_station_id']})")
    print(f"Documents considered: {len(rows)}")
    years = sorted(set(re.findall(r"\b20\d{2}\b", " ".join(r["title"] for r in rows))))
    if years:
        print(f"Year mentions in titles: {', '.join(years)}")
    print("Recent docs:")
    for r in rows[:8]:
        print(f"- {r['title']}")
        print(f"  source: `{r['file_path']}`")


def is_boilerplate_hit(pat: str, text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 160) : start].replace("\n", " ")
    context = text[max(0, start - 320) : min(len(text), end + 320)]
    context = context.replace("\n", " ")
    # Normalize punctuation variants from PDF extraction.
    normalized = (
        context.replace("’", "'")
        .replace("‘", "'")
        .replace("“", '"')
        .replace("”", '"')
        .replace("\t", " ")
    )
    normalized = " ".join(normalized.split())
    before_norm = " ".join(
        before.replace("’", "'").replace("‘", "'").replace("\t", " ").split()
    )
    if pat in {r"material weakness", r"significant deficien"}:
        if "is a deficiency" in normalized and "internal control" in normalized:
            return True
    if pat == r"going concern":
        if "if we conclude that" in before_norm:
            return True
        if (
            "auditors' responsibilities for the audit" in normalized
            or "auditors responsibilities for the audit" in normalized
            or "required to conclude whether" in normalized
            or "conclude whether, in our judgment" in normalized
            or "our objectives are to obtain reasonable assurance" in normalized
            or "management is required to evaluate whether" in normalized
        ):
            return True
        disclosure_markers = [
            "substantial doubt",
            "ability to continue",
            "conditions and events",
            "management's plans",
            "management plans",
            "doubt exists",
            "alleviate the substantial doubt",
            "note ",
        ]
        if not any(m in normalized for m in disclosure_markers):
            return True
        generic_audit_markers = [
            "our objectives are to",
            "as part of an audit",
            "obtain reasonable assurance",
        ]
        if any(m in normalized for m in generic_audit_markers):
            return True
    return False


def risk_confidence(pat: str, text: str, start: int, end: int) -> str:
    context = text[max(0, start - 320) : min(len(text), end + 320)].replace("\n", " ")
    normalized = " ".join(
        context.replace("’", "'").replace("‘", "'").replace("\t", " ").split()
    )
    if pat in {r"debt", r"line of credit"}:
        if any(marker in normalized for marker in FINANCING_STRESS_MARKERS):
            return "higher"
        return "watchlist"
    if pat == r"deficit":
        if any(marker in normalized for marker in DEFICIT_STRESS_MARKERS):
            return "higher"
        return "watchlist"
    return "higher"


def print_risks(
    conn,
    station_ids: list[str],
    limit: int,
    strict: bool = False,
    watchlist: bool = False,
    explain: bool = False,
) -> None:
    if not station_ids:
        print("No matching station.")
        return
    placeholders = ",".join(["?"] * len(station_ids))
    params: list[str] = list(station_ids)
    where_extra = ""
    if path_date:
        where_extra = " AND file_path LIKE ?"
        params.append(f"%/{path_date}/%")
    rows = conn.execute(
        f"""
        SELECT canonical_station_id, canonical_station_name, title, file_path, content_text
        FROM docs
        WHERE canonical_station_id IN ({placeholders}){where_extra}
        ORDER BY file_mtime DESC
        LIMIT 400
        """,
        tuple(params),
    ).fetchall()
    findings = []
    watchlist_findings = []
    seen = set()
    for r in rows:
        text = (r["content_text"] or "").lower()
        if not text:
            continue
        for pat in RISK_PATTERNS:
            if strict and pat not in STRICT_PATTERNS:
                continue
            m = re.search(pat, text)
            if m:
                start = max(0, m.start() - 80)
                end = min(len(text), m.end() + 120)
                snippet = text[start:end].replace("\n", " ").strip()
                explain_start = max(0, m.start() - 320)
                explain_end = min(len(text), m.end() + 420)
                explain_snippet = text[explain_start:explain_end].replace("\n", " ").strip()
                key = (r["canonical_station_id"], r["title"], pat)
                if key in seen:
                    continue
                seen.add(key)
                confidence = risk_confidence(pat, text, m.start(), m.end())
                hit = (pat, r, snippet, explain_snippet, confidence)
                if is_boilerplate_hit(pat, text, m.start(), m.end()):
                    if watchlist:
                        watchlist_findings.append((pat, r, snippet, explain_snippet, "watchlist"))
                    continue
                if confidence == "watchlist":
                    if watchlist:
                        watchlist_findings.append(hit)
                    continue
                findings.append(hit)
                break
    if not findings and not (watchlist and watchlist_findings):
        print("No risk keywords found in available extracted text.")
        print("Note: This depends on PDF text extraction quality.")
        return
    if strict:
        label = "Potential risk signals (strict)"
    elif watchlist:
        label = "Potential risk signals (watchlist)"
    else:
        label = "Potential risk signals"
    print(f"{label}:")
    output_hits = findings[:]
    if watchlist and len(output_hits) < limit:
        output_hits.extend(watchlist_findings[: max(0, limit - len(output_hits))])
        # In watchlist mode, collapse repetitive pattern hits per station to reduce noise.
        collapsed = []
        seen_station_pattern = set()
        for hit in output_hits:
            pat, r, snip, explain_snip, confidence = hit
            sp_key = (r["canonical_station_id"], pat)
            if sp_key in seen_station_pattern:
                continue
            seen_station_pattern.add(sp_key)
            collapsed.append(hit)
        output_hits = collapsed
    for pat, r, snip, explain_snip, confidence in output_hits[:limit]:
        print(f"- [{pat}] {r['canonical_station_name']} ({r['canonical_station_id']}): {r['title']}")
        print(f"  confidence: {confidence}")
        if explain:
            print(f"  snippet: {explain_snip[:850]}...")
        else:
            print(f"  snippet: {snip[:220]}...")
        print(f"  source: `{r['file_path']}`")


def collect_risks(
    conn,
    station_ids: list[str],
    strict: bool = False,
    watchlist: bool = False,
    path_date: str = "",
) -> tuple[list[tuple], list[tuple]]:
    if not station_ids:
        return [], []
    placeholders = ",".join(["?"] * len(station_ids))
    params: list[str] = list(station_ids)
    where_extra = ""
    if path_date:
        where_extra = " AND file_path LIKE ?"
        params.append(f"%/{path_date}/%")
    rows = conn.execute(
        f"""
        SELECT canonical_station_id, canonical_station_name, title, file_path, content_text
        FROM docs
        WHERE canonical_station_id IN ({placeholders}){where_extra}
        ORDER BY file_mtime DESC
        LIMIT 400
        """,
        tuple(params),
    ).fetchall()
    findings = []
    watchlist_findings = []
    seen = set()
    for r in rows:
        text = (r["content_text"] or "").lower()
        if not text:
            continue
        for pat in RISK_PATTERNS:
            if strict and pat not in STRICT_PATTERNS:
                continue
            m = re.search(pat, text)
            if not m:
                continue
            start = max(0, m.start() - 80)
            end = min(len(text), m.end() + 120)
            snippet = text[start:end].replace("\n", " ").strip()
            explain_start = max(0, m.start() - 320)
            explain_end = min(len(text), m.end() + 420)
            explain_snippet = text[explain_start:explain_end].replace("\n", " ").strip()
            key = (r["canonical_station_id"], r["title"], pat)
            if key in seen:
                continue
            seen.add(key)
            confidence = risk_confidence(pat, text, m.start(), m.end())
            hit = (pat, r, snippet, explain_snippet, confidence)
            if is_boilerplate_hit(pat, text, m.start(), m.end()):
                if watchlist:
                    watchlist_findings.append((pat, r, snippet, explain_snippet, "watchlist"))
                break
            if confidence == "watchlist":
                if watchlist:
                    watchlist_findings.append(hit)
                break
            findings.append(hit)
            break
    return findings, watchlist_findings


def run_risks_all(
    conn,
    limit: int,
    path_date: str = "",
    out: str = "",
    json_out: str = "",
) -> int:
    station_ids = all_station_ids(conn)
    strict_hits = []
    watch_hits = []
    for sid in station_ids:
        strict, watch = collect_risks(
            conn,
            [sid],
            strict=True,
            watchlist=True,
            path_date=path_date,
        )
        if strict:
            strict_hits.append(strict[0])
        elif watch:
            watch_hits.append(watch[0])

    print(f"Stations considered: {len(station_ids)}")
    if path_date:
        print(f"Path date filter: {path_date}")
    print(f"Strict stations: {len(strict_hits)}")
    print(f"Watchlist stations: {len(watch_hits)}")

    def _fmt_line(hit: tuple) -> str:
        pat, r, _, __, confidence = hit
        return f"- [{pat}] {r['canonical_station_name']} ({r['canonical_station_id']}) [{confidence}]"

    if strict_hits:
        print("Strict highlights:")
        for hit in strict_hits[:limit]:
            print(_fmt_line(hit))
    else:
        print("Strict highlights: none")
    if watch_hits:
        print("Watchlist highlights:")
        for hit in watch_hits[:limit]:
            print(_fmt_line(hit))
    else:
        print("Watchlist highlights: none")

    if out:
        out_path = Path(out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Audit Risk Briefing",
            "",
            f"Stations considered: {len(station_ids)}",
            f"Path date filter: {path_date or 'none'}",
            f"Strict stations: {len(strict_hits)}",
            f"Watchlist stations: {len(watch_hits)}",
            "",
            "## Strict Highlights",
        ]
        if strict_hits:
            for pat, r, snip, _, confidence in strict_hits[:limit]:
                lines.append(
                    f"- [{pat}] {r['canonical_station_name']} ({r['canonical_station_id']}) [{confidence}]"
                )
                lines.append(f"  snippet: {snip[:220]}...")
                lines.append(f"  source: `{r['file_path']}`")
        else:
            lines.append("- None.")
        lines.append("")
        lines.append("## Watchlist Highlights")
        if watch_hits:
            for pat, r, snip, _, confidence in watch_hits[:limit]:
                lines.append(
                    f"- [{pat}] {r['canonical_station_name']} ({r['canonical_station_id']}) [{confidence}]"
                )
                lines.append(f"  snippet: {snip[:220]}...")
                lines.append(f"  source: `{r['file_path']}`")
        else:
            lines.append("- None.")
        out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"Wrote risk briefing: {out_path}")

    if json_out:
        out_json = Path(json_out)
        out_json.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "stations_considered": len(station_ids),
            "path_date": path_date,
            "strict_station_count": len(strict_hits),
            "watchlist_station_count": len(watch_hits),
            "strict_highlights": [
                {
                    "pattern": pat,
                    "station_id": r["canonical_station_id"],
                    "station_name": r["canonical_station_name"],
                    "title": r["title"],
                    "source": r["file_path"],
                    "snippet": snip,
                    "confidence": confidence,
                }
                for pat, r, snip, _, confidence in strict_hits[:limit]
            ],
            "watchlist_highlights": [
                {
                    "pattern": pat,
                    "station_id": r["canonical_station_id"],
                    "station_name": r["canonical_station_name"],
                    "title": r["title"],
                    "source": r["file_path"],
                    "snippet": snip,
                    "confidence": confidence,
                }
                for pat, r, snip, _, confidence in watch_hits[:limit]
            ],
        }
        out_json.write_text(__import__("json").dumps(payload, indent=2), encoding="utf-8")
        print(f"Wrote risk JSON: {out_json}")
    return 0


def print_search(conn, phrase: str, limit: int) -> None:
    rows = conn.execute(
        """
        SELECT d.canonical_station_id, d.canonical_station_name, d.title, d.file_path,
               snippet(docs_fts, 5, '[', ']', ' … ', 20) AS snippet
        FROM docs_fts
        JOIN docs d ON d.rowid = docs_fts.rowid
        WHERE docs_fts MATCH ?
        LIMIT ?
        """,
        (phrase, limit),
    ).fetchall()
    if not rows:
        print("No matches.")
        return
    for r in rows:
        print(f"- {r['canonical_station_name']} ({r['canonical_station_id']}): {r['title']}")
        print(f"  snippet: {r['snippet']}")
        print(f"  source: `{r['file_path']}`")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    query_text = " ".join(args.query).strip()
    conn = connect(args.db)

    if args.command == "risks-all":
        return run_risks_all(
            conn,
            limit=args.limit,
            path_date=args.path_date.strip(),
            out=args.out.strip(),
            json_out=args.json_out.strip(),
        )

    if args.command == "search":
        if not query_text:
            print("search requires a query phrase")
            return 2
        print_search(conn, query_text, args.limit)
        return 0

    if not query_text:
        print(f"{args.command} requires a station query")
        return 2

    station_ids = resolve_station_ids(conn, query_text)
    if not station_ids:
        print(f"No station match for: {query_text}")
        return 1

    if args.command == "docs":
        rows = docs_for_station(conn, station_ids, args.limit)
        print_docs(rows)
        return 0

    if args.command == "summary":
        rows = docs_for_station(conn, station_ids, max(args.limit, 20))
        print_summary(rows)
        return 0

    if args.command == "risks":
        print_risks(
            conn,
            station_ids,
            args.limit,
            strict=args.strict,
            watchlist=args.watchlist,
            explain=args.explain,
            path_date=args.path_date.strip(),
        )
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
