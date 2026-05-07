# Repository Guidelines

## Project Structure & Module Organization

`src/fcc_lms_watch/` contains the Python package. `cli.py` owns command routing, alert creation, state updates, and Slack posting. `lms_client.py`, `browser_client.py`, and `html_forms.py` handle FCC LMS requests, browser fallback, and table/form parsing. `cpb.py` loads CPB grantee matching data from `../990s/cpb-grantees.csv`; `station-aliases.json` adds project-specific call sign, facility ID, and organization-name aliases.

`tests/` contains unit tests for state handling, public-notice parsing, and PN command behavior. `scripts/` contains local and launchd entrypoints. `launchd/` contains the daily LMS and separate PN LaunchAgent plists. `output/` is runtime state, logs, debug exports, and dry-run reports; do not treat it as source.

## Build, Test, and Development Commands

Run all tests:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

Run the daily LMS scan without Slack/state mutation:

```bash
PYTHONPATH=src python3 -m fcc_lms_watch.cli daily --dry-run --debug-export-dir output/debug
```

Run the separate Public Notice scan without Slack/state mutation:

```bash
PYTHONPATH=src python3 -m fcc_lms_watch.cli pn --dry-run --date-page-fallback
```

Known-positive checks:

```bash
scripts/run-validation.sh
scripts/run-pn-validation.sh
```

## Coding Style & Naming Conventions

Use Python 3.10+ standard-library code unless a dependency is already established. Keep functions small and explicit. Use snake_case for functions, variables, and JSON keys. Preserve the existing dataclass-based payload style for alerts and diagnostics. Keep comments sparse and practical, especially around FCC LMS workarounds.

## Testing Guidelines

Add or update unit tests when changing alert keys, state mutation, parsing, Slack behavior, or FCC fallback logic. Prefer fake clients and saved HTML-style rows over live FCC calls in tests. Live FCC checks should be dry-run and write to temporary/manual `output/` files.

## Operations & Safety

Do not post to Slack or update normal state during investigations unless explicitly requested. Use `--dry-run`, alternate `--state`, and alternate `--out` paths for backfills. Normal state files are `output/state.json` and `output/pn-state.json`.

Public Notice alerts should link to stable FCC application/facility pages, not generated `publicNoticeSearchResult.html?...` pages. The daily scan intentionally skips PN work; PN runs separately because FCC date-page scraping is slower and more fragile.

## Commit & Pull Request Guidelines

No project-specific commit convention is enforced. Use concise imperative messages, for example `Fix PN alert links`. PRs should describe the scan path affected, state whether Slack/state were touched, include test results, and note any live FCC validation performed.
