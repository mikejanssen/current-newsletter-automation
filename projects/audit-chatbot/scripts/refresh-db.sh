#!/bin/zsh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

: "${AUDIT_CHATBOT_DB_PATH:=output/audit-chatbot.db}"
: "${AUDIT_ARCHIVE_ROOT:=../audit-watch/output/audits}"
: "${AUDIT_STATIONS_CSV:=../audit-watch/config/stations.csv}"

if [[ ! -d "$AUDIT_ARCHIVE_ROOT" ]]; then
  echo "archive root not found: $AUDIT_ARCHIVE_ROOT"
  exit 2
fi

if [[ ! -f "$AUDIT_STATIONS_CSV" ]]; then
  echo "stations csv not found: $AUDIT_STATIONS_CSV"
  exit 2
fi

PYTHONPATH=src python3 -m audit_chatbot ingest \
  --db "$AUDIT_CHATBOT_DB_PATH" \
  --archive-root "$AUDIT_ARCHIVE_ROOT" \
  --stations "$AUDIT_STATIONS_CSV"

echo "refreshed db: $AUDIT_CHATBOT_DB_PATH"
