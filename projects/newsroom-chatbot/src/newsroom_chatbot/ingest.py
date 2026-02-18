import argparse
from collections import deque
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import time
from typing import Iterable
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup

from newsroom_chatbot.db import connect, upsert_article
from newsroom_chatbot.text_utils import normalize_whitespace

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

COMMENT_SECTION_SELECTORS = (
    "#comments",
    ".comments",
    ".comment-list",
    ".comment-respond",
    ".wp-block-comments",
    ".wpd-comments",
    ".wpd-comment-content",
    "[data-component='comments']",
)

CORRECTION_SECTION_SELECTORS = (
    ".correction",
    ".corrections",
    ".entry-corrections",
    ".entry-correction",
    "[class*='correction']",
    "[id*='correction']",
)

CORRECTION_PREFIX_RE = re.compile(
    r"(?i)^\s*(correction|corrections|clarification|editor(?:'|’)s note)\s*[:\-]"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest articles from sitemap into SQLite")
    parser.add_argument("--sitemap-url", required=True, help="Root sitemap XML URL")
    parser.add_argument("--db", default="output/newsroom.db", help="SQLite DB path")
    parser.add_argument("--max-urls", type=int, default=0, help="Optional URL cap for trial runs")
    parser.add_argument(
        "--url-contains",
        default="",
        help="Optional substring filter, e.g. /2025/",
    )
    parser.add_argument(
        "--same-domain-only",
        action="store_true",
        help="Only include URLs matching the sitemap hostname",
    )
    parser.add_argument("--min-chars", type=int, default=300, help="Minimum extracted text length")
    parser.add_argument(
        "--fetch-retries",
        type=int,
        default=4,
        help="Retries for transient HTTP/network failures",
    )
    parser.add_argument(
        "--fetch-backoff-seconds",
        type=float,
        default=1.5,
        help="Base seconds for exponential retry backoff",
    )
    parser.add_argument(
        "--skip-log",
        default="output/ingest-skips.jsonl",
        help="Where to write skip diagnostics (JSONL)",
    )
    return parser.parse_args()


def fetch_text(
    url: str,
    timeout: int = 25,
    *,
    retries: int = 4,
    backoff_seconds: float = 1.5,
) -> tuple[str, str]:
    for attempt in range(retries + 1):
        try:
            response = requests.get(url, timeout=timeout, headers=REQUEST_HEADERS, allow_redirects=True)
            status = response.status_code
            if status == 429 or 500 <= status < 600:
                raise requests.HTTPError(
                    f"{status} from {url}",
                    response=response,
                )
            response.raise_for_status()
            return response.text, str(response.url)
        except (requests.RequestException, requests.HTTPError):
            if attempt >= retries:
                raise
            delay = backoff_seconds * (2**attempt)
            time.sleep(delay)
    raise RuntimeError("unreachable")


def _extract_loc_text(loc_node) -> str | None:
    if loc_node is None or loc_node.text is None:
        return None
    value = loc_node.text.strip()
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return value


def parse_sitemap_urls(sitemap_xml: str) -> tuple[list[str], list[str]]:
    root = ElementTree.fromstring(sitemap_xml)
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    sitemap_urls = [
        value
        for value in (_extract_loc_text(loc) for loc in root.findall(".//sm:sitemap/sm:loc", ns))
        if value
    ]
    page_urls = [
        value
        for value in (_extract_loc_text(loc) for loc in root.findall(".//sm:url/sm:loc", ns))
        if value
    ]
    return sitemap_urls, page_urls


def walk_sitemaps(root_sitemap_url: str, max_urls: int, *, retries: int, backoff_seconds: float) -> list[str]:
    queue: deque[str] = deque([root_sitemap_url])
    seen_sitemaps: set[str] = set()
    seen_pages: set[str] = set()
    page_urls: list[str] = []

    while queue:
        sitemap_url = queue.popleft()
        if sitemap_url in seen_sitemaps:
            continue
        seen_sitemaps.add(sitemap_url)

        try:
            xml_text, _ = fetch_text(sitemap_url, retries=retries, backoff_seconds=backoff_seconds)
        except Exception as exc:
            print(f"warn: failed sitemap {sitemap_url}: {exc}")
            continue

        child_sitemaps, pages = parse_sitemap_urls(xml_text)
        for child in child_sitemaps:
            if child not in seen_sitemaps:
                queue.append(child)

        for page in pages:
            if page in seen_pages:
                continue
            seen_pages.add(page)
            page_urls.append(page)
            if max_urls and len(page_urls) >= max_urls:
                return page_urls

    return page_urls


def extract_article(html: str, fallback_url: str) -> dict[str, str | None]:
    soup = BeautifulSoup(html, "html.parser")
    for node in soup.select("script,style,noscript,nav,footer,aside,form"):
        node.decompose()
    for selector in COMMENT_SECTION_SELECTORS:
        for node in soup.select(selector):
            node.decompose()

    title = ""
    title_tag = soup.find("meta", property="og:title")
    if title_tag and title_tag.get("content"):
        title = title_tag["content"].strip()
    if not title and soup.title and soup.title.string:
        title = soup.title.string.strip()

    published_at = None
    pub_tag = soup.find("meta", property="article:published_time")
    if pub_tag and pub_tag.get("content"):
        published_at = pub_tag["content"].strip()

    author = None
    author_tag = soup.find("meta", attrs={"name": "author"})
    if author_tag and author_tag.get("content"):
        author = author_tag["content"].strip()

    candidates = [
        soup.select_one("article"),
        soup.select_one("main article"),
        soup.select_one("[itemprop='articleBody']"),
        soup.select_one(".entry-content"),
        soup.select_one(".post-content"),
        soup.select_one(".wp-block-post-content"),
        soup.select_one(".article-content"),
        soup.select_one("main"),
        soup.body,
    ]
    best_text = ""
    for node in candidates:
        if node is None:
            continue
        candidate_text = normalize_whitespace(node.get_text(" ", strip=True))
        if len(candidate_text) > len(best_text):
            best_text = candidate_text
    if not best_text:
        text = ""
    else:
        text = best_text
    correction_notes = extract_correction_notes(soup)
    if correction_notes:
        missing_notes = [note for note in correction_notes if note.lower() not in text.lower()]
        if missing_notes:
            text = f"{text}\n\nCorrection notes: {' '.join(missing_notes)}".strip()

    canonical_url = fallback_url
    canonical_tag = soup.find("link", rel="canonical")
    if canonical_tag and canonical_tag.get("href"):
        canonical_url = urljoin(fallback_url, canonical_tag["href"].strip())

    return {
        "url": canonical_url,
        "title": title or canonical_url,
        "published_at": published_at,
        "author": author,
        "text": text,
    }


def extract_correction_notes(soup: BeautifulSoup) -> list[str]:
    notes: list[str] = []
    seen: set[str] = set()

    def push(value: str) -> None:
        cleaned = normalize_whitespace(value)
        if len(cleaned) < 20:
            return
        key = cleaned.lower()
        if key in seen:
            return
        seen.add(key)
        notes.append(cleaned)

    for selector in CORRECTION_SECTION_SELECTORS:
        for node in soup.select(selector):
            push(node.get_text(" ", strip=True))

    correction_text_nodes = soup.select("article p, main p, .entry-content p, .post-content p")
    for node in correction_text_nodes:
        text = node.get_text(" ", strip=True)
        lower_text = text.lower()
        if CORRECTION_PREFIX_RE.match(text) or "was corrected" in lower_text:
            push(text)

    return notes


def filter_urls(
    urls: Iterable[str],
    *,
    url_contains: str,
    same_domain_only: bool,
    root_sitemap_url: str,
) -> list[str]:
    root_domain = urlparse(root_sitemap_url).netloc.lower()
    output: list[str] = []
    seen: set[str] = set()
    for url in urls:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            continue
        normalized = url.strip()
        if not normalized:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        if url_contains and url_contains not in url:
            continue
        if same_domain_only and urlparse(url).netloc.lower() != root_domain:
            continue
        output.append(normalized)
    return output


def main() -> None:
    args = parse_args()
    conn = connect(args.db)

    all_urls = walk_sitemaps(
        args.sitemap_url,
        args.max_urls,
        retries=args.fetch_retries,
        backoff_seconds=args.fetch_backoff_seconds,
    )
    page_urls = filter_urls(
        all_urls,
        url_contains=args.url_contains,
        same_domain_only=args.same_domain_only,
        root_sitemap_url=args.sitemap_url,
    )

    fetched_at = datetime.now(UTC).isoformat()
    saved = 0
    skipped = 0
    skip_log_path = args.skip_log
    Path(skip_log_path).parent.mkdir(parents=True, exist_ok=True)
    for idx, url in enumerate(page_urls, start=1):
        try:
            html, final_url = fetch_text(
                url,
                retries=args.fetch_retries,
                backoff_seconds=args.fetch_backoff_seconds,
            )
            article = extract_article(html, final_url)
            text_len = len(article["text"] or "")
            if text_len < args.min_chars:
                skipped += 1
                print(f"skip {idx}/{len(page_urls)}: too short ({text_len} chars) {url}")
                with open(skip_log_path, "a", encoding="utf-8") as skip_file:
                    skip_file.write(
                        json.dumps(
                            {
                                "url": url,
                                "final_url": final_url,
                                "title": article["title"],
                                "reason": "too_short",
                                "chars": text_len,
                                "sample": (article["text"] or "")[:220],
                            },
                            ensure_ascii=True,
                        )
                        + "\n"
                    )
                continue
            upsert_article(
                conn,
                url=str(article["url"]),
                title=str(article["title"]),
                published_at=article["published_at"],
                author=article["author"],
                text=str(article["text"]),
                fetched_at=fetched_at,
            )
            conn.commit()
            saved += 1
            print(f"ok   {idx}/{len(page_urls)}: {article['title']}")
        except Exception as exc:
            print(f"warn {idx}/{len(page_urls)}: {url} ({exc})")

    conn.close()
    print(f"done: saved={saved} skipped={skipped} scanned={len(page_urls)}")


if __name__ == "__main__":
    main()
