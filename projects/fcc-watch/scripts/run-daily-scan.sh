#!/bin/zsh
set -euo pipefail

PROJECT_DIR="/Users/jansen/Current Dropbox/Mike Janssen/my-assistant/projects/fcc-watch"
WORKSPACE_PYTHON="/Users/jansen/Current Dropbox/Mike Janssen/my-assistant/.venv/bin/python"

cd "$PROJECT_DIR"
PYTHON_BIN="python3"
if [[ -x "$WORKSPACE_PYTHON" ]]; then
  PYTHON_BIN="$WORKSPACE_PYTHON"
fi

# Resilient defaults for FCC-hosted sources; allow env to override.
: "${FCC_WATCH_DIGEST_TIMEOUT_SECONDS:=75}"
: "${FCC_WATCH_DIGEST_RETRIES:=2}"
: "${FCC_WATCH_ECFS_TIMEOUT_SECONDS:=45}"
: "${FCC_WATCH_ECFS_RETRIES:=2}"
: "${FCC_WATCH_MEETING_TIMEOUT_SECONDS:=45}"
: "${FCC_WATCH_MEETING_RETRIES:=2}"
: "${FCC_WATCH_SKIP_DIGEST_ON_PREFLIGHT_FAILURE:=1}"

export FCC_WATCH_DIGEST_TIMEOUT_SECONDS
export FCC_WATCH_DIGEST_RETRIES
export FCC_WATCH_ECFS_TIMEOUT_SECONDS
export FCC_WATCH_ECFS_RETRIES
export FCC_WATCH_MEETING_TIMEOUT_SECONDS
export FCC_WATCH_MEETING_RETRIES
export FCC_WATCH_SKIP_DIGEST_ON_PREFLIGHT_FAILURE

if ! PYTHONPATH=src "$PYTHON_BIN" -m fcc_watch.cli preflight \
  --out output/preflight-last.json
then
  echo "fcc-watch: preflight command failed unexpectedly; continuing daily scan"
fi

if [[ "$FCC_WATCH_SKIP_DIGEST_ON_PREFLIGHT_FAILURE" == "1" ]]; then
  if PYTHONPATH=src "$PYTHON_BIN" - <<'PY'
import json
from pathlib import Path

preflight = Path("output/preflight-last.json")
if not preflight.exists():
    raise SystemExit(1)
payload = json.loads(preflight.read_text(encoding="utf-8"))
for result in payload.get("results", []):
    if "fcc.gov/edocs/daily-digest" in result.get("url", "") and not result.get("http_ok"):
        raise SystemExit(0)
raise SystemExit(1)
PY
  then
    echo "fcc-watch: Daily Digest preflight failed; skipping Daily Digest for this run"
    export FCC_WATCH_SKIP_DIGEST=1
  fi
fi

PYTHONPATH=src "$PYTHON_BIN" -m fcc_watch.cli daily \
  --cpb ../990s/cpb-grantees.csv \
  --state output/state.json \
  --out output/last-run.json
