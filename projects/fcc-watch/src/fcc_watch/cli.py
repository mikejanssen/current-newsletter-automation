from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .config import (
    DEFAULT_DIGEST_MAX_CATCHUP_DAYS,
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_PREFLIGHT_RETRIES,
    DEFAULT_PREFLIGHT_TIMEOUT_SECONDS,
)
from .cpb import load_cpb_grantees, summarize_cpb
from .daily_digest import fetch_daily_digest
from .ecfs import fetch_ecfs_filings
from .meeting_watch import fetch_meeting_items
from .preflight import default_probe_urls, run_preflight
from .public_files_rss import fetch_public_files_rss
from .slack import post_to_slack, SlackMessage, SlackPostError
from .state import load_state, prune_seen_items, save_state


@dataclass(frozen=True)
class DigestAlert:
    title: str
    link: str
    categories: list[str]
    keywords: list[str]


@dataclass(frozen=True)
class PublicFilesAlert:
    title: str
    link: str
    document_link: str
    published_at: str
    source_call_sign: str
    call_signs: list[str]
    keywords: list[str]


@dataclass(frozen=True)
class EcfsAlert:
    title: str
    link: str
    date_received: str
    proceedings: list[str]
    filers: list[str]
    call_signs: list[str]
    keywords: list[str]


@dataclass(frozen=True)
class MeetingAlert:
    title: str
    link: str
    source_url: str
    date_hint: str
    keywords: list[str]


def _ensure_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _format_digest_item(item: DigestAlert) -> str:
    tags = []
    if item.categories:
        tags.append(", ".join(item.categories))
    if item.keywords:
        tags.append("keywords: " + ", ".join(item.keywords))
    tag_str = f" ({'; '.join(tags)})" if tags else ""
    return f"- {item.title}{tag_str} {item.link}"


def _build_extra_keywords(cpb_path: Path) -> list[str]:
    cpb_data = load_cpb_grantees(cpb_path)
    keywords: list[str] = []
    for network in cpb_data.networks:
        if network.name:
            keywords.append(network.name)
        if network.licensee:
            keywords.append(network.licensee)
    return keywords


def _format_public_files_item(item: PublicFilesAlert) -> str:
    tags = []
    if item.published_at:
        tags.append(item.published_at)
    if item.source_call_sign:
        tags.append("feed: " + item.source_call_sign)
    if item.call_signs:
        tags.append("call signs: " + ", ".join(item.call_signs))
    if item.keywords:
        tags.append("keywords: " + ", ".join(item.keywords))
    tag_str = f" ({'; '.join(tags)})" if tags else ""
    link = f" {item.link}" if item.link else ""
    return f"- {item.title}{tag_str}{link}"


def _format_ecfs_item(item: EcfsAlert) -> str:
    tags = []
    if item.date_received:
        tags.append(item.date_received)
    if item.proceedings:
        tags.append("dockets: " + ", ".join(item.proceedings))
    if item.filers:
        tags.append("filers: " + ", ".join(item.filers))
    if item.call_signs:
        tags.append("call signs: " + ", ".join(item.call_signs))
    if item.keywords:
        tags.append("keywords: " + ", ".join(item.keywords))
    tag_str = f" ({'; '.join(tags)})" if tags else ""
    link = f" {item.link}" if item.link else ""
    return f"- {item.title}{tag_str}{link}"


def _format_meeting_item(item: MeetingAlert) -> str:
    tags = []
    if item.date_hint:
        tags.append(item.date_hint)
    if item.source_url:
        tags.append("source: " + item.source_url)
    if item.keywords:
        tags.append("keywords: " + ", ".join(item.keywords))
    tag_str = f" ({'; '.join(tags)})" if tags else ""
    link = f" {item.link}" if item.link else ""
    return f"- {item.title}{tag_str}{link}"


def _target_dates(
    *,
    lookback_days: int,
    last_successful_digest_date: date | None,
    max_catchup_days: int,
) -> list[date]:
    today = date.today()
    default_start = today - timedelta(days=max(lookback_days - 1, 0))
    earliest_allowed = today - timedelta(days=max(max_catchup_days - 1, 0))

    start = default_start
    if last_successful_digest_date is not None:
        catchup_start = last_successful_digest_date + timedelta(days=1)
        if catchup_start < earliest_allowed:
            catchup_start = earliest_allowed
        if catchup_start < start:
            start = catchup_start

    days = (today - start).days
    return [today - timedelta(days=offset) for offset in range(days + 1)]


def build_digest_alerts(
    *,
    lookback_days: int,
    extra_keywords: list[str],
    last_successful_digest_date: date | None,
    max_catchup_days: int,
) -> tuple[list[DigestAlert], list[dict[str, str]], list[str], list[str]]:
    alerts: list[DigestAlert] = []
    failures: list[dict[str, str]] = []
    target_dates = _target_dates(
        lookback_days=lookback_days,
        last_successful_digest_date=last_successful_digest_date,
        max_catchup_days=max_catchup_days,
    )
    successful_dates: list[str] = []
    for target in target_dates:
        try:
            items = fetch_daily_digest(target, extra_keywords=extra_keywords)
        except Exception as err:
            failures.append({"date": target.isoformat(), "error": str(err)})
            continue
        successful_dates.append(target.isoformat())
        for item in items:
            alerts.append(
                DigestAlert(
                    title=item.title,
                    link=item.link,
                    categories=item.categories,
                    keywords=item.matched_keywords,
                )
            )
    return alerts, failures, [d.isoformat() for d in target_dates], successful_dates


def build_public_files_alerts(
    *,
    cpb_call_signs: set[str],
    extra_keywords: list[str],
    lookback_days: int,
) -> tuple[list[PublicFilesAlert], list[dict[str, str]]]:
    try:
        items = fetch_public_files_rss(cpb_call_signs=cpb_call_signs, extra_keywords=extra_keywords)
    except Exception as err:
        return [], [{"source": "public_files_rss", "error": str(err)}]

    cutoff = datetime.combine(date.today(), datetime.min.time()) - timedelta(days=max(lookback_days - 1, 0))
    alerts: list[PublicFilesAlert] = []
    for item in items:
        published_raw = item.published_at.replace("Z", "+00:00") if item.published_at else ""
        if published_raw:
            try:
                published = datetime.fromisoformat(published_raw).replace(tzinfo=None)
            except ValueError:
                published = None
            if published and published < cutoff:
                continue
        alerts.append(
            PublicFilesAlert(
                title=item.title,
                link=item.link,
                document_link=item.document_link,
                published_at=item.published_at,
                source_call_sign=item.source_call_sign,
                call_signs=item.matched_call_signs,
                keywords=item.matched_keywords,
            )
        )
    return alerts, []


def build_ecfs_alerts(
    *,
    cpb_call_signs: set[str],
    extra_keywords: list[str],
    lookback_days: int,
) -> tuple[list[EcfsAlert], list[dict[str, str]]]:
    try:
        items = fetch_ecfs_filings(
            cpb_call_signs=cpb_call_signs,
            extra_keywords=extra_keywords,
            lookback_days=lookback_days,
        )
    except Exception as err:
        return [], [{"source": "ecfs", "error": str(err)}]

    alerts: list[EcfsAlert] = []
    for item in items:
        alerts.append(
            EcfsAlert(
                title=item.title,
                link=item.link,
                date_received=item.date_received,
                proceedings=item.proceedings,
                filers=item.filers,
                call_signs=item.matched_call_signs,
                keywords=item.matched_keywords,
            )
        )
    return alerts, []


def build_meeting_alerts(
    *,
    extra_keywords: list[str],
    lookback_days: int,
) -> tuple[list[MeetingAlert], list[dict[str, str]]]:
    try:
        items = fetch_meeting_items(extra_keywords=extra_keywords, lookback_days=lookback_days)
    except Exception as err:
        return [], [{"source": "meeting_watch", "error": str(err)}]

    alerts: list[MeetingAlert] = []
    for item in items:
        alerts.append(
            MeetingAlert(
                title=item.title,
                link=item.link,
                source_url=item.source_url,
                date_hint=item.date_hint,
                keywords=item.matched_keywords,
            )
        )
    return alerts, []


def run_daily(args: argparse.Namespace) -> dict:
    cpb_path = Path(args.cpb)
    state_path = Path(args.state)
    out_path = Path(args.out)

    state = load_state(state_path)

    cpb_data = load_cpb_grantees(cpb_path)
    extra_keywords = _build_extra_keywords(cpb_path)
    skip_digest = args.skip_digest or os.environ.get("FCC_WATCH_SKIP_DIGEST", "").strip() == "1"
    if skip_digest:
        digest_items: list[DigestAlert] = []
        digest_failures = [{"source": "daily_digest", "error": "skipped by FCC_WATCH_SKIP_DIGEST"}]
        digest_target_dates: list[str] = []
        digest_successful_dates: list[str] = []
    else:
        digest_items, digest_failures, digest_target_dates, digest_successful_dates = build_digest_alerts(
            lookback_days=args.lookback_days,
            extra_keywords=extra_keywords,
            last_successful_digest_date=state.last_successful_digest_date,
            max_catchup_days=args.max_catchup_days,
        )
    public_file_items, public_file_failures = build_public_files_alerts(
        cpb_call_signs=cpb_data.call_signs,
        extra_keywords=extra_keywords,
        lookback_days=args.lookback_days,
    )
    ecfs_items, ecfs_failures = build_ecfs_alerts(
        cpb_call_signs=cpb_data.call_signs,
        extra_keywords=extra_keywords,
        lookback_days=args.lookback_days,
    )
    meeting_items, meeting_failures = build_meeting_alerts(
        extra_keywords=extra_keywords,
        lookback_days=args.lookback_days,
    )

    new_digest_items: list[DigestAlert] = []
    new_public_file_items: list[PublicFilesAlert] = []
    new_ecfs_items: list[EcfsAlert] = []
    new_meeting_items: list[MeetingAlert] = []
    new_seen_keys: list[str] = []
    existing_seen = set(state.seen_items)

    for item in digest_items:
        key = f"digest:{item.link}"
        if key in existing_seen:
            continue
        existing_seen.add(key)
        new_seen_keys.append(key)
        new_digest_items.append(item)

    for item in public_file_items:
        key = f"public_files:{item.document_link or item.title + '|' + item.published_at + '|' + item.source_call_sign}"
        if key in existing_seen:
            continue
        existing_seen.add(key)
        new_seen_keys.append(key)
        new_public_file_items.append(item)

    for item in ecfs_items:
        key = f"ecfs:{item.link or item.title + '|' + item.date_received + '|' + '|'.join(item.proceedings)}"
        if key in existing_seen:
            continue
        existing_seen.add(key)
        new_seen_keys.append(key)
        new_ecfs_items.append(item)

    for item in meeting_items:
        key = f"meeting:{item.link or item.title + '|' + item.source_url}"
        if key in existing_seen:
            continue
        existing_seen.add(key)
        new_seen_keys.append(key)
        new_meeting_items.append(item)

    payload = {
        "cpb": summarize_cpb(cpb_data),
        "digest_items": [asdict(item) for item in new_digest_items],
        "public_file_items": [asdict(item) for item in new_public_file_items],
        "ecfs_items": [asdict(item) for item in new_ecfs_items],
        "meeting_items": [asdict(item) for item in new_meeting_items],
        "digest_fetch_failures": digest_failures,
        "public_file_fetch_failures": public_file_failures,
        "ecfs_fetch_failures": ecfs_failures,
        "meeting_fetch_failures": meeting_failures,
        "digest_target_dates": digest_target_dates,
        "digest_successful_dates": digest_successful_dates,
        "slack_status": "not_attempted",
        "slack_error": "",
        "state_updated": False,
    }
    _ensure_dir(out_path)
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    if args.dry_run:
        payload["slack_status"] = "dry_run"
        out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return payload

    if not new_digest_items and not new_public_file_items and not new_ecfs_items and not new_meeting_items:
        if digest_successful_dates:
            state.last_successful_digest_date = date.fromisoformat(max(digest_successful_dates))
        state.last_run = datetime.now()
        _ensure_dir(state_path)
        save_state(state_path, state)
        payload["state_updated"] = True
        out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return payload

    lines = [f"FCC Watch — {date.today():%Y-%m-%d}"]
    if new_digest_items:
        lines.append("")
        lines.append("FCC Daily Digest")
        lines.extend(_format_digest_item(item) for item in new_digest_items)
    if new_public_file_items:
        lines.append("")
        lines.append("FCC Public File Feed")
        lines.extend(_format_public_files_item(item) for item in new_public_file_items)
    if new_ecfs_items:
        lines.append("")
        lines.append("FCC ECFS Filings")
        lines.extend(_format_ecfs_item(item) for item in new_ecfs_items)
    if new_meeting_items:
        lines.append("")
        lines.append("FCC Agenda/Circulation")
        lines.extend(_format_meeting_item(item) for item in new_meeting_items)

    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook:
        raise RuntimeError("SLACK_WEBHOOK_URL is not set")

    try:
        post_to_slack(webhook, SlackMessage(text="\n".join(lines)))
    except SlackPostError as err:
        payload["slack_status"] = "failed"
        payload["slack_error"] = str(err)
        out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        print(f"fcc-watch: Slack post failed: {err}")
        return payload

    state.seen_items = prune_seen_items([*state.seen_items, *new_seen_keys])
    if digest_successful_dates:
        state.last_successful_digest_date = date.fromisoformat(max(digest_successful_dates))
    state.last_run = datetime.now()
    _ensure_dir(state_path)
    save_state(state_path, state)
    payload["slack_status"] = "sent"
    payload["state_updated"] = True
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return payload


def run_preflight_command(args: argparse.Namespace) -> dict:
    target_date = date.fromisoformat(args.date) if args.date else date.today()
    urls = list(args.url) if args.url else default_probe_urls(target_date)

    payload = run_preflight(
        urls=urls,
        timeout_seconds=args.timeout_seconds,
        retries=args.retries,
    )

    out_path = Path(args.out)
    _ensure_dir(out_path)
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))

    if args.fail_on_unreachable and not payload["all_reachable"]:
        raise SystemExit(2)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="FCC Watch")
    sub = parser.add_subparsers(dest="command", required=True)

    daily = sub.add_parser("daily", help="Run daily scan")
    daily.add_argument("--cpb", default="../990s/cpb-grantees.csv")
    daily.add_argument("--state", default="output/state.json")
    daily.add_argument("--out", default="output/last-run.json")
    default_lookback = int(os.environ.get("FCC_WATCH_LOOKBACK_DAYS", DEFAULT_LOOKBACK_DAYS))
    daily.add_argument("--lookback-days", type=int, default=default_lookback)
    daily.add_argument(
        "--max-catchup-days",
        type=int,
        default=int(os.environ.get("FCC_WATCH_DIGEST_MAX_CATCHUP_DAYS", DEFAULT_DIGEST_MAX_CATCHUP_DAYS)),
    )
    daily.add_argument("--skip-digest", action="store_true", help="Skip FCC Daily Digest fetches for this run")
    daily.add_argument("--dry-run", action="store_true")
    daily.set_defaults(func=run_daily)

    preflight = sub.add_parser("preflight", help="Quick host/network reachability checks")
    preflight.add_argument("--out", default="output/preflight-last.json")
    preflight.add_argument("--date", default="", help="Optional YYYY-MM-DD for daily-digest probe URL")
    preflight.add_argument("--url", action="append", default=[], help="Override probe URL; repeatable")
    preflight.add_argument("--timeout-seconds", type=float, default=DEFAULT_PREFLIGHT_TIMEOUT_SECONDS)
    preflight.add_argument("--retries", type=int, default=DEFAULT_PREFLIGHT_RETRIES)
    preflight.add_argument("--fail-on-unreachable", action="store_true")
    preflight.set_defaults(func=run_preflight_command)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
