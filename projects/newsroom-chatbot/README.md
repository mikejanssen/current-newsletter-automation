# Newsroom Chatbot (MVP)

Internal chatbot for querying and summarizing your publication's historical coverage with source citations.

## What it does
- Crawls article URLs from an XML sitemap.
- Extracts and stores article text + metadata in SQLite.
- Chunks article text and generates embeddings.
- Retrieves relevant chunks for a user question and asks an LLM to answer with citations.
- Serves a simple internal web chat interface.

## Requirements
- Python 3.10+
- `OPENAI_API_KEY` environment variable

## Install
From `projects/newsroom-chatbot`:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 1) Ingest coverage from sitemap

```bash
PYTHONPATH=src python3 -m newsroom_chatbot.ingest \
  --sitemap-url "https://current.org/sitemap_index.xml" \
  --db output/newsroom.db
```

Optional:
- `--max-urls 500` for trial runs
- `--url-contains /2025/` to filter URLs

## 2) Build embeddings index

```bash
PYTHONPATH=src python3 -m newsroom_chatbot.index \
  --db output/newsroom.db
```

Optional:
- `--embedding-model text-embedding-3-small`
- `--max-chunks 2000` for trial runs
- `--skip-rechunk` to resume embedding without rebuilding chunks

Resume after an interrupted run:

```bash
PYTHONPATH=src python3 -m newsroom_chatbot.index \
  --db output/newsroom.db \
  --skip-rechunk
```

## 3) Run web app

```bash
PYTHONPATH=src uvicorn newsroom_chatbot.app:app --reload --port 8787
```

Open `http://127.0.0.1:8787`.

## 4) Minimal auth gate (recommended for shared use)

Set both environment variables below to enable HTTP Basic auth:

```bash
export NEWSROOM_AUTH_USERNAME="your-username"
export NEWSROOM_AUTH_PASSWORD="your-strong-password"
```

If neither variable is set, auth is disabled.

## 5) Deploy in one shot

This project now includes:
- `Dockerfile`
- `.dockerignore`
- `Procfile`
- `render.yaml`

Startup command (for Render/Railway/Fly/Heroku-style services):

```bash
PYTHONPATH=src uvicorn newsroom_chatbot.app:app --host 0.0.0.0 --port ${PORT:-8787}
```

Required env vars in your host:
- `OPENAI_API_KEY`

Recommended env vars:
- `NEWSROOM_AUTH_USERNAME`
- `NEWSROOM_AUTH_PASSWORD`
- `NEWSROOM_DB_PATH` (if you store DB outside default `output/newsroom.db`)

### Render blueprint quick start

1. In Render, create a new Blueprint service from this repo.
2. Use blueprint spec: `projects/newsroom-chatbot/render.yaml`.
3. Set secrets in Render:
   - `OPENAI_API_KEY`
   - `NEWSROOM_AUTH_USERNAME`
   - `NEWSROOM_AUTH_PASSWORD`
4. After first deploy, open a Render Shell and run:

```bash
cd /opt/render/project/src/projects/newsroom-chatbot
PYTHONPATH=src python3 -m newsroom_chatbot.ingest \
  --sitemap-url "https://current.org/sitemap_index.xml" \
  --db /var/data/newsroom.db
PYTHONPATH=src python3 -m newsroom_chatbot.index \
  --db /var/data/newsroom.db
```

The blueprint already mounts `/var/data` and points `NEWSROOM_DB_PATH` to `/var/data/newsroom.db`.

## Notes
- This MVP stores vectors in SQLite as JSON and computes cosine similarity in Python. This is fine for early internal testing.
- For larger corpora and multiple users, migrate retrieval to Postgres + `pgvector`.
- Ingestion excludes common comment-section containers so discussion threads are not indexed as article facts.
- Ingestion also preserves correction notes and appends them to article text when needed, so answers can prioritize corrected information.

If you already ingested content before these rules, rerun ingest and then rebuild embeddings:

```bash
PYTHONPATH=src python3 -m newsroom_chatbot.ingest \
  --sitemap-url "https://current.org/sitemap_index.xml" \
  --db output/newsroom.db

PYTHONPATH=src python3 -m newsroom_chatbot.index \
  --db output/newsroom.db
```
