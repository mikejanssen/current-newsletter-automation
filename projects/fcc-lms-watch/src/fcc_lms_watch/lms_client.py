from __future__ import annotations

import csv
import http.client
import io
import json
import os
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
from dataclasses import dataclass
from datetime import date
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.request import HTTPCookieProcessor, Request, build_opener

from .config import USER_AGENT, SearchFormConfig
from .html_forms import parse_forms, parse_results_table_rows


@dataclass(frozen=True)
class CsvResult:
    rows: list[dict[str, str]]
    source_mode: str = "csv_export"


def _env_int(name: str, default: int, *, minimum: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= minimum else default


class LmsClient:
    def __init__(self) -> None:
        self._cookies = CookieJar()
        self._opener = build_opener(HTTPCookieProcessor(self._cookies))
        self._request_timeout_seconds = _env_int("FCC_LMS_REQUEST_TIMEOUT_SECONDS", 120, minimum=5)
        self._request_retries = _env_int("FCC_LMS_REQUEST_RETRIES", 2, minimum=0)
        self._retry_backoff_seconds = _env_int("FCC_LMS_RETRY_BACKOFF_SECONDS", 2, minimum=0)

    def _request(self, url: str, data: dict[str, str] | None = None, referer: str | None = None) -> bytes:
        last_error: Exception | None = None
        for attempt in range(self._request_retries + 1):
            try:
                headers = {"User-Agent": USER_AGENT}
                if referer:
                    headers["Referer"] = referer
                if data is None:
                    req = Request(url, headers=headers)
                else:
                    payload = urllib.parse.urlencode(data).encode("utf-8")
                    req = Request(
                        url,
                        data=payload,
                        headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
                    )
                with self._opener.open(req, timeout=self._request_timeout_seconds) as resp:  # nosec B310
                    return resp.read()
            except (
                TimeoutError,
                socket.timeout,
                ConnectionResetError,
                ssl.SSLError,
                urllib.error.URLError,
                http.client.HTTPException,
                OSError,
            ) as err:
                last_error = err
                if attempt >= self._request_retries:
                    break
                time.sleep(self._retry_backoff_seconds * (attempt + 1))
        raise RuntimeError(f"LMS request failed after retries: {url}") from last_error

    def fetch_search_results_html(
        self,
        config: SearchFormConfig,
        from_date: date,
        to_date: date,
        call_sign: str | None = None,
    ) -> str:
        page = self._request(config.url).decode("utf-8", errors="ignore")
        forms = parse_forms(page)
        viewstate = ""
        for form in forms:
            if "javax.faces.ViewState" in form.inputs:
                viewstate = form.inputs["javax.faces.ViewState"]
                break
        if not viewstate:
            raise RuntimeError("Unable to find javax.faces.ViewState on search page")

        data: dict[str, str] = {}
        for field in config.field_names:
            if field == "frm-advSearch":
                data[field] = field
            else:
                data[field] = ""
        if call_sign:
            for field in config.field_names:
                if field.lower() == "txt-callsign":
                    data[field] = call_sign.strip().upper()
        data[config.date_from_name] = from_date.strftime("%m/%d/%Y")
        data[config.date_to_name] = to_date.strftime("%m/%d/%Y")
        data[config.submit_name] = "Search"
        data["javax.faces.ViewState"] = viewstate

        html = self._request(config.url, data=data).decode("utf-8", errors="ignore")
        return html

    def fetch_public_notice_rows(
        self,
        *,
        notice_type: str,
        from_date: date,
        to_date: date,
        call_sign: str | None = None,
        debug_dir: Path | None = None,
    ) -> CsvResult:
        if notice_type not in {"Application", "Action"}:
            raise ValueError(f"Unsupported notice_type: {notice_type}")

        url = f"https://enterpriseefiling.fcc.gov/dataentry/public/tv/publicNoticeSearch.html?noticeType={notice_type}"
        page = self._request(url).decode("utf-8", errors="ignore")
        forms = parse_forms(page)
        form = next((f for f in forms if f.name == "frm-advSearch" or f.form_id == "frm-advSearch"), None)
        if form is None:
            if debug_dir is not None:
                debug_dir.mkdir(parents=True, exist_ok=True)
                (debug_dir / f"pn-{notice_type.lower()}-search-page.html").write_text(page, encoding="utf-8")
            return CsvResult(rows=[], source_mode=f"pn_{notice_type.lower()}_no_form")

        data: dict[str, str] = {}
        for field in form.inputs:
            # Avoid sending every control state; set known search inputs explicitly below.
            if field.startswith("j_id"):
                continue
            data[field] = ""
        data["frm-advSearch"] = "frm-advSearch"
        data["txt-fromDate"] = from_date.strftime("%m/%d/%Y")
        data["txt-toDate"] = to_date.strftime("%m/%d/%Y")
        data["srchtype"] = "R"
        if call_sign:
            data["txt-callSign"] = call_sign.strip().upper()
        if "javax.faces.ViewState" in form.inputs:
            data["javax.faces.ViewState"] = form.inputs["javax.faces.ViewState"]
        data["j_idt99"] = "Search"

        try:
            html = self._request(url, data=data, referer=url).decode("utf-8", errors="ignore")
        except Exception as err:
            if debug_dir is not None:
                debug_dir.mkdir(parents=True, exist_ok=True)
                (debug_dir / f"pn-{notice_type.lower()}-request-error.txt").write_text(str(err), encoding="utf-8")
            return CsvResult(rows=[], source_mode=f"pn_{notice_type.lower()}_request_error")

        if debug_dir is not None:
            debug_dir.mkdir(parents=True, exist_ok=True)
            (debug_dir / f"pn-{notice_type.lower()}-results.html").write_text(html, encoding="utf-8")
            (debug_dir / f"pn-{notice_type.lower()}-payload.json").write_text(
                json.dumps(data, indent=2, sort_keys=True), encoding="utf-8"
            )

        rows = parse_results_table_rows(html)
        if not rows:
            return CsvResult(rows=[], source_mode=f"pn_{notice_type.lower()}_html_empty")
        return CsvResult(rows=rows, source_mode=f"pn_{notice_type.lower()}_html_table")

    def _write_export_debug(
        self,
        debug_dir: Path,
        debug_label: str,
        *,
        results_url: str,
        form_inputs: dict[str, str],
        payload: dict[str, str],
        response_text: str | None = None,
    ) -> None:
        debug_dir.mkdir(parents=True, exist_ok=True)
        (debug_dir / f"{debug_label}-results-url.txt").write_text(results_url, encoding="utf-8")
        (debug_dir / f"{debug_label}-form-inputs.json").write_text(
            json.dumps(form_inputs, indent=2, sort_keys=True), encoding="utf-8"
        )
        (debug_dir / f"{debug_label}-post-payload.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
        )
        (debug_dir / f"{debug_label}-post-payload-urlencoded.txt").write_text(
            urllib.parse.urlencode(payload), encoding="utf-8"
        )
        if response_text is not None:
            suffix = "html" if response_text.lstrip().lower().startswith(("<!doctype html", "<html")) else "csv"
            (debug_dir / f"{debug_label}-response.{suffix}").write_text(response_text, encoding="utf-8")

    def _extract_pagination_actions(self, html: str, form_name: str) -> list[dict[str, str]]:
        pattern = (
            r"mojarra\.jsfcljs\(\s*document\.getElementById\('"
            + re.escape(form_name)
            + r"'\)\s*,\s*\{([^}]*)\}\s*,\s*''\s*\)"
        )
        matches = re.findall(pattern, html)
        actions: list[dict[str, str]] = []
        seen_pages: set[str] = set()
        for body in matches:
            pairs = re.findall(r"'([^']+)'\s*:\s*'([^']*)'", body)
            action = {k: v for k, v in pairs}
            page = action.get("moveToPageNum")
            if not page:
                continue
            if page in seen_pages:
                continue
            seen_pages.add(page)
            actions.append(action)
        actions.sort(key=lambda a: int(a.get("moveToPageNum", "0") or "0"))
        return actions

    def _collect_application_fallback_rows(
        self,
        *,
        results_url: str,
        base_form_data: dict[str, str],
        initial_html: str,
        debug_dir: Path | None,
        debug_label: str,
    ) -> list[dict[str, str]]:
        all_rows = parse_results_table_rows(initial_html)
        actions = self._extract_pagination_actions(initial_html, "inbox")

        # Protect runtime; try only first few pages if paginator is large.
        for idx, action in enumerate(actions[:8], start=1):
            request_data = dict(base_form_data)
            request_data.update(action)
            request_data.setdefault("pageIndex", "")
            try:
                page_html = self._request(results_url, data=request_data, referer=results_url).decode(
                    "utf-8", errors="ignore"
                )
            except Exception:
                continue
            if debug_dir is not None:
                (debug_dir / f"{debug_label}-fallback-page-{idx}.html").write_text(page_html, encoding="utf-8")
            page_rows = parse_results_table_rows(page_html)
            if page_rows:
                all_rows.extend(page_rows)

        # Deduplicate rows by sorted key/value signature.
        deduped: list[dict[str, str]] = []
        seen: set[str] = set()
        for row in all_rows:
            signature = json.dumps(row, sort_keys=True)
            if signature in seen:
                continue
            seen.add(signature)
            deduped.append(row)
        return deduped

    def _looks_like_results_page(self, html: str, debug_label: str) -> bool:
        lower = html.lower()
        if debug_label == "assignment":
            markers = [
                "assignment/transfer search results",
                "assignmentsearchresults",
                "assignmenttransfersearchresults",
            ]
        elif debug_label == "application":
            markers = [
                "application search results",
                "publicappsearchresults",
                "inbox",
            ]
        else:
            markers = ["search results"]
        return any(m in lower for m in markers)

    def export_csv(
        self,
        results_url: str,
        html: str,
        debug_dir: Path | None = None,
        debug_label: str = "export",
    ) -> CsvResult:
        forms = parse_forms(html)
        export_form = None
        for form in forms:
            if "assignmentSearchResults" in form.inputs or "inbox" in form.inputs:
                export_form = form
                break
        if export_form is None:
            for form in forms:
                if "javax.faces.ViewState" in form.inputs:
                    export_form = form
                    break
        if export_form is None:
            fallback_rows = parse_results_table_rows(html)
            if debug_dir is not None:
                debug_dir.mkdir(parents=True, exist_ok=True)
                (debug_dir / f"{debug_label}-results-html.html").write_text(html, encoding="utf-8")
                (debug_dir / f"{debug_label}-fallback-note.txt").write_text(
                    "Export form not found; used visible HTML rows when available.",
                    encoding="utf-8",
                )
            if fallback_rows:
                return CsvResult(rows=fallback_rows, source_mode="html_fallback_no_export_form")
            return CsvResult(rows=[], source_mode="html_fallback_no_export_form_empty")

        data = dict(export_form.inputs)
        data["exportType"] = "csv"

        if "javax.faces.ViewState" not in data:
            for form in forms:
                if "javax.faces.ViewState" in form.inputs:
                    data["javax.faces.ViewState"] = form.inputs["javax.faces.ViewState"]
                    break

        submit_name = None
        csv_submit_patterns = [
            r"mojarra\.jsfcljs\([^)]*\{\s*'([^']+)'\s*:\s*'[^']+'\s*,\s*'exportType'\s*:\s*'csv'\s*\}",
            r"mojarra\.jsfcljs\([^)]*\{\s*'exportType'\s*:\s*'csv'\s*,\s*'([^']+)'\s*:\s*'[^']+'\s*\}",
            r"\{\s*'([^']+)'\s*:\s*'[^']+'\s*,\s*'exportType'\s*:\s*'csv'\s*\}",
        ]
        for pattern in csv_submit_patterns:
            match = re.search(pattern, html)
            if match:
                submit_name = match.group(1)
                break
        if submit_name is None:
            for name in export_form.inputs:
                if name.startswith("j_idt"):
                    submit_name = name
                    break
        if submit_name:
            data[submit_name] = submit_name

        fallback_html = html
        fallback_form_data = dict(export_form.inputs)
        if debug_label == "application":
            # Try to enlarge visible page size for fallback parsing.
            table_data = dict(export_form.inputs)
            if "javax.faces.ViewState" in data:
                table_data["javax.faces.ViewState"] = data["javax.faces.ViewState"]
            table_data["sel-displayOpts1"] = "100"
            fallback_form_data = dict(table_data)
            try:
                fallback_html = self._request(results_url, data=table_data, referer=results_url).decode(
                    "utf-8", errors="ignore"
                )
                if debug_dir is not None:
                    (debug_dir / f"{debug_label}-fallback-results-page.html").write_text(
                        fallback_html, encoding="utf-8"
                    )
            except Exception:
                fallback_html = html

        if debug_dir is not None:
            self._write_export_debug(
                debug_dir,
                debug_label,
                results_url=results_url,
                form_inputs=export_form.inputs,
                payload=data,
            )
        try:
            content = self._request(results_url, data=data, referer=results_url).decode("utf-8", errors="ignore")
        except Exception as err:
            if debug_dir is not None:
                (debug_dir / f"{debug_label}-request-error.txt").write_text(str(err), encoding="utf-8")
            # Export is unstable; fall back to parsing visible results table.
            if debug_label == "application":
                fallback_rows = self._collect_application_fallback_rows(
                    results_url=results_url,
                    base_form_data=fallback_form_data,
                    initial_html=fallback_html,
                    debug_dir=debug_dir,
                    debug_label=debug_label,
                )
            else:
                fallback_rows = parse_results_table_rows(fallback_html)

            if fallback_rows or self._looks_like_results_page(fallback_html, debug_label):
                if debug_dir is not None:
                    (debug_dir / f"{debug_label}-fallback-note.txt").write_text(
                        "Used HTML table fallback due to export request failure; attempted additional pages when available.",
                        encoding="utf-8",
                    )
                return CsvResult(rows=fallback_rows, source_mode="html_fallback_request_error")
            if debug_dir is not None:
                (debug_dir / f"{debug_label}-fallback-note.txt").write_text(
                    "Export request failed and no visible rows were parsed; returning empty fallback rows.",
                    encoding="utf-8",
                )
            return CsvResult(rows=[], source_mode="html_fallback_request_error_empty")
        if debug_dir is not None:
            self._write_export_debug(
                debug_dir,
                debug_label,
                results_url=results_url,
                form_inputs=export_form.inputs,
                payload=data,
                response_text=content,
            )

        if content.lstrip().lower().startswith("<!doctype html") or content.lstrip().lower().startswith("<html"):
            if debug_label == "application":
                fallback_rows = self._collect_application_fallback_rows(
                    results_url=results_url,
                    base_form_data=fallback_form_data,
                    initial_html=content,
                    debug_dir=debug_dir,
                    debug_label=debug_label,
                )
            else:
                fallback_rows = parse_results_table_rows(content)
            if fallback_rows or self._looks_like_results_page(content, debug_label):
                if debug_dir is not None:
                    (debug_dir / f"{debug_label}-fallback-note.txt").write_text(
                        "Used HTML table fallback because export endpoint returned HTML; attempted additional pages when available.",
                        encoding="utf-8",
                    )
                return CsvResult(rows=fallback_rows, source_mode="html_fallback_export_html")
            if debug_dir is not None:
                (debug_dir / f"{debug_label}-fallback-note.txt").write_text(
                    "Export endpoint returned HTML and no rows were parsed; returning empty fallback rows.",
                    encoding="utf-8",
                )
            return CsvResult(rows=[], source_mode="html_fallback_export_html_empty")
        rows = []
        reader = csv.DictReader(io.StringIO(content))
        for row in reader:
            rows.append({k: v.strip() for k, v in row.items() if k is not None})
        return CsvResult(rows=rows, source_mode="csv_export")
