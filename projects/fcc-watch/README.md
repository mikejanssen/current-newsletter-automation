# FCC Watch

Daily monitor for FCC activity related to public broadcasting stations and entities.

## What it does
- FCC-wide alerts from the FCC Daily Digest (rulemakings, NPRMs/orders, public notices, enforcement, auctions).
- FCC Public File RSS alerts, filtered by CPB call signs and station/network keywords.
  - Prioritizes editorially relevant filing buckets (issues/programs, public notices, renewal, EEO, ownership, applications).
  - Suppresses common low-signal items like standalone donor-list uploads.
  - Slack links point to station profile pages for reliability; raw document URLs are included as reference.
- ECFS filing alerts, filtered by CPB call signs + station/network keywords, with optional docket filters.
- FCC agenda/circulation alerts from meeting-related FCC pages, filtered by public-media and meeting keywords.
- Adds network/licensee names from `cpb-grantees.csv` to the keyword matcher.
- Slack delivery via incoming webhook.

## Data sources
- FCC Daily Digest pages:
  https://www.fcc.gov/edocs/daily-digest
- FCC Public Files station RSS feeds:
  `https://publicfiles.fcc.gov/{fm|tv|am}-profile/{call-sign}/rss`
- FCC ECFS filings API:
  `https://www.fcc.gov/ecfs/search/api/filings`
- FCC meeting/news pages (configurable list):
  defaults include `https://www.fcc.gov/news-events/events/open-commission-meeting`, `https://www.fcc.gov/news-events/events`, and `https://www.fcc.gov/circulation`

## Configuration
Required environment variable:
- SLACK_WEBHOOK_URL

Optional:
- FCC_WATCH_LOOKBACK_DAYS (default: 2)
- FCC_WATCH_DIGEST_TIMEOUT_SECONDS (default: 20)
- FCC_WATCH_DIGEST_RETRIES (default: 1)
- FCC_WATCH_DIGEST_RETRY_BACKOFF_SECONDS (default: 1.5)
- FCC_WATCH_DIGEST_MAX_CATCHUP_DAYS (default: 14)
- FCC_WATCH_DIGEST_BASE_URLS (comma-separated; default: `https://www.fcc.gov/edocs/daily-digest`)
- FCC_WATCH_PUBLIC_FILES_BASE_URL (default: `https://publicfiles.fcc.gov`)
- FCC_WATCH_PUBLIC_FILES_TIMEOUT_SECONDS (default: 20)
- FCC_WATCH_PUBLIC_FILES_RETRIES (default: 1)
- FCC_WATCH_PUBLIC_FILES_RETRY_BACKOFF_SECONDS (default: 1.5)
- FCC_WATCH_PUBLIC_FILES_STATION_LIMIT (default: 120)
- FCC_WATCH_PUBLIC_FILES_PRIORITY_MODE (`balanced` or `high`; default: `balanced`)
- FCC_WATCH_ECFS_BASE_URLS (comma-separated; default: `https://www.fcc.gov/ecfs/search/api/filings`)
- FCC_WATCH_ECFS_TIMEOUT_SECONDS (default: 20)
- FCC_WATCH_ECFS_RETRIES (default: 1)
- FCC_WATCH_ECFS_RETRY_BACKOFF_SECONDS (default: 1.5)
- FCC_WATCH_ECFS_LIMIT (default: 200)
- FCC_WATCH_ECFS_DOCKETS (comma-separated; defaults to a conservative public-media starter set; set to empty string to disable docket-term filtering)
- FCC_WATCH_MEETING_BASE_URLS (comma-separated source pages for agenda/circulation monitoring)
- FCC_WATCH_MEETING_TIMEOUT_SECONDS (default: 20)
- FCC_WATCH_MEETING_RETRIES (default: 1)
- FCC_WATCH_MEETING_RETRY_BACKOFF_SECONDS (default: 1.5)
- FCC_WATCH_MEETING_MAX_ITEMS (default: 250)
- FCC_WATCH_PREFLIGHT_TIMEOUT_SECONDS (default: 4)
- FCC_WATCH_PREFLIGHT_RETRIES (default: 0)

Catch-up behavior:
- On success, the tool records the latest successful digest date in state.
- On later runs, it can auto-extend beyond `--lookback-days` (up to `--max-catchup-days`) to backfill days missed during FCC timeout windows.

## Preflight
Quickly test digest host/app reachability and emit a report:

```bash
PYTHONPATH=src python3 -m fcc_watch.cli preflight \
  --out output/preflight-last.json \
  --fail-on-unreachable
```

- Exits non-zero when any probe is unreachable if `--fail-on-unreachable` is set.
- `scripts/run-daily-scan.sh` runs preflight first for diagnostics, then continues to `daily` so non-Digest sources can still run during Digest outages.

## Run locally
From `projects/fcc-watch`:

```bash
PYTHONPATH=src python3 -m fcc_watch.cli daily \
  --cpb ../990s/cpb-grantees.csv \
  --state output/state.json \
  --out output/last-run.json
```

Use `--dry-run` with alternate state/output files for investigations. Dry runs do not post to Slack or update state:

```bash
FCC_WATCH_PUBLIC_FILES_STATION_LIMIT=10 \
PYTHONPATH=src python3 -m fcc_watch.cli daily \
  --cpb ../990s/cpb-grantees.csv \
  --state output/review-state.json \
  --out output/review-last-run.json \
  --dry-run
```

## Tests
Run local unit tests with:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

## Scheduling
See `launchd/com.current.fccwatch.daily.plist` for a sample LaunchAgent. Set `SLACK_WEBHOOK_URL` in the plist `EnvironmentVariables`.

The sample schedule is 8:30 a.m., staggered from `fcc-lms-watch` at 9:00 a.m. State is updated only after a successful Slack post when alerts are present.
