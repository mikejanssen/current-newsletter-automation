#!/bin/zsh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

: "${AUDIT_CHATBOT_DB_PATH:=output/audit-chatbot.db}"
: "${AUDIT_ARCHIVE_ROOT:=../audit-watch/output/audits}"
: "${AUDIT_STATIONS_CSV:=../audit-watch/config/stations.csv}"
: "${AUDIT_SEMIPUBLIC_ROOT:=}"

if [[ ! -d "$AUDIT_ARCHIVE_ROOT" ]]; then
  echo "archive root not found: $AUDIT_ARCHIVE_ROOT"
  exit 2
fi

if [[ ! -f "$AUDIT_STATIONS_CSV" ]]; then
  echo "stations csv not found: $AUDIT_STATIONS_CSV"
  exit 2
fi

cmd=(
  python3 -m audit_chatbot ingest
  --db "$AUDIT_CHATBOT_DB_PATH"
  --archive-root "$AUDIT_ARCHIVE_ROOT"
  --stations "$AUDIT_STATIONS_CSV"
)

if [[ -n "$AUDIT_SEMIPUBLIC_ROOT" ]]; then
  if [[ ! -d "$AUDIT_SEMIPUBLIC_ROOT" ]]; then
    echo "semipublic root not found: $AUDIT_SEMIPUBLIC_ROOT"
    exit 2
  fi
  cmd+=(--semipublic-root "$AUDIT_SEMIPUBLIC_ROOT")
fi

PYTHONPATH=src "${cmd[@]}"

echo "refreshed db: $AUDIT_CHATBOT_DB_PATH"
