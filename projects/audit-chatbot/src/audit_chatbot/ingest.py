from __future__ import annotations

import argparse
import csv
import hashlib
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from audit_chatbot.db import connect, init_schema, rebuild_fts


MANUAL_STATION_ALIASES: dict[str, list[str]] = {
    "wnet": ["thirteen", "wnet group", "the wnet group", "channel 13"],
    "pbssocal": ["pbs socal", "pbs socal koce", "socal pbs"],
    "gbh": ["wgbh", "wgbh educational foundation"],
    "wnyc": ["new york public radio", "nypr"],
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ingest archived audit docs into audit-chatbot DB")
    p.add_argument("--db", default="output/audit-chatbot.db")
    p.add_argument("--archive-root", default="../audit-watch/output/audits")
    p.add_argument("--stations", default="../audit-watch/config/stations.csv")
    p.add_argument(
        "--semipublic-root",
        default="",
        help="Optional local path to a Semipublic public-repository checkout",
    )
    p.add_argument("--max-text-chars", type=int, default=200000)
    return p.parse_args(argv)


@dataclass(frozen=True)
class DocRecord:
    source_kind: str
    source_label: str
    station_id: str
    station_name: str
    canonical_station_id: str
    canonical_station_name: str
    title: str
    source_path: str
    report_year: int
    dedupe_scope: str


def load_station_rows(path: Path) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            fixed = {(k or "").lstrip("\ufeff"): (v or "") for k, v in row.items()}
            sid = fixed.get("station_id", "").strip()
            name = fixed.get("station_name", "").strip()
            page_url = fixed.get("page_url", "").strip()
            notes = fixed.get("notes", "").strip()
            if sid:
                out[sid] = {
                    "station_name": name or sid,
                    "page_url": page_url,
                    "notes": notes,
                }
    return out


def normalized_name_tokens(text: str) -> list[str]:
    normalized = text.lower()
    normalized = normalized.replace("&", " and ")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    normalized = re.sub(r"\b(inc|incorporated|corporation|corp|foundation|authority|commission|council|company|co|communications|communication|educational|telecommunications|broadcasting|broadcast|network|public|radio|television|tv|fm|am|media|friends|licensee|licenses)\b", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return [token for token in normalized.split(" ") if token]


def call_sign_variants(name: str) -> set[str]:
    text = re.sub(r"[^A-Za-z0-9]+", " ", name.upper())
    variants: set[str] = set()
    for token in text.split():
        if re.fullmatch(r"[WK][A-Z]{2,4}(?:FM|TV|AM)?", token):
            variants.add(token)
            for suffix in ("FM", "TV", "AM"):
                if token.endswith(suffix) and len(token) > len(suffix) + 2:
                    variants.add(token[: -len(suffix)])
    return variants


def alias_variants(name: str) -> set[str]:
    variants: set[str] = set()
    if not name:
        return variants
    cleaned = name.strip()
    variants.add(cleaned)
    variants.add(cleaned.lower())
    variants.add(slugify(cleaned))

    token_string = " ".join(normalized_name_tokens(cleaned))
    if token_string:
        variants.add(token_string)
        variants.add(slugify(token_string))

    without_parens = re.sub(r"\([^)]*\)", " ", cleaned)
    without_parens = re.sub(r"\s+", " ", without_parens).strip()
    if without_parens and without_parens != cleaned:
        variants.add(without_parens)
        variants.add(without_parens.lower())
        variants.add(slugify(without_parens))

    for call in call_sign_variants(cleaned):
        variants.add(call)
        variants.add(call.lower())
        variants.add(slugify(call))
    return {v for v in variants if v}


def normalize_url(url: str) -> str:
    text = url.strip().lower()
    if not text:
        return ""
    text = re.sub(r"^https?://", "", text)
    text = re.sub(r"^www\.", "", text)
    text = text.split("#", 1)[0].split("?", 1)[0].rstrip("/")
    return text


def canonical_rank(name: str) -> tuple[int, int, str]:
    n = name.lower()
    legal_markers = [
        "foundation",
        "inc",
        "corporation",
        "authority",
        "commission",
        "school board",
        "county",
        "educational",
        "television",
    ]
    penalty = sum(1 for m in legal_markers if m in n)
    return (penalty, len(name), n)


def build_canonical_map(rows: dict[str, dict[str, str]]) -> dict[str, tuple[str, str]]:
    by_url: dict[str, list[str]] = defaultdict(list)
    for sid, row in rows.items():
        key = normalize_url(row.get("page_url", ""))
        if key:
            by_url[key].append(sid)

    canonical_map: dict[str, tuple[str, str]] = {}
    for station_ids in by_url.values():
        station_ids.sort(key=lambda sid: canonical_rank(rows[sid]["station_name"]))
        can_id = station_ids[0]
        can_name = rows[can_id]["station_name"]
        for sid in station_ids:
            canonical_map[sid] = (can_id, can_name)

    for sid, row in rows.items():
        canonical_map.setdefault(sid, (sid, row["station_name"]))
    return canonical_map


def build_semipublic_alias_map(
    station_rows: dict[str, dict[str, str]],
    canonical_map: dict[str, tuple[str, str]],
) -> dict[str, tuple[str, str]]:
    alias_map: dict[str, tuple[str, str]] = {}
    token_index: dict[str, set[tuple[str, str]]] = defaultdict(set)
    call_index: dict[str, tuple[str, str]] = {}

    for sid, row in station_rows.items():
        canonical_station_id, canonical_station_name = canonical_map.get(
            sid, (sid, row.get("station_name", sid))
        )
        target = (canonical_station_id, canonical_station_name)
        names = {row.get("station_name", ""), canonical_station_name, sid.replace("-", " ")}
        for name in names:
            if not name:
                continue
            alias_map[slugify(name)] = target
            token_string = " ".join(normalized_name_tokens(name))
            if token_string:
                token_index[token_string].add(target)
            for variant in call_sign_variants(name):
                call_index[variant] = target

    # Favor exact unambiguous legal-name/token matches first, then call-sign matches.
    for token_string, targets in token_index.items():
        if len(targets) == 1:
            alias_map[token_string.replace(" ", "-")] = next(iter(targets))
    for variant, target in call_index.items():
        alias_map[slugify(variant)] = target
    return alias_map


def title_from_filename(name: str) -> str:
    stem = Path(name).stem
    text = re.sub(r"[_-]+", " ", stem)
    text = re.sub(r"\s+", " ", text).strip()
    return text or name


def slugify(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return value or "unknown"


def infer_report_year(*parts: str) -> int:
    def _extract_years(part: str) -> list[int]:
        years = [int(y) for y in re.findall(r"(?<!\d)(20\d{2})(?!\d)", part)]
        fy4 = [int(y) for y in re.findall(r"(?i)(?:^|[^a-z0-9])fy[\s_-]?(20\d{2})(?!\d)", part)]
        fy2 = []
        for y in re.findall(r"(?i)(?:^|[^a-z0-9])fy[\s_-]?(\d{2})(?!\d)", part):
            year = int(y)
            fy2.append(2000 + year if year <= 69 else 1900 + year)
        return years + fy4 + fy2

    for part in parts:
        years = _extract_years(part)
        if years:
            return max(years)
    return 0


def semipublic_station_name_from_stem(stem: str) -> str:
    text = stem
    text = re.sub(r"^\d+[-_ ]*", "", text)
    text = re.sub(r"[_-](AFR|AFRS|FSR|FSRS|AFS)(?:[_-]?\d{4})?$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(AFR|AFRS|FSR|FSRS|AFS)\b(?:[_ -]?\d{4})?$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[_-]+\d{4}$", "", text)
    text = re.sub(r"\s+", " ", text.replace("_", " ")).strip(" -_")
    return text or stem


def semipublic_canonical_name(name: str) -> str:
    # Collapse obvious service suffixes so queries like "WMHT" can match FM/TV variants together.
    normalized = re.sub(r"\b(?:FM|AM|TV)\b$", "", name, flags=re.IGNORECASE)
    normalized = re.sub(r"\s+", " ", normalized).strip(" -_")
    return normalized or name


def build_audit_watch_record(
    path: Path,
    archive_root: Path,
    station_rows: dict[str, dict[str, str]],
    canonical_map: dict[str, tuple[str, str]],
) -> DocRecord | None:
    rel = path.relative_to(archive_root)
    if len(rel.parts) < 2:
        return None
    station_id = rel.parts[0]
    station_name = station_rows.get(station_id, {}).get("station_name", station_id)
    canonical_station_id, canonical_station_name = canonical_map.get(
        station_id, (station_id, station_name)
    )
    return DocRecord(
        source_kind="audit_watch",
        source_label="audit-watch",
        station_id=station_id,
        station_name=station_name,
        canonical_station_id=canonical_station_id,
        canonical_station_name=canonical_station_name,
        title=title_from_filename(path.name),
        source_path=rel.as_posix(),
        report_year=infer_report_year(path.name, rel.as_posix()),
        dedupe_scope=canonical_station_id,
    )


def build_semipublic_record(path: Path, semipublic_root: Path) -> DocRecord:
    rel = path.relative_to(semipublic_root)
    station_name = semipublic_station_name_from_stem(path.stem)
    canonical_station_name = semipublic_canonical_name(station_name)
    canonical_station_id = slugify(canonical_station_name)
    return DocRecord(
        source_kind="semipublic",
        source_label="semipublic",
        station_id=slugify(station_name),
        station_name=station_name,
        canonical_station_id=canonical_station_id,
        canonical_station_name=canonical_station_name,
        title=title_from_filename(path.name),
        source_path=rel.as_posix(),
        report_year=infer_report_year(path.name, rel.as_posix()),
        dedupe_scope=f"semipublic:{canonical_station_id}",
    )


def remap_semipublic_record(
    record: DocRecord,
    alias_map: dict[str, tuple[str, str]],
) -> DocRecord:
    keys = [
        slugify(record.station_name),
        slugify(record.canonical_station_name),
        slugify(" ".join(normalized_name_tokens(record.station_name))),
        slugify(" ".join(normalized_name_tokens(record.canonical_station_name))),
    ]
    for variant in sorted(call_sign_variants(record.station_name)):
        keys.append(slugify(variant))

    for key in keys:
        if not key:
            continue
        target = alias_map.get(key)
        if not target:
            continue
        canonical_station_id, canonical_station_name = target
        return DocRecord(
            source_kind=record.source_kind,
            source_label=record.source_label,
            station_id=record.station_id,
            station_name=record.station_name,
            canonical_station_id=canonical_station_id,
            canonical_station_name=canonical_station_name,
            title=record.title,
            source_path=record.source_path,
            report_year=record.report_year,
            dedupe_scope=f"merged:{canonical_station_id}",
        )
    return record


def extract_text(path: Path, max_chars: int) -> tuple[str, bool]:
    try:
        proc = subprocess.run(
            ["pdftotext", "-q", str(path), "-"],
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0 and proc.stdout:
            text = proc.stdout[:max_chars]
            return text, True
    except FileNotFoundError:
        pass

    # Fallback: binary strings extraction (lower quality).
    try:
        proc = subprocess.run(
            ["strings", str(path)],
            check=False,
            capture_output=True,
            text=True,
            errors="ignore",
        )
        text = (proc.stdout or "")[:max_chars]
        return text, False
    except Exception:
        return "", False


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def insert_doc(
    conn,
    record: DocRecord,
    path: Path,
    sha: str,
    content_text: str,
    extracted_ok: bool,
) -> bool:
    st = path.stat()
    doc_id = hashlib.sha256(
        f"{record.source_kind}|{record.canonical_station_id}|{record.source_path}|{sha}".encode("utf-8")
    ).hexdigest()[:24]
    cur = conn.execute("SELECT 1 FROM docs WHERE doc_id = ?", (doc_id,))
    exists = cur.fetchone() is not None
    conn.execute(
        """
        INSERT OR REPLACE INTO docs(
          doc_id, source_kind, source_label, station_id, station_name,
          canonical_station_id, canonical_station_name,
          title, file_path, source_path, file_name, file_sha256, content_text,
          report_year, extracted_ok, file_mtime, file_size
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            doc_id,
            record.source_kind,
            record.source_label,
            record.station_id,
            record.station_name,
            record.canonical_station_id,
            record.canonical_station_name,
            record.title,
            path.as_posix(),
            record.source_path,
            path.name,
            sha,
            content_text,
            record.report_year,
            1 if extracted_ok else 0,
            float(st.st_mtime),
            int(st.st_size),
        ),
    )
    return exists


def rebuild_station_aliases(
    conn,
    station_rows: dict[str, dict[str, str]],
    canonical_map: dict[str, tuple[str, str]],
) -> None:
    conn.execute("DELETE FROM station_aliases")
    alias_records: dict[str, tuple[str, str, str]] = {}

    def add_alias(alias: str, canonical_station_id: str, canonical_station_name: str, alias_source: str) -> None:
        normalized = alias.strip().lower()
        if not normalized:
            return
        existing = alias_records.get(normalized)
        candidate = (canonical_station_id, canonical_station_name, alias_source)
        if existing is None or alias_source == "manual":
            alias_records[normalized] = candidate

    for sid, row in station_rows.items():
        canonical_station_id, canonical_station_name = canonical_map.get(
            sid, (sid, row.get("station_name", sid))
        )
        for name in {
            sid,
            sid.replace("-", " "),
            row.get("station_name", ""),
            canonical_station_name,
        }:
            for alias in alias_variants(str(name or "")):
                add_alias(alias, canonical_station_id, canonical_station_name, "derived")

    rows = conn.execute(
        """
        SELECT DISTINCT station_id, station_name, canonical_station_id, canonical_station_name
        FROM docs
        """
    ).fetchall()
    for row in rows:
        for name in {
            row["station_id"],
            str(row["station_id"]).replace("-", " "),
            row["station_name"],
            row["canonical_station_id"],
            row["canonical_station_name"],
        }:
            for alias in alias_variants(str(name or "")):
                add_alias(
                    alias,
                    str(row["canonical_station_id"]),
                    str(row["canonical_station_name"]),
                    "derived",
                )

    for canonical_station_id, aliases in MANUAL_STATION_ALIASES.items():
        canonical_id, canonical_name = canonical_map.get(
            canonical_station_id, (canonical_station_id, canonical_station_id)
        )
        for alias in aliases:
            add_alias(alias, canonical_id, canonical_name, "manual")

    conn.executemany(
        """
        INSERT OR REPLACE INTO station_aliases(alias, canonical_station_id, canonical_station_name, alias_source)
        VALUES (?, ?, ?, ?)
        """,
        [(alias, cid, cname, source) for alias, (cid, cname, source) in sorted(alias_records.items())],
    )
    conn.commit()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    archive_root = Path(args.archive_root)
    station_rows = load_station_rows(Path(args.stations))
    canonical_map = build_canonical_map(station_rows)
    semipublic_alias_map = build_semipublic_alias_map(station_rows, canonical_map)
    semipublic_root = Path(args.semipublic_root).expanduser() if args.semipublic_root else None
    db_path = Path(args.db)

    if not archive_root.exists():
        print(f"Archive root not found: {archive_root}")
        return 2
    if semipublic_root and not semipublic_root.exists():
        print(f"Semipublic root not found: {semipublic_root}")
        return 2

    if db_path.exists():
        db_path.unlink()

    conn = connect(args.db)
    init_schema(conn)

    files = [p for p in archive_root.rglob("*.pdf") if p.is_file()]
    semipublic_files: list[Path] = []
    if semipublic_root:
        semipublic_files = [p for p in semipublic_root.rglob("*.pdf") if p.is_file()]

    inserted = 0
    updated = 0
    deduped = 0
    seen_station_hashes: set[tuple[str, str]] = set()
    for path in files:
        record = build_audit_watch_record(path, archive_root, station_rows, canonical_map)
        if record is None:
            continue
        content_text, extracted_ok = extract_text(path, args.max_text_chars)
        sha = file_sha256(path)
        dedupe_key = (record.dedupe_scope, sha)
        if dedupe_key in seen_station_hashes:
            deduped += 1
            continue
        seen_station_hashes.add(dedupe_key)
        exists = insert_doc(conn, record, path, sha, content_text, extracted_ok)
        if exists:
            updated += 1
        else:
            inserted += 1

    for path in semipublic_files:
        record = remap_semipublic_record(
            build_semipublic_record(path, semipublic_root),
            semipublic_alias_map,
        )
        content_text, extracted_ok = extract_text(path, args.max_text_chars)
        sha = file_sha256(path)
        dedupe_key = (record.dedupe_scope, sha)
        if dedupe_key in seen_station_hashes:
            deduped += 1
            continue
        seen_station_hashes.add(dedupe_key)
        exists = insert_doc(conn, record, path, sha, content_text, extracted_ok)
        if exists:
            updated += 1
        else:
            inserted += 1

    rebuild_station_aliases(conn, station_rows, canonical_map)
    rebuild_fts(conn)

    print(f"Audit-watch PDF files seen: {len(files)}")
    print(f"Semipublic PDF files seen: {len(semipublic_files)}")
    print(f"Inserted docs: {inserted}")
    print(f"Updated docs: {updated}")
    print(f"Deduped docs skipped: {deduped}")
    print(f"DB: {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
