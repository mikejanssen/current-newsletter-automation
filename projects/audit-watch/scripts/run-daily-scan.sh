#!/bin/zsh
set -euo pipefail

PROJECT_DIR="/Users/jansen/Current Dropbox/Mike Janssen/my-assistant/projects/audit-watch"
cd "$PROJECT_DIR"

: "${AUDIT_WATCH_TIMEOUT_SECONDS:=20}"
: "${AUDIT_WATCH_SLACK_MAX_NEW_DOCS:=5}"
: "${AUDIT_WATCH_SLACK_MAX_FAILURES:=10}"
: "${AUDIT_WATCH_SLACK_MAX_STRICT_RISKS:=5}"
: "${AUDIT_WATCH_SLACK_MAX_WATCHLIST_RISKS:=5}"
: "${AUDIT_CHATBOT_DB:=../audit-chatbot/output/audit-chatbot.db}"
: "${AUDIT_CHATBOT_RISK_LIMIT:=8}"

PYTHONPATH=src python3 -m audit_watch.cli daily-run \
  --stations config/stations.csv \
  --state output/state.json \
  --out output/last-run.json \
  --brief output/briefing.md \
  --failures-out output/fetch-failures.json \
  --archive-root output/audits \
  --timeout-seconds "$AUDIT_WATCH_TIMEOUT_SECONDS"

RUN_DATE="$(python3 - <<'PY'
import json
from pathlib import Path
p = Path("output/last-run.json")
if not p.exists():
    print("")
else:
    payload = json.loads(p.read_text(encoding="utf-8"))
    print(str(payload.get("run_date", "")).strip())
PY
)"

PYTHONPATH=../audit-chatbot/src python3 -m audit_chatbot ingest \
  --db "$AUDIT_CHATBOT_DB" \
  --archive-root output/audits \
  --stations config/stations.csv

PYTHONPATH=../audit-chatbot/src python3 -m audit_chatbot query \
  --db "$AUDIT_CHATBOT_DB" \
  --limit "$AUDIT_CHATBOT_RISK_LIMIT" \
  --path-date "$RUN_DATE" \
  --out output/risk-briefing.md \
  --json-out output/risk-briefing.json \
  risks-all

python3 - <<'PY'
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

run_path = Path("output/last-run.json")
if not run_path.exists():
    print("audit-watch: no output/last-run.json; skipping Slack post")
    raise SystemExit(0)

webhook = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
if not webhook:
    print("audit-watch: SLACK_WEBHOOK_URL not set; skipping Slack post")
    raise SystemExit(0)

payload = json.loads(run_path.read_text(encoding="utf-8"))
failures_path = Path("output/fetch-failures.json")
failures_payload = {}
if failures_path.exists():
    failures_payload = json.loads(failures_path.read_text(encoding="utf-8"))
failures = failures_payload.get("failures") or []
risk_path = Path("output/risk-briefing.json")
risk_payload = {}
if risk_path.exists():
    risk_payload = json.loads(risk_path.read_text(encoding="utf-8"))

counts = payload.get("counts", {})
new_docs = int(counts.get("new_documents", 0))
flagged_docs = int(counts.get("flagged_documents", 0))
station_failures = int(counts.get("stations_with_failures", 0))
run_date = str(payload.get("run_date", "unknown"))
max_new_docs = int(os.environ.get("AUDIT_WATCH_SLACK_MAX_NEW_DOCS", "5") or "5")
max_failures = int(os.environ.get("AUDIT_WATCH_SLACK_MAX_FAILURES", "10") or "10")
max_strict = int(os.environ.get("AUDIT_WATCH_SLACK_MAX_STRICT_RISKS", "5") or "5")
max_watchlist = int(os.environ.get("AUDIT_WATCH_SLACK_MAX_WATCHLIST_RISKS", "5") or "5")
strict_count = int(risk_payload.get("strict_station_count", 0) or 0)
watchlist_count = int(risk_payload.get("watchlist_station_count", 0) or 0)

notify_on_no_changes = os.environ.get("AUDIT_WATCH_NOTIFY_ON_NO_CHANGES", "0").strip().lower()
if (
    new_docs == 0
    and station_failures == 0
    and strict_count == 0
    and watchlist_count == 0
    and notify_on_no_changes not in {"1", "true", "yes"}
):
    print("audit-watch: no new docs and no failures; Slack post skipped")
    raise SystemExit(0)

lines = [
    f"*Audit Watch* ({run_date})",
    f"New docs: {new_docs} | Flagged: {flagged_docs} | Stations with failures: {station_failures}",
    f"Risk signals: strict={strict_count} | watchlist={watchlist_count}",
]
docs = payload.get("new_documents") or []
if docs:
    lines.append("")
    lines.append("Top new docs:")
    for doc in docs[:max_new_docs]:
        station = str(doc.get("station_name", "Unknown station"))
        title = str(doc.get("title", "Untitled")).replace("\n", " ").strip()
        flags = str(doc.get("flags", "")).strip()
        doc_url = str(doc.get("document_url", "")).strip()
        detail = f"{station}: {title}"
        if doc_url:
            detail += f" (<{doc_url}|link>)"
        if flags:
            detail += f" [{flags}]"
        lines.append(f"- {detail}")

if failures:
    lines.append("")
    lines.append("Top failed pages:")
    for item in failures[:max_failures]:
        station = str(item.get("station_name") or item.get("station_id") or "Unknown station")
        page_url = str(item.get("page_url", "")).strip()
        error = str(item.get("error", "")).replace("\n", " ").strip()
        if len(error) > 180:
            error = error[:177] + "..."
        if page_url:
            lines.append(f"- {station}: <{page_url}|page> — {error}")
        else:
            lines.append(f"- {station}: {error}")

strict_hits = risk_payload.get("strict_highlights") or []
if strict_hits:
    lines.append("")
    lines.append("Strict risk highlights:")
    for item in strict_hits[:max_strict]:
        station = str(item.get("station_name", "Unknown station"))
        title = str(item.get("title", "Untitled")).replace("\n", " ").strip()
        pattern = str(item.get("pattern", "")).strip()
        source = str(item.get("source", "")).strip()
        if source:
            lines.append(f"- [{pattern}] {station}: {title} (`{source}`)")
        else:
            lines.append(f"- [{pattern}] {station}: {title}")

watch_hits = risk_payload.get("watchlist_highlights") or []
if watch_hits:
    lines.append("")
    lines.append("Watchlist highlights:")
    for item in watch_hits[:max_watchlist]:
        station = str(item.get("station_name", "Unknown station"))
        title = str(item.get("title", "Untitled")).replace("\n", " ").strip()
        pattern = str(item.get("pattern", "")).strip()
        source = str(item.get("source", "")).strip()
        if source:
            lines.append(f"- [{pattern}] {station}: {title} (`{source}`)")
        else:
            lines.append(f"- [{pattern}] {station}: {title}")

body = json.dumps({"text": "\n".join(lines)}).encode("utf-8")
req = Request(webhook, data=body, headers={"Content-Type": "application/json"})
with urlopen(req, timeout=20) as resp:
    _ = resp.read()
print("audit-watch: posted Slack summary")
PY
