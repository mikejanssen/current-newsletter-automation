from __future__ import annotations

import argparse
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.request import Request, urlopen

from .models import AuditDocument, StationRecord
from .core import (
    ValidationError,
    archive_document,
    build_payload,
    build_health_payload,
    classify_failure_type,
    discover_page_candidates,
    discover_station_docs,
    extract_document_year,
    is_transient_failure,
    latest_station_documents,
    load_state,
    load_stations,
    unresolved_stations,
    save_state,
    summarize_failures,
    utc_now_iso,
    validate_station_records,
    write_brief,
    write_json,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="audit_watch",
        description="Track newly posted station audit documents, archive copies, and flag unusual findings.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    daily = subparsers.add_parser("daily-run", help="Run station page scan, archive new docs, and build briefing outputs")
    daily.add_argument("--stations", default="config/stations.csv")
    daily.add_argument("--state", default="output/state.json")
    daily.add_argument("--out", default="output/last-run.json")
    daily.add_argument("--brief", default="output/briefing.md")
    daily.add_argument("--failures-out", default="output/fetch-failures.json")
    daily.add_argument("--archive-root", default="output/audits")
    daily.add_argument("--timeout-seconds", type=int, default=20)
    daily.add_argument("--dry-run", action="store_true", help="Do not update state file")
    daily.add_argument("--health-out", default="output/health.json")
    daily.add_argument("--max-stations", type=int, help="Optional cap for bounded validation runs")
    daily.add_argument("--workers", type=int, default=int(os.environ.get("AUDIT_WATCH_WORKERS", "8")))
    daily.add_argument(
        "--failure-retry-passes",
        type=int,
        default=int(os.environ.get("AUDIT_WATCH_FAILURE_RETRY_PASSES", "1")),
        help="Retry transient station fetch failures this many times at reduced concurrency",
    )
    daily.add_argument(
        "--failure-retry-workers",
        type=int,
        default=int(os.environ.get("AUDIT_WATCH_FAILURE_RETRY_WORKERS", "2")),
        help="Worker count for transient failure retry passes",
    )
    daily.add_argument(
        "--failure-retry-timeout-multiplier",
        type=float,
        default=float(os.environ.get("AUDIT_WATCH_FAILURE_RETRY_TIMEOUT_MULTIPLIER", "1.5")),
        help="Timeout multiplier for transient failure retry passes",
    )
    daily.add_argument("--discover-only", action="store_true", help="Fetch station pages but do not archive or update state")
    daily.add_argument("--no-archive", action="store_true", help="Do not download documents or update state")
    daily.add_argument(
        "--archive-scope",
        choices=("all", "latest"),
        default=os.environ.get("AUDIT_WATCH_ARCHIVE_SCOPE", "all"),
        help="Archive all new docs or only the current best document(s) per station",
    )

    discover = subparsers.add_parser(
        "discover-pages",
        help="Find likely financial/audit page candidates for unresolved stations",
    )
    discover.add_argument("--stations", default="config/stations.csv")
    discover.add_argument("--out", default="output/page-discovery-candidates.csv")
    discover.add_argument("--failures-out", default="output/page-discovery-failures.json")
    discover.add_argument("--timeout-seconds", type=int, default=12)
    discover.add_argument("--limit", type=int, default=50, help="Max unresolved stations to process")
    discover.add_argument("--max-candidates", type=int, default=5, help="Max URLs to keep per station")
    discover.add_argument(
        "--apply",
        action="store_true",
        help="Apply top candidate URL into stations.csv as enabled=0 with AUTO_DISCOVERY note",
    )

    review_disabled = subparsers.add_parser(
        "review-disabled",
        help="Scan disabled stations that already have page URLs and write a review queue",
    )
    review_disabled.add_argument("--stations", default="config/stations.csv")
    review_disabled.add_argument("--out", default="output/disabled-review.json")
    review_disabled.add_argument("--csv-out", default="output/disabled-review.csv")
    review_disabled.add_argument("--failures-out", default="output/disabled-review-failures.json")
    review_disabled.add_argument("--timeout-seconds", type=int, default=20)
    review_disabled.add_argument("--limit", type=int, default=25, help="Max disabled stations to scan")
    review_disabled.add_argument("--offset", type=int, default=0, help="Skip this many disabled stations before scanning")
    review_disabled.add_argument("--workers", type=int, default=int(os.environ.get("AUDIT_WATCH_WORKERS", "8")))

    validate = subparsers.add_parser("validate-stations", help="Validate station config and coverage hygiene")
    validate.add_argument("--stations", default="config/stations.csv")
    validate.add_argument("--out", default="", help="Optional JSON output path")

    notify = subparsers.add_parser(
        "run-and-notify",
        help="Run scan, audit-chatbot risk rollup, health output, and optional Slack notification",
    )
    notify.add_argument("--stations", default="config/stations.csv")
    notify.add_argument("--state", default="output/state.json")
    notify.add_argument("--out", default="output/last-run.json")
    notify.add_argument("--brief", default="output/briefing.md")
    notify.add_argument("--failures-out", default="output/fetch-failures.json")
    notify.add_argument("--archive-root", default="output/audits")
    notify.add_argument("--timeout-seconds", type=int, default=int(os.environ.get("AUDIT_WATCH_TIMEOUT_SECONDS", "20")))
    notify.add_argument("--max-stations", type=int, help="Optional cap for bounded validation runs")
    notify.add_argument("--workers", type=int, default=int(os.environ.get("AUDIT_WATCH_WORKERS", "8")))
    notify.add_argument(
        "--failure-retry-passes",
        type=int,
        default=int(os.environ.get("AUDIT_WATCH_FAILURE_RETRY_PASSES", "1")),
        help="Retry transient station fetch failures this many times at reduced concurrency",
    )
    notify.add_argument(
        "--failure-retry-workers",
        type=int,
        default=int(os.environ.get("AUDIT_WATCH_FAILURE_RETRY_WORKERS", "2")),
        help="Worker count for transient failure retry passes",
    )
    notify.add_argument(
        "--failure-retry-timeout-multiplier",
        type=float,
        default=float(os.environ.get("AUDIT_WATCH_FAILURE_RETRY_TIMEOUT_MULTIPLIER", "1.5")),
        help="Timeout multiplier for transient failure retry passes",
    )
    notify.add_argument("--discover-only", action="store_true", help="Fetch station pages but do not archive, update state, run risk, or post Slack")
    notify.add_argument("--no-archive", action="store_true", help="Do not download documents, update state, run risk, or post Slack")
    notify.add_argument(
        "--archive-scope",
        choices=("all", "latest"),
        default=os.environ.get("AUDIT_WATCH_ARCHIVE_SCOPE", "all"),
        help="Archive all new docs or only the current best document(s) per station",
    )
    notify.add_argument("--audit-chatbot-db", default=os.environ.get("AUDIT_CHATBOT_DB", "../audit-chatbot/output/audit-chatbot.db"))
    notify.add_argument("--risk-brief", default="output/risk-briefing.md")
    notify.add_argument("--risk-json-out", default="output/risk-briefing.json")
    notify.add_argument("--risk-limit", type=int, default=int(os.environ.get("AUDIT_CHATBOT_RISK_LIMIT", "8")))
    notify.add_argument("--health-out", default="output/health.json")
    notify.add_argument("--slack-webhook", default=os.environ.get("SLACK_WEBHOOK_URL", ""))
    notify.add_argument("--slack-max-new-docs", type=int, default=int(os.environ.get("AUDIT_WATCH_SLACK_MAX_NEW_DOCS", "5")))
    notify.add_argument("--slack-max-failures", type=int, default=int(os.environ.get("AUDIT_WATCH_SLACK_MAX_FAILURES", "10")))
    notify.add_argument("--slack-max-strict-risks", type=int, default=int(os.environ.get("AUDIT_WATCH_SLACK_MAX_STRICT_RISKS", "5")))
    notify.add_argument("--slack-max-watchlist-risks", type=int, default=int(os.environ.get("AUDIT_WATCH_SLACK_MAX_WATCHLIST_RISKS", "5")))
    notify.add_argument(
        "--notify-on-no-changes",
        action="store_true",
        default=os.environ.get("AUDIT_WATCH_NOTIFY_ON_NO_CHANGES", "0").strip().lower() in {"1", "true", "yes"},
    )
    notify.add_argument("--dry-run", action="store_true", help="Do not update state or post Slack")

    status = subparsers.add_parser("status", help="Summarize last run health, failures, and optional launchd state")
    status.add_argument("--health", default="output/health.json")
    status.add_argument("--failures", default="output/fetch-failures.json")
    status.add_argument("--limit-failures", type=int, default=8)
    status.add_argument("--launchd-label", default="com.current.audit-watch.weekly")
    status.add_argument("--no-launchd", action="store_true", help="Do not query launchd")

    recent = subparsers.add_parser("recent-docs", help="Write a CSV of documents that appear to pertain to a recent year")
    recent.add_argument("--archive-root", default="output/audits")
    recent.add_argument("--run-json", action="append", default=[], help="Optional run JSON to include; can be repeated")
    recent.add_argument("--stations", default="config/stations.csv", help="Station config used to label archived files")
    recent.add_argument("--since-year", type=int, default=2025)
    recent.add_argument("--after-archive-date", default="", help="Only include archived files after this YYYY-MM-DD date")
    recent.add_argument("--out", default="output/recent-documents.csv")

    return parser


def _bounded_workers(requested: int, item_count: int) -> int:
    return max(1, min(max(1, requested), max(1, item_count)))


def _discover_station(station: StationRecord, timeout_seconds: int) -> tuple[list[AuditDocument], dict[str, str] | None]:
    try:
        return discover_station_docs(station, timeout_seconds=timeout_seconds), None
    except Exception as exc:  # pragma: no cover
        error = str(exc)
        return [], {
            "station_id": station.station_id,
            "station_name": station.station_name,
            "page_url": station.page_url,
            "error": error,
            "failure_type": classify_failure_type(error),
            "transient": is_transient_failure(error),
        }


def _archive_new_doc(doc: AuditDocument, archive_root: Path, timeout_seconds: int) -> tuple[AuditDocument | None, dict[str, str] | None]:
    try:
        return archive_document(
            doc,
            archive_root=archive_root,
            timeout_seconds=timeout_seconds,
        ), None
    except Exception as exc:  # pragma: no cover
        error = f"download/archive failed for {doc.document_url}: {exc}"
        return None, {
            "station_id": doc.station_id,
            "station_name": doc.station_name,
            "page_url": doc.page_url,
            "error": error,
            "failure_type": classify_failure_type(error),
            "transient": is_transient_failure(error),
        }


def _skipped_station_rows(stations: list[StationRecord]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for station in stations:
        if station.enabled and station.page_url.strip():
            continue
        if not station.enabled:
            reason = "disabled entry"
        else:
            reason = "enabled without page_url"
        rows.append(
            {
                "station_id": station.station_id,
                "station_name": station.station_name,
                "page_url": station.page_url,
                "reason": reason,
            }
        )
    return rows


def _count_by_reason(rows: list[dict[str, str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        reason = row.get("reason", "unknown")
        counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))


def _discover_stations_parallel(
    stations: list[StationRecord],
    *,
    workers: int,
    timeout_seconds: int,
    retry_pass: int = 0,
) -> tuple[list[AuditDocument], list[dict[str, str]]]:
    discovered: list[AuditDocument] = []
    failures: list[dict[str, str]] = []
    station_workers = _bounded_workers(workers, len(stations))
    with ThreadPoolExecutor(max_workers=station_workers) as executor:
        futures = {
            executor.submit(_discover_station, station, timeout_seconds): station.station_id
            for station in stations
        }
        for future in as_completed(futures):
            station_docs, failure = future.result()
            discovered.extend(station_docs)
            if failure:
                failure["retry_pass"] = retry_pass
                failures.append(failure)
    return discovered, failures


def _retry_transient_station_failures(
    failures: list[dict[str, str]],
    station_by_id: dict[str, StationRecord],
    *,
    retry_passes: int,
    workers: int,
    timeout_seconds: int,
) -> tuple[list[AuditDocument], list[dict[str, str]], int]:
    discovered: list[AuditDocument] = []
    current_failures = failures
    attempts = 0

    for retry_pass in range(1, max(0, retry_passes) + 1):
        retry_stations = []
        seen_station_ids = set()
        for failure in current_failures:
            if not failure.get("transient", is_transient_failure(failure.get("error", ""))):
                continue
            station_id = failure.get("station_id", "")
            if station_id in seen_station_ids:
                continue
            station = station_by_id.get(station_id)
            if station is None:
                continue
            seen_station_ids.add(station_id)
            retry_stations.append(station)

        if not retry_stations:
            break

        attempts += len(retry_stations)
        retry_timeout = max(1, int(round(timeout_seconds)))
        retry_docs, retry_failures = _discover_stations_parallel(
            retry_stations,
            workers=workers,
            timeout_seconds=retry_timeout,
            retry_pass=retry_pass,
        )
        discovered.extend(retry_docs)
        retry_failed_ids = {failure.get("station_id", "") for failure in retry_failures}
        retried_ids = {station.station_id for station in retry_stations}
        preserved_failures = [
            failure
            for failure in current_failures
            if failure.get("station_id", "") not in retried_ids
        ]
        current_failures = preserved_failures + retry_failures
        if not retry_failed_ids:
            break

    return discovered, current_failures, attempts


def _run_daily_scan(args: argparse.Namespace) -> dict:
    started_at = utc_now_iso()
    stations = load_stations(Path(args.stations))
    seen_ids = load_state(Path(args.state))

    runnable_stations = [s for s in stations if s.enabled and s.page_url.strip()]
    if getattr(args, "max_stations", None) is not None:
        runnable_stations = runnable_stations[: args.max_stations]
    skipped_rows = _skipped_station_rows(stations)

    station_workers = _bounded_workers(args.workers, len(runnable_stations))
    discovered, failures = _discover_stations_parallel(
        runnable_stations,
        workers=args.workers,
        timeout_seconds=args.timeout_seconds,
    )
    retry_attempts = 0
    if failures and getattr(args, "failure_retry_passes", 0):
        retry_timeout = max(
            args.timeout_seconds,
            int(round(args.timeout_seconds * max(1.0, getattr(args, "failure_retry_timeout_multiplier", 1.0)))),
        )
        retry_docs, failures, retry_attempts = _retry_transient_station_failures(
            failures,
            {station.station_id: station for station in runnable_stations},
            retry_passes=getattr(args, "failure_retry_passes", 0),
            workers=getattr(args, "failure_retry_workers", 1),
            timeout_seconds=retry_timeout,
        )
        discovered.extend(retry_docs)

    by_id = {d.doc_id: d for d in discovered}
    new_docs = [d for d in by_id.values() if d.doc_id not in seen_ids]
    new_docs = sorted(new_docs, key=lambda d: (d.station_name, d.title, d.document_url))
    archive_scope = getattr(args, "archive_scope", "all")
    if archive_scope == "latest":
        latest_ids = {d.doc_id for d in latest_station_documents(list(by_id.values()))}
        archive_candidates = [d for d in new_docs if d.doc_id in latest_ids]
    else:
        archive_candidates = new_docs

    archive_enabled = not (args.discover_only or args.no_archive or args.dry_run)
    archived_docs = []
    if archive_enabled:
        doc_workers = _bounded_workers(args.workers, len(archive_candidates))
        with ThreadPoolExecutor(max_workers=doc_workers) as executor:
            futures = {
                executor.submit(_archive_new_doc, doc, Path(args.archive_root), args.timeout_seconds): doc.doc_id
                for doc in archive_candidates
            }
            for future in as_completed(futures):
                archived, failure = future.result()
                if archived:
                    archived_docs.append(archived)
                if failure:
                    failures.append(failure)
        archived_docs = sorted(archived_docs, key=lambda d: (d.station_name, d.title, d.document_url))
    else:
        archived_docs = new_docs

    failures = sorted(failures, key=lambda f: (f.get("station_name", ""), f.get("page_url", ""), f.get("error", "")))

    finished_at = utc_now_iso()
    payload = build_payload(
        archived_docs,
        failures,
        started_at=started_at,
        finished_at=finished_at,
        stations_total=len(stations),
        stations_scanned=len(runnable_stations),
        stations_skipped=len(skipped_rows),
        documents_discovered=len(discovered),
        documents_archived=len(archived_docs) if archive_enabled else 0,
        documents_archive_candidates=len(archive_candidates) if archive_enabled else None,
        documents_skipped_by_archive_scope=(len(new_docs) - len(archive_candidates)) if archive_enabled else None,
        archive_scope=archive_scope,
        scan_status="completed" if archive_enabled else "discovered",
    )
    payload["retry_summary"] = {
        "station_retry_passes": max(0, getattr(args, "failure_retry_passes", 0)),
        "station_retry_attempts": retry_attempts,
        "station_retry_workers": getattr(args, "failure_retry_workers", 1),
    }
    write_json(Path(args.out), payload)
    write_brief(Path(args.brief), payload)
    failures_payload = {
        "failures": failures,
        "failure_count": len(failures),
        "skipped_count": len(skipped_rows),
        "skipped_by_reason": _count_by_reason(skipped_rows),
        "skipped": skipped_rows,
    }
    write_json(Path(args.failures_out), failures_payload)

    if archive_enabled and not args.dry_run:
        new_seen = set(seen_ids)
        for doc in archived_docs:
            new_seen.add(doc.doc_id)
        save_state(Path(args.state), new_seen)

    health_out = getattr(args, "health_out", "")
    if health_out:
        write_json(
            Path(health_out),
            build_health_payload(
                run_payload=payload,
                failures_payload=failures_payload,
                risk_payload={},
                started_at=started_at,
                finished_at=finished_at,
                scan_status="completed",
                risk_status="not_attempted",
                slack_status="not_attempted",
            ),
        )

    return {
        "payload": payload,
        "failures_payload": failures_payload,
        "stations_total": len(stations),
        "stations_scanned": len(runnable_stations),
        "stations_skipped": len(skipped_rows),
        "documents_discovered": len(discovered),
        "documents_archive_candidates": len(archive_candidates),
        "archived_docs": archived_docs,
        "archive_enabled": archive_enabled,
        "archive_scope": archive_scope,
        "workers": station_workers,
    }


def _cmd_daily_run(args: argparse.Namespace) -> int:
    result = _run_daily_scan(args)
    payload = result["payload"]
    failures_payload = result["failures_payload"]

    print(f"Stations total: {result['stations_total']}")
    print(f"Stations scanned: {result['stations_scanned']}")
    print(f"Stations skipped: {result['stations_skipped']}")
    print(f"Discovered candidate documents: {result['documents_discovered']}")
    print(f"New documents found: {payload['counts']['new_documents']}")
    print(f"Archive scope: {result['archive_scope']}")
    print(f"Archive candidates: {result['documents_archive_candidates']}")
    print(f"Documents archived: {payload['counts'].get('documents_archived', 0)}")
    print(f"Archive enabled: {result['archive_enabled']}")
    print(f"Workers: {result['workers']}")
    print(f"Station retry attempts: {payload.get('retry_summary', {}).get('station_retry_attempts', 0)}")
    print(f"Fetch/archive failures: {failures_payload['failure_count']}")
    print(f"Wrote run JSON: {args.out}")
    print(f"Wrote briefing: {args.brief}")
    print(f"Wrote failures report: {args.failures_out}")
    return 0


def _cmd_validate_stations(args: argparse.Namespace) -> int:
    stations = load_stations(Path(args.stations))
    payload = validate_station_records(stations)
    if args.out:
        write_json(Path(args.out), payload)
    print(f"Stations: {payload['station_count']}")
    print(f"Enabled: {payload['enabled_count']}")
    print(f"Enabled with page_url: {payload['enabled_with_page_url_count']}")
    print(f"Disabled: {payload['disabled_count']}")
    print(f"Issues: {payload['issue_count']}")
    if payload["duplicate_station_ids"]:
        print("Duplicate station IDs: " + ", ".join(payload["duplicate_station_ids"]))
    if payload["malformed_urls"]:
        print(f"Malformed URLs: {len(payload['malformed_urls'])}")
    if payload["enabled_without_page_url"]:
        print(f"Enabled rows without page_url: {len(payload['enabled_without_page_url'])}")
    return 1 if payload["issue_count"] else 0


def _run_audit_chatbot(args: argparse.Namespace, run_date: str) -> tuple[str, dict, str]:
    env = os.environ.copy()
    chatbot_src = str((Path.cwd() / "../audit-chatbot/src").resolve())
    env["PYTHONPATH"] = chatbot_src
    ingest_cmd = [
        sys.executable,
        "-m",
        "audit_chatbot",
        "ingest",
        "--db",
        args.audit_chatbot_db,
        "--archive-root",
        args.archive_root,
        "--stations",
        args.stations,
    ]
    query_cmd = [
        sys.executable,
        "-m",
        "audit_chatbot",
        "query",
        "--db",
        args.audit_chatbot_db,
        "--limit",
        str(args.risk_limit),
        "--path-date",
        run_date,
        "--out",
        args.risk_brief,
        "--json-out",
        args.risk_json_out,
        "risks-all",
    ]
    try:
        subprocess.run(ingest_cmd, check=True, env=env)
        subprocess.run(query_cmd, check=True, env=env)
    except subprocess.CalledProcessError as exc:
        return "failed", {}, str(exc)
    risk_path = Path(args.risk_json_out)
    if not risk_path.exists():
        return "missing_output", {}, f"Risk JSON not found: {risk_path}"
    return "completed", json.loads(risk_path.read_text(encoding="utf-8")), ""


def _slack_text(
    *,
    run_payload: dict,
    failures_payload: dict,
    risk_payload: dict,
    max_new_docs: int,
    max_failures: int,
    max_strict: int,
    max_watchlist: int,
) -> str:
    counts = run_payload.get("counts", {})
    failures = failures_payload.get("failures") or []
    strict_hits = risk_payload.get("strict_highlights") or []
    watch_hits = risk_payload.get("watchlist_highlights") or []
    lines = [
        f"*Audit Watch* ({run_payload.get('run_date', 'unknown')})",
        (
            f"New docs: {counts.get('new_documents', 0)} | "
            f"Flagged: {counts.get('flagged_documents', 0)} | "
            f"Stations with failures: {counts.get('stations_with_failures', 0)}"
        ),
        (
            f"Risk signals: strict={risk_payload.get('strict_station_count', 0) or 0} | "
            f"watchlist={risk_payload.get('watchlist_station_count', 0) or 0}"
        ),
    ]
    docs = run_payload.get("new_documents") or []
    if docs:
        latest_count = sum(1 for doc in docs if doc.get("is_latest_for_station"))
        lines.extend(["", f"Top new docs ({latest_count} latest-for-station):"])
        for doc in docs[:max_new_docs]:
            station = str(doc.get("station_name", "Unknown station"))
            title = str(doc.get("title", "Untitled")).replace("\n", " ").strip()
            flags = str(doc.get("flags", "")).strip()
            doc_url = str(doc.get("document_url", "")).strip()
            detail = f"{station}: {title}"
            if doc.get("document_year"):
                detail += f" ({doc['document_year']})"
            if doc.get("is_latest_for_station"):
                detail += " [latest]"
            if doc_url:
                detail += f" (<{doc_url}|link>)"
            if flags:
                detail += f" [{flags}]"
            lines.append(f"- {detail}")
    if failures:
        lines.extend(["", "Top failed pages:"])
        for item in failures[:max_failures]:
            station = str(item.get("station_name") or item.get("station_id") or "Unknown station")
            page_url = str(item.get("page_url", "")).strip()
            error = str(item.get("error", "")).replace("\n", " ").strip()
            if len(error) > 180:
                error = error[:177] + "..."
            lines.append(f"- {station}: <{page_url}|page> - {error}" if page_url else f"- {station}: {error}")
    if strict_hits:
        lines.extend(["", "Strict risk highlights:"])
        for item in strict_hits[:max_strict]:
            lines.append(f"- [{item.get('pattern', '')}] {item.get('station_name', 'Unknown station')}: {item.get('title', 'Untitled')}")
    if watch_hits:
        lines.extend(["", "Watchlist highlights:"])
        for item in watch_hits[:max_watchlist]:
            lines.append(f"- [{item.get('pattern', '')}] {item.get('station_name', 'Unknown station')}: {item.get('title', 'Untitled')}")
    return "\n".join(lines)


def _post_slack(webhook: str, text: str) -> None:
    body = json.dumps({"text": text}).encode("utf-8")
    req = Request(webhook, data=body, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=20) as resp:
        resp.read()


def _read_json_file(path: Path) -> tuple[dict, str]:
    if not path.exists():
        return {}, f"missing: {path}"
    try:
        return json.loads(path.read_text(encoding="utf-8")), ""
    except Exception as exc:
        return {}, f"could not read {path}: {exc}"


def _parse_launchd_print(text: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    patterns = {
        "state": r"^\s*state = (.+)$",
        "path": r"^\s*path = (.+)$",
        "runs": r"^\s*runs = (.+)$",
        "last_exit_code": r"^\s*last exit code = (.+)$",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text, flags=re.MULTILINE)
        if match:
            parsed[key] = match.group(1).strip()
    archive_scope = re.search(r"^\s*AUDIT_WATCH_ARCHIVE_SCOPE => (.+)$", text, flags=re.MULTILINE)
    if archive_scope:
        parsed["archive_scope"] = archive_scope.group(1).strip()
    schedule = re.search(
        r'"Minute" =>\s*(\d+).*?"Hour" =>\s*(\d+)(?:.*?"Weekday" =>\s*(\d+))?',
        text,
        flags=re.DOTALL,
    )
    if schedule:
        minute = int(schedule.group(1))
        hour = int(schedule.group(2))
        weekday = schedule.group(3)
        parsed["schedule"] = f"weekday {weekday} at {hour:02d}:{minute:02d}" if weekday else f"daily at {hour:02d}:{minute:02d}"
    return parsed


def _launchd_status(label: str) -> tuple[dict[str, str], str]:
    if not label.strip():
        return {}, ""
    try:
        result = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{label}"],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception as exc:
        return {}, str(exc)
    if result.returncode != 0:
        return {}, (result.stderr or result.stdout or f"launchctl exited {result.returncode}").strip()
    return _parse_launchd_print(result.stdout), ""


def _cmd_status(args: argparse.Namespace) -> int:
    health, health_error = _read_json_file(Path(args.health))
    failures_payload, failures_error = _read_json_file(Path(args.failures))

    print("Audit Watch status")
    if health_error:
        print(f"Health: {health_error}")
    else:
        counts = health.get("counts", {})
        print(f"Last run: {health.get('last_run_date', 'unknown')}")
        print(f"Started: {health.get('started_at', 'unknown')}")
        print(f"Finished: {health.get('finished_at', 'unknown')}")
        print(f"Scan status: {health.get('scan_status', 'unknown')}")
        print(f"Risk status: {health.get('risk_status', 'unknown')}")
        if health.get("risk_error"):
            print(f"Risk error: {health.get('risk_error')}")
        print(f"Slack status: {health.get('slack_status', 'unknown')}")
        if health.get("slack_error"):
            print(f"Slack error: {health.get('slack_error')}")
        print(
            "Counts: "
            f"new_docs={counts.get('new_documents', 0)} | "
            f"flagged={counts.get('flagged_documents', 0)} | "
            f"failures={counts.get('stations_with_failures', 0)} | "
            f"strict_risk={counts.get('strict_risk_stations', 0)} | "
            f"watchlist_risk={counts.get('watchlist_risk_stations', 0)}"
        )

    if failures_error:
        print(f"Failures: {failures_error}")
    else:
        failures = failures_payload.get("failures") or []
        skipped = failures_payload.get("skipped_by_reason") or {}
        print(f"Failure rows: {failures_payload.get('failure_count', len(failures))}")
        summary = health.get("failure_summary") or {}
        if failures and (
            "transient_failure_count" not in summary
            or summary.get("failure_count", len(failures)) != len(failures)
        ):
            summary = summarize_failures(failures)
        if summary:
            print(
                "Failure types: "
                + ", ".join(f"{key}={value}" for key, value in sorted((summary.get("by_type") or {}).items()))
            )
            print(
                "Failure persistence: "
                f"transient={summary.get('transient_failure_count', 0)} | "
                f"persistent={summary.get('persistent_failure_count', 0)}"
            )
        if skipped:
            skipped_text = ", ".join(f"{reason}={count}" for reason, count in sorted(skipped.items()))
            print(f"Skipped rows: {failures_payload.get('skipped_count', 0)} ({skipped_text})")
        if failures:
            print(f"Top failures:")
            for item in failures[: max(0, args.limit_failures)]:
                station = item.get("station_name") or item.get("station_id") or "Unknown station"
                page_url = item.get("page_url") or ""
                error = str(item.get("error") or "").replace("\n", " ").strip()
                if len(error) > 160:
                    error = error[:157] + "..."
                suffix = f" - {page_url}" if page_url else ""
                print(f"- {station}{suffix}: {error}")

    if not args.no_launchd:
        launchd, launchd_error = _launchd_status(args.launchd_label)
        if launchd_error:
            print(f"LaunchAgent: {args.launchd_label} ({launchd_error})")
        else:
            print(f"LaunchAgent: {args.launchd_label}")
            print(f"LaunchAgent state: {launchd.get('state', 'unknown')}")
            print(f"LaunchAgent runs: {launchd.get('runs', 'unknown')}")
            print(f"LaunchAgent last exit: {launchd.get('last_exit_code', 'unknown')}")
            if launchd.get("schedule"):
                print(f"LaunchAgent schedule: {launchd['schedule']}")
            if launchd.get("archive_scope"):
                print(f"LaunchAgent archive scope: {launchd['archive_scope']}")
    return 0


def _recent_archive_rows(
    *,
    archive_root: Path,
    station_names: dict[str, str],
    since_year: int,
    after_archive_date: str,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    if not archive_root.exists():
        return rows
    for path in archive_root.glob("*/*/*"):
        if not path.is_file() or path.name.startswith("."):
            continue
        try:
            station_id = path.parts[-3]
            archive_date = path.parts[-2]
        except IndexError:
            continue
        if after_archive_date and archive_date <= after_archive_date:
            continue
        year = extract_document_year(path.name, path.name)
        if year is None or year < since_year:
            continue
        rows.append(
            {
                "source": "archive",
                "archive_date": archive_date,
                "station_id": station_id,
                "station_name": station_names.get(station_id, ""),
                "document_year": year,
                "title": path.stem,
                "document_url": "",
                "path": str(path),
            }
        )
    return rows


def _recent_run_json_rows(*, run_json: Path, since_year: int) -> list[dict[str, object]]:
    payload, error = _read_json_file(run_json)
    if error:
        raise ValidationError(error)
    rows: list[dict[str, object]] = []
    for doc in payload.get("new_documents") or []:
        title = str(doc.get("title", ""))
        url = str(doc.get("document_url", ""))
        year = extract_document_year(title, url)
        if year is None or year < since_year:
            continue
        rows.append(
            {
                "source": str(run_json),
                "archive_date": "",
                "station_id": str(doc.get("station_id", "")),
                "station_name": str(doc.get("station_name", "")),
                "document_year": year,
                "title": title,
                "document_url": url,
                "path": str(doc.get("downloaded_path", "")),
            }
        )
    return rows


def _dedupe_recent_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, object]] = []
    for row in sorted(
        rows,
        key=lambda item: (
            str(item.get("archive_date", "")),
            str(item.get("station_id", "")),
            str(item.get("title", "")),
            str(item.get("path", "")),
            str(item.get("document_url", "")),
        ),
    ):
        key = (
            str(row.get("station_id", "")),
            str(row.get("document_url", "")) or Path(str(row.get("path", ""))).name,
            str(row.get("document_year", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _cmd_recent_docs(args: argparse.Namespace) -> int:
    station_names = {}
    stations_path = Path(args.stations)
    if stations_path.exists():
        station_names = {station.station_id: station.station_name for station in load_stations(stations_path)}

    rows = _recent_archive_rows(
        archive_root=Path(args.archive_root),
        station_names=station_names,
        since_year=args.since_year,
        after_archive_date=args.after_archive_date,
    )
    for run_json in args.run_json:
        rows.extend(_recent_run_json_rows(run_json=Path(run_json), since_year=args.since_year))
    rows = _dedupe_recent_rows(rows)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "source",
        "archive_date",
        "station_id",
        "station_name",
        "document_year",
        "title",
        "document_url",
        "path",
    ]
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Recent document rows: {len(rows)}")
    print(f"Since year: {args.since_year}")
    if args.after_archive_date:
        print(f"After archive date: {args.after_archive_date}")
    print(f"Wrote recent documents CSV: {out_path}")
    return 0


def _cmd_run_and_notify(args: argparse.Namespace) -> int:
    started_at = utc_now_iso()
    scan_result = _run_daily_scan(args)
    run_payload = scan_result["payload"]
    failures_payload = scan_result["failures_payload"]
    run_date = str(run_payload.get("run_date", "")).strip()

    risk_enabled = not (args.discover_only or args.no_archive)
    if risk_enabled:
        risk_status, risk_payload, risk_error = _run_audit_chatbot(args, run_date)
    else:
        risk_status, risk_payload, risk_error = "not_attempted", {}, "archive disabled"

    counts = run_payload.get("counts", {})
    should_notify = args.notify_on_no_changes or any(
        [
            int(counts.get("new_documents") or 0) > 0,
            int(counts.get("stations_with_failures") or 0) > 0,
            int(risk_payload.get("strict_station_count", 0) or 0) > 0,
            int(risk_payload.get("watchlist_station_count", 0) or 0) > 0,
        ]
    )
    slack_status = "skipped"
    slack_error = ""
    if args.dry_run:
        slack_status = "dry_run"
    elif not scan_result["archive_enabled"]:
        slack_status = "skipped_validation"
    elif not args.slack_webhook.strip():
        slack_status = "not_configured"
    elif should_notify:
        try:
            _post_slack(
                args.slack_webhook.strip(),
                _slack_text(
                    run_payload=run_payload,
                    failures_payload=failures_payload,
                    risk_payload=risk_payload,
                    max_new_docs=args.slack_max_new_docs,
                    max_failures=args.slack_max_failures,
                    max_strict=args.slack_max_strict_risks,
                    max_watchlist=args.slack_max_watchlist_risks,
                ),
            )
            slack_status = "sent"
        except Exception as exc:
            slack_status = "failed"
            slack_error = str(exc)

    finished_at = utc_now_iso()
    health = build_health_payload(
        run_payload=run_payload,
        failures_payload=failures_payload,
        risk_payload=risk_payload,
        started_at=started_at,
        finished_at=finished_at,
        scan_status="completed",
        risk_status=risk_status,
        slack_status=slack_status,
        risk_error=risk_error,
        slack_error=slack_error,
    )
    write_json(Path(args.health_out), health)

    print(f"Stations total: {scan_result['stations_total']}")
    print(f"Stations scanned: {scan_result['stations_scanned']}")
    print(f"Stations skipped: {scan_result['stations_skipped']}")
    print(f"Discovered candidate documents: {scan_result['documents_discovered']}")
    print(f"New documents found: {counts.get('new_documents', 0)}")
    print(f"Archive scope: {scan_result['archive_scope']}")
    print(f"Archive candidates: {scan_result['documents_archive_candidates']}")
    print(f"Documents archived: {counts.get('documents_archived', 0)}")
    print(f"Archive enabled: {scan_result['archive_enabled']}")
    print(f"Workers: {scan_result['workers']}")
    print(f"Station retry attempts: {run_payload.get('retry_summary', {}).get('station_retry_attempts', 0)}")
    print(f"Fetch/archive failures: {failures_payload['failure_count']}")
    print(f"Risk status: {risk_status}")
    print(f"Slack status: {slack_status}")
    if slack_error:
        print(f"Slack error: {slack_error}")
    print(f"Wrote health: {args.health_out}")
    return 0


def _cmd_discover_pages(args: argparse.Namespace) -> int:
    stations_path = Path(args.stations)
    stations = load_stations(stations_path)
    targets = unresolved_stations(stations)[: args.limit]
    if not targets:
        print("No unresolved stations found.")
        return 0

    candidates: list[dict[str, str]] = []
    failures: list[dict[str, str]] = []
    top_by_station: dict[str, dict[str, str]] = {}

    for station in targets:
        try:
            found = discover_page_candidates(
                station,
                timeout_seconds=args.timeout_seconds,
                max_candidates=args.max_candidates,
            )
            candidates.extend(found)
            if found:
                top_by_station[station.station_id] = found[0]
        except Exception as exc:  # pragma: no cover
            failures.append(
                {
                    "station_id": station.station_id,
                    "station_name": station.station_name,
                    "error": str(exc),
                }
            )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    import csv

    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["station_id", "station_name", "candidate_url", "score"],
        )
        writer.writeheader()
        writer.writerows(candidates)

    write_json(
        Path(args.failures_out),
        {
            "considered": len(targets),
            "candidate_urls_found": len(candidates),
            "failure_count": len(failures),
            "failures": failures,
        },
    )

    applied = 0
    if args.apply and top_by_station:
        rows = []
        with stations_path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = list(reader.fieldnames or [])
            rows = [dict(r) for r in reader]

        for required in ("station_id", "station_name", "page_url", "notes", "enabled"):
            if required not in fieldnames:
                fieldnames.append(required)

        for row in rows:
            sid = (row.get("station_id") or "").strip()
            if sid not in top_by_station:
                continue
            if (row.get("page_url") or "").strip():
                continue
            top = top_by_station[sid]
            row["page_url"] = top["candidate_url"]
            note = (row.get("notes") or "").strip()
            auto_note = f"AUTO_DISCOVERY_CANDIDATE score={top['score']}"
            if auto_note not in note:
                row["notes"] = (note + " | " + auto_note).strip(" |")
            row["enabled"] = "0"
            applied += 1

        with stations_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    print(f"Unresolved stations considered: {len(targets)}")
    print(f"Candidate URLs found: {len(candidates)}")
    print(f"Discovery failures: {len(failures)}")
    print(f"Wrote candidates CSV: {out_path}")
    print(f"Wrote discovery failures JSON: {args.failures_out}")
    if args.apply:
        print(f"Applied top candidates to stations.csv: {applied}")
    return 0


def _disabled_review_row(station: StationRecord, docs: list[AuditDocument]) -> dict[str, object]:
    return {
        "station_id": station.station_id,
        "station_name": station.station_name,
        "page_url": station.page_url,
        "notes": station.notes,
        "document_count": len(docs),
        "documents": [
            {
                "title": doc.title,
                "document_url": doc.document_url,
                "file_ext": doc.file_ext,
                "confidence": doc.confidence,
            }
            for doc in docs
        ],
    }


def _cmd_review_disabled(args: argparse.Namespace) -> int:
    stations = load_stations(Path(args.stations))
    disabled_with_urls = [s for s in stations if not s.enabled and s.page_url.strip()]
    targets = disabled_with_urls[args.offset : args.offset + args.limit]
    if not targets:
        print("No disabled stations with page URLs found for that range.")
        return 0

    review_rows: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    workers = max(1, args.workers)

    def scan(station: StationRecord) -> tuple[StationRecord, list[AuditDocument], Exception | None]:
        try:
            return station, discover_station_docs(station, timeout_seconds=args.timeout_seconds), None
        except Exception as exc:  # pragma: no cover
            return station, [], exc

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(scan, station) for station in targets]
        for future in as_completed(futures):
            station, docs, exc = future.result()
            if exc is not None:
                failures.append(
                    {
                        "station_id": station.station_id,
                        "station_name": station.station_name,
                        "page_url": station.page_url,
                        "error": str(exc),
                        "failure_type": classify_failure_type(str(exc)),
                        "transient": is_transient_failure(str(exc)),
                    }
                )
                docs = []
            review_rows.append(_disabled_review_row(station, docs))

    review_rows.sort(key=lambda row: str(row["station_id"]))
    positive_rows = [row for row in review_rows if int(row["document_count"]) > 0]

    payload = {
        "stations_total": len(stations),
        "disabled_with_urls": len(disabled_with_urls),
        "offset": args.offset,
        "limit": args.limit,
        "stations_scanned": len(targets),
        "stations_with_candidate_documents": len(positive_rows),
        "candidate_documents": sum(int(row["document_count"]) for row in review_rows),
        "failure_count": len(failures),
        "review": review_rows,
    }
    write_json(Path(args.out), payload)
    write_json(
        Path(args.failures_out),
        {
            "stations_scanned": len(targets),
            "failure_count": len(failures),
            "failures": sorted(failures, key=lambda row: str(row["station_id"])),
        },
    )

    csv_out = Path(args.csv_out)
    csv_out.parent.mkdir(parents=True, exist_ok=True)
    with csv_out.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "station_id",
                "station_name",
                "page_url",
                "document_count",
                "first_title",
                "first_document_url",
                "notes",
            ],
        )
        writer.writeheader()
        for row in review_rows:
            docs = row["documents"]
            first_doc = docs[0] if isinstance(docs, list) and docs else {}
            writer.writerow(
                {
                    "station_id": row["station_id"],
                    "station_name": row["station_name"],
                    "page_url": row["page_url"],
                    "document_count": row["document_count"],
                    "first_title": first_doc.get("title", "") if isinstance(first_doc, dict) else "",
                    "first_document_url": first_doc.get("document_url", "") if isinstance(first_doc, dict) else "",
                    "notes": row["notes"],
                }
            )

    print(f"Disabled stations with page URLs: {len(disabled_with_urls)}")
    print(f"Stations scanned: {len(targets)}")
    print(f"Stations with candidate documents: {len(positive_rows)}")
    print(f"Candidate documents: {payload['candidate_documents']}")
    print(f"Failures: {len(failures)}")
    print(f"Wrote disabled review JSON: {args.out}")
    print(f"Wrote disabled review CSV: {args.csv_out}")
    print(f"Wrote disabled review failures JSON: {args.failures_out}")
    return 0


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    try:
        if args.command == "daily-run":
            return _cmd_daily_run(args)
        if args.command == "discover-pages":
            return _cmd_discover_pages(args)
        if args.command == "review-disabled":
            return _cmd_review_disabled(args)
        if args.command == "validate-stations":
            return _cmd_validate_stations(args)
        if args.command == "run-and-notify":
            return _cmd_run_and_notify(args)
        if args.command == "status":
            return _cmd_status(args)
        if args.command == "recent-docs":
            return _cmd_recent_docs(args)
        parser.error(f"Unsupported command: {args.command}")
        return 2
    except ValidationError as exc:
        print(f"Validation error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
