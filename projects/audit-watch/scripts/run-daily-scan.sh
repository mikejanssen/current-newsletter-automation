#!/bin/zsh
set -euo pipefail

PROJECT_DIR="/Users/jansen/Current Dropbox/Mike Janssen/my-assistant/projects/audit-watch"
cd "$PROJECT_DIR"

if [[ -f ./.env ]]; then
  set -a
  . ./.env
  set +a
fi

: "${AUDIT_WATCH_TIMEOUT_SECONDS:=20}"
: "${AUDIT_WATCH_WORKERS:=8}"
: "${AUDIT_WATCH_FAILURE_RETRY_PASSES:=1}"
: "${AUDIT_WATCH_FAILURE_RETRY_WORKERS:=2}"
: "${AUDIT_WATCH_FAILURE_RETRY_TIMEOUT_MULTIPLIER:=1.5}"
: "${AUDIT_WATCH_SLACK_MAX_NEW_DOCS:=5}"
: "${AUDIT_WATCH_SLACK_MAX_FAILURES:=10}"
: "${AUDIT_WATCH_SLACK_MAX_STRICT_RISKS:=5}"
: "${AUDIT_WATCH_SLACK_MAX_WATCHLIST_RISKS:=5}"
: "${AUDIT_CHATBOT_DB:=../audit-chatbot/output/audit-chatbot.db}"
: "${AUDIT_CHATBOT_RISK_LIMIT:=8}"
: "${AUDIT_WATCH_ARCHIVE_SCOPE:=latest}"

PYTHONPATH=src python3 -m audit_watch.cli run-and-notify \
  --stations config/stations.csv \
  --state output/state.json \
  --out output/last-run.json \
  --brief output/briefing.md \
  --failures-out output/fetch-failures.json \
  --archive-root output/audits \
  --archive-scope "$AUDIT_WATCH_ARCHIVE_SCOPE" \
  --timeout-seconds "$AUDIT_WATCH_TIMEOUT_SECONDS" \
  --workers "$AUDIT_WATCH_WORKERS" \
  --failure-retry-passes "$AUDIT_WATCH_FAILURE_RETRY_PASSES" \
  --failure-retry-workers "$AUDIT_WATCH_FAILURE_RETRY_WORKERS" \
  --failure-retry-timeout-multiplier "$AUDIT_WATCH_FAILURE_RETRY_TIMEOUT_MULTIPLIER" \
  --audit-chatbot-db "$AUDIT_CHATBOT_DB" \
  --risk-limit "$AUDIT_CHATBOT_RISK_LIMIT" \
  --risk-brief output/risk-briefing.md \
  --risk-json-out output/risk-briefing.json \
  --health-out output/health.json \
  --slack-max-new-docs "$AUDIT_WATCH_SLACK_MAX_NEW_DOCS" \
  --slack-max-failures "$AUDIT_WATCH_SLACK_MAX_FAILURES" \
  --slack-max-strict-risks "$AUDIT_WATCH_SLACK_MAX_STRICT_RISKS" \
  --slack-max-watchlist-risks "$AUDIT_WATCH_SLACK_MAX_WATCHLIST_RISKS"
