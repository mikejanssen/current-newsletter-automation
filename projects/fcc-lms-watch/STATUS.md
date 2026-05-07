# FCC LMS Watch — Status Handoff (2026-05-07)

## Current State

This project monitors FCC LMS filings for public-media relevance by matching LMS rows against CPB grantees and local aliases. It now has two separate scheduled paths:

- `daily`: Assignment/Transfer plus Application Search categories.
- `pn`: Application and Action Public Notices, with separate state/output files.

The split is intentional. Public Notice scraping relies on slower FCC date-page/browser fallbacks and should not block the core daily LMS scan.

## Recent Changes

- Added the separate `pn` command and `pn-validate` command.
- Added `scripts/run-pn-scan.sh` and `scripts/run-pn-validation.sh`.
- Added `launchd/com.current.fcc-lms-pn-watch.daily.plist`, scheduled for 10:30 a.m.
- Updated the daily scan so PN modes are reported as `skipped_separate_pn_command`.
- Added PN dry-run/status fields: `slack_status`, `state_updated`, `alert_keys`, source modes, request diagnostics, matched counts, and unmatched call-sign summaries.
- Fixed PN alert links so generated `publicNoticeSearchResult.html?...` URLs are not emitted. PN alerts now prefer direct application links when available, otherwise stable facility URLs such as `publicFacilityDetails.html?facilityId=63115`.
- Removed PN alert URL from the de-duplication key so future link formatting changes do not create duplicate alerts.

## Validation

Unit tests passed on 2026-05-07:

```bash
PYTHONPATH=src python3 -m unittest tests.test_pn_command
PYTHONPATH=src python3 -m unittest discover tests
```

Results:

- PN focused tests: 3 passed.
- Full unit suite: 7 passed.

Live non-mutating WRVO PN validation also passed as a dry run:

- File: `0000296158`
- Call sign: `WRVO`
- Facility ID: `63115`
- Service: `Full Power FM`
- Status: `Accepted for Filing`
- Emitted detail URL: `https://enterpriseefiling.fcc.gov/dataentry/public/tv/publicFacilityDetails.html?facilityId=63115`
- `slack_status`: `dry_run`
- `state_updated`: `false`

## Known Limitations

- FCC LMS Application Search can still hang or fail in the browser/export path. Manual week scan on 2026-05-06 completed PN and Assignment/Transfer parsing, but Application Search was inconclusive.
- Assignment/Transfer CSV rows can occasionally appear column-shifted; verify individual filing fields before publication.
- PN date-page URLs are session-like and may show FCC technical-error pages when opened directly. Use facility/application detail URLs in alerts and reporting.

## Useful Commands

Daily dry run:

```bash
PYTHONPATH=src python3 -m fcc_lms_watch.cli daily --dry-run --debug-export-dir output/debug
```

PN dry run:

```bash
PYTHONPATH=src python3 -m fcc_lms_watch.cli pn --dry-run --date-page-fallback
```

Known-positive validations:

```bash
scripts/run-validation.sh
scripts/run-pn-validation.sh
```

## Next Priorities

- Harden Application Search so FCC hangs fail fast and degrade predictably.
- Consider enriching PN rows with direct application links by cross-checking facility/application history when feasible.
- Confirm both launchd jobs are loaded after any machine or plist changes:

```bash
launchctl list | rg 'fcc-lms-watch|fcc-lms-pn-watch'
```
