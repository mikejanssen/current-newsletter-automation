# FCC LMS Watch

Daily delta monitor for LMS Assignment/Transfer and Application Search, filtered to CPB stations.

## Scope
- Assignment/Transfer applications
- STA / silent notifications
- Construction Permit / License to Cover
- Application PN Search (public notices for submitted filings)
- Action PN Search (public notices for actions on applications)

## How it works
- Submits LMS public search forms with a date window.
- Exports CSV results from LMS.
- Filters to CPB station list using call sign, facility ID, and org-name matching.
- Deduplicates by file number + application id.
- Sends Slack alerts.

## Configuration
Required environment variable:
- SLACK_WEBHOOK_URL

Optional:
- FCC_LMS_LOOKBACK_DAYS (default: 3)
- FCC_LMS_REQUEST_TIMEOUT_SECONDS (default: 120)
- FCC_LMS_REQUEST_RETRIES (default: 2)
- FCC_LMS_RETRY_BACKOFF_SECONDS (default: 2)
- FCC_LMS_BROWSER_FALLBACK (`1` to enable optional Playwright fallback when installed)
- FCC_LMS_BROWSER_ENGINE (`chromium` by default)
- FCC_LMS_PN_DATE_PAGE_FALLBACK (`1` to enable experimental PN date-page scraping; off by default because LMS can be slow)

## Run locally
From `projects/fcc-lms-watch`:

```bash
PYTHONPATH=src python3 -m fcc_lms_watch.cli daily \
  --cpb ../990s/cpb-grantees.csv \
  --cpb-aliases station-aliases.json \
  --state output/state.json \
  --out output/last-run.json
```

Use `--dry-run` for diagnostics. Dry runs write the run report but do not post Slack alerts or update `output/state.json`.

Optional aliases file:
- `station-aliases.json` supports extra match keys to treat as CPB matches:
  - `call_signs` (existing behavior)
  - `facility_ids` (FCC facility ID strings)
  - `org_names` (licensee/organization names)

## Validate Known Positive
Run a non-mutating known-positive check against the WNED-FM filing validated during development:

```bash
scripts/run-validation.sh
```

The command uses dry-run semantics and expects file `0000288968` with category `minor_modification`.
It uses the same workspace Python preference as the scheduled job, enables the browser fallback, and uses short LMS timeouts by default.

## Public Notice Scan
Public Notice scraping is separate from the daily LMS scan because date-page scraping is slower and more fragile. Run it dry first:

```bash
PYTHONPATH=src python3 -m fcc_lms_watch.cli pn --dry-run \
  --state output/pn-state.json \
  --out output/pn-last-run.json
```

Use `scripts/run-pn-scan.sh` for the scheduled form. It uses separate PN state/output files. `pn-validate` checks a known Application PN row, file `0000296158` for WRVO, without posting Slack or updating state:

```bash
scripts/run-pn-validation.sh
```

The sample LaunchAgent is `launchd/com.current.fcc-lms-pn-watch.daily.plist`, scheduled for 10:30 a.m. with separate PN logs.

## Tests
Run local unit tests with:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

## Probe LMS Access
Compare the Python client path with `curl` against an LMS page:

```bash
PYTHONPATH=src python3 -m fcc_lms_watch.cli probe \
  --url "https://enterpriseefiling.fcc.gov/dataentry/public/tv/publicSearchLanding.html" \
  --out output/probe-last.json
```

This writes a JSON report with:
- Python request diagnostics
- `curl` response headers/body size/cookies

## Optional Browser Fallback
If LMS blocks scripted `urllib`/`curl` requests with `403`, you can enable a Playwright-backed fallback:

```bash
python3 -m pip install playwright
python3 -m playwright install chromium
```

Then run with:

```bash
FCC_LMS_BROWSER_FALLBACK=1 PYTHONPATH=src python3 -m fcc_lms_watch.cli daily \
  --cpb ../990s/cpb-grantees.csv \
  --cpb-aliases station-aliases.json \
  --state output/state.json \
  --out output/last-run.json
```

## Scheduling
See `launchd/com.current.fcc-lms-watch.daily.plist` for a sample LaunchAgent. Set `SLACK_WEBHOOK_URL` in the plist `EnvironmentVariables`.

State is updated only after a successful Slack post when alerts or warnings are present. If Slack fails, the filing remains eligible for the next run.
