import json
import math
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

    scored = sorted(best_by_article.values(), key=lambda item: item[1], reverse=True)
    top_score = scored[0][1]
    min_score = max(0.12, top_score * 0.55)
    minimum_results = min(3, top_k)

    selected: list[tuple[int, float]] = [item for item in scored if item[1] >= min_score][:top_k]

    # If thresholding is too strict, backfill with next-best unique articles.
    if len(selected) < minimum_results:
        selected = scored[:minimum_results]

    selected = selected[:top_k]
    if not selected:
        return []

    selected_chunk_ids = [chunk_id for chunk_id, _ in selected]
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
    for chunk_id, score in selected:
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
