# Repository Guidelines

## Project Structure & Module Organization

`src/fcc_watch/` contains the Python package. `cli.py` coordinates source scans, alert formatting, state updates, and Slack delivery. Source-specific fetchers live in `daily_digest.py`, `public_files_rss.py`, `ecfs.py`, and `meeting_watch.py`. `cpb.py` loads CPB grantees from `../990s/cpb-grantees.csv`; `state.py` manages de-duplication state.

`scripts/run-daily-scan.sh` is the production entrypoint used by launchd. `launchd/com.current.fccwatch.daily.plist` is the LaunchAgent template. `output/` contains state, logs, preflight results, and manual run artifacts; do not treat it as source.

## Build, Test, and Development Commands

Run unit tests:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

Run preflight only:

```bash
PYTHONPATH=src python3 -m fcc_watch.cli preflight --out output/preflight-last.json
```

Run a bounded dry run:

```bash
FCC_WATCH_PUBLIC_FILES_STATION_LIMIT=10 \
PYTHONPATH=src python3 -m fcc_watch.cli daily \
  --cpb ../990s/cpb-grantees.csv \
  --state output/review-state.json \
  --out output/review-last-run.json \
  --dry-run
```

## Coding Style & Naming Conventions

Use Python 3.10+ standard-library code. Keep source fetchers independent and return dataclass items to `cli.py`. Use snake_case for functions, variables, and JSON keys. Keep network timeouts and retries configurable through `FCC_WATCH_*` environment variables.

## Testing Guidelines

Add tests when changing state handling, Slack behavior, de-duplication keys, source filtering, or parser behavior. Prefer mocked builders or saved sample rows over live FCC calls. Live source checks should be dry-run and use alternate `--state` and `--out` files.

## Operations & Safety

Do not post to Slack or update normal state during investigation unless explicitly requested. Dry runs must not write `output/state.json`. Alerts should only be marked seen after a successful Slack post. The normal launchd state file is `output/state.json`.

`www.fcc.gov` endpoints are intermittent. Public Files RSS has been more reliable than Daily Digest, ECFS, and meeting pages. Use preflight results to decide whether to skip Digest for a run.

## Commit & Pull Request Guidelines

No project-specific commit convention is enforced. Use concise imperative commits such as `Fix FCC Watch state handling`. PRs should identify which source paths changed, whether Slack/state were touched, test results, and any live FCC dry-run evidence.
