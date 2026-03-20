#!/bin/sh
set -eu

APP_ROOT="${APP_ROOT:-$(pwd)}"
DB_PATH="${AUDIT_CHATBOT_DB_PATH:-/var/data/audit-chatbot.db}"
SEEDED_DB_NAME="${AUDIT_CHATBOT_SEEDED_DB:-audit-chatbot-combined.db}"
SEEDED_DB_CANDIDATES="
$APP_ROOT/output/$SEEDED_DB_NAME
$APP_ROOT/output/audit-chatbot.db
"

mkdir -p "$(dirname "$DB_PATH")"

if [ ! -f "$DB_PATH" ]; then
  for candidate in $SEEDED_DB_CANDIDATES; do
    if [ -f "$candidate" ]; then
      cp "$candidate" "$DB_PATH"
      break
    fi
  done
fi

exec python3 -m uvicorn audit_chatbot.app:app --host 0.0.0.0 --port "${PORT:-8790}"
