# RSS Watch

OPML-based RSS triage for public-broadcasting reporting workflows.

## What it does
- Loads feed URLs from an OPML export.
- Fetches RSS/Atom feeds and normalizes items.
- De-duplicates by canonical URL and near-duplicate title.
- Scores items for public-broadcasting relevance.
- Writes:
  - machine-readable run output (`output/last-run.json`)
  - full pre-trim candidate log (`output/candidates.json`)
  - a concise editorial briefing (`output/briefing.md`)
- Supports two review modes:
  - `morning`: review recent window (default 24h)
  - `update`: review only items since your previous run

## Run
From `projects/rss-watch`:

```bash
PYTHONPATH=src python3 -m rss_watch.cli \
  --opml /path/to/your-feeds.opml \
  --mode morning
```

Then for intra-day checks:

```bash
PYTHONPATH=src python3 -m rss_watch.cli \
  --opml /path/to/your-feeds.opml \
  --mode update
```

## Key options
- `--state` default: `output/state.json`
- `--out` default: `output/last-run.json`
- `--candidates-out` default: `output/candidates.json`
- `--brief` default: `output/briefing.md`
- `--window-hours` default: `24` (used by `morning` mode)
- `--max-items` default: `200`
- `--max-item-age-days` default: `30` (exclude stale resurfaced items; `0` disables)
- `--include-low` include low-priority section in briefing
- `--include-seen` include previously-seen items
- `--feed-timeout-seconds` default: `20`
- `--feed-retries` default: `1`
- `--parallelism` default: `8`
- `--max-feeds` optional cap for quick trial runs
- `--slack-webhook` default: `SLACK_WEBHOOK_URL` env var
- `--slack-max-items` default: `10`
- `--dry-run` do not update state

## Tests
Run local unit tests with:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

Use a bounded dry run for live-feed checks:

```bash
PYTHONPATH=src python3 -m rss_watch.cli \
  --opml "Inoreader Feeds 20260211.xml" \
  --mode morning \
  --dry-run \
  --max-feeds 10 \
  --out output/review-last-run.json \
  --candidates-out output/review-candidates.json \
  --brief output/review-briefing.md
```

## Automated Runs (launchd)
- Morning briefing job:
  - `launchd/com.current.rss-watch.morning.plist`
  - runs weekdays at `08:45`
  - runner executes `morning` mode with a `72h` lookback safety window
- Intra-day update job:
  - `launchd/com.current.rss-watch.update.plist`
  - runs weekdays at `14:00` with `14:10` fallback trigger
  - runner skips update if `last_checked` is within `45` minutes (`RSS_WATCH_UPDATE_SKIP_RECENT_MINUTES`)
- Runner script:
  - `scripts/run-rss-watch.sh`

State is updated only after a successful Slack post when Slack delivery is configured. Dry runs do not update state.

## Hosted Runs (GitHub Actions)

`.github/workflows/rss-watch.yml` runs independently of the local Mac on weekdays:

- morning briefing at `08:45 America/New_York`
- intra-day update at `14:00 America/New_York`
- paired UTC schedules plus an Eastern-time offset gate handle daylight saving time
- a serialized Actions cache carries `output/state.json` between runs for de-duplication
- if no state cache exists, the workflow starts at its current time rather than replaying old items

The workflow requires these GitHub Actions secrets:

- `RSS_WATCH_OPML_B64`: base64-encoded contents of the current OPML export
- `RSS_WATCH_SLACK_WEBHOOK_URL`: Slack incoming-webhook URL

Manual runs default to dry-run mode and upload their briefing and JSON output as a workflow artifact. Scheduled runs post to Slack and update the hosted state only after a successful post.
