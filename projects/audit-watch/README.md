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
  - `output/health.json` (last scan/risk/Slack status and failure summary)
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
  --health-out output/health.json \
  --archive-root output/audits \
  --workers 8
```

Station page fetches and new-document downloads run with bounded parallelism. Use
`--workers N` or `AUDIT_WATCH_WORKERS=N` to tune concurrency.
Transient station-page failures are retried once by default at lower concurrency
with a longer timeout. Tune with `--failure-retry-passes`,
`--failure-retry-workers`, `--failure-retry-timeout-multiplier`, or the matching
`AUDIT_WATCH_FAILURE_RETRY_*` environment variables.

To avoid archiving every historical backfile on a first run, use latest-only
archive mode:

```bash
PYTHONPATH=src python3 -m audit_watch.cli daily-run \
  --stations config/stations.csv \
  --state output/state.json \
  --out output/last-run.json \
  --brief output/briefing.md \
  --failures-out output/fetch-failures.json \
  --health-out output/health.json \
  --archive-root output/audits \
  --archive-scope latest
```

`--archive-scope latest` still discovers all configured page documents, but only
downloads and marks seen the current best audit/financial document(s) per
station. The CLI default remains `--archive-scope all`; the scheduled runner
defaults to `AUDIT_WATCH_ARCHIVE_SCOPE=latest`.

Run the production path, including audit-chatbot risk rollup, health output, and Slack notification:

```bash
PYTHONPATH=src python3 -m audit_watch.cli run-and-notify \
  --stations config/stations.csv \
  --state output/state.json \
  --out output/last-run.json \
  --brief output/briefing.md \
  --failures-out output/fetch-failures.json \
  --archive-root output/audits \
  --archive-scope latest \
  --health-out output/health.json
```

Validate station config:

```bash
PYTHONPATH=src python3 -m audit_watch.cli validate-stations \
  --stations config/stations.csv
```

Check the latest saved run and scheduled job state:

```bash
PYTHONPATH=src python3 -m audit_watch.cli status
```

Use `--no-launchd` to read only saved output files, or `--limit-failures N` to
control how many current failures are printed.

Fast live validation without archive downloads or state updates:

```bash
PYTHONPATH=src python3 -m audit_watch.cli daily-run \
  --stations config/stations.csv \
  --state /tmp/audit-watch-state.json \
  --out /tmp/audit-watch-last-run.json \
  --brief /tmp/audit-watch-briefing.md \
  --failures-out /tmp/audit-watch-failures.json \
  --health-out /tmp/audit-watch-health.json \
  --archive-root /tmp/audit-watch-audits \
  --no-archive
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

Review disabled stations that already have page URLs:

```bash
PYTHONPATH=src python3 -m audit_watch.cli review-disabled \
  --stations config/stations.csv \
  --out output/disabled-review.json \
  --csv-out output/disabled-review.csv \
  --failures-out output/disabled-review-failures.json \
  --limit 25
```

Use `--offset` to advance through the disabled backlog in batches. This command
does not archive documents or update state; it writes a review queue showing
which disabled station pages currently expose candidate audit/financial files.

List archived documents that appear to pertain to a recent fiscal/calendar year:

```bash
PYTHONPATH=src python3 -m audit_watch.cli recent-docs \
  --archive-root output/audits \
  --since-year 2025 \
  --after-archive-date 2026-03-04 \
  --out output/recent-documents-2025-plus.csv
```

The year filter prefers the document title or filename, so upload folders such
as `/2026/01/` do not make an older audit look like a 2026 document.

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

For manual runs, the script loads `./.env` if present before applying defaults.
Put local-only secrets such as `SLACK_WEBHOOK_URL` there; `.env` files are
ignored by git. LaunchAgent environment variables still work when no `.env` is
present.

Install as a LaunchAgent:

```bash
cp launchd/com.current.audit-watch.weekly.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.current.audit-watch.weekly.plist
launchctl enable gui/$(id -u)/com.current.audit-watch.weekly
```

Sample schedule is weekly (Monday) at 9:10 local time.

## Notifications
`scripts/run-daily-scan.sh` calls `audit_watch.cli run-and-notify`, which posts a Slack summary if:
- there are new documents, or
- one or more stations had fetch/archive failures, or
- strict/watchlist risk signals are found for the run date

Set `SLACK_WEBHOOK_URL` in the plist or in local `./.env` for manual runs.

By default, no Slack message is sent on a fully quiet run. Set `AUDIT_WATCH_NOTIFY_ON_NO_CHANGES=1` to force heartbeat messages even when quiet.
Slack failures are recorded in `output/health.json` as `slack_status = failed`; they do not erase successful scan/risk outputs.
Slack output now includes:
- top new docs (with links)
- top failed pages (with page links and error snippets)
- strict risk highlights (high-confidence)
- watchlist highlights (manual-review queue)

Optional env vars for Slack detail limits:
- `AUDIT_WATCH_SLACK_MAX_STRICT_RISKS` (default `5`)
- `AUDIT_WATCH_SLACK_MAX_WATCHLIST_RISKS` (default `5`)
- `AUDIT_CHATBOT_RISK_LIMIT` (default `8`, max rows emitted in risk briefing files)
- `AUDIT_WATCH_ARCHIVE_SCOPE` (`all` or `latest`; scheduled runner default `latest`)
- `AUDIT_WATCH_FAILURE_RETRY_PASSES` (default `1`)
- `AUDIT_WATCH_FAILURE_RETRY_WORKERS` (default `2`)
- `AUDIT_WATCH_FAILURE_RETRY_TIMEOUT_MULTIPLIER` (default `1.5`)

## Tests
Run the local test suite:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

For live validation without updating normal state, point outputs at `/tmp`:

```bash
PYTHONPATH=src python3 -m audit_watch.cli daily-run \
  --stations config/stations.csv \
  --state /tmp/audit-watch-state.json \
  --out /tmp/audit-watch-last-run.json \
  --brief /tmp/audit-watch-briefing.md \
  --failures-out /tmp/audit-watch-failures.json \
  --health-out /tmp/audit-watch-health.json \
  --archive-root /tmp/audit-watch-audits \
  --no-archive \
  --dry-run
```

## Current limitations (MVP)
- No deep site crawling yet: this version scans only configured page URLs.
- PDF text extraction uses `pdftotext` if available; otherwise flagging uses title/URL only.
- “Unusual” detection is keyword-based, not a full accounting/forensic review.
