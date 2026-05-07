# RSS Watch — Status Handoff (2026-05-07)

## Goal

Monitor RSS/Atom feeds for public-broadcasting story leads and post concise Slack briefings for editorial review.

## Current State

- Source package: `src/rss_watch/`
- Production runner: `scripts/run-rss-watch.sh`
- Morning LaunchAgent: `com.current.rss-watch.morning`
- Update LaunchAgent: `com.current.rss-watch.update`
- OPML source: `Inoreader Feeds 20260211.xml`
- Normal state: `output/state.json`
- Morning output: `output/last-run.json`, `output/candidates.json`, `output/briefing.md`
- Update output: `output/last-run-update.json`, `output/candidates-update.json`, `output/briefing-update.md`

## Recent Fixes

- Added `.gitignore` for generated output and local cache files.
- Added unit tests for state/Slack safety and scoring examples.
- Added `SlackPostError` wrapping in `slack.py`.
- Changed `run()` so dry runs do not update state.
- Changed `run()` so items are marked seen only after Slack posts successfully when Slack is configured.
- Added `slack_status`, `slack_error`, and `state_updated` to run payloads.
- Updated `scripts/run-rss-watch.sh` to prefer the workspace Python.
- Staggered morning LaunchAgent template to 8:45 a.m., away from 9:00 FCC LMS jobs.
- Tuned scoring to strip HTML from summaries before matching, avoid fake call-sign/departure boosts from HTML attributes, preserve public-media commentary, and surface public-media leadership appointments.

## Validation

Run tests:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

Run a bounded live-feed dry run:

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

Validation completed on 2026-05-07:

- Unit tests passed: 5 tests.
- `zsh -n scripts/run-rss-watch.sh` passed.
- Both launchd templates passed `plutil -lint`.
- Live bounded dry run with 10 feeds completed with 0 feed failures, 2 candidates, `slack_status = dry_run`, and `state_updated = false`.
- Installed morning LaunchAgent was updated to 8:45 a.m. while preserving the local Slack webhook; `launchctl print gui/501/com.current.rss-watch.morning` shows it loaded, not running, with `runs = 0` after reload.
- Scoring validation on a 72-hour dry run completed with 66 feeds, 5 feed failures, 99 candidates, 2 maybe items, `slack_status = dry_run`, and `state_updated = false`. The previous generic radio/music-industry false positives no longer appeared as high/maybe, while public-media commentary and a public-media appointment remained visible.

## Known Limitations

- `src/rss_watch/cli.py` still combines fetching, parsing, scoring, dedupe, rendering, Slack, and CLI orchestration. Split it into smaller modules when making deeper scoring changes.
- Ranking still needs editorial tuning. Recent output over-promoted a Google News item with a social-media source hint.
- Feed failures are listed per run, but there is no persistent feed-health report yet.
- OPML exports are editorial working files; review before sharing.

## Next Priorities

- Add feed-health tracking for recurring failures.
- Tune ranking for Google News/social source hints, opinion items, and weak `NPR`/`PBS` mentions.
- Split scoring/dedupe/feed parsing out of `cli.py`.
