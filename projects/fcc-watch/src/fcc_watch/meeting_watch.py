from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
import re
import socket
import time
from typing import Iterable
import urllib.error
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from .config import (
    DEFAULT_MEETING_BASE_URLS,
    DEFAULT_MEETING_MAX_ITEMS,
    DEFAULT_MEETING_RETRIES,
    DEFAULT_MEETING_RETRY_BACKOFF_SECONDS,
    DEFAULT_MEETING_TIMEOUT_SECONDS,
    KEYWORDS,
    USER_AGENT,
)


@dataclass(frozen=True)
class MeetingItem:
    title: str
    link: str
    source_url: str
    date_hint: str
    matched_keywords: list[str]


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._current_href: str | None = None
        self.items: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = None
        for key, value in attrs:
            if key.lower() == "href":
                href = value
                break
        self._current_href = href

    def handle_data(self, data: str) -> None:
        if self._current_href and data.strip():
            self.items.append((data.strip(), self._current_href))

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a":
            self._current_href = None


def _keyword_matches(text: str, keywords: Iterable[str]) -> list[str]:
    matched: list[str] = []
    lowered = text.lower()
    for keyword in keywords:
        if keyword.lower() in lowered:
            matched.append(keyword)
    return matched


def _fetch_page(url: str) -> str:
    last_error: Exception | None = None
    for attempt in range(DEFAULT_MEETING_RETRIES + 1):
        try:
            req = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(req, timeout=DEFAULT_MEETING_TIMEOUT_SECONDS) as resp:  # nosec B310
                return resp.read().decode("utf-8", errors="ignore")
        except urllib.error.HTTPError as err:
            if err.code in (404, 410):
                return ""
            last_error = err
            if attempt >= DEFAULT_MEETING_RETRIES:
                break
            time.sleep(DEFAULT_MEETING_RETRY_BACKOFF_SECONDS * (attempt + 1))
        except (TimeoutError, socket.timeout, urllib.error.URLError, OSError) as err:
            last_error = err
            if attempt >= DEFAULT_MEETING_RETRIES:
                break
            time.sleep(DEFAULT_MEETING_RETRY_BACKOFF_SECONDS * (attempt + 1))
    detail = f"{type(last_error).__name__}: {last_error}" if last_error else "unknown error"
    raise RuntimeError(f"Meeting source request failed after retries: {url} (last_error={detail})") from last_error


def _is_internal_fcc_link(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.netloc in {"", "fcc.gov", "www.fcc.gov"}


def _date_hint(text: str) -> str:
    patterns = [
        r"\b\d{4}-\d{2}-\d{2}\b",
        r"\b\d{1,2}/\d{1,2}/\d{4}\b",
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s+\d{4}\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(0)
    return ""


def _is_recent(date_hint: str, lookback_days: int) -> bool:
    if not date_hint:
        return True
    formats = ("%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y")
    parsed: datetime | None = None
    for fmt in formats:
        try:
            parsed = datetime.strptime(date_hint, fmt)
            break
        except ValueError:
            continue
    if not parsed:
        return True
    cutoff = datetime.combine(date.today(), datetime.min.time()) - timedelta(days=max(lookback_days - 1, 0))
    return parsed >= cutoff


def fetch_meeting_items(*, extra_keywords: Iterable[str] | None = None, lookback_days: int) -> list[MeetingItem]:
    keywords = list(KEYWORDS)
    keywords.extend(
        [
            "open commission meeting",
            "commission meeting",
            "sunshine notice",
            "circulation",
            "agenda",
            "media bureau",
            "draft item",
            "consent agenda",
        ]
    )
    if extra_keywords:
        keywords.extend(extra_keywords)

    watch_hints = {
        "meeting",
        "agenda",
        "circulation",
        "sunshine",
        "commission",
        "item",
        "bureau",
        "fcc",
    }

    items: list[MeetingItem] = []
    seen_links: set[str] = set()
    errors: list[str] = []
    successes = 0

    for source_url in DEFAULT_MEETING_BASE_URLS:
        try:
            content = _fetch_page(source_url)
        except Exception as err:
            errors.append(str(err))
            continue
        if not content.strip():
            continue
        successes += 1

        parser = _AnchorParser()
        parser.feed(content)

        for title, href in parser.items:
            if len(items) >= DEFAULT_MEETING_MAX_ITEMS:
                break
            link = urljoin(source_url, href)
            if not _is_internal_fcc_link(link):
                continue
            if link in seen_links:
                continue

            text = f"{title}\n{link}"
            lowered = text.lower()
            if not any(hint in lowered for hint in watch_hints):
                continue

            matched_keywords = _keyword_matches(text, keywords)
            if not matched_keywords:
                continue

            date_hint = _date_hint(text)
            if not _is_recent(date_hint, lookback_days):
                continue

            seen_links.add(link)
            items.append(
                MeetingItem(
                    title=title,
                    link=link,
                    source_url=source_url,
                    date_hint=date_hint,
                    matched_keywords=matched_keywords,
                )
            )

    if successes == 0:
        detail = errors[-1] if errors else "all meeting source pages were empty"
        raise RuntimeError(
            f"Meeting source fetch had no successful pages (attempted={len(DEFAULT_MEETING_BASE_URLS)}, last_error={detail})"
        )

    return items
