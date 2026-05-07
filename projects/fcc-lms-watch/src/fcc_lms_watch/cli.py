from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .config import (
    APPLICATION_FORM,
    ASSIGNMENT_FORM,
    KEYWORDS_ASSIGNMENT_TRANSFER,
    DEFAULT_LOOKBACK_DAYS,
    KEYWORDS_CP,
    KEYWORDS_MINOR_MOD,
    KEYWORDS_STA,
)
from .cpb import load_cpb_grantees, normalize_call_sign, normalize_org_name
from .lms_client import LmsClient, RequestDiagnostic
from .slack import SlackMessage, SlackPostError, post_to_slack
from .state import load_state, prune_seen_items, save_state


@dataclass(frozen=True)
class AlertItem:
    category: str
    call_sign: str | None
    facility_id: str | None
    service: str | None
    file_number: str | None
    application_id: str | None
    detail_url: str | None
    pdf_url: str | None
    summary: str
    source: str


def _ensure_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _get_field(row: dict[str, str], candidates: list[str]) -> str | None:
    lowered = {k.lower(): k for k in row.keys()}
    for candidate in candidates:
        key = lowered.get(candidate.lower())
        if key:
            value = row.get(key)
            if value:
                return value.strip()
    return None


def _find_call_sign(row: dict[str, str]) -> str | None:
    explicit = _get_field(
        row,
        [
            "Call Sign",
            "CallSign",
            "Callsign",
            "Call Letters",
            "Station",
        ],
    )
    if explicit:
        return explicit.strip().upper()

    for key, value in row.items():
        if key is None or value is None:
            continue
        if "call" in key.lower() and "sign" in key.lower():
            value = value.strip()
            if value:
                return value.upper()

    # Fallback: scan all text cells for broadcast call-sign-like tokens.
    pattern = re.compile(r"\b([WK][A-Z0-9]{2,7}(?:-FM|-AM|-TV)?)\b")
    for value in row.values():
        if not value:
            continue
        for token in pattern.findall(value.upper()):
            return token
    return None


def _find_file_number(row: dict[str, str]) -> str | None:
    return _get_field(
        row,
        [
            "File Number",
            "File No",
            "File #",
            "FileNum",
            "FileNum/",
            "Lead File Number",
            "Member File Number",
        ],
    )


def _find_application_id(row: dict[str, str]) -> str | None:
    return _get_field(row, ["Application ID", "App ID", "Application Id", "AppId"])


def _find_detail_url(row: dict[str, str]) -> str | None:
    return _get_field(row, ["Detail URL"])


def _find_pdf_url(row: dict[str, str]) -> str | None:
    return _get_field(row, ["PDF URL"])


def _facility_detail_url(facility_id: str | None) -> str | None:
    if not facility_id:
        return None
    return f"https://enterpriseefiling.fcc.gov/dataentry/public/tv/publicFacilityDetails.html?facilityId={facility_id}"


def _stable_detail_url(row: dict[str, str]) -> str | None:
    detail_url = _find_detail_url(row)
    if detail_url and "publicNoticeSearchResult.html" not in detail_url:
        return detail_url
    return _facility_detail_url(_find_facility_id(row))


def _find_facility_id(row: dict[str, str]) -> str | None:
    value = _get_field(row, ["Facility ID", "FacilityId", "Facility"])
    if not value:
        return None
    digits = re.sub(r"[^0-9]", "", value)
    return digits or None


def _find_service(row: dict[str, str]) -> str | None:
    return _get_field(row, ["Service", "Application Service"])


def _normalize_results_row(row: dict[str, str], source: str) -> dict[str, str]:
    normalized = dict(row)
    if source == "Application Search":
        file_number = _get_field(
            row,
            ["File Number", "File No", "File #", "FileNum", "FileNum/", "Lead File Number", "Member File Number"],
        )
        if file_number and "File Number" not in normalized:
            normalized["File Number"] = file_number
        app_type = _get_field(row, ["Application Type", "Type", "Purpose"])
        if app_type and "Application Type" not in normalized:
            normalized["Application Type"] = app_type
    return normalized


def _row_text(row: dict[str, str]) -> str:
    return " ".join(v for v in row.values() if v).lower()


def _matches_keywords(text: str, keywords: list[str]) -> bool:
    return any(keyword.lower() in text for keyword in keywords)


def _candidate_org_names(row: dict[str, str]) -> list[str]:
    candidates: list[str] = []
    explicit_keys = [
        "Assignor/Transferor",
        "Assignee/Transferee",
        "Applicant Name",
        "Licensee Name",
        "Entity Name",
        "Grantee Name",
    ]
    for key in explicit_keys:
        value = _get_field(row, [key])
        if value:
            candidates.append(value)

    for key, value in row.items():
        if not key or not value:
            continue
        lowered = key.lower()
        if any(token in lowered for token in ["assignor", "assignee", "applicant", "licensee", "grantee"]):
            candidates.append(value.strip())

    deduped: list[str] = []
    seen: set[str] = set()
    for value in candidates:
        marker = value.lower()
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(value)
    return deduped


def _matches_cpb(row: dict[str, str], cpb_data) -> tuple[bool, str]:
    call_sign = _find_call_sign(row)
    if call_sign:
        normalized_call_sign = normalize_call_sign(call_sign)
        if normalized_call_sign in cpb_data.call_signs:
            return True, "call_sign"

    facility_id = _find_facility_id(row)
    if facility_id and facility_id in cpb_data.facility_ids:
        return True, "facility_id"

    for name in _candidate_org_names(row):
        normalized_name = normalize_org_name(name)
        if normalized_name and normalized_name in cpb_data.org_names:
            return True, "org_name"

    return False, "none"


def _build_alerts(rows: list[dict[str, str]], source: str, categories: list[str]) -> list[AlertItem]:
    alerts: list[AlertItem] = []
    for row in rows:
        call_sign = _find_call_sign(row)
        facility_id = _find_facility_id(row)
        service = _find_service(row)
        file_number = _find_file_number(row)
        application_id = _find_application_id(row)
        detail_url = _find_detail_url(row)
        pdf_url = _find_pdf_url(row)
        summary = _get_field(row, ["Purpose", "Application Type", "Type", "Description"]) or "LMS filing"
        for category in categories:
            alerts.append(
                AlertItem(
                    category=category,
                    call_sign=call_sign,
                    facility_id=facility_id,
                    service=service,
                    file_number=file_number,
                    application_id=application_id,
                    detail_url=detail_url,
                    pdf_url=pdf_url,
                    summary=summary,
                    source=source,
                )
            )
    return alerts


def _build_pn_alerts(rows: list[dict[str, str]], source: str, category: str) -> list[AlertItem]:
    alerts: list[AlertItem] = []
    for row in rows:
        alerts.append(
            AlertItem(
                category=category,
                call_sign=_find_call_sign(row),
                facility_id=_find_facility_id(row),
                service=_find_service(row),
                file_number=_find_file_number(row),
                application_id=_find_application_id(row),
                detail_url=_stable_detail_url(row),
                pdf_url=_find_pdf_url(row),
                summary=_get_field(row, ["Description", "Status", "Purpose", "Application Type"]) or "Public notice filing",
                source=source,
            )
        )
    return alerts


def _alert_key(alert: AlertItem) -> str:
    if "PN Search" in alert.source:
        return ":".join(
            [
                alert.source,
                alert.file_number or "",
                alert.application_id or "",
                alert.facility_id or "",
                alert.category,
            ]
        )
    if alert.file_number or alert.application_id:
        return f"{alert.source}:{alert.file_number}:{alert.application_id}:{alert.category}"
    fallback = "|".join(
        [
            alert.call_sign or "",
            alert.facility_id or "",
            alert.summary or "",
            alert.service or "",
        ]
    )
    return f"{alert.source}:{alert.category}:{fallback}"


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Invalid date format: {value}")


def _diagnostics_payload(items: list[RequestDiagnostic]) -> list[dict[str, object]]:
    return [asdict(item) for item in items]


def _alert_lines(title: str, warnings: list[str], alerts: list[AlertItem]) -> list[str]:
    lines = [title]
    for warning in warnings:
        lines.append(f"WARNING: {warning}")
    for alert in alerts:
        parts = [alert.category, alert.call_sign or "", alert.service or "", alert.summary]
        file_info = ""
        if alert.facility_id:
            file_info = f"facility {alert.facility_id}"
        if alert.file_number:
            file_info = f"{file_info} file {alert.file_number}".strip()
        if alert.application_id:
            file_info = f"{file_info} app {alert.application_id}".strip()
        line = " - ".join(p for p in parts if p)
        if file_info:
            line = f"{line} ({file_info})"
        if alert.detail_url:
            line = f"{line} {alert.detail_url}"
        elif alert.pdf_url:
            line = f"{line} {alert.pdf_url}"
        lines.append(line)
    return lines


def run_probe(args: argparse.Namespace) -> dict:
    out_path = Path(args.out)
    target_url = args.url or ASSIGNMENT_FORM.url

    client = LmsClient()
    python_result: dict[str, object]
    try:
        body = client._request(target_url)
        python_result = {
            "ok": True,
            "status": "success",
            "body_bytes": len(body),
            "diagnostics": _diagnostics_payload(client.consume_request_log()),
        }
    except Exception as err:
        python_result = {
            "ok": False,
            "status": f"error:{type(err).__name__}",
            "error": str(err),
            "diagnostics": _diagnostics_payload(client.consume_request_log()),
        }

    curl_bin = shutil.which("curl")
    curl_result: dict[str, object]
    if not curl_bin:
        curl_result = {"ok": False, "status": "missing_curl"}
    else:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            headers_path = tmp / "headers.txt"
            body_path = tmp / "body.bin"
            cookies_path = tmp / "cookies.txt"
            cmd = [
                curl_bin,
                "-sS",
                "-L",
                "-D",
                str(headers_path),
                "-o",
                str(body_path),
                "-c",
                str(cookies_path),
                "-A",
                os.environ.get("FCC_LMS_CURL_USER_AGENT", ""),
                target_url,
            ]
            if not cmd[-2]:
                cmd[-2] = (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/135.0.0.0 Safari/537.36"
                )
            proc = subprocess.run(cmd, capture_output=True, text=True)
            curl_result = {
                "ok": proc.returncode == 0,
                "status": "success" if proc.returncode == 0 else f"exit:{proc.returncode}",
                "stderr": proc.stderr.strip(),
                "headers": headers_path.read_text(encoding="utf-8", errors="ignore") if headers_path.exists() else "",
                "body_bytes": body_path.stat().st_size if body_path.exists() else 0,
                "cookies": cookies_path.read_text(encoding="utf-8", errors="ignore") if cookies_path.exists() else "",
            }

    payload = {
        "checked_at": datetime.now().isoformat(),
        "url": target_url,
        "python_request": python_result,
        "curl_request": curl_result,
    }
    _ensure_dir(out_path)
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return payload


def run_daily(args: argparse.Namespace) -> dict:
    cpb_path = Path(args.cpb)
    cpb_aliases_path = Path(args.cpb_aliases) if args.cpb_aliases else None
    state_path = Path(args.state)
    out_path = Path(args.out)

    state = load_state(state_path)
    cpb_data = load_cpb_grantees(cpb_path, aliases_path=cpb_aliases_path)

    if args.from_date or args.to_date:
        from_date = _parse_date(args.from_date) or date.today()
        to_date = _parse_date(args.to_date) or date.today()
    else:
        lookback = args.lookback_days
        from_date = date.today() - timedelta(days=lookback)
        to_date = date.today()

    client = LmsClient()
    debug_export_dir = Path(args.debug_export_dir) if args.debug_export_dir else None
    warnings: list[str] = []

    assignment_mode = "unavailable"
    assignment_csv: list[dict[str, str]] = []
    assignment_diagnostics: list[RequestDiagnostic] = []
    try:
        assignment_html = client.fetch_search_results_html(
            ASSIGNMENT_FORM, from_date, to_date, call_sign=args.call_sign
        )
        assignment_result = client.export_csv(
            ASSIGNMENT_FORM.results_url,
            assignment_html,
            debug_dir=debug_export_dir,
            debug_label="assignment",
        )
        assignment_mode = assignment_result.source_mode
        assignment_csv = assignment_result.rows
    except Exception as err:
        assignment_mode = f"error:{type(err).__name__}"
        warnings.append(f"Assignment/Transfer search failed: {err}")
    finally:
        assignment_diagnostics = client.consume_request_log()

    application_mode = "unavailable"
    application_csv: list[dict[str, str]] = []
    application_diagnostics: list[RequestDiagnostic] = []
    try:
        application_html = client.fetch_search_results_html(
            APPLICATION_FORM, from_date, to_date, call_sign=args.call_sign
        )
        application_result = client.export_csv(
            APPLICATION_FORM.results_url,
            application_html,
            debug_dir=debug_export_dir,
            debug_label="application",
        )
        application_mode = application_result.source_mode
        application_csv = application_result.rows
    except Exception as err:
        application_mode = f"error:{type(err).__name__}"
        warnings.append(f"Application search failed: {err}")
    finally:
        application_diagnostics = client.consume_request_log()

    pn_application_mode = "skipped_separate_pn_command"
    pn_application_rows_raw: list[dict[str, str]] = []
    pn_application_diagnostics: list[RequestDiagnostic] = []

    pn_action_mode = "skipped_separate_pn_command"
    pn_action_rows_raw: list[dict[str, str]] = []
    pn_action_diagnostics: list[RequestDiagnostic] = []

    assignment_rows = []
    assignment_callsign_found = 0
    assignment_unmatched_calls: dict[str, int] = {}
    assignment_matched_by = {"call_sign": 0, "facility_id": 0, "org_name": 0}
    for row in assignment_csv:
        row = _normalize_results_row(row, "Assignment/Transfer")
        call_sign = _find_call_sign(row)
        if call_sign:
            assignment_callsign_found += 1
        matched, matched_by = _matches_cpb(row, cpb_data)
        if matched:
            assignment_rows.append(row)
            assignment_matched_by[matched_by] += 1
        elif call_sign:
            assignment_unmatched_calls[call_sign] = assignment_unmatched_calls.get(call_sign, 0) + 1

    application_rows = []
    application_callsign_found = 0
    application_unmatched_calls: dict[str, int] = {}
    application_matched_by = {"call_sign": 0, "facility_id": 0, "org_name": 0}
    for row in application_csv:
        row = _normalize_results_row(row, "Application Search")
        call_sign = _find_call_sign(row)
        if call_sign:
            application_callsign_found += 1
        matched, matched_by = _matches_cpb(row, cpb_data)
        if matched:
            application_rows.append(row)
            application_matched_by[matched_by] += 1
        elif call_sign:
            application_unmatched_calls[call_sign] = application_unmatched_calls.get(call_sign, 0) + 1

    pn_application_rows = []
    pn_application_matched_by = {"call_sign": 0, "facility_id": 0, "org_name": 0}
    for row in pn_application_rows_raw:
        row = _normalize_results_row(row, "Application PN Search")
        matched, matched_by = _matches_cpb(row, cpb_data)
        if matched:
            pn_application_rows.append(row)
            pn_application_matched_by[matched_by] += 1

    pn_action_rows = []
    pn_action_matched_by = {"call_sign": 0, "facility_id": 0, "org_name": 0}
    for row in pn_action_rows_raw:
        row = _normalize_results_row(row, "Action PN Search")
        matched, matched_by = _matches_cpb(row, cpb_data)
        if matched:
            pn_action_rows.append(row)
            pn_action_matched_by[matched_by] += 1

    alerts: list[AlertItem] = []
    if assignment_rows:
        alerts.extend(_build_alerts(assignment_rows, "Assignment/Transfer", ["assignment_transfer"]))

    for row in application_rows:
        text = _row_text(row)
        categories: list[str] = []
        if _matches_keywords(text, KEYWORDS_ASSIGNMENT_TRANSFER):
            categories.append("assignment_transfer")
        if _matches_keywords(text, KEYWORDS_STA):
            categories.append("sta_silent")
        if _matches_keywords(text, KEYWORDS_CP):
            categories.append("cp_license_to_cover")
        if _matches_keywords(text, KEYWORDS_MINOR_MOD):
            categories.append("minor_modification")
        if not categories:
            continue
        alerts.extend(_build_alerts([row], "Application Search", categories))

    if pn_application_rows:
        alerts.extend(_build_alerts(pn_application_rows, "Application PN Search", ["application_public_notice"]))
    if pn_action_rows:
        alerts.extend(_build_alerts(pn_action_rows, "Action PN Search", ["action_public_notice"]))

    if application_mode != "csv_export":
        warnings.append(
            f"Application export degraded ({application_mode}); results may be partial."
        )
    for label, mode in [
        ("Application PN", pn_application_mode),
        ("Action PN", pn_action_mode),
    ]:
        if mode.endswith("_technical_error") or mode.endswith("_request_error") or mode.endswith("_no_form"):
            warnings.append(f"{label} search degraded ({mode}); public-notice results may be missing.")

    new_alerts: list[AlertItem] = []
    new_alert_keys: list[str] = []
    for alert in alerts:
        key = _alert_key(alert)
        if key in state.seen_items:
            continue
        new_alerts.append(alert)
        new_alert_keys.append(key)

    payload = {
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "assignment_export_mode": assignment_mode,
        "application_export_mode": application_mode,
        "pn_application_mode": pn_application_mode,
        "pn_action_mode": pn_action_mode,
        "assignment_request_diagnostics": _diagnostics_payload(assignment_diagnostics),
        "application_request_diagnostics": _diagnostics_payload(application_diagnostics),
        "pn_application_request_diagnostics": _diagnostics_payload(pn_application_diagnostics),
        "pn_action_request_diagnostics": _diagnostics_payload(pn_action_diagnostics),
        "assignment_csv_rows": len(assignment_csv),
        "application_csv_rows": len(application_csv),
        "pn_application_rows_raw": len(pn_application_rows_raw),
        "pn_action_rows_raw": len(pn_action_rows_raw),
        "assignment_rows": len(assignment_rows),
        "application_rows": len(application_rows),
        "pn_application_rows": len(pn_application_rows),
        "pn_action_rows": len(pn_action_rows),
        "assignment_matched_by": assignment_matched_by,
        "application_matched_by": application_matched_by,
        "pn_application_matched_by": pn_application_matched_by,
        "pn_action_matched_by": pn_action_matched_by,
        "assignment_callsign_found": assignment_callsign_found,
        "application_callsign_found": application_callsign_found,
        "assignment_top_unmatched_callsigns": sorted(
            assignment_unmatched_calls.items(), key=lambda kv: kv[1], reverse=True
        )[:15],
        "application_top_unmatched_callsigns": sorted(
            application_unmatched_calls.items(), key=lambda kv: kv[1], reverse=True
        )[:15],
        "warnings": warnings,
        "alerts": [asdict(alert) for alert in new_alerts],
        "alert_keys": new_alert_keys,
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

    if not new_alerts and not warnings:
        state.last_run = datetime.now()
        _ensure_dir(state_path)
        save_state(state_path, state)
        payload["state_updated"] = True
        out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return payload

    lines = _alert_lines(f"FCC LMS Watch — {date.today():%Y-%m-%d}", warnings, new_alerts)

    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook:
        raise RuntimeError("SLACK_WEBHOOK_URL is not set")
    try:
        post_to_slack(webhook, SlackMessage(text="\n".join(lines)))
    except SlackPostError as err:
        payload["slack_status"] = "failed"
        payload["slack_error"] = str(err)
        out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        print(f"fcc-lms-watch: Slack post failed: {err}")
        return payload

    state.seen_items = prune_seen_items([*state.seen_items, *new_alert_keys])
    state.last_run = datetime.now()
    _ensure_dir(state_path)
    save_state(state_path, state)

    payload["slack_status"] = "sent"
    payload["state_updated"] = True
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    return payload


def run_pn(args: argparse.Namespace) -> dict:
    os.environ.setdefault("FCC_LMS_BROWSER_FALLBACK", "1")
    os.environ.setdefault("FCC_LMS_REQUEST_TIMEOUT_SECONDS", "15")
    os.environ.setdefault("FCC_LMS_REQUEST_RETRIES", "0")
    if args.date_page_fallback:
        os.environ["FCC_LMS_PN_DATE_PAGE_FALLBACK"] = "1"

    cpb_path = Path(args.cpb)
    cpb_aliases_path = Path(args.cpb_aliases) if args.cpb_aliases else None
    state_path = Path(args.state)
    out_path = Path(args.out)
    debug_export_dir = Path(args.debug_export_dir) if args.debug_export_dir else None

    state = load_state(state_path)
    cpb_data = load_cpb_grantees(cpb_path, aliases_path=cpb_aliases_path)

    if args.from_date or args.to_date:
        from_date = _parse_date(args.from_date) or date.today()
        to_date = _parse_date(args.to_date) or date.today()
    else:
        from_date = date.today() - timedelta(days=args.lookback_days)
        to_date = date.today()

    notice_types = args.notice_type or ["Application", "Action"]
    client = LmsClient()
    warnings: list[str] = []
    source_modes: dict[str, str] = {}
    diagnostics: dict[str, list[dict[str, object]]] = {}
    raw_counts: dict[str, int] = {}
    matched_counts: dict[str, int] = {}
    matched_by = {
        "Application": {"call_sign": 0, "facility_id": 0, "org_name": 0},
        "Action": {"call_sign": 0, "facility_id": 0, "org_name": 0},
    }
    top_unmatched: dict[str, list[tuple[str, int]]] = {}
    alerts: list[AlertItem] = []

    for notice_type in notice_types:
        rows_raw: list[dict[str, str]] = []
        try:
            result = client.fetch_public_notice_rows(
                notice_type=notice_type,
                from_date=from_date,
                to_date=to_date,
                call_sign=args.call_sign,
                debug_dir=debug_export_dir,
            )
            source_modes[notice_type] = result.source_mode
            rows_raw = result.rows
        except Exception as err:
            source_modes[notice_type] = f"error:{type(err).__name__}"
            warnings.append(f"{notice_type} PN search failed: {err}")
        finally:
            diagnostics[notice_type] = _diagnostics_payload(client.consume_request_log())

        if source_modes[notice_type].endswith("_technical_error"):
            warnings.append(f"{notice_type} PN search hit an FCC technical-error page.")
        if source_modes[notice_type].endswith("_request_error") or source_modes[notice_type].endswith("_no_form"):
            warnings.append(f"{notice_type} PN search degraded ({source_modes[notice_type]}).")

        raw_counts[notice_type] = len(rows_raw)
        matched_rows: list[dict[str, str]] = []
        unmatched_calls: dict[str, int] = {}
        for row in rows_raw:
            row = _normalize_results_row(row, f"{notice_type} PN Search")
            matched, by = _matches_cpb(row, cpb_data)
            if matched:
                matched_rows.append(row)
                matched_by[notice_type][by] += 1
            else:
                call_sign = _find_call_sign(row)
                if call_sign:
                    unmatched_calls[call_sign] = unmatched_calls.get(call_sign, 0) + 1

        matched_counts[notice_type] = len(matched_rows)
        top_unmatched[notice_type] = sorted(unmatched_calls.items(), key=lambda kv: kv[1], reverse=True)[:15]
        category = "application_public_notice" if notice_type == "Application" else "action_public_notice"
        alerts.extend(_build_pn_alerts(matched_rows, f"{notice_type} PN Search", category))

    new_alerts: list[AlertItem] = []
    new_alert_keys: list[str] = []
    for alert in alerts:
        key = _alert_key(alert)
        if key in state.seen_items:
            continue
        new_alerts.append(alert)
        new_alert_keys.append(key)

    payload = {
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "notice_types": notice_types,
        "source_modes": source_modes,
        "request_diagnostics": diagnostics,
        "raw_counts": raw_counts,
        "matched_counts": matched_counts,
        "matched_by": matched_by,
        "top_unmatched_callsigns": top_unmatched,
        "warnings": warnings,
        "alerts": [asdict(alert) for alert in new_alerts],
        "alert_keys": new_alert_keys,
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

    if not new_alerts and not warnings:
        state.last_run = datetime.now()
        _ensure_dir(state_path)
        save_state(state_path, state)
        payload["state_updated"] = True
        out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return payload

    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook:
        raise RuntimeError("SLACK_WEBHOOK_URL is not set")
    try:
        lines = _alert_lines(f"FCC LMS PN Watch — {date.today():%Y-%m-%d}", warnings, new_alerts)
        post_to_slack(webhook, SlackMessage(text="\n".join(lines)))
    except SlackPostError as err:
        payload["slack_status"] = "failed"
        payload["slack_error"] = str(err)
        out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        print(f"fcc-lms-watch pn: Slack post failed: {err}")
        return payload

    state.seen_items = prune_seen_items([*state.seen_items, *new_alert_keys])
    state.last_run = datetime.now()
    _ensure_dir(state_path)
    save_state(state_path, state)
    payload["slack_status"] = "sent"
    payload["state_updated"] = True
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return payload


def run_pn_validate(args: argparse.Namespace) -> dict:
    validation_args = argparse.Namespace(
        cpb=args.cpb,
        cpb_aliases=args.cpb_aliases,
        state=args.state,
        out=args.out,
        lookback_days=args.lookback_days,
        from_date=args.from_date,
        to_date=args.to_date,
        call_sign=args.call_sign,
        notice_type=[args.notice_type],
        debug_export_dir=args.debug_export_dir,
        date_page_fallback=True,
        dry_run=True,
    )
    payload = run_pn(validation_args)
    expected_file = args.expect_file_number
    matching_alerts = [
        alert for alert in payload.get("alerts", []) if alert.get("file_number") == expected_file
    ]
    payload["validation"] = {
        "ok": bool(matching_alerts),
        "expected_file_number": expected_file,
        "matching_alerts": matching_alerts,
    }
    out_path = Path(args.out)
    _ensure_dir(out_path)
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    if not matching_alerts:
        raise RuntimeError(f"PN validation failed: expected file {expected_file}")
    return payload


def run_validate(args: argparse.Namespace) -> dict:
    os.environ.setdefault("FCC_LMS_BROWSER_FALLBACK", "1")
    os.environ.setdefault("FCC_LMS_REQUEST_TIMEOUT_SECONDS", "15")
    os.environ.setdefault("FCC_LMS_REQUEST_RETRIES", "0")
    validation_args = argparse.Namespace(
        cpb=args.cpb,
        cpb_aliases=args.cpb_aliases,
        state=args.state,
        out=args.out,
        lookback_days=args.lookback_days,
        from_date=args.from_date,
        to_date=args.to_date,
        call_sign=args.call_sign,
        debug_export_dir=args.debug_export_dir,
        dry_run=True,
    )
    payload = run_daily(validation_args)
    expected_file = args.expect_file_number
    expected_category = args.expect_category
    matching_alerts = [
        alert
        for alert in payload.get("alerts", [])
        if alert.get("file_number") == expected_file
        and (not expected_category or alert.get("category") == expected_category)
    ]
    payload["validation"] = {
        "ok": bool(matching_alerts),
        "expected_file_number": expected_file,
        "expected_category": expected_category,
        "matching_alerts": matching_alerts,
    }
    out_path = Path(args.out)
    _ensure_dir(out_path)
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    if not matching_alerts:
        raise RuntimeError(
            f"Validation failed: expected file {expected_file}"
            + (f" with category {expected_category}" if expected_category else "")
        )
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="FCC LMS Watch")
    sub = parser.add_subparsers(dest="command", required=True)

    daily = sub.add_parser("daily", help="Run daily scan")
    daily.add_argument("--cpb", default="../990s/cpb-grantees.csv")
    daily.add_argument(
        "--cpb-aliases",
        default=os.environ.get("FCC_LMS_CPB_ALIASES", "station-aliases.json"),
        help="Optional JSON file with extra call-sign aliases",
    )
    daily.add_argument("--state", default="output/state.json")
    daily.add_argument("--out", default="output/last-run.json")
    default_lookback = int(os.environ.get("FCC_LMS_LOOKBACK_DAYS", DEFAULT_LOOKBACK_DAYS))
    daily.add_argument("--lookback-days", type=int, default=default_lookback)
    daily.add_argument("--from-date", help="Override start date (YYYY-MM-DD)")
    daily.add_argument("--to-date", help="Override end date (YYYY-MM-DD)")
    daily.add_argument("--call-sign", help="Optional call sign filter for LMS search (e.g., WNED-FM)")
    daily.add_argument(
        "--debug-export-dir",
        default=os.environ.get("FCC_LMS_DEBUG_EXPORT_DIR"),
        help="Optional directory to dump export POST payloads/responses for debugging",
    )
    daily.add_argument("--dry-run", action="store_true")
    daily.set_defaults(func=run_daily)

    pn = sub.add_parser("pn", help="Run separate public-notice scan")
    pn.add_argument("--cpb", default="../990s/cpb-grantees.csv")
    pn.add_argument(
        "--cpb-aliases",
        default=os.environ.get("FCC_LMS_CPB_ALIASES", "station-aliases.json"),
        help="Optional JSON file with extra call-sign aliases",
    )
    pn.add_argument("--state", default="output/pn-state.json")
    pn.add_argument("--out", default="output/pn-last-run.json")
    pn.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    pn.add_argument("--from-date", help="Override start date (YYYY-MM-DD)")
    pn.add_argument("--to-date", help="Override end date (YYYY-MM-DD)")
    pn.add_argument("--call-sign", help="Optional call sign filter for LMS search")
    pn.add_argument(
        "--notice-type",
        action="append",
        choices=["Application", "Action"],
        help="Notice type to scan; repeat for both, omit for both",
    )
    pn.add_argument(
        "--debug-export-dir",
        default=os.environ.get("FCC_LMS_DEBUG_EXPORT_DIR"),
        help="Optional directory to dump PN payloads/responses for debugging",
    )
    pn.add_argument(
        "--date-page-fallback",
        action="store_true",
        default=os.environ.get("FCC_LMS_PN_DATE_PAGE_FALLBACK", "").strip() == "1",
        help="Enable slower browser date-page scraping",
    )
    pn.add_argument("--dry-run", action="store_true")
    pn.set_defaults(func=run_pn)

    probe = sub.add_parser("probe", help="Compare Python and curl access to an LMS URL")
    probe.add_argument("--url", default=ASSIGNMENT_FORM.url)
    probe.add_argument("--out", default="output/probe-last.json")
    probe.set_defaults(func=run_probe)

    validate = sub.add_parser("validate", help="Run non-mutating known-positive validation")
    validate.add_argument("--cpb", default="../990s/cpb-grantees.csv")
    validate.add_argument(
        "--cpb-aliases",
        default=os.environ.get("FCC_LMS_CPB_ALIASES", "station-aliases.json"),
        help="Optional JSON file with extra call-sign aliases",
    )
    validate.add_argument("--state", default="output/validation-state.json")
    validate.add_argument("--out", default="output/validation-last-run.json")
    validate.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    validate.add_argument("--from-date", default="2026-01-01")
    validate.add_argument("--to-date", default="2026-02-10")
    validate.add_argument("--call-sign", default="WNED-FM")
    validate.add_argument("--expect-file-number", default="0000288968")
    validate.add_argument("--expect-category", default="minor_modification")
    validate.add_argument(
        "--debug-export-dir",
        default=os.environ.get("FCC_LMS_DEBUG_EXPORT_DIR"),
        help="Optional directory to dump export POST payloads/responses for debugging",
    )
    validate.set_defaults(func=run_validate)

    pn_validate = sub.add_parser("pn-validate", help="Run non-mutating PN known-positive validation")
    pn_validate.add_argument("--cpb", default="../990s/cpb-grantees.csv")
    pn_validate.add_argument(
        "--cpb-aliases",
        default=os.environ.get("FCC_LMS_CPB_ALIASES", "station-aliases.json"),
        help="Optional JSON file with extra call-sign aliases",
    )
    pn_validate.add_argument("--state", default="output/pn-validation-state.json")
    pn_validate.add_argument("--out", default="output/pn-validation-last-run.json")
    pn_validate.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    pn_validate.add_argument("--from-date", default="2026-05-01")
    pn_validate.add_argument("--to-date", default="2026-05-06")
    pn_validate.add_argument("--call-sign", default="WRVO")
    pn_validate.add_argument("--notice-type", choices=["Application", "Action"], default="Application")
    pn_validate.add_argument("--expect-file-number", default="0000296158")
    pn_validate.add_argument(
        "--debug-export-dir",
        default=os.environ.get("FCC_LMS_DEBUG_EXPORT_DIR", "output/pn-validation-debug"),
        help="Optional directory to dump PN payloads/responses for debugging",
    )
    pn_validate.set_defaults(func=run_pn_validate)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
