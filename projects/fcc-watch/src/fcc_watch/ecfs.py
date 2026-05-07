from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import json
import re
import socket
import time
from typing import Any, Iterable
from urllib.parse import urlencode
import urllib.error
from urllib.request import Request, urlopen

from .config import (
    DEFAULT_ECFS_BASE_URLS,
    DEFAULT_ECFS_DOCKETS,
    DEFAULT_ECFS_LIMIT,
    DEFAULT_ECFS_RETRIES,
    DEFAULT_ECFS_RETRY_BACKOFF_SECONDS,
    DEFAULT_ECFS_TIMEOUT_SECONDS,
    KEYWORDS,
    USER_AGENT,
)


@dataclass(frozen=True)
class EcfsItem:
    title: str
    link: str
    date_received: str
    proceedings: list[str]
    filers: list[str]
    matched_call_signs: list[str]
    matched_keywords: list[str]


def _keyword_matches(text: str, keywords: Iterable[str]) -> list[str]:
    matched: list[str] = []
    lowered = text.lower()
    for keyword in keywords:
        if keyword.lower() in lowered:
            matched.append(keyword)
    return matched


def _expand_call_sign_variants(call_sign: str) -> set[str]:
    variants = {call_sign.upper()}
    if "-" in call_sign:
        variants.add(call_sign.split("-", 1)[0].upper())
    return variants


def _call_sign_matches(text: str, cpb_call_signs: set[str]) -> list[str]:
    lowered = text.lower()
    matched: list[str] = []
    for call_sign in sorted(cpb_call_signs):
        variants = _expand_call_sign_variants(call_sign)
        if any(re.search(rf"\b{re.escape(variant.lower())}\b", lowered) for variant in variants):
            matched.append(call_sign)
    return matched


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return []


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _pull_names(seq: Any) -> list[str]:
    names: list[str] = []
    for item in _as_list(seq):
        if isinstance(item, dict):
            candidate = (
                _coerce_text(item.get("name"))
                or _coerce_text(item.get("label"))
                or _coerce_text(item.get("value"))
            )
        else:
            candidate = _coerce_text(item)
        if candidate:
            names.append(candidate)
    return names


def _parse_datetime(raw: str) -> datetime | None:
    value = raw.strip()
    if not value:
        return None

    normalized = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        dt = None

    if dt is None:
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S"):
            try:
                dt = datetime.strptime(value, fmt)
                break
            except ValueError:
                continue

    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _extract_payload_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    candidates = (
        payload.get("results"),
        payload.get("filings"),
        payload.get("items"),
        payload.get("data"),
    )
    for candidate in candidates:
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]
    return []


def _build_query_url(base_url: str, params: dict[str, str]) -> str:
    if not params:
        return base_url
    joiner = "&" if "?" in base_url else "?"
    return f"{base_url}{joiner}{urlencode(params)}"


def _fetch_json(url: str) -> Any:
    last_error: Exception | None = None
    for attempt in range(DEFAULT_ECFS_RETRIES + 1):
        try:
            req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urlopen(req, timeout=DEFAULT_ECFS_TIMEOUT_SECONDS) as resp:  # nosec B310
                return json.loads(resp.read().decode("utf-8", errors="ignore"))
        except json.JSONDecodeError as err:
            last_error = err
            break
        except urllib.error.HTTPError as err:
            if err.code in (400, 404, 410):
                return []
            last_error = err
            if attempt >= DEFAULT_ECFS_RETRIES:
                break
            time.sleep(DEFAULT_ECFS_RETRY_BACKOFF_SECONDS * (attempt + 1))
        except (TimeoutError, socket.timeout, urllib.error.URLError, OSError) as err:
            last_error = err
            if attempt >= DEFAULT_ECFS_RETRIES:
                break
            time.sleep(DEFAULT_ECFS_RETRY_BACKOFF_SECONDS * (attempt + 1))

    detail = f"{type(last_error).__name__}: {last_error}" if last_error else "unknown error"
    raise RuntimeError(f"ECFS request failed after retries: {url} (last_error={detail})") from last_error


def _primary_link(item: dict[str, Any]) -> str:
    direct = (
        _coerce_text(item.get("link"))
        or _coerce_text(item.get("url"))
        or _coerce_text(item.get("detail_url"))
    )
    if direct:
        return direct

    filing_id = (
        _coerce_text(item.get("id"))
        or _coerce_text(item.get("filing_id"))
        or _coerce_text(item.get("filing_number"))
    )
    if filing_id:
        return f"https://www.fcc.gov/ecfs/document/{filing_id}"
    return ""


def fetch_ecfs_filings(
    *,
    cpb_call_signs: set[str],
    extra_keywords: Iterable[str] | None = None,
    lookback_days: int,
) -> list[EcfsItem]:
    keywords = list(KEYWORDS)
    if extra_keywords:
        keywords.extend(extra_keywords)

    docket_filters = DEFAULT_ECFS_DOCKETS
    cutoff = datetime.combine(date.today(), datetime.min.time(), tzinfo=timezone.utc) - timedelta(
        days=max(lookback_days - 1, 0)
    )
    now = datetime.now(timezone.utc)

    queries: list[dict[str, str]] = [
        {
            "limit": str(DEFAULT_ECFS_LIMIT),
            "date_received_min": cutoff.date().isoformat(),
            "date_received_max": now.date().isoformat(),
        }
    ]

    raw_items: list[dict[str, Any]] = []
    errors: list[str] = []

    for base_url in DEFAULT_ECFS_BASE_URLS:
        for params in queries:
            url = _build_query_url(base_url, params)
            try:
                payload = _fetch_json(url)
            except Exception as err:
                errors.append(str(err))
                continue
            raw_items.extend(_extract_payload_items(payload))

    if not raw_items:
        detail = errors[-1] if errors else "no data returned"
        raise RuntimeError(f"ECFS fetch produced no items (queries={len(queries)}, last_error={detail})")

    seen: set[str] = set()
    filtered: list[EcfsItem] = []

    for item in raw_items:
        proceedings = _pull_names(item.get("proceedings"))
        filers = _pull_names(item.get("filers"))

        title = (
            _coerce_text(item.get("title"))
            or _coerce_text(item.get("brief"))
            or _coerce_text(item.get("description"))
            or _coerce_text(item.get("summary"))
            or "(untitled ECFS filing)"
        )
        filing_date_raw = (
            _coerce_text(item.get("date_received"))
            or _coerce_text(item.get("dateReceived"))
            or _coerce_text(item.get("date_disseminated"))
            or _coerce_text(item.get("dateDisseminated"))
        )
        filing_date = _parse_datetime(filing_date_raw)
        if filing_date and filing_date < cutoff:
            continue

        text = "\n".join(
            [
                title,
                _coerce_text(item.get("description")),
                _coerce_text(item.get("summary")),
                ", ".join(proceedings),
                ", ".join(filers),
            ]
        )
        matched_call_signs = _call_sign_matches(text, cpb_call_signs)
        matched_keywords = _keyword_matches(text, keywords)

        if docket_filters:
            proceedings_text = " ".join(proceedings).lower()
            if not any(docket.lower() in proceedings_text for docket in docket_filters):
                continue

        if not matched_call_signs and not matched_keywords:
            continue

        link = _primary_link(item)
        dedupe_key = link or "|".join(
            [
                title,
                filing_date_raw,
                ",".join(proceedings),
                ",".join(filers),
            ]
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        filtered.append(
            EcfsItem(
                title=title,
                link=link,
                date_received=filing_date_raw,
                proceedings=proceedings,
                filers=filers,
                matched_call_signs=matched_call_signs,
                matched_keywords=matched_keywords,
            )
        )

    return filtered
