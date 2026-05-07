# FCC Watch — Status Handoff (2026-05-07)

## Goal

Track non-LMS FCC activity relevant to public broadcasting and post alerts to Slack.

## Scope

`fcc-watch` is complementary to `fcc-lms-watch`. It monitors:

- FCC Daily Digest
- FCC Public File RSS feeds
- ECFS filings
- FCC meeting, agenda, and circulation pages

It does not monitor LMS Assignment/Transfer, LMS Application Search, or LMS Public Notice Search; those belong to `projects/fcc-lms-watch`.

## Current State

- LaunchAgent label: `com.current.fccwatch.daily`
- Template plist: `launchd/com.current.fccwatch.daily.plist`
- Production script: `scripts/run-daily-scan.sh`
- Normal state: `output/state.json`
- Normal output: `output/last-run.json`

The project now has state-safety behavior matching `fcc-lms-watch`: dry runs do not update state, and alerts are marked seen only after Slack posts successfully.

## Recent Fixes

- Added `AGENTS.md` contributor/agent guide.
- Added unit tests for dry-run, Slack-failure, and successful state handling.
- Fixed `run_daily` so state is not updated before `--dry-run` returns.
- Fixed `run_daily` so Slack failures do not mark alerts as seen.
- Added `state_updated` and dry-run `slack_status` reporting to daily payloads.
- Updated `scripts/run-daily-scan.sh` to prefer the workspace Python at `../../.venv/bin/python` when available.
- Staggered launchd template schedule to 8:30 a.m. so it does not collide with `fcc-lms-watch` at 9:00 a.m.

## Validation

Run from this project:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

Bounded dry-run health check:

```bash
FCC_WATCH_PUBLIC_FILES_STATION_LIMIT=10 \
PYTHONPATH=src python3 -m fcc_watch.cli daily \
  --cpb ../990s/cpb-grantees.csv \
  --state output/review-state.json \
  --out output/review-last-run.json \
  --dry-run
```

Validation completed on 2026-05-07:

- Unit tests passed: 3 tests.
- Launchd plist lint passed.
- Installed LaunchAgent was updated in `~/Library/LaunchAgents/com.current.fccwatch.daily.plist` without overwriting the local Slack webhook.
- Installed LaunchAgent is loaded at 8:30 a.m.; `launchctl print gui/501/com.current.fccwatch.daily` shows `state = not running`, `runs = 0`, and `last exit code = (never exited)` after reload.
- Preflight result: `www.fcc.gov` Daily Digest timed out; `publicfiles.fcc.gov` returned HTTP 200.
- Bounded dry run produced no new alerts, no public-file failures, Daily Digest/ECFS/meeting timeouts, `slack_status = dry_run`, and `state_updated = false`.
- Dry run did not create `output/review-state.json`.

## Known Limitations

- `www.fcc.gov` endpoints remain intermittent. Daily Digest, ECFS, and meeting pages may timeout.
- `publicfiles.fcc.gov` has been the most reliable source.
- Public File scanning is capped by `FCC_WATCH_PUBLIC_FILES_STATION_LIMIT`; the default is 120.
- ECFS filtering depends on broad docket-term defaults and may miss relevant filings outside those terms.

## Next Priorities

- Run one bounded dry run after source/network changes and inspect failure fields.
- Consider source-specific subcommands for easier isolated debugging.
- Consider adding direct FCC Enforcement release monitoring if Daily Digest remains unreliable.
