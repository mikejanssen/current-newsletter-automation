#!/bin/zsh
set -euo pipefail

PROJECT_DIR="/Users/jansen/Current Dropbox/Mike Janssen/my-assistant/projects/fcc-lms-watch"
WORKSPACE_PYTHON="/Users/jansen/Current Dropbox/Mike Janssen/my-assistant/.venv/bin/python"

cd "$PROJECT_DIR"
PYTHON_BIN="python3"
if [[ -x "$WORKSPACE_PYTHON" ]]; then
  PYTHON_BIN="$WORKSPACE_PYTHON"
fi

PYTHONPATH=src "$PYTHON_BIN" -m fcc_lms_watch.cli pn-validate
