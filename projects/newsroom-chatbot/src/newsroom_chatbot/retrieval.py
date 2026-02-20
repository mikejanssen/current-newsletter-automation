import json
import math
import re
import sqlite3
from dataclasses import dataclass
from typing import Any


@dataclass
class RetrievedChunk:
    chunk_id: int
    article_id: int
    title: str
    url: str
    published_at: str | None
    content: str
    score: float


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


STOPWORDS = {
    "a",
    "about",
    "again",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "can",
    "could",
    "for",
    "from",
    "have",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "our",
    "that",
    "the",
    "there",
    "to",
    "us",
    "we",
    "what",
    "when",
    "where",
    "which",
    "who",
    "would",
    "with",
    "reported",
    "reporting",
    "reports",
    "story",
    "stories",
    "article",
    "articles",
    "find",
    "show",
    "tell",
    "coverage",
}


def _normalize_query(text: str) -> str:
    lowered = text.lower()
    cleaned = re.sub(r"[^a-z0-9\\s]", " ", lowered)
    return re.sub(r"\\s+", " ", cleaned).strip()


def _extract_phrase_candidates(query_text: str) -> list[str]:
    normalized = _normalize_query(query_text)
    if not normalized:
        return []

    candidates: list[str] = []

    # Prefer explicit quoted phrase if user provided one.
    quoted = re.findall(r'"([^"]+)"', query_text)
    for q in quoted:
        nq = _normalize_query(q)
        if len(nq) >= 4:
            candidates.append(nq)

    # Common intent pattern: "about <entity>".
    m_about = re.search(r"\babout\s+(.+)$", normalized)
    if m_about:
        about_phrase = m_about.group(1).strip()
        if len(about_phrase) >= 4:
            candidates.append(about_phrase)

    # Fallback to non-stopword tail phrase.
    tokens = normalized.split()
    content_tokens = [t for t in tokens if t not in STOPWORDS]
    if content_tokens:
        # Include short tokens (e.g., "lab") inside phrase candidates.
        for size in (3, 2):
            if len(content_tokens) >= size:
                phrase = " ".join(content_tokens[-size:])
                if len(phrase) >= 4:
                    candidates.append(phrase)
        full = " ".join(content_tokens)
        if len(full) >= 4:
            candidates.append(full)

    # Deduplicate while preserving order.
    deduped: list[str] = []
    seen: set[str] = set()
    for c in candidates:
        if c not in seen:
            deduped.append(c)
            seen.add(c)
    return deduped


def _lexical_rescue_candidates(
    conn: sqlite3.Connection,
    *,
    query_text: str,
    top_k: int,
    date_from: str | None,
    date_to: str | None,
    top_score: float,
) -> list[tuple[int, int, float]]:
    normalized = _normalize_query(query_text)
    if len(normalized) < 3:
        return []
    phrase_candidates = _extract_phrase_candidates(query_text)
    if not phrase_candidates:
        phrase_candidates = [normalized]

    base_sql = """
    SELECT
      c.id AS chunk_id,
      c.article_id AS article_id,
      a.title AS title,
      c.content AS content
    FROM chunks c
    JOIN articles a ON a.id = c.article_id
    WHERE c.embedding_json IS NOT NULL
    """
    base_params: list[str] = []
    if date_from:
        base_sql += " AND (a.published_at IS NOT NULL AND a.published_at >= ?)"
        base_params.append(date_from)
    if date_to:
        base_sql += " AND (a.published_at IS NOT NULL AND a.published_at <= ?)"
        base_params.append(date_to)

    by_article: dict[int, tuple[int, int, float]] = {}

    # Exact phrase search catches named entities that embeddings can miss.
    for phrase in phrase_candidates[:4]:
        phrase_like = f"%{phrase}%"
        phrase_sql = (
            base_sql
            + """
        AND (LOWER(a.title) LIKE ? OR LOWER(c.content) LIKE ?)
        ORDER BY c.id
        LIMIT ?
        """
        )
        phrase_rows = conn.execute(
            phrase_sql,
            [*base_params, phrase_like, phrase_like, top_k * 10],
        ).fetchall()
        for row in phrase_rows:
            article_id = int(row["article_id"])
            if article_id in by_article:
                continue
            title = str(row["title"] or "").lower()
            score = top_score + (0.08 if phrase in title else 0.06)
            by_article[article_id] = (int(row["chunk_id"]), article_id, score)

    terms = [t for t in normalized.split() if len(t) >= 3 and t not in STOPWORDS]
    terms = terms[:6]
    if terms:
        # Broader fallback: any one term can match, with score by number of term hits.
        term_clauses = " OR ".join("(LOWER(a.title) LIKE ? OR LOWER(c.content) LIKE ?)" for _ in terms)
        term_params: list[str] = []
        for term in terms:
            like = f"%{term}%"
            term_params.extend([like, like])
        term_sql = (
            base_sql
            + f"""
        AND ({term_clauses})
        ORDER BY c.id
        LIMIT ?
        """
        )
        term_rows = conn.execute(term_sql, [*base_params, *term_params, top_k * 20]).fetchall()
        for row in term_rows:
            article_id = int(row["article_id"])
            if article_id in by_article:
                continue
            hay = f"{str(row['title'] or '').lower()} {str(row['content'] or '').lower()}"
            hits = sum(1 for t in terms if t in hay)
            if hits <= 0:
                continue
            score = top_score + min(0.05, 0.01 * hits)
            by_article[article_id] = (int(row["chunk_id"]), article_id, score)

    candidates = sorted(by_article.values(), key=lambda x: x[2], reverse=True)
    return candidates[: min(top_k, 4)]


def search_chunks(
    conn: sqlite3.Connection,
    *,
    query_text: str,
    query_embedding: list[float],
    top_k: int = 8,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[RetrievedChunk]:
    sql = """
    SELECT
      c.id AS chunk_id,
      c.article_id AS article_id,
      c.embedding_json AS embedding_json
    FROM chunks c
    JOIN articles a ON a.id = c.article_id
    WHERE c.embedding_json IS NOT NULL
    """
    params: list[str] = []
    if date_from:
        sql += " AND (a.published_at IS NOT NULL AND a.published_at >= ?)"
        params.append(date_from)
    if date_to:
        sql += " AND (a.published_at IS NOT NULL AND a.published_at <= ?)"
        params.append(date_to)
    sql += " ORDER BY c.id"

    best_by_article: dict[int, tuple[int, float]] = {}
    for row in conn.execute(sql, params):
        embedding = json.loads(row["embedding_json"])
        score = cosine_similarity(query_embedding, embedding)
        article_id = int(row["article_id"])
        chunk_id = int(row["chunk_id"])
        prev = best_by_article.get(article_id)
        if prev is None or score > prev[1]:
            best_by_article[article_id] = (chunk_id, score)

    if not best_by_article:
        return []

    scored = sorted(
        ((article_id, chunk_id, score) for article_id, (chunk_id, score) in best_by_article.items()),
        key=lambda item: item[2],
        reverse=True,
    )
    top_score = scored[0][2]
    min_score = max(0.12, top_score * 0.55)
    minimum_results = min(3, top_k)

    selected: list[tuple[int, int, float]] = [item for item in scored if item[2] >= min_score][:top_k]

    # If thresholding is too strict, backfill with next-best unique articles.
    if len(selected) < minimum_results:
        selected = scored[:minimum_results]

    lexical = _lexical_rescue_candidates(
        conn,
        query_text=query_text,
        top_k=top_k,
        date_from=date_from,
        date_to=date_to,
        top_score=top_score,
    )
    if lexical:
        seen = {article_id for article_id, _, _ in selected}
        merged = list(lexical)
        seen.update(article_id for _, article_id, _ in lexical)
        for item in selected:
            if item[0] in seen:
                continue
            merged.append(item)
            seen.add(item[0])
            if len(merged) >= top_k:
                break
        selected = merged[:top_k]

    selected = selected[:top_k]
    if not selected:
        return []

    selected_chunk_ids = [chunk_id for _, chunk_id, _ in selected]
    placeholders = ",".join("?" for _ in selected_chunk_ids)
    details_sql = f"""
    SELECT
      c.id AS chunk_id,
      c.article_id AS article_id,
      c.content AS content,
      a.title AS title,
      a.url AS url,
      a.published_at AS published_at
    FROM chunks c
    JOIN articles a ON a.id = c.article_id
    WHERE c.id IN ({placeholders})
    """
    details_rows = conn.execute(details_sql, selected_chunk_ids).fetchall()
    details_by_chunk_id: dict[int, sqlite3.Row] = {int(row["chunk_id"]): row for row in details_rows}

    output: list[RetrievedChunk] = []
    for _, chunk_id, score in selected:
        row: Any = details_by_chunk_id.get(chunk_id)
        if row is None:
            continue
        output.append(
            RetrievedChunk(
                chunk_id=int(row["chunk_id"]),
                article_id=int(row["article_id"]),
                title=str(row["title"]),
                url=str(row["url"]),
                published_at=row["published_at"],
                content=str(row["content"]),
                score=score,
            )
        )
    return output
