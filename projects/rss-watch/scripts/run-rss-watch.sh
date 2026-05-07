#!/bin/zsh
set -euo pipefail

PROJECT_DIR="/Users/jansen/Current Dropbox/Mike Janssen/my-assistant/projects/rss-watch"
WORKSPACE_PYTHON="/Users/jansen/Current Dropbox/Mike Janssen/my-assistant/.venv/bin/python"
OPML_PATH="$PROJECT_DIR/Inoreader Feeds 20260211.xml"
MODE="${1:-morning}"
STATE_PATH="output/state.json"
UPDATE_SKIP_RECENT_MINUTES="${RSS_WATCH_UPDATE_SKIP_RECENT_MINUTES:-45}"

if [[ "$MODE" == "morning" ]]; then
  CLI_MODE="morning"
  WINDOW_HOURS="72"
  OUT_PATH="output/last-run.json"
  CANDIDATES_PATH="output/candidates.json"
  BRIEF_PATH="output/briefing.md"
else
  CLI_MODE="update"
  WINDOW_HOURS="24"
  OUT_PATH="output/last-run-update.json"
  CANDIDATES_PATH="output/candidates-update.json"
  BRIEF_PATH="output/briefing-update.md"
fi

cd "$PROJECT_DIR"
PYTHON_BIN="python3"
if [[ -x "$WORKSPACE_PYTHON" ]]; then
  PYTHON_BIN="$WORKSPACE_PYTHON"
fi

if [[ "$CLI_MODE" == "update" && -f "$STATE_PATH" ]]; then
  if "$PYTHON_BIN" - "$STATE_PATH" "$UPDATE_SKIP_RECENT_MINUTES" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

state_path = Path(sys.argv[1])
threshold_minutes = int(sys.argv[2])
data = json.loads(state_path.read_text(encoding="utf-8"))
last_checked = data.get("last_checked")
if not last_checked:
    raise SystemExit(1)
last_dt = datetime.fromisoformat(last_checked.replace("Z", "+00:00")).astimezone(timezone.utc)
age_seconds = (datetime.now(timezone.utc) - last_dt).total_seconds()
raise SystemExit(0 if age_seconds < (threshold_minutes * 60) else 1)
PY
  then
    echo "Skipping update run: last_checked is within ${UPDATE_SKIP_RECENT_MINUTES} minutes."
    exit 0
  fi
fi

PYTHONPATH=src "$PYTHON_BIN" -m rss_watch.cli \
  --opml "$OPML_PATH" \
  --mode "$CLI_MODE" \
  --window-hours "$WINDOW_HOURS" \
  --state "$STATE_PATH" \
  --out "$OUT_PATH" \
  --candidates-out "$CANDIDATES_PATH" \
  --brief "$BRIEF_PATH" \
  --max-items 200 \
  --max-item-age-days 30 \
  --feed-timeout-seconds 8 \
  --feed-retries 0 \
  --parallelism 12
