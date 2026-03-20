# Audit Chatbot (MVP)

CLI chatbot-style retrieval over your archived audit documents and optional Semipublic repo PDFs.

## What it does
- Indexes PDFs in `../audit-watch/output/audits` into SQLite.
- Optionally indexes PDFs from a local checkout of `Semipublic/public-repository`.
- Canonicalizes legal-entity aliases to a single station when rows share the same `page_url` in `stations.csv`.
- Deduplicates identical PDFs across alias station IDs.
- Carries source metadata so results show whether a document came from `audit-watch` or `semipublic`.
- Supports station-focused commands:
  - `summary <station>`
  - `risks <station>`
  - `risks-all` (portfolio rollup; can filter by run date in path)
  - `docs <station>`
  - `search <phrase>`
- Returns source file citations in every response.

## Run
From `projects/audit-chatbot`:

```bash
PYTHONPATH=src python3 -m audit_chatbot ingest \
  --db output/audit-chatbot.db \
  --archive-root ../audit-watch/output/audits \
  --stations ../audit-watch/config/stations.csv
```

Include Semipublic documents if you have a local checkout:

```bash
PYTHONPATH=src python3 -m audit_chatbot ingest \
  --db output/audit-chatbot.db \
  --archive-root ../audit-watch/output/audits \
  --stations ../audit-watch/config/stations.csv \
  --semipublic-root ../sources/public-repository
```

Examples:

```bash
PYTHONPATH=src python3 -m audit_chatbot query --db output/audit-chatbot.db summary "Minnesota Public Radio"
PYTHONPATH=src python3 -m audit_chatbot query --db output/audit-chatbot.db risks "GBH"
PYTHONPATH=src python3 -m audit_chatbot query --db output/audit-chatbot.db risks "GBH" --strict
PYTHONPATH=src python3 -m audit_chatbot query --db output/audit-chatbot.db risks "GBH" --watchlist
PYTHONPATH=src python3 -m audit_chatbot query --db output/audit-chatbot.db risks "GBH" --strict --explain
PYTHONPATH=src python3 -m audit_chatbot query --db output/audit-chatbot.db --path-date 2026-03-04 --out output/risk-briefing.md --json-out output/risk-briefing.json risks-all
PYTHONPATH=src python3 -m audit_chatbot query --db output/audit-chatbot.db docs "WNET" --limit 20
PYTHONPATH=src python3 -m audit_chatbot query --db output/audit-chatbot.db search "material weakness"
```

## Web app for colleagues

Install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run local web UI:

```bash
PYTHONPATH=src uvicorn audit_chatbot.app:app --reload --port 8790
```

Or use the helper script (loads `.env.local` automatically if present):

```bash
./scripts/run-web.sh
```

Open `http://127.0.0.1:8790`.
By default the helper script uses `output/audit-chatbot-combined.db` if it exists.

Optional env vars:
- `AUDIT_CHATBOT_DB_PATH` (default: `output/audit-chatbot.db`)
- `AUDIT_CHATBOT_AUTH_USERNAME` and `AUDIT_CHATBOT_AUTH_PASSWORD` (enable HTTP Basic auth)
- `OPENAI_API_KEY` (enables AI narrative summaries for `summary`; without it, app falls back to deterministic timeline summary)
- `AUDIT_CHATBOT_HOST` (default: `0.0.0.0`)
- `AUDIT_CHATBOT_PORT` (default: `8790`)

## Deploy

This folder includes deploy scaffolding:
- `Dockerfile`
- `Procfile`
- `render.yaml`

Render blueprint quick start:
1. Create a new Blueprint service in Render from this repo.
2. Use blueprint spec: `projects/audit-chatbot/render.yaml`.
3. Set secrets:
   - `OPENAI_API_KEY`
   - `AUDIT_CHATBOT_AUTH_USERNAME`
   - `AUDIT_CHATBOT_AUTH_PASSWORD`
4. Keep persistent disk mounted at `/var/data` and set:
   - `AUDIT_CHATBOT_DB_PATH=/var/data/audit-chatbot.db`
5. On first boot, the app seeds `/var/data/audit-chatbot.db` from the bundled `output/audit-chatbot-combined.db` if the disk is empty.

Security notes:
- Do not commit API keys, usernames, or passwords into the repo.
- Set `OPENAI_API_KEY`, `AUDIT_CHATBOT_AUTH_USERNAME`, and `AUDIT_CHATBOT_AUTH_PASSWORD` only in Render's secret env-var UI.
- The web UI never needs the API key; it stays server-side.

## Refresh DB

Refresh from `audit-watch` archives:

```bash
./scripts/refresh-db.sh
```

Override paths if needed:

```bash
AUDIT_CHATBOT_DB_PATH=/var/data/audit-chatbot.db \
AUDIT_ARCHIVE_ROOT=../audit-watch/output/audits \
AUDIT_STATIONS_CSV=../audit-watch/config/stations.csv \
./scripts/refresh-db.sh
```

Include Semipublic docs in the same rebuild:

```bash
AUDIT_CHATBOT_DB_PATH=/var/data/audit-chatbot.db \
AUDIT_ARCHIVE_ROOT=../audit-watch/output/audits \
AUDIT_STATIONS_CSV=../audit-watch/config/stations.csv \
AUDIT_SEMIPUBLIC_ROOT=../sources/public-repository \
./scripts/refresh-db.sh
```

## Notes
- If `pdftotext` is installed, text extraction quality is better.
- Without it, the index falls back to `strings` and may miss content.
- `summary` in the web app is AI-generated when `OPENAI_API_KEY` is present.
- Semipublic station names are inferred from filenames, so some cross-source station matching will be approximate until we add a stronger alias layer.
