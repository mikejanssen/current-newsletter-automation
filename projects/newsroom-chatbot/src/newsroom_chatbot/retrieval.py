import json
import math
import sqlite3
from dataclasses import dataclass


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


def search_chunks(
    conn: sqlite3.Connection,
    *,
    query_embedding: list[float],
    top_k: int = 8,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[RetrievedChunk]:
    sql = """
    SELECT
      c.id AS chunk_id,
      c.article_id AS article_id,
      c.content AS content,
      c.embedding_json AS embedding_json,
      a.title AS title,
      a.url AS url,
      a.published_at AS published_at
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

    scored: list[RetrievedChunk] = []
    for row in conn.execute(sql, params).fetchall():
        embedding = json.loads(row["embedding_json"])
        score = cosine_similarity(query_embedding, embedding)
        scored.append(
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

    scored.sort(key=lambda item: item.score, reverse=True)
    if not scored:
        return []

    top_score = scored[0].score
    min_score = max(0.12, top_score * 0.55)
    minimum_results = min(3, top_k)

    selected: list[RetrievedChunk] = []
    seen_article_ids: set[int] = set()

    # Prefer relevance + diversity: one chunk per article.
    for item in scored:
        if item.article_id in seen_article_ids:
            continue
        if item.score < min_score:
            continue
        selected.append(item)
        seen_article_ids.add(item.article_id)
        if len(selected) >= top_k:
            return selected

    # If thresholding is too strict, backfill with next-best unique articles.
    if len(selected) < minimum_results:
        for item in scored:
            if item.article_id in seen_article_ids:
                continue
            selected.append(item)
            seen_article_ids.add(item.article_id)
            if len(selected) >= minimum_results:
                break

    return selected[:top_k]
