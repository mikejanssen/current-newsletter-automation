#!/bin/zsh
set -euo pipefail

PROJECT_DIR="/Users/jansen/Current Dropbox/Mike Janssen/my-assistant/projects/fcc-lms-watch"
WORKSPACE_PYTHON="/Users/jansen/Current Dropbox/Mike Janssen/my-assistant/.venv/bin/python"

cd "$PROJECT_DIR"
PYTHON_BIN="python3"
if [[ -x "$WORKSPACE_PYTHON" ]]; then
  PYTHON_BIN="$WORKSPACE_PYTHON"
fi

: "${FCC_LMS_BROWSER_FALLBACK:=1}"
: "${FCC_LMS_REQUEST_TIMEOUT_SECONDS:=15}"
: "${FCC_LMS_REQUEST_RETRIES:=0}"
export FCC_LMS_BROWSER_FALLBACK FCC_LMS_REQUEST_TIMEOUT_SECONDS FCC_LMS_REQUEST_RETRIES

PYTHONPATH=src "$PYTHON_BIN" -m fcc_lms_watch.cli pn \
  --cpb ../990s/cpb-grantees.csv \
  --state output/pn-state.json \
  --out output/pn-last-run.json
