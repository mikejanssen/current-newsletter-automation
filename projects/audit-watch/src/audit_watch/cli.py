from __future__ import annotations

import argparse
from pathlib import Path

from .core import (
    ValidationError,
    archive_document,
    build_payload,
    discover_page_candidates,
    discover_station_docs,
    load_state,
    load_stations,
    unresolved_stations,
    save_state,
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

    return parser


def _cmd_daily_run(args: argparse.Namespace) -> int:
    stations = load_stations(Path(args.stations))
    seen_ids = load_state(Path(args.state))

    runnable_stations = [s for s in stations if s.enabled and s.page_url.strip()]
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

    payload = build_payload(archived_docs, failures)
    write_json(Path(args.out), payload)
    write_brief(Path(args.brief), payload)
    write_json(
        Path(args.failures_out),
        {
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
        },
    )

    if not args.dry_run:
        new_seen = set(seen_ids)
        for doc in new_docs:
            new_seen.add(doc.doc_id)
        save_state(Path(args.state), new_seen)

    print(f"Stations total: {len(stations)}")
    print(f"Stations scanned: {len(runnable_stations)}")
    print(f"Stations skipped: {len(skipped_stations)}")
    print(f"Discovered candidate documents: {len(discovered)}")
    print(f"New documents archived: {len(archived_docs)}")
    print(f"Fetch/archive failures: {len(failures)}")
    print(f"Wrote run JSON: {args.out}")
    print(f"Wrote briefing: {args.brief}")
    print(f"Wrote failures report: {args.failures_out}")
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
        parser.error(f"Unsupported command: {args.command}")
        return 2
    except ValidationError as exc:
        print(f"Validation error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
