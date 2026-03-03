import json
import sqlite3
from pathlib import Path
from typing import Iterable


SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  url TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  published_at TEXT,
  author TEXT,
  text TEXT NOT NULL,
  fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  article_id INTEGER NOT NULL,
  chunk_index INTEGER NOT NULL,
  content TEXT NOT NULL,
  embedding_json TEXT,
  FOREIGN KEY(article_id) REFERENCES articles(id),
  UNIQUE(article_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_articles_published_at ON articles(published_at);
CREATE INDEX IF NOT EXISTS idx_chunks_article_id ON chunks(article_id);
"""


def connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def upsert_article(
    conn: sqlite3.Connection,
    *,
    url: str,
    title: str,
    published_at: str | None,
    author: str | None,
    text: str,
    fetched_at: str,
) -> int:
    conn.execute(
        """
        INSERT INTO articles (url, title, published_at, author, text, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
          title = excluded.title,
          published_at = excluded.published_at,
          author = excluded.author,
          text = excluded.text,
          fetched_at = excluded.fetched_at
        """,
        (url, title, published_at, author, text, fetched_at),
    )
    row = conn.execute("SELECT id FROM articles WHERE url = ?", (url,)).fetchone()
    if row is None:
        raise RuntimeError(f"Unable to fetch article id for {url}")
    return int(row["id"])


def replace_chunks(
    conn: sqlite3.Connection,
    *,
    article_id: int,
    chunks: Iterable[str],
) -> None:
    conn.execute("DELETE FROM chunks WHERE article_id = ?", (article_id,))
    conn.executemany(
        """
        INSERT INTO chunks (article_id, chunk_index, content, embedding_json)
        VALUES (?, ?, ?, NULL)
        """,
        [(article_id, idx, content) for idx, content in enumerate(chunks)],
    )


def set_chunk_embedding(conn: sqlite3.Connection, chunk_id: int, embedding: list[float]) -> None:
    conn.execute(
        "UPDATE chunks SET embedding_json = ? WHERE id = ?",
        (json.dumps(embedding), chunk_id),
    )
