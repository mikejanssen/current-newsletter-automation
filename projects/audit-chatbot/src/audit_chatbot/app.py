from __future__ import annotations

import os
import secrets
import re
import json
from base64 import b64decode
from collections import defaultdict
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
COMBINED_DB_PATH = str(ROOT / "output" / "audit-chatbot-combined.db")

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
    command: str = Field(pattern="^(summary|docs|risks|search|ask)$")
    query: str = Field(min_length=1)
    station: str | None = None
    limit: int = Field(default=8, ge=1, le=50)
    strict: bool = False
    watchlist: bool = False
    path_date: str | None = None
    model: str = "gpt-4.1-mini"


def get_db_path() -> str:
    configured = os.environ.get("AUDIT_CHATBOT_DB_PATH", "").strip()
    if configured:
        return configured
    if Path(COMBINED_DB_PATH).exists():
        return COMBINED_DB_PATH
    return DEFAULT_DB_PATH


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
    by_source_rows = db.execute(
        """
        SELECT source_label, COUNT(*) AS n
        FROM docs
        GROUP BY source_label
        ORDER BY source_label
        """
    ).fetchall()
    db.close()
    return {
        "ok": True,
        "db_path": get_db_path(),
        "documents": doc_count,
        "stations": station_count,
        "documents_by_source": {row["source_label"]: row["n"] for row in by_source_rows},
    }


def _format_docs(rows: list[dict[str, Any]], *, include_header: bool = False) -> str:
    if not rows:
        return "No matching documents."
    lines: list[str] = []
    if include_header:
        lines.append(f"Documents: {len(rows)}")
    for r in rows:
        lines.append(f"- {r['title']}")
        lines.append(f"  corpus: {r['source_label']} :: {r['source_path']}")
        lines.append(f"  source: {r['file_path']}")
    return "\n".join(lines)


def _extract_year(row: dict[str, Any]) -> int:
    if "report_year" in row.keys():
        report_year = int(row["report_year"] or 0)
        if report_year:
            return report_year
    title = str(row["title"] or "")
    m = re.search(r"\b(20\d{2})\b", title)
    if m:
        return int(m.group(1))
    m = re.search(r"(?i)(?:^|[^a-z0-9])fy[\s_-]?(20\d{2})(?!\d)", title)
    if m:
        return int(m.group(1))
    m = re.search(r"(?i)(?:^|[^a-z0-9])fy[\s_-]?(\d{2})(?!\d)", title)
    if m:
        year = int(m.group(1))
        return 2000 + year if year <= 69 else 1900 + year
    path = str(row["file_path"] or "")
    m = re.search(r"/(20\d{2})-\d{2}-\d{2}/", path)
    if m:
        return int(m.group(1))
    return 0


def _clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _looks_low_quality(text: str) -> bool:
    if not text:
        return True
    sample = text[:300]
    ascii_letters = sum(1 for ch in sample if ch.isalpha())
    weird = sum(1 for ch in sample if ord(ch) > 127)
    return ascii_letters < 40 or weird > max(10, len(sample) // 10)


def _is_generic_audit_boilerplate(text: str) -> bool:
    normalized = _clean_text(text).lower()
    markers = [
        "we believe that the audit evidence we have obtained is sufficient",
        "identify and assess the risks of material misstatement",
        "the procedures selected depend on our judgment",
        "reasonable assurance about whether the financial statements",
        "basis for our audit opinion",
        "our objectives are to obtain reasonable assurance",
        "whether due to fraud or error",
    ]
    return any(marker in normalized for marker in markers)


def _extract_highlight(content_text: str) -> str:
    if not content_text:
        return "No extracted text available."
    text = _clean_text(content_text)
    if _looks_low_quality(text):
        return "Low-quality extracted text; inspect the PDF directly."
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
            if not _is_generic_audit_boilerplate(sentence):
                return sentence
    # Fallback to first sentence-like chunk.
    m = re.search(r"[^.]{25,260}\.", text)
    if m:
        sentence = _clean_text(m.group(0))
        if not _is_generic_audit_boilerplate(sentence):
            return sentence
    return (text[:257] + "...") if len(text) > 260 else text


SUMMARY_SYSTEM_PROMPT = """You are an investigative newsroom assistant focused on station audits and financial filings.
Write a coherent, chronological narrative summary grounded ONLY in the provided document excerpts.
Prioritize significant financial or audit signals (changes in deficits/surpluses, debt/liquidity mentions, going-concern language, internal-control findings, notable trend shifts).
Avoid generic boilerplate unless it materially changes interpretation.
Use citations inline in square brackets like [S1], [S2].
If evidence is weak, say so explicitly.
"""

ASK_SYSTEM_PROMPT = """You are an investigative newsroom assistant focused on station audits and financial filings.
Answer the user's question using ONLY the provided excerpts.
Prioritize specific, document-grounded facts over generalities.
Be explicit when the evidence is weak, partial, or conflicting.
Use inline citations like [S1], [S2] at sentence level.
Do not fabricate figures or conclusions not supported by the excerpts.
"""

FINANCE_MARKERS = [
    "revenue",
    "revenues",
    "expense",
    "expenses",
    "operating",
    "net assets",
    "change in net assets",
    "debt",
    "line of credit",
    "liquidity",
    "cash",
    "cash flow",
    "deficit",
    "surplus",
    "unrestricted",
    "assets",
    "liabilities",
    "going concern",
    "material weakness",
    "significant deficiency",
    "underwriting",
    "memberships and subscriptions",
    "contributions",
    "grants",
]

FINANCE_SECTION_PATTERNS = [
    r"statements? of activities",
    r"statement of activities",
    r"statements? of financial position",
    r"statement of financial position",
    r"statements? of cash flows",
    r"statement of cash flows",
    r"statements? of functional expenses",
    r"statement of functional expenses",
    r"change in net assets",
    r"net assets without donor restrictions",
    r"net assets with donor restrictions",
    r"operating revenues",
    r"operating expenses",
    r"memberships and subscriptions",
    r"program and production underwriting",
    r"grants and contributions",
    r"community service grants",
]


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
        source_label = str(row["source_label"] or "")
        source_path = str(row["source_path"] or "")
        content = _clean_text(str(row["content_text"] or ""))
        excerpt = content[:1800] if content else "(no extracted text)"
        parts.append(
            f"[{source_id}] year={year_label} corpus={source_label} source_path={source_path}\n"
            f"title={title}\nsource={path}\nexcerpt={excerpt}"
        )
        sources.append(
            {
                "source_id": source_id,
                "source_label": source_label,
                "source_path": source_path,
                "title": title,
                "file_path": path,
                "year": year_label,
            }
        )
    return "\n\n".join(parts), sources


def _question_tokens(question: str) -> list[str]:
    stop = {
        "the", "and", "for", "with", "from", "that", "this", "what", "which", "have",
        "has", "had", "were", "was", "are", "about", "their", "there", "would", "could",
        "should", "into", "over", "under", "after", "before", "when", "where", "why",
        "how", "does", "did", "show", "tell", "than", "them", "they", "been", "your",
        "you", "can", "any", "our", "out", "get", "based",
    }
    tokens = re.findall(r"[a-z0-9]{3,}", question.lower())
    return [t for t in tokens if t not in stop]


def _question_seems_trend_like(question: str) -> bool:
    q = question.lower()
    markers = [
        "past five years",
        "last five years",
        "over time",
        "trend",
        "trends",
        "recent years",
        "how have",
        "how has",
        "over the past",
        "in recent years",
    ]
    return any(marker in q for marker in markers)


def _year_coverage(rows: list[Any]) -> list[int]:
    years = sorted({year for year in (_extract_year(row) for row in rows) if year})
    return years


def _split_text_chunks(text: str) -> list[str]:
    if not text:
        return []
    cleaned = _clean_text(text)
    if not cleaned:
        return []
    paragraphs = re.split(r"\n\s*\n", text)
    chunks: list[str] = []
    for para in paragraphs:
        para_clean = _clean_text(para)
        if not para_clean:
            continue
        if len(para_clean) <= 700:
            chunks.append(para_clean)
            continue
        sentences = re.split(r"(?<=[.!?])\s+", para_clean)
        current = ""
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            candidate = f"{current} {sentence}".strip() if current else sentence
            if len(candidate) > 700 and current:
                chunks.append(current)
                current = sentence
            else:
                current = candidate
        if current:
            chunks.append(current)
    return chunks or [cleaned[:700]]


def _section_window(text: str, pattern: str, window: int = 1400) -> str | None:
    m = re.search(pattern, text, flags=re.IGNORECASE)
    if not m:
        return None
    start = max(0, m.start() - 180)
    end = min(len(text), m.end() + window)
    snippet = text[start:end]
    return _clean_text(snippet)


def _best_excerpt_for_question(content_text: str, tokens: list[str]) -> tuple[str, int]:
    text = _clean_text(content_text)
    if not text:
        return "No extracted text available.", 0
    if _looks_low_quality(text):
        return "Low-quality extracted text; inspect the PDF directly.", 0
    if not tokens:
        return _extract_highlight(text), 0

    sentence_like = re.split(r"(?<=[.!?])\s+", text)
    best = ""
    best_score = -1
    for sentence in sentence_like:
        normalized = sentence.lower()
        score = sum(1 for token in tokens if token in normalized)
        if _is_generic_audit_boilerplate(sentence):
            score -= 2
        if score > best_score and len(sentence.strip()) >= 25:
            best = sentence.strip()
            best_score = score
    if best_score <= 0:
        return _extract_highlight(text), 0
    if len(best) > 320:
        best = best[:317] + "..."
    return best, best_score


def _chunk_score(chunk: str, tokens: list[str], title: str, question: str) -> int:
    normalized = chunk.lower()
    score = 0
    score += sum(1 for token in tokens if token in normalized) * 4
    score += sum(1 for marker in FINANCE_MARKERS if marker in normalized) * 2
    if any(marker in normalized for marker in ["going concern", "material weakness", "significant deficiency", "debt", "deficit", "liquidity"]):
        score += 4
    if _question_seems_trend_like(question) and any(marker in normalized for marker in ["revenue", "expenses", "net assets", "cash", "operating", "change in net assets"]):
        score += 4
    if title and any(token in title.lower() for token in tokens):
        score += 2
    if _is_generic_audit_boilerplate(chunk):
        score -= 6
    return score


def _best_excerpts_for_row(row: Any, question: str, tokens: list[str], max_excerpts: int = 2) -> list[tuple[str, int]]:
    content = str(row["content_text"] or "")
    if not content:
        return [("No extracted text available.", 0)]
    text = _clean_text(content)
    if _looks_low_quality(text):
        return [("Low-quality extracted text; inspect the PDF directly.", 0)]

    title = str(row["title"] or "")
    chunks = _split_text_chunks(content)
    scored: list[tuple[str, int]] = []
    section_hits: list[tuple[str, int]] = []
    for pattern in FINANCE_SECTION_PATTERNS:
        window = _section_window(content, pattern)
        if not window:
            continue
        score = _chunk_score(window, tokens, title, question) + 8
        if score > 0:
            section_hits.append((window[:900] + "..." if len(window) > 900 else window, score))

    for chunk, score in section_hits:
        scored.append((chunk, score))
    for chunk in chunks:
        score = _chunk_score(chunk, tokens, title, question)
        if score <= 0:
            continue
        trimmed = chunk if len(chunk) <= 700 else chunk[:697] + "..."
        scored.append((trimmed, score))

    if not scored:
        excerpt, score = _best_excerpt_for_question(content, tokens)
        return [(excerpt, score)]

    scored.sort(key=lambda item: (-item[1], -len(item[0])))
    unique: list[tuple[str, int]] = []
    seen = set()
    for excerpt, score in scored:
        key = excerpt[:120]
        if key in seen:
            continue
        seen.add(key)
        unique.append((excerpt, score))
        if len(unique) >= max_excerpts:
            break
    return unique


def _rank_rows_for_question(rows: list[Any], question: str) -> list[dict[str, Any]]:
    tokens = _question_tokens(question)
    ranked: list[dict[str, Any]] = []
    for row in rows:
        title = str(row["title"] or "")
        content = str(row["content_text"] or "")
        excerpts = _best_excerpts_for_row(row, question, tokens, max_excerpts=2)
        excerpt, excerpt_score = excerpts[0]
        haystack = f"{title} {content}".lower()
        token_hits = sum(1 for token in tokens if token in haystack)
        finance_bonus = 0
        if any(marker in haystack for marker in FINANCE_MARKERS):
            finance_bonus = 1
        year_bonus = 2 if _extract_year(row) else 0
        score = token_hits * 4 + excerpt_score * 3 + finance_bonus + year_bonus
        ranked.append(
            {
                "row": row,
                "score": score,
                "excerpt": excerpt,
                "excerpts": excerpts,
            }
        )
    ranked.sort(
        key=lambda item: (
            -int(item["score"]),
            -_extract_year(item["row"]),
            -float(item["row"]["file_mtime"] or 0.0),
            str(item["row"]["title"] or ""),
        )
    )
    return ranked


def _build_question_context(
    rows: list[Any],
    question: str,
    max_docs: int,
) -> tuple[str, list[dict[str, str]]]:
    ranked = _rank_rows_for_question(rows, question)
    sources: list[dict[str, str]] = []
    parts: list[str] = []
    seen_paths: set[str] = set()
    used_years: set[int] = set()
    trend_like = _question_seems_trend_like(question)
    prioritized: list[dict[str, Any]] = []
    remainder: list[dict[str, Any]] = []
    year_counts: dict[int, int] = defaultdict(int)
    for item in ranked:
        year = _extract_year(item["row"])
        if trend_like and year and year not in used_years:
            prioritized.append(item)
            used_years.add(year)
        else:
            remainder.append(item)
    if trend_like:
        remainder.sort(
            key=lambda item: (
                _extract_year(item["row"]) or 9999,
                -int(item["score"]),
                str(item["row"]["title"] or ""),
            )
        )
        ordered_items = prioritized + remainder
    else:
        ordered_items = prioritized + remainder

    for item in ordered_items:
        row = item["row"]
        path = str(row["file_path"] or "")
        if path in seen_paths:
            continue
        year = _extract_year(row)
        if trend_like and year:
            if year_counts.get(year, 0) >= 2:
                continue
            year_counts[year] += 1
        seen_paths.add(path)
        if len(sources) >= max_docs:
            break
        source_id = f"S{len(sources) + 1}"
        title = str(row["title"] or "")
        year_label = str(year) if year else "undated"
        source_label = str(row["source_label"] or "")
        source_path = str(row["source_path"] or "")
        excerpts = [str(excerpt or "") for excerpt, _ in item.get("excerpts", []) if str(excerpt or "")]
        excerpt_block = "\n".join(f"- {excerpt}" for excerpt in excerpts[:2]) or f"- {str(item['excerpt'] or '')}"
        parts.append(
            f"[{source_id}] year={year_label} score={item['score']} corpus={source_label} "
            f"source_path={source_path}\n"
            f"title={title}\nsource={path}\nexcerpts:\n{excerpt_block}"
        )
        sources.append(
            {
                "source_id": source_id,
                "source_label": source_label,
                "source_path": source_path,
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
        SELECT canonical_station_id, canonical_station_name, source_label, source_path,
               title, file_path, file_mtime, content_text, report_year
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


def _deterministic_question_answer(
    conn,
    station_ids: list[str],
    question: str,
    limit: int,
) -> tuple[str, list[dict[str, str]], str, str]:
    placeholders = ",".join(["?"] * len(station_ids))
    rows = conn.execute(
        f"""
        SELECT canonical_station_id, canonical_station_name, source_label, source_path,
               title, file_path, file_mtime, content_text, report_year
        FROM docs
        WHERE canonical_station_id IN ({placeholders})
        ORDER BY file_mtime DESC
        LIMIT 80
        """,
        tuple(station_ids),
    ).fetchall()
    if not rows:
        return "No matching documents.", [], "empty", ""
    station_name = str(rows[0]["canonical_station_name"])
    station_id = str(rows[0]["canonical_station_id"])
    years = _year_coverage(rows)
    context, sources = _build_question_context(rows, question, max_docs=max(6, min(limit, 10)))
    lines = [
        f"Answer for {station_name} ({station_id})",
        f"Question: {question}",
        "",
    ]
    if _question_seems_trend_like(question):
        if years:
            lines.append(f"Indexed years available for this question: {', '.join(str(y) for y in years)}")
        else:
            lines.append("Indexed years available for this question: none identified")
        lines.append("")
    lines.extend([
        "Best matching excerpts:",
    ])
    for source in sources:
        source_id = source["source_id"]
        match = next((block for block in context.split("\n\n") if block.startswith(f"[{source_id}]")), "")
        excerpt = ""
        m = re.search(r"excerpts:\n(.*)$", match, flags=re.MULTILINE | re.DOTALL)
        if m:
            excerpt = m.group(1).strip()
        lines.append(f"- [{source_id}] {source['title']}")
        lines.append(f"  excerpt: {excerpt or 'No extracted text available.'}")
        lines.append(f"  corpus: {source['source_label']} :: {source['source_path']}")
    lines.append("")
    lines.append("AI answer unavailable because OPENAI_API_KEY is not configured.")
    return "\n".join(lines), sources, "fallback:no_api_key", ""


def _ai_station_answer(
    conn,
    station_ids: list[str],
    question: str,
    limit: int,
    model: str,
) -> tuple[str, list[dict[str, str]], str, str]:
    placeholders = ",".join(["?"] * len(station_ids))
    rows = conn.execute(
        f"""
        SELECT canonical_station_id, canonical_station_name, source_label, source_path,
               title, file_path, file_mtime, content_text, report_year
        FROM docs
        WHERE canonical_station_id IN ({placeholders})
        ORDER BY file_mtime DESC
        LIMIT 80
        """,
        tuple(station_ids),
    ).fetchall()
    if not rows:
        return "No matching documents.", [], "empty", ""

    station_name = str(rows[0]["canonical_station_name"])
    station_id = str(rows[0]["canonical_station_id"])
    years = _year_coverage(rows)
    context, sources = _build_question_context(rows, question, max_docs=max(6, min(limit, 10)))
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return _deterministic_question_answer(conn, station_ids, question, limit)

    user_prompt = (
        f"Station: {station_name} ({station_id})\n"
        f"Question: {question}\n\n"
        f"Indexed years available: {', '.join(str(y) for y in years) if years else 'unknown'}\n\n"
        "Answer in plain newsroom language. Start with the direct answer, then include a short "
        "'Evidence' section. For multi-year questions, synthesize across years instead of anchoring on one detail. "
        "If the documents do not fully answer the question, say what is missing and be explicit about the years covered.\n\n"
        f"Source excerpts:\n{context}\n"
    )
    try:
        body = {
            "model": model,
            "input": [
                {"role": "system", "content": ASK_SYSTEM_PROMPT},
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
        text, sources, _, _ = _deterministic_question_answer(conn, station_ids, question, limit)
        return text, sources, f"fallback:openai_request_failed:http_{err.code}", detail
    except URLError as err:
        text, sources, _, _ = _deterministic_question_answer(conn, station_ids, question, limit)
        return text, sources, "fallback:openai_request_failed:network", str(err)[:280]
    except Exception as err:
        text, sources, _, _ = _deterministic_question_answer(conn, station_ids, question, limit)
        return text, sources, "fallback:openai_request_failed", str(err)[:280]


def _chronological_summary(conn, station_ids: list[str], limit: int) -> str:
    placeholders = ",".join(["?"] * len(station_ids))
    rows = conn.execute(
        f"""
        SELECT canonical_station_id, canonical_station_name, source_label, source_path,
               title, file_path, file_mtime, content_text, report_year
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
        lines.append(f"  corpus: {row['source_label']} :: {row['source_path']}")
        lines.append(f"  highlight: {highlight}")
        lines.append(f"  source: {row['file_path']}")
    return "\n".join(lines)


def _search(conn, phrase: str, limit: int) -> str:
    rows = conn.execute(
        """
        SELECT d.canonical_station_name, d.canonical_station_id, d.source_label, d.source_path,
               d.title, d.file_path,
               snippet(docs_fts, 7, '[', ']', ' … ', 20) AS snippet
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
        lines.append(f"  corpus: {r['source_label']} :: {r['source_path']}")
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

        station_query = (request.station or query_text).strip()
        station_ids = resolve_station_ids(db, station_query)
        if not station_ids:
            raise HTTPException(status_code=404, detail=f"No station match for: {station_query}")

        if command == "docs":
            rows = docs_for_station(db, station_ids, limit)
            return {"output": _format_docs(rows)}

        if command == "summary":
            text, sources, mode, error = _ai_chronological_summary(db, station_ids, limit, request.model)
            return {"output": text, "sources": sources, "summary_mode": mode, "summary_error": error}

        if command == "ask":
            text, sources, mode, error = _ai_station_answer(
                db, station_ids, query_text, limit, request.model
            )
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
                lines.append(f"  corpus: {r['source_label']} :: {r['source_path']}")
                lines.append(f"  source: {r['file_path']}")
            return {"output": "\n".join(lines)}

        raise HTTPException(status_code=400, detail=f"Unsupported command: {command}")
    finally:
        db.close()
