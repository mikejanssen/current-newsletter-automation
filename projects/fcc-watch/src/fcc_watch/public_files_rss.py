from __future__ import annotations

from dataclasses import dataclass
import re
import socket
import time
from typing import Iterable
import urllib.error
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from .config import (
    DEFAULT_PUBLIC_FILES_BASE_URL,
    DEFAULT_PUBLIC_FILES_PRIORITY_MODE,
    DEFAULT_PUBLIC_FILES_RETRIES,
    DEFAULT_PUBLIC_FILES_RETRY_BACKOFF_SECONDS,
    DEFAULT_PUBLIC_FILES_STATION_LIMIT,
    DEFAULT_PUBLIC_FILES_TIMEOUT_SECONDS,
    KEYWORDS,
    USER_AGENT,
)


@dataclass(frozen=True)
class PublicFilesItem:
    title: str
    link: str
    document_link: str
    published_at: str
    source_call_sign: str
    source_url: str
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


INCLUDE_SECTION_HINTS_BALANCED = [
    "issues and programs",
    "local public notice announcements",
    "license renewal",
    "equal employment opportunity",
    "ownership report",
    "applications and related materials",
]

INCLUDE_SECTION_HINTS_HIGH = [
    "local public notice announcements",
    "license renewal",
    "equal employment opportunity",
    "applications and related materials",
]

EXCLUDE_SECTION_HINTS = [
    "donor lists - applicable only for nces",
]

WEAK_KEYWORDS = {"nce"}


def _contains_any(text: str, phrases: list[str]) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in phrases)


def _priority_mode() -> str:
    mode = DEFAULT_PUBLIC_FILES_PRIORITY_MODE
    if mode in {"high", "balanced"}:
        return mode
    return "balanced"


def _is_editorially_relevant(
    *,
    text: str,
    matched_call_signs: list[str],
    matched_keywords: list[str],
) -> bool:
    include_hints = INCLUDE_SECTION_HINTS_BALANCED
    if _priority_mode() == "high":
        include_hints = INCLUDE_SECTION_HINTS_HIGH
    include_hit = _contains_any(text, include_hints)
    exclude_hit = _contains_any(text, EXCLUDE_SECTION_HINTS)
    strong_keyword_hit = any(keyword.lower() not in WEAK_KEYWORDS for keyword in matched_keywords)

    if exclude_hit and not include_hit and not strong_keyword_hit:
        return False
    if include_hit:
        return True
    if matched_call_signs and strong_keyword_hit:
        return True
    return False


def _candidate_feed_urls(call_sign: str) -> list[str]:
    # OPIF RSS is station profile specific (not a global /rss endpoint).
    normalized = call_sign.strip().upper()
    base = normalized.split("-", 1)[0].lower()
    profile_types: list[str] = []
    if "-AM" in normalized:
        profile_types = ["am-profile"]
    elif "-FM" in normalized:
        profile_types = ["fm-profile"]
    elif any(suffix in normalized for suffix in ("-TV", "-DT", "-LD", "-CD", "-LP")):
        profile_types = ["tv-profile"]
    else:
        profile_types = ["fm-profile", "tv-profile"]
    return [f"{DEFAULT_PUBLIC_FILES_BASE_URL}/{profile}/{base}/rss" for profile in profile_types]


def _fetch_public_files_rss_xml(url: str) -> str:
    last_error: Exception | None = None
    for attempt in range(DEFAULT_PUBLIC_FILES_RETRIES + 1):
        try:
            req = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(req, timeout=DEFAULT_PUBLIC_FILES_TIMEOUT_SECONDS) as resp:  # nosec B310
                return resp.read().decode("utf-8", errors="ignore")
        except urllib.error.HTTPError as err:
            if err.code in (404, 410):
                return ""
            last_error = err
            if attempt >= DEFAULT_PUBLIC_FILES_RETRIES:
                break
            time.sleep(DEFAULT_PUBLIC_FILES_RETRY_BACKOFF_SECONDS * (attempt + 1))
        except (TimeoutError, socket.timeout, urllib.error.URLError, OSError) as err:
            last_error = err
            if attempt >= DEFAULT_PUBLIC_FILES_RETRIES:
                break
            time.sleep(DEFAULT_PUBLIC_FILES_RETRY_BACKOFF_SECONDS * (attempt + 1))

    detail = f"{type(last_error).__name__}: {last_error}" if last_error else "unknown error"
    raise RuntimeError(f"Public Files RSS request failed after retries: {url} (last_error={detail})") from last_error


def fetch_public_files_rss(
    *,
    cpb_call_signs: set[str],
    extra_keywords: Iterable[str] | None = None,
) -> list[PublicFilesItem]:
    keywords = list(KEYWORDS)
    if extra_keywords:
        keywords.extend(extra_keywords)

    attempted = 0
    successes = 0
    parse_failures = 0
    seen_links: set[str] = set()
    items: list[PublicFilesItem] = []
    errors: list[str] = []

    for call_sign in sorted(cpb_call_signs)[:DEFAULT_PUBLIC_FILES_STATION_LIMIT]:
        for feed_url in _candidate_feed_urls(call_sign):
            attempted += 1
            try:
                content = _fetch_public_files_rss_xml(feed_url)
            except Exception as err:
                errors.append(str(err))
                continue
            if not content.strip():
                continue

            try:
                root = ET.fromstring(content)
            except ET.ParseError:
                parse_failures += 1
                continue
            successes += 1

            ns = {"atom": "http://www.w3.org/2005/Atom"}
            entries = root.findall("./atom:entry", ns)
            for entry in entries:
                title = (entry.findtext("atom:title", default="", namespaces=ns) or "").strip()
                link = (
                    entry.find("atom:link", ns).attrib.get("href", "") if entry.find("atom:link", ns) is not None else ""
                ).strip()
                profile_link = feed_url[:-4] if feed_url.endswith("/rss") else feed_url
                published_at = (entry.findtext("atom:updated", default="", namespaces=ns) or "").strip()
                summary = (entry.findtext("atom:summary", default="", namespaces=ns) or "").strip()
                content_text = (entry.findtext("atom:content", default="", namespaces=ns) or "").strip()
                if not title and not link:
                    continue
                if link and link in seen_links:
                    continue

                text = f"{title}\n{summary}\n{content_text}"
                matched_call_signs = _call_sign_matches(text, cpb_call_signs)
                if call_sign not in matched_call_signs:
                    matched_call_signs = sorted({call_sign, *matched_call_signs})
                matched_keywords = _keyword_matches(text, keywords)
                if not matched_call_signs and not matched_keywords:
                    continue
                if not _is_editorially_relevant(
                    text=text,
                    matched_call_signs=matched_call_signs,
                    matched_keywords=matched_keywords,
                ):
                    continue

                if link:
                    seen_links.add(link)
                items.append(
                    PublicFilesItem(
                        title=title or "(untitled public file item)",
                        link=profile_link,
                        document_link=link,
                        published_at=published_at,
                        source_call_sign=call_sign,
                        source_url=feed_url,
                        matched_call_signs=matched_call_signs,
                        matched_keywords=matched_keywords,
                    )
                )
            # Stop trying other profile candidates for this call sign once one feed works.
            break

    if attempted > 0 and successes == 0:
        detail = errors[-1] if errors else "all candidate feeds returned empty/404"
        raise RuntimeError(
            f"Public Files RSS had no successful station feeds (attempted={attempted}, parse_failures={parse_failures}, last_error={detail})"
        )

    return items
