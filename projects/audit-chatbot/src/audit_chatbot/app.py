from __future__ import annotations

import os
import secrets
import re
import json
from base64 import b64decode
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest, urlopen

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from audit_chatbot.db import connect
from audit_chatbot.query import collect_risks, docs_for_station, resolve_station_ids


ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = ROOT / "web"
DEFAULT_DB_PATH = str(ROOT / "output" / "audit-chatbot.db")

app = FastAPI(title="Audit Chatbot")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


class QueryRequest(BaseModel):
    command: str = Field(pattern="^(summary|docs|risks|search)$")
    query: str = Field(min_length=1)
    limit: int = Field(default=8, ge=1, le=50)
    strict: bool = False
    watchlist: bool = False
    path_date: str | None = None
    model: str = "gpt-4.1-mini"


def get_db_path() -> str:
    return os.environ.get("AUDIT_CHATBOT_DB_PATH", DEFAULT_DB_PATH)


def _parse_basic_auth(authorization: str | None) -> tuple[str, str] | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "basic" or not token:
        return None
    try:
        decoded = b64decode(token).decode("utf-8")
    except Exception:
        return None
    username, sep, password = decoded.partition(":")
    if not sep:
        return None
    return username, password


@app.middleware("http")
async def basic_auth_gate(request: Request, call_next):  # type: ignore[no-untyped-def]
    if request.url.path == "/healthz":
        return await call_next(request)

    auth_user = os.environ.get("AUDIT_CHATBOT_AUTH_USERNAME")
    auth_pass = os.environ.get("AUDIT_CHATBOT_AUTH_PASSWORD")
    auth_enabled = bool(auth_user or auth_pass)

    if not auth_enabled:
        return await call_next(request)
    if not auth_user or not auth_pass:
        return JSONResponse(
            status_code=500,
            content={
                "detail": (
                    "Auth misconfigured: set both AUDIT_CHATBOT_AUTH_USERNAME "
                    "and AUDIT_CHATBOT_AUTH_PASSWORD."
                )
            },
        )

    parsed = _parse_basic_auth(request.headers.get("Authorization"))
    valid = (
        parsed is not None
        and secrets.compare_digest(parsed[0], auth_user)
        and secrets.compare_digest(parsed[1], auth_pass)
    )
    if valid:
        return await call_next(request)

    return JSONResponse(
        status_code=401,
        content={"detail": "Authentication required."},
        headers={"WWW-Authenticate": "Basic"},
    )


@app.get("/")
def home() -> FileResponse:
    return FileResponse(str(WEB_DIR / "index.html"))


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    db = connect(get_db_path())
    doc_count = db.execute("SELECT COUNT(*) AS n FROM docs").fetchone()["n"]
    station_count = db.execute(
        "SELECT COUNT(DISTINCT canonical_station_id) AS n FROM docs"
    ).fetchone()["n"]
    db.close()
    return {
        "ok": True,
        "db_path": get_db_path(),
        "documents": doc_count,
        "stations": station_count,
    }


def _format_docs(rows: list[dict[str, Any]], *, include_header: bool = False) -> str:
    if not rows:
        return "No matching documents."
    lines: list[str] = []
    if include_header:
        lines.append(f"Documents: {len(rows)}")
    for r in rows:
        lines.append(f"- {r['title']}")
        lines.append(f"  source: {r['file_path']}")
    return "\n".join(lines)


def _extract_year(row: dict[str, Any]) -> int:
    title = str(row["title"] or "")
    m = re.search(r"\b(20\d{2})\b", title)
    if m:
        return int(m.group(1))
    path = str(row["file_path"] or "")
    m = re.search(r"/(20\d{2})-\d{2}-\d{2}/", path)
    if m:
        return int(m.group(1))
    return 0


def _clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_highlight(content_text: str) -> str:
    if not content_text:
        return "No extracted text available."
    text = _clean_text(content_text)
    # Prefer materially useful finance/audit cues over generic prose.
    priority_patterns = [
        r"going concern[^.]{0,220}\.",
        r"material weakness[^.]{0,220}\.",
        r"significant deficien[^.]{0,220}\.",
        r"deficit[^.]{0,220}\.",
        r"line of credit[^.]{0,220}\.",
        r"debt[^.]{0,220}\.",
        r"net assets[^.]{0,220}\.",
        r"cash flows?[^.]{0,220}\.",
        r"operating expenses[^.]{0,220}\.",
    ]
    for pat in priority_patterns:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            sentence = _clean_text(m.group(0))
            if len(sentence) > 260:
                sentence = sentence[:257] + "..."
            return sentence
    # Fallback to first sentence-like chunk.
    m = re.search(r"[^.]{25,260}\.", text)
    if m:
        return _clean_text(m.group(0))
    return (text[:257] + "...") if len(text) > 260 else text


SUMMARY_SYSTEM_PROMPT = """You are an investigative newsroom assistant focused on station audits and financial filings.
Write a coherent, chronological narrative summary grounded ONLY in the provided document excerpts.
Prioritize significant financial or audit signals (changes in deficits/surpluses, debt/liquidity mentions, going-concern language, internal-control findings, notable trend shifts).
Avoid generic boilerplate unless it materially changes interpretation.
Use citations inline in square brackets like [S1], [S2].
If evidence is weak, say so explicitly.
"""


def _extract_response_text(payload: dict[str, Any]) -> str:
    direct = str(payload.get("output_text") or "").strip()
    if direct:
        return direct
    parts: list[str] = []
    for item in payload.get("output") or []:
        for content in item.get("content") or []:
            text = content.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
    return "\n\n".join(parts).strip()


def _build_summary_context(rows: list[Any], max_docs: int) -> tuple[str, list[dict[str, str]]]:
    ordered = sorted(rows, key=lambda r: (_extract_year(r), float(r["file_mtime"] or 0.0), str(r["title"])))
    sources: list[dict[str, str]] = []
    parts: list[str] = []
    for idx, row in enumerate(ordered[:max_docs], start=1):
        source_id = f"S{idx}"
        title = str(row["title"] or "")
        year = _extract_year(row)
        year_label = str(year) if year else "undated"
        path = str(row["file_path"] or "")
        content = _clean_text(str(row["content_text"] or ""))
        excerpt = content[:1800] if content else "(no extracted text)"
        parts.append(
            f"[{source_id}] year={year_label} title={title}\nsource={path}\nexcerpt={excerpt}"
        )
        sources.append(
            {
                "source_id": source_id,
                "title": title,
                "file_path": path,
                "year": year_label,
            }
        )
    return "\n\n".join(parts), sources


def _ai_chronological_summary(
    conn, station_ids: list[str], limit: int, model: str
) -> tuple[str, list[dict[str, str]], str, str]:
    placeholders = ",".join(["?"] * len(station_ids))
    rows = conn.execute(
        f"""
        SELECT canonical_station_id, canonical_station_name, title, file_path, file_mtime, content_text
        FROM docs
        WHERE canonical_station_id IN ({placeholders})
        ORDER BY file_mtime ASC
        LIMIT ?
        """,
        (*station_ids, 40),
    ).fetchall()
    if not rows:
        return "No matching documents.", [], "empty", ""

    station_name = str(rows[0]["canonical_station_name"])
    station_id = str(rows[0]["canonical_station_id"])
    context, sources = _build_summary_context(rows, max_docs=max(10, min(limit, 20)))

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        # Deterministic fallback if LLM key is not configured.
        return _chronological_summary(conn, station_ids, limit), sources, "fallback:no_api_key", ""

    user_prompt = (
        f"Station: {station_name} ({station_id})\n\n"
        "Task: Write a chronological summary in plain newsroom language. "
        "Surface the most newsworthy/significant details and trends. "
        "Include citations like [S1], [S2] at sentence level.\n\n"
        f"Source excerpts:\n{context}\n\n"
        "Output format:\n"
        "1) One short thesis paragraph.\n"
        "2) A chronological narrative (oldest to newest).\n"
        "3) A brief 'What stands out' section.\n"
    )
    try:
        body = {
            "model": model,
            "input": [
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        }
        req = UrlRequest(
            "https://api.openai.com/v1/responses",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(req, timeout=45) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        text = _extract_response_text(payload)
        if not text:
            raise RuntimeError("Empty model output")
        return text, sources, "ai", ""
    except HTTPError as err:
        detail = ""
        try:
            body = err.read().decode("utf-8", errors="ignore")
            data = json.loads(body) if body else {}
            detail = str(data.get("error", {}).get("message") or body)[:280]
        except Exception:
            detail = str(err)[:280]
        return (
            _chronological_summary(conn, station_ids, limit),
            sources,
            f"fallback:openai_request_failed:http_{err.code}",
            detail,
        )
    except URLError as err:
        return (
            _chronological_summary(conn, station_ids, limit),
            sources,
            "fallback:openai_request_failed:network",
            str(err)[:280],
        )
    except Exception as err:
        # Preserve availability even if model call fails.
        return (
            _chronological_summary(conn, station_ids, limit),
            sources,
            "fallback:openai_request_failed",
            str(err)[:280],
        )


def _chronological_summary(conn, station_ids: list[str], limit: int) -> str:
    placeholders = ",".join(["?"] * len(station_ids))
    rows = conn.execute(
        f"""
        SELECT canonical_station_id, canonical_station_name, title, file_path, file_mtime, content_text
        FROM docs
        WHERE canonical_station_id IN ({placeholders})
        ORDER BY file_mtime ASC
        LIMIT ?
        """,
        (*station_ids, max(limit, 20)),
    ).fetchall()
    if not rows:
        return "No matching documents."

    # Sort by inferred report year first, then by file_mtime for stable timeline order.
    ordered = sorted(rows, key=lambda r: (_extract_year(r), float(r["file_mtime"] or 0.0), str(r["title"])))
    station_name = ordered[0]["canonical_station_name"]
    station_id = ordered[0]["canonical_station_id"]

    lines = [
        f"Chronological summary for {station_name} ({station_id})",
        f"Documents considered: {len(ordered)}",
        "",
    ]
    for row in ordered[: max(8, min(len(ordered), limit))]:
        year = _extract_year(row)
        label = str(year) if year else "Undated"
        title = str(row["title"])
        highlight = _extract_highlight(str(row["content_text"] or ""))
        lines.append(f"- {label}: {title}")
        lines.append(f"  highlight: {highlight}")
        lines.append(f"  source: {row['file_path']}")
    return "\n".join(lines)


def _search(conn, phrase: str, limit: int) -> str:
    rows = conn.execute(
        """
        SELECT d.canonical_station_name, d.canonical_station_id, d.title, d.file_path,
               snippet(docs_fts, 5, '[', ']', ' … ', 20) AS snippet
        FROM docs_fts
        JOIN docs d ON d.rowid = docs_fts.rowid
        WHERE docs_fts MATCH ?
        LIMIT ?
        """,
        (phrase, limit),
    ).fetchall()
    if not rows:
        return "No matches."
    lines = []
    for r in rows:
        lines.append(f"- {r['canonical_station_name']} ({r['canonical_station_id']}): {r['title']}")
        lines.append(f"  snippet: {r['snippet']}")
        lines.append(f"  source: {r['file_path']}")
    return "\n".join(lines)


@app.post("/api/query")
def api_query(request: QueryRequest) -> dict[str, Any]:
    db = connect(get_db_path())
    try:
        command = request.command
        query_text = request.query.strip()
        limit = request.limit

        if command == "search":
            return {"output": _search(db, query_text, limit)}

        station_ids = resolve_station_ids(db, query_text)
        if not station_ids:
            raise HTTPException(status_code=404, detail=f"No station match for: {query_text}")

        if command == "docs":
            rows = docs_for_station(db, station_ids, limit)
            return {"output": _format_docs(rows)}

        if command == "summary":
            text, sources, mode, error = _ai_chronological_summary(db, station_ids, limit, request.model)
            return {"output": text, "sources": sources, "summary_mode": mode, "summary_error": error}

        if command == "risks":
            strict = bool(request.strict)
            watchlist = bool(request.watchlist)
            if strict and watchlist:
                raise HTTPException(status_code=400, detail="Choose either strict or watchlist, not both.")
            findings, watch_findings = collect_risks(
                db,
                station_ids,
                strict=strict,
                watchlist=watchlist,
                path_date=(request.path_date or "").strip(),
            )
            output_hits = findings[:]
            if watchlist and len(output_hits) < limit:
                output_hits.extend(watch_findings[: max(0, limit - len(output_hits))])
            if not output_hits:
                return {
                    "output": "No risk keywords found in available extracted text.\nNote: This depends on PDF text extraction quality."
                }
            lines = []
            if strict:
                lines.append("Potential risk signals (strict):")
            elif watchlist:
                lines.append("Potential risk signals (watchlist):")
            else:
                lines.append("Potential risk signals:")
            for pat, r, snip, _, confidence in output_hits[:limit]:
                lines.append(f"- [{pat}] {r['canonical_station_name']} ({r['canonical_station_id']}): {r['title']}")
                lines.append(f"  confidence: {confidence}")
                lines.append(f"  snippet: {snip[:260]}...")
                lines.append(f"  source: {r['file_path']}")
            return {"output": "\n".join(lines)}

        raise HTTPException(status_code=400, detail=f"Unsupported command: {command}")
    finally:
        db.close()
