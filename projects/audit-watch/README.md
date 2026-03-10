# Audit Watch (MVP scaffold)

Track when stations post audit/financial documents, archive copies, and flag potentially unusual findings.

## What it does today
- Scans configured station financial/audit pages (`config/stations.csv`).
- Detects new candidate audit files (PDF/DOC/XLS links with audit/financial hints).
- Downloads new files to `output/audits/<station_id>/<YYYY-MM-DD>/`.
- Flags unusual audit language using keyword patterns (material weakness, significant deficiency, going concern, qualified/adverse/disclaimer opinion, questioned costs, noncompliance).
- Produces:
  - `output/last-run.json` (machine-readable run output)
  - `output/briefing.md` (editorial digest)
  - `output/fetch-failures.json` (station/page/download failures)
  - `output/state.json` (dedupe state)
  - `output/risk-briefing.md` (audit-chatbot risk rollup for this run date)
  - `output/risk-briefing.json` (machine-readable strict/watchlist risk highlights)

## Quick start
From `projects/audit-watch`:

```bash
PYTHONPATH=src python3 -m audit_watch.cli daily-run \
  --stations config/stations.csv \
  --state output/state.json \
  --out output/last-run.json \
  --brief output/briefing.md \
  --failures-out output/fetch-failures.json \
  --archive-root output/audits
```

Discover likely page URLs for unresolved stations:

```bash
PYTHONPATH=src python3 -m audit_watch.cli discover-pages \
  --stations config/stations.csv \
  --out output/page-discovery-candidates.csv \
  --limit 100 \
  --max-candidates 5
```

Auto-apply top candidates (kept disabled for review):

```bash
PYTHONPATH=src python3 -m audit_watch.cli discover-pages \
  --stations config/stations.csv \
  --out output/page-discovery-candidates.csv \
  --apply
```

## Configure stations
Edit `config/stations.csv`:
- `station_id`: stable slug (used in output paths)
- `station_name`: display name
- `page_url`: station page that lists audits/financial docs (can be blank for unresolved entries)
- `notes`: optional context
- `enabled`: `1` to scan this row, `0` to skip until verified

Use `config/stations-candidates.csv` as a backlog of additional stations (seeded from your 990 watchlist) that still need verified `page_url` entries.
`discover-pages` can prefill likely `page_url` values with `enabled=0` and an `AUTO_DISCOVERY_CANDIDATE` note for fast review.

## Weekly automation
Run manually:

```bash
./scripts/run-daily-scan.sh
```

Install as a LaunchAgent:

```bash
cp launchd/com.current.audit-watch.weekly.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.current.audit-watch.weekly.plist
launchctl enable gui/$(id -u)/com.current.audit-watch.weekly
```

Sample schedule is weekly (Monday) at 9:10 local time.

## Notifications
`scripts/run-daily-scan.sh` posts a Slack summary if:
- there are new documents, or
- one or more stations had fetch/archive failures, or
- strict/watchlist risk signals are found for the run date

Set `SLACK_WEBHOOK_URL` in the plist (or your shell env for manual runs).

By default, no Slack message is sent on a fully quiet run. Set `AUDIT_WATCH_NOTIFY_ON_NO_CHANGES=1` to force heartbeat messages even when quiet.
Slack output now includes:
- top new docs (with links)
- top failed pages (with page links and error snippets)
- strict risk highlights (high-confidence)
- watchlist highlights (manual-review queue)

Optional env vars for Slack detail limits:
- `AUDIT_WATCH_SLACK_MAX_STRICT_RISKS` (default `5`)
- `AUDIT_WATCH_SLACK_MAX_WATCHLIST_RISKS` (default `5`)
- `AUDIT_CHATBOT_RISK_LIMIT` (default `8`, max rows emitted in risk briefing files)

## Current limitations (MVP)
- No deep site crawling yet: this version scans only configured page URLs.
- PDF text extraction uses `pdftotext` if available; otherwise flagging uses title/URL only.
- “Unusual” detection is keyword-based, not a full accounting/forensic review.
