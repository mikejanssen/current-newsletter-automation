# Repository Guidelines

## Project Structure & Module Organization

This repository is a Python RSS triage utility for public-broadcasting editorial monitoring. Source code lives in `src/rss_watch/`:

- `cli.py` contains feed fetching, normalization, scoring, briefing generation, and CLI argument handling.
- `state.py` loads and saves run state in JSON.
- `slack.py` posts briefing text to Slack webhooks.

Operational files are separate from source code. `scripts/run-rss-watch.sh` is the local/launchd runner, `launchd/` contains macOS launchd job definitions, and `output/` contains generated run artifacts such as `briefing.md`, `last-run.json`, and `state.json`. The OPML feed export is stored at the repository root.

## Build, Test, and Development Commands

Run from the repository root.

```bash
PYTHONPATH=src python3 -m rss_watch.cli --opml "Inoreader Feeds 20260211.xml" --mode morning
```

Runs a morning scan and writes default artifacts under `output/`.

```bash
PYTHONPATH=src python3 -m rss_watch.cli --opml "Inoreader Feeds 20260211.xml" --mode update --dry-run
```

Runs an intra-day update without modifying state.

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

Runs the unit test suite.

```bash
scripts/run-rss-watch.sh morning
scripts/run-rss-watch.sh update
```

Uses the production-style wrapper and configured output paths.

## Coding Style & Naming Conventions

Use standard-library Python where practical; this project currently has no package manager or third-party dependency file. Keep `from __future__ import annotations` in Python modules, prefer dataclasses for structured records, and use type hints on public helpers. Follow existing style: 4-space indentation, snake_case functions and variables, UPPER_CASE constants, and `Path` objects for filesystem paths.

## Testing Guidelines

For behavior changes, run the unit test suite and a bounded dry run before updating state:

```bash
PYTHONPATH=src python3 -m rss_watch.cli --opml "Inoreader Feeds 20260211.xml" --mode morning --dry-run --max-feeds 10
```

Place tests under `tests/`, name files `test_*.py`, and prefer focused fixtures for OPML, RSS, Atom, scoring, Slack failure, and state JSON edge cases.

## Commit & Pull Request Guidelines

Recent commits use short imperative subjects, for example `Harden fcc-lms-watch timeouts and partial-failure handling`. Keep commits scoped to one concern and avoid committing generated `output/` churn unless it is intentionally part of the change.

Pull requests should describe the workflow affected, list verification commands run, note any launchd or environment changes, and include sample briefing output only when it helps review the behavior.

## Security & Configuration Tips

Do not commit real Slack webhook URLs or private credentials. Prefer `SLACK_WEBHOOK_URL` in the environment. Dry runs must not update state, and items should be marked seen only after Slack posts successfully when Slack delivery is configured. Treat OPML exports and generated briefings as editorial working material; review before sharing outside the project.
