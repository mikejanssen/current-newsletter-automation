from __future__ import annotations

import sqlite3
from pathlib import Path


def connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS docs_fts;
        DROP TABLE IF EXISTS docs;

        CREATE TABLE IF NOT EXISTS docs (
          doc_id TEXT PRIMARY KEY,
          station_id TEXT NOT NULL,
          station_name TEXT NOT NULL,
          canonical_station_id TEXT NOT NULL,
          canonical_station_name TEXT NOT NULL,
          title TEXT NOT NULL,
          file_path TEXT NOT NULL,
          file_name TEXT NOT NULL,
          file_sha256 TEXT NOT NULL,
          content_text TEXT NOT NULL,
          extracted_ok INTEGER NOT NULL DEFAULT 0,
          file_mtime REAL NOT NULL,
          file_size INTEGER NOT NULL
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
          station_id,
          station_name,
          canonical_station_id,
          canonical_station_name,
          title,
          content_text,
          content='docs',
          content_rowid='rowid'
        );
        """
    )


def rebuild_fts(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT INTO docs_fts(
          rowid, station_id, station_name, canonical_station_id, canonical_station_name, title, content_text
        )
        SELECT rowid, station_id, station_name, canonical_station_id, canonical_station_name, title, content_text
        FROM docs;
        """
    )
    conn.commit()
