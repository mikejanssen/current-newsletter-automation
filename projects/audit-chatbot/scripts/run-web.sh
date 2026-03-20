#!/bin/zsh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

if [[ -f ".env.local" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ".env.local"
  set +a
fi

: "${AUDIT_CHATBOT_HOST:=0.0.0.0}"
: "${AUDIT_CHATBOT_PORT:=8790}"
: "${AUDIT_CHATBOT_DB_PATH:=output/audit-chatbot-combined.db}"

if [[ ! -f "$AUDIT_CHATBOT_DB_PATH" ]]; then
  echo "chatbot db not found: $AUDIT_CHATBOT_DB_PATH"
  exit 2
fi

PYTHONPATH=src .venv/bin/python -m uvicorn audit_chatbot.app:app --host "$AUDIT_CHATBOT_HOST" --port "$AUDIT_CHATBOT_PORT"
