from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
import socket
import time
from typing import Iterable
import urllib.error
from urllib.request import Request, urlopen

from .config import (
    CATEGORY_KEYWORDS,
    DEFAULT_DIGEST_BASE_URLS,
    DEFAULT_DIGEST_RETRIES,
    DEFAULT_DIGEST_RETRY_BACKOFF_SECONDS,
    DEFAULT_DIGEST_TIMEOUT_SECONDS,
    KEYWORDS,
    USER_AGENT,
)


@dataclass(frozen=True)
class DigestItem:
    title: str
    link: str
    matched_keywords: list[str]
    categories: list[str]


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


def _category_matches(text: str) -> list[str]:
    categories: list[str] = []
    lowered = text.lower()
    for category, keys in CATEGORY_KEYWORDS.items():
        if any(key in lowered for key in keys):
            categories.append(category)
    return categories


def _fetch_daily_digest_html(target_date: date) -> str:
    urls = [f"{base.rstrip('/')}/{target_date:%Y/%m/%d}" for base in DEFAULT_DIGEST_BASE_URLS]
    last_error: Exception | None = None
    for url in urls:
        for attempt in range(DEFAULT_DIGEST_RETRIES + 1):
            try:
                req = Request(url, headers={"User-Agent": USER_AGENT})
                with urlopen(req, timeout=DEFAULT_DIGEST_TIMEOUT_SECONDS) as resp:  # nosec B310
                    return resp.read().decode("utf-8", errors="ignore")
            except urllib.error.HTTPError as err:
                if err.code in (404, 410):
                    return ""
                last_error = err
                if attempt >= DEFAULT_DIGEST_RETRIES:
                    break
                time.sleep(DEFAULT_DIGEST_RETRY_BACKOFF_SECONDS * (attempt + 1))
            except (TimeoutError, socket.timeout, urllib.error.URLError, OSError) as err:
                last_error = err
                if attempt >= DEFAULT_DIGEST_RETRIES:
                    break
                time.sleep(DEFAULT_DIGEST_RETRY_BACKOFF_SECONDS * (attempt + 1))
    detail = f"{type(last_error).__name__}: {last_error}" if last_error else "unknown error"
    raise RuntimeError(
        f"Daily Digest request failed after retries: {urls[0]} (sources tried: {len(urls)}; last_error={detail})"
    ) from last_error


def fetch_daily_digest(target_date: date, extra_keywords: Iterable[str] | None = None) -> list[DigestItem]:
    content = _fetch_daily_digest_html(target_date)

    parser = _AnchorParser()
    parser.feed(content)

    keywords = list(KEYWORDS)
    if extra_keywords:
        keywords.extend(extra_keywords)

    items: list[DigestItem] = []
    for text, link in parser.items:
        matches = _keyword_matches(text, keywords)
        categories = _category_matches(text)
        if matches or categories:
            items.append(
                DigestItem(
                    title=text,
                    link=link,
                    matched_keywords=matches,
                    categories=categories,
                )
            )

    return items
