from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.request import Request, urlopen

from .core import (
    ValidationError,
    archive_document,
    build_payload,
    build_health_payload,
    discover_page_candidates,
    discover_station_docs,
    load_state,
    load_stations,
    unresolved_stations,
    save_state,
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

    return parser


def _run_daily_scan(args: argparse.Namespace) -> dict:
    started_at = utc_now_iso()
    stations = load_stations(Path(args.stations))
    seen_ids = load_state(Path(args.state))

    runnable_stations = [s for s in stations if s.enabled and s.page_url.strip()]
    if getattr(args, "max_stations", None) is not None:
        runnable_stations = runnable_stations[: args.max_stations]
    skipped_stations = [s for s in stations if not (s.enabled and s.page_url.strip())]

    discovered = []
    failures: list[dict[str, str]] = []

    for station in runnable_stations:
        try:
            station_docs = discover_station_docs(station, timeout_seconds=args.timeout_seconds)
            discovered.extend(station_docs)
        except Exception as exc:  # pragma: no cover
            failures.append(
                {
                    "station_id": station.station_id,
                    "station_name": station.station_name,
                    "page_url": station.page_url,
                    "error": str(exc),
                }
            )

    by_id = {d.doc_id: d for d in discovered}
    new_docs = [d for d in by_id.values() if d.doc_id not in seen_ids]
    new_docs = sorted(new_docs, key=lambda d: (d.station_name, d.title, d.document_url))

    archived_docs = []
    for doc in new_docs:
        try:
            archived = archive_document(
                doc,
                archive_root=Path(args.archive_root),
                timeout_seconds=args.timeout_seconds,
            )
            archived_docs.append(archived)
        except Exception as exc:  # pragma: no cover
            failures.append(
                {
                    "station_id": doc.station_id,
                    "station_name": doc.station_name,
                    "page_url": doc.page_url,
                    "error": f"download/archive failed for {doc.document_url}: {exc}",
                }
            )

    finished_at = utc_now_iso()
    payload = build_payload(
        archived_docs,
        failures,
        started_at=started_at,
        finished_at=finished_at,
        stations_total=len(stations),
        stations_scanned=len(runnable_stations),
        stations_skipped=len(skipped_stations),
        documents_discovered=len(discovered),
    )
    write_json(Path(args.out), payload)
    write_brief(Path(args.brief), payload)
    failures_payload = {
        "failures": failures,
        "failure_count": len(failures),
        "skipped_count": len(skipped_stations),
        "skipped": [
            {
                "station_id": s.station_id,
                "station_name": s.station_name,
                "page_url": s.page_url,
                "reason": "unresolved page_url or disabled entry",
            }
            for s in skipped_stations
        ],
    }
    write_json(Path(args.failures_out), failures_payload)

    if not args.dry_run:
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
        "stations_skipped": len(skipped_stations),
        "documents_discovered": len(discovered),
        "archived_docs": archived_docs,
    }


def _cmd_daily_run(args: argparse.Namespace) -> int:
    result = _run_daily_scan(args)
    payload = result["payload"]
    failures_payload = result["failures_payload"]

    print(f"Stations total: {result['stations_total']}")
    print(f"Stations scanned: {result['stations_scanned']}")
    print(f"Stations skipped: {result['stations_skipped']}")
    print(f"Discovered candidate documents: {result['documents_discovered']}")
    print(f"New documents archived: {payload['counts']['new_documents']}")
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
        lines.extend(["", "Top new docs:"])
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


def _cmd_run_and_notify(args: argparse.Namespace) -> int:
    started_at = utc_now_iso()
    scan_result = _run_daily_scan(args)
    run_payload = scan_result["payload"]
    failures_payload = scan_result["failures_payload"]
    run_date = str(run_payload.get("run_date", "")).strip()

    risk_status, risk_payload, risk_error = _run_audit_chatbot(args, run_date)

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
    print(f"New documents archived: {counts.get('new_documents', 0)}")
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


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    try:
        if args.command == "daily-run":
            return _cmd_daily_run(args)
        if args.command == "discover-pages":
            return _cmd_discover_pages(args)
        if args.command == "validate-stations":
            return _cmd_validate_stations(args)
        if args.command == "run-and-notify":
            return _cmd_run_and_notify(args)
        parser.error(f"Unsupported command: {args.command}")
        return 2
    except ValidationError as exc:
        print(f"Validation error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
