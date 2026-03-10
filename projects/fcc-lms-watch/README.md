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

## Run locally
From `projects/fcc-lms-watch`:

```bash
PYTHONPATH=src python3 -m fcc_lms_watch.cli daily \
  --cpb ../990s/cpb-grantees.csv \
  --cpb-aliases station-aliases.json \
  --state output/state.json \
  --out output/last-run.json
```

Optional aliases file:
- `station-aliases.json` supports extra match keys to treat as CPB matches:
  - `call_signs` (existing behavior)
  - `facility_ids` (FCC facility ID strings)
  - `org_names` (licensee/organization names)

## Scheduling
See `launchd/com.current.fcc-lms-watch.daily.plist` for a sample LaunchAgent. Set `SLACK_WEBHOOK_URL` in the plist `EnvironmentVariables`.
