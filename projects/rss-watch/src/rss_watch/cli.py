from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterable
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from .state import RunState, load_state, save_state
from .slack import post_to_slack, SlackMessage, SlackPostError


USER_AGENT = "Mozilla/5.0 (compatible; CurrentRSSWatch/1.0; +https://current.org)"
TRACKING_QUERY_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "gclid",
    "fbclid",
    "mc_cid",
    "mc_eid",
}

CORE_KEYWORDS = [
    "public broadcasting",
    "public media",
    "public television",
    "public radio",
]

WEAK_BRAND_KEYWORDS = [
    "pbs",
    "npr",
]

CORE_KEYWORDS.extend(WEAK_BRAND_KEYWORDS)

HIGH_SIGNAL_TERMS = [
    "license renewal",
    "station sale",
    "merger",
    "merge",
    "layoff",
    "cuts",
    "funding",
    "federal funding",
    "defund",
    "ceo",
    "gm",
    "general manager",
    "board",
    "board chair",
]

COMMENTARY_TERMS = [
    "commentary",
    "opinion",
    "analysis",
    "editorial",
    "column",
    "argue",
    "argues",
    "case for",
    "case against",
]

PUBLIC_MEDIA_POLICY_TERMS = [
    "editorial independence",
    "local journalism",
    "local news",
    "public service media",
    "federal funding",
    "state funding",
    "public funding",
    "funding cuts",
    "rescission",
    "license",
    "fcc",
    "board",
    "governance",
]

MANAGEMENT_CHANGE_TERMS = [
    "gm",
    "manager",
    "managers",
    "co-manager",
    "co-managers",
    "general manager",
    "board chair",
    "chair",
    "leadership",
    "leadership team",
]

LEADERSHIP_APPOINTMENT_TERMS = [
    "appoint",
    "appoints",
    "appointed",
    "name",
    "names",
    "named",
    "hire",
    "hires",
    "hired",
    "succeed",
    "succeeds",
    "succeeded",
    "promote",
    "promotes",
    "promoted",
]

DEPARTURE_TERMS = [
    "leave",
    "leaves",
    "left",
    "retire",
    "retires",
    "retiring",
    "retirement",
    "resign",
    "resigns",
    "resigned",
    "step down",
    "stepping down",
    "departure",
    "departing",
    "exit",
    "exits",
    "out",
]

STATION_TURMOIL_TERMS = [
    "leadership",
    "employees",
    "escalation",
    "future at stake",
    "sound the alarm",
    "staff sound the alarm",
    "could lose",
    "flagship station",
    "flagship npr station",
    "flagship public radio station",
    "staff warn",
    "warn",
    "uncertainty",
    "turmoil",
    "crisis",
    "walkout",
    "vote of no confidence",
]

STATION_OPERATION_TERMS = [
    "local news",
    "newscast",
    "newscasts",
    "newsroom",
    "studio",
    "studios",
    "broadcast center",
    "production center",
    "remote",
    "remotely",
]

OPERATION_DISRUPTION_TERMS = [
    "close",
    "closes",
    "closing",
    "closure",
    "shut",
    "shuts",
    "shutdown",
    "remote",
    "remotely",
    "end",
    "ends",
    "ending",
]

CRITICAL_TERMS = [
    "funding",
    "federal funding",
    "defund",
    "layoff",
    "cuts",
    "license renewal",
    "station sale",
    "ceo",
    "general manager",
    "board",
    "lawsuit",
    "congress",
    "state budget",
]

US_SIGNAL_TERMS = [
    "u.s.",
    "united states",
    "fcc",
    "cpb",
    "pbs",
    "npr",
    "state budget",
    "congress",
]

NON_US_SIGNAL_TERMS = [
    "armenia",
    "india",
    "malaysia",
    "zambia",
    "europe",
    "eu",
    "bbc",
    "canada",
    "australia",
    "philippines",
    "sahel",
]

ENTERTAINMENT_TERMS = [
    "tiny desk",
    "tiktok",
    "youtube",
    "threads",
    "podcast episode",
    "concert",
    "sundance",
]

NOISE_TERMS = [
    "social media",
    "influencer",
    "public relations",
    "press release",
    "wire service",
    "coupon",
    "black friday",
    "deal",
    "sponsored",
]

LOW_SIGNAL_DOMAINS = {
    "linkedin.com",
    "instagram.com",
    "facebook.com",
    "youtube.com",
    "flickr.com",
    "www.flickr.com",
    "tiktok.com",
    "www.tiktok.com",
    "twitter.com",
    "www.twitter.com",
    "x.com",
    "www.x.com",
}

SUPPRESSED_DOMAINS = {
    "current.org",
}
STOPWORDS = {
    "a",
    "an",
    "the",
    "and",
    "or",
    "to",
    "of",
    "for",
    "on",
    "in",
    "with",
    "at",
    "by",
    "from",
    "is",
    "are",
    "be",
}

_TERM_PATTERN_CACHE: dict[str, re.Pattern[str]] = {}


@dataclass(frozen=True)
class FeedItem:
    feed_title: str
    title: str
    link: str
    summary: str
    published: datetime | None


@dataclass(frozen=True)
class RankedItem:
    item_id: str
    title: str
    link: str
    domain: str
    summary: str
    published: str | None
    score: int
    bucket: str
    reasons: list[str]
    sources: list[str]
    duplicate_count: int
    source_hint_domain: str | None


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        pass
    for candidate in [value.replace("Z", "+00:00"), value]:
        try:
            dt = datetime.fromisoformat(candidate)
            return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _text(elem: ET.Element | None) -> str:
    if elem is None:
        return ""
    return " ".join("".join(elem.itertext()).split())


def _normalize_url(url: str) -> str:
    if not url:
        return ""
    parsed = urllib.parse.urlsplit(url.strip())
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    filtered = [(k, v) for k, v in query if k.lower() not in TRACKING_QUERY_KEYS]
    filtered.sort()
    path = parsed.path.rstrip("/") or "/"
    clean = urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, urllib.parse.urlencode(filtered), ""))
    return clean


def _headline_core(title: str) -> str:
    normalized = (title or "").strip()
    if " - " in normalized:
        normalized = normalized.rsplit(" - ", 1)[0].strip()
    normalized = normalized.lower()
    replacements = {
        "national public radio": "npr",
        "public broadcasting service": "pbs",
        "first amendment violation": "first amendment",
        "citing first amendment": "first amendment",
    }
    for old, new in replacements.items():
        normalized = normalized.replace(old, new)
    return normalized


def _title_fingerprint(title: str) -> str:
    tokens = re.findall(r"[a-z0-9]+", _headline_core(title))
    tokens = [t for t in tokens if t not in STOPWORDS]
    return " ".join(tokens[:12])


def _term_pattern(term: str) -> re.Pattern[str]:
    normalized = term.lower().strip()
    pattern = _TERM_PATTERN_CACHE.get(normalized)
    if pattern is None:
        # Match full terms only, not partial word substrings (e.g. "gm" in "Fragomen").
        pattern = re.compile(rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])")
        _TERM_PATTERN_CACHE[normalized] = pattern
    return pattern


def _has_term(text: str, term: str) -> bool:
    return bool(_term_pattern(term).search(text))


def _has_any_term(text: str, terms: Iterable[str]) -> bool:
    return any(_has_term(text, term) for term in terms)


def _plain_text(value: str) -> str:
    unescaped = html.unescape(value or "")
    without_tags = re.sub(r"<[^>]+>", " ", unescaped)
    return " ".join(without_tags.split())


def _looks_like_named_person(title: str) -> bool:
    tokens = re.findall(r"\b[A-Z][a-z]+(?:[-'][A-Z][a-z]+)?\b", title)
    return len(tokens) >= 2


def _has_us_call_sign(text: str) -> bool:
    for match in re.finditer(r"\b[KW][A-Z]{2,4}(?:-(?:FM|TV|DT|AM|LP|LD|CD))?\b", text):
        token = match.group(0)
        if token in {"WKRP"}:
            continue
        return True
    return False


def _has_substantive_public_media_signal(text: str) -> bool:
    return _has_any_term(
        text,
        CORE_KEYWORDS[:4]
        + WEAK_BRAND_KEYWORDS
        + CRITICAL_TERMS
        + PUBLIC_MEDIA_POLICY_TERMS
        + ["station", "newsroom"],
    )


def _is_public_media_commentary(text: str) -> bool:
    has_public_media = _has_any_term(text, CORE_KEYWORDS[:4] + WEAK_BRAND_KEYWORDS)
    has_commentary = _has_any_term(text, COMMENTARY_TERMS)
    has_policy_or_operation = _has_any_term(text, PUBLIC_MEDIA_POLICY_TERMS + CRITICAL_TERMS)
    return has_public_media and (has_commentary or has_policy_or_operation)


def _iter_outline_nodes(root: ET.Element) -> Iterable[ET.Element]:
    for node in root.findall(".//outline"):
        if node.get("xmlUrl"):
            yield node


def load_feed_urls_from_opml(opml_path: Path) -> list[tuple[str, str]]:
    root = ET.fromstring(opml_path.read_text(encoding="utf-8"))
    feeds: list[tuple[str, str]] = []
    seen: set[str] = set()
    for node in _iter_outline_nodes(root):
        url = (node.get("xmlUrl") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        title = (node.get("title") or node.get("text") or url).strip()
        feeds.append((title, url))
    return feeds


def _fetch_url(url: str, timeout_seconds: int, retries: int) -> str:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(req, timeout=timeout_seconds) as resp:  # nosec B310
                return resp.read().decode("utf-8", errors="ignore")
        except (TimeoutError, urllib.error.URLError, OSError) as err:
            last_error = err
            if attempt >= retries:
                break
            time.sleep(1.25 * (attempt + 1))
    raise RuntimeError(f"Feed fetch failed after retries: {url}") from last_error


def _choose_atom_link(entry: ET.Element) -> str:
    for link in entry.findall("{*}link"):
        rel = (link.get("rel") or "alternate").lower()
        href = (link.get("href") or "").strip()
        if href and rel == "alternate":
            return href
    for link in entry.findall("{*}link"):
        href = (link.get("href") or "").strip()
        if href:
            return href
    return ""


def _parse_feed_xml(xml_text: str, default_feed_title: str) -> list[FeedItem]:
    root = ET.fromstring(xml_text)
    items: list[FeedItem] = []
    if root.tag.lower().endswith("rss") or root.tag.lower().endswith("rdf"):
        channel = root.find("./channel")
        feed_title = _text(channel.find("title")) if channel is not None else default_feed_title
        for item in root.findall(".//item"):
            title = _text(item.find("title"))
            link = _text(item.find("link"))
            summary = _text(item.find("description"))
            published = _parse_datetime(_text(item.find("pubDate")) or _text(item.find("dc:date")))
            if title or link:
                items.append(
                    FeedItem(
                        feed_title=feed_title or default_feed_title,
                        title=title or "(no title)",
                        link=link,
                        summary=summary,
                        published=published,
                    )
                )
        return items

    feed_title = _text(root.find("{*}title")) or default_feed_title
    for entry in root.findall("{*}entry"):
        title = _text(entry.find("{*}title"))
        link = _choose_atom_link(entry)
        summary = _text(entry.find("{*}summary")) or _text(entry.find("{*}content"))
        published = _parse_datetime(_text(entry.find("{*}published")) or _text(entry.find("{*}updated")))
        if title or link:
            items.append(
                FeedItem(
                    feed_title=feed_title or default_feed_title,
                    title=title or "(no title)",
                    link=link,
                    summary=summary,
                    published=published,
                )
            )
    return items


def _score_item(title: str, summary: str, domain: str, published: datetime | None) -> tuple[int, list[str]]:
    raw_text = _plain_text(f"{title} {summary}")
    text = raw_text.lower()
    title_text = title.lower()
    score = 0
    reasons: list[str] = []
    core_public_hits = [term for term in CORE_KEYWORDS[:4] if _has_term(text, term)]
    weak_brand_hits = [term for term in WEAK_BRAND_KEYWORDS if _has_term(text, term)]
    us_term_hit = _has_any_term(text, US_SIGNAL_TERMS)
    us_callsign_hit = _has_us_call_sign(raw_text)
    station_operation_disruption = _has_any_term(text, STATION_OPERATION_TERMS) and _has_any_term(
        text, OPERATION_DISRUPTION_TERMS
    )

    # Promote "community radio" only when it appears in headline/title with U.S. station context.
    if _has_term(title_text, "community radio"):
        us_community_station_context = us_term_hit or us_callsign_hit or _has_any_term(
            text, ["community radio station", "community radio stations"]
        )
        if us_community_station_context:
            score += 5
            reasons.append("headline:community radio")
            reasons.append("us-community-radio-context")
        elif _has_any_term(text, NON_US_SIGNAL_TERMS):
            score -= 4
            reasons.append("non-us-community-radio-context")

    for term in CORE_KEYWORDS:
        if _has_term(text, term):
            # Core public-media phrases should generally surface on their own.
            score += 4 if term in CORE_KEYWORDS[:4] else 2
            reasons.append(f"keyword:{term}")
    for term in HIGH_SIGNAL_TERMS:
        if _has_term(text, term):
            score += 2
            reasons.append(f"high-signal:{term}")
    if _is_public_media_commentary(text):
        score += 2
        reasons.append("public-media-commentary")
    if core_public_hits and _has_any_term(text, MANAGEMENT_CHANGE_TERMS) and _has_any_term(text, DEPARTURE_TERMS):
        score += 3
        reasons.append("public-media-management-change")
    if core_public_hits and _has_any_term(text, LEADERSHIP_APPOINTMENT_TERMS) and _looks_like_named_person(title):
        score += 2
        reasons.append("public-media-leadership-appointment")
    if us_callsign_hit and _has_any_term(text, MANAGEMENT_CHANGE_TERMS) and _has_any_term(text, DEPARTURE_TERMS):
        score += 5
        reasons.append("callsign+management-departure")
    if us_callsign_hit and _looks_like_named_person(title) and _has_any_term(text, DEPARTURE_TERMS):
        score += 5
        reasons.append("callsign+named-person-departure")
    if us_callsign_hit and _has_any_term(text, STATION_TURMOIL_TERMS):
        score += 5
        reasons.append("callsign+station-turmoil")
    if weak_brand_hits and station_operation_disruption:
        score += 6
        reasons.append("weak-brand+station-operations-disruption")
    for term in NOISE_TERMS:
        if _has_term(text, term):
            score -= 2
            reasons.append(f"noise:{term}")
    for term in ENTERTAINMENT_TERMS:
        if _has_term(text, term):
            score -= 2
            reasons.append(f"entertainment:{term}")
    if domain in LOW_SIGNAL_DOMAINS:
        score -= 2
        reasons.append("low-signal-domain")
    if re.fullmatch(r"\d{8}_\d+(?:\(\d+\))?", title.strip()):
        score -= 4
        reasons.append("likely-media-dump-title")
    strong_or_critical = _has_any_term(text, CORE_KEYWORDS[:4] + CRITICAL_TERMS + PUBLIC_MEDIA_POLICY_TERMS + ["station"])
    strong_or_critical = strong_or_critical or station_operation_disruption
    if weak_brand_hits and not strong_or_critical:
        score -= 3
        reasons.append("weak-brand-only")

    if core_public_hits:
        non_us_term_hit = _has_any_term(text, NON_US_SIGNAL_TERMS)
        if non_us_term_hit and not (us_term_hit or us_callsign_hit):
            score -= 6
            reasons.append("non-us-public-media-context")

    if " - " in title_text:
        head, tail = title_text.rsplit(" - ", 1)
        if _has_any_term(tail, CORE_KEYWORDS[:4]) and not _has_any_term(head, CORE_KEYWORDS[:4]) and not strong_or_critical:
            score -= 3
            reasons.append("attribution-only-core-term")

    if score >= 8:
        bucket = "high"
    elif score >= 5:
        bucket = "maybe"
    else:
        bucket = "low"
    return score, [bucket] + reasons


def _bucket_for_score(score: int) -> str:
    if score >= 8:
        return "high"
    if score >= 5:
        return "maybe"
    return "low"


def _item_id(normalized_link: str, title: str, domain: str, published: datetime | None) -> str:
    if normalized_link:
        return normalized_link
    stamp = published.isoformat() if published else ""
    return hashlib.sha1(f"{title}|{domain}|{stamp}".encode("utf-8")).hexdigest()


def _source_hint_domain_from_title(title: str) -> str | None:
    if " - " not in title:
        return None
    tail = title.rsplit(" - ", 1)[-1].strip().lower()
    if re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", tail):
        return tail
    return None


def _canonical_domain(domain: str) -> str:
    domain = (domain or "").lower().strip()
    if domain.startswith("www."):
        return domain[4:]
    return domain


def _should_use_headline_level_dedupe(domain: str, source_hint_domain: str | None) -> bool:
    canonical_domain = _canonical_domain(domain)
    canonical_hint = _canonical_domain(source_hint_domain or "")
    return canonical_domain == "news.google.com" or canonical_hint in LOW_SIGNAL_DOMAINS


def dedupe_and_rank(
    items: list[FeedItem],
    since: datetime,
    include_seen: bool,
    seen_ids: set[str],
    max_items: int,
    max_item_age_days: int,
) -> tuple[list[RankedItem], list[RankedItem], dict[str, int]]:
    dedup: dict[str, dict] = {}
    now = datetime.now(timezone.utc)

    for raw in items:
        published = raw.published
        if published and published < since:
            continue
        if max_item_age_days > 0 and published and published < (now - timedelta(days=max_item_age_days)):
            continue
        normalized_link = _normalize_url(raw.link)
        domain = urllib.parse.urlsplit(normalized_link or raw.link).netloc.lower()
        if _canonical_domain(domain) in SUPPRESSED_DOMAINS:
            continue
        source_hint_domain = _source_hint_domain_from_title(raw.title)
        fp = _title_fingerprint(raw.title)
        if _should_use_headline_level_dedupe(domain, source_hint_domain) and fp:
            dedupe_key = f"headline|{fp}"
        else:
            dedupe_key = normalized_link or f"{domain}|{fp}"
        item_id = _item_id(normalized_link, raw.title, domain, published)
        if not include_seen and item_id in seen_ids:
            continue

        score, reasons = _score_item(raw.title, raw.summary, domain, published)
        text = _plain_text(f"{raw.title} {raw.summary}").lower()
        if source_hint_domain in LOW_SIGNAL_DOMAINS:
            if _is_public_media_commentary(text):
                score -= 1
                reasons.append(f"social-source-commentary:{source_hint_domain}")
            elif not _has_substantive_public_media_signal(text):
                score -= 5
                reasons.append(f"weak-social-source:{source_hint_domain}")
            else:
                score -= 3
                reasons.append(f"social-source:{source_hint_domain}")
        bucket = _bucket_for_score(score)
        entry = dedup.get(dedupe_key)
        if entry is None:
            dedup[dedupe_key] = {
                "item_id": item_id,
                "title": raw.title,
                "link": normalized_link or raw.link,
                "domain": domain,
                "summary": raw.summary,
                "published": published,
                "score": score,
                "bucket": bucket,
                "reasons": reasons[1:],
                "sources": [raw.feed_title],
                "duplicate_count": 0,
                "source_hint_domain": source_hint_domain,
            }
            continue
        entry["duplicate_count"] += 1
        if raw.feed_title not in entry["sources"]:
            entry["sources"].append(raw.feed_title)
        if published and (entry["published"] is None or published > entry["published"]):
            entry["published"] = published
        if score > entry["score"]:
            entry["score"] = score
            entry["bucket"] = bucket
            entry["reasons"] = reasons[1:]
            entry["source_hint_domain"] = source_hint_domain

    all_ranked = sorted(
        dedup.values(),
        key=lambda i: (
            {"high": 3, "maybe": 2, "low": 1}.get(i["bucket"], 0),
            i["score"],
            i["published"] or now - timedelta(days=3650),
        ),
        reverse=True,
    )
    ranked = all_ranked[:max_items]

    result = [
        RankedItem(
            item_id=i["item_id"],
            title=i["title"],
            link=i["link"],
            domain=i["domain"],
            summary=i["summary"],
            published=i["published"].isoformat() if i["published"] else None,
            score=i["score"],
            bucket=i["bucket"],
            reasons=i["reasons"],
            sources=i["sources"],
            duplicate_count=i["duplicate_count"],
            source_hint_domain=i.get("source_hint_domain"),
        )
        for i in ranked
    ]
    all_result = [
        RankedItem(
            item_id=i["item_id"],
            title=i["title"],
            link=i["link"],
            domain=i["domain"],
            summary=i["summary"],
            published=i["published"].isoformat() if i["published"] else None,
            score=i["score"],
            bucket=i["bucket"],
            reasons=i["reasons"],
            sources=i["sources"],
            duplicate_count=i["duplicate_count"],
            source_hint_domain=i.get("source_hint_domain"),
        )
        for i in all_ranked
    ]

    counts = {"high": 0, "maybe": 0, "low": 0}
    for item in result:
        counts[item.bucket] += 1
    return result, all_result, counts


def _render_briefing(
    mode: str,
    since: datetime,
    items: list[RankedItem],
    counts: dict[str, int],
    include_low: bool,
    failures: list[dict[str, str]],
) -> str:
    lines = [
        f"# RSS Watch Briefing ({mode})",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        f"Window start (UTC): {since.isoformat()}",
        f"Counts: high={counts['high']} maybe={counts['maybe']} low={counts['low']}",
        "",
    ]
    for bucket in ["high", "maybe", "low"]:
        if bucket == "low" and not include_low:
            continue
        bucket_items = [i for i in items if i.bucket == bucket]
        if not bucket_items:
            continue
        lines.append(f"## {bucket.title()} Priority")
        for item in bucket_items:
            dup = f" dupes:{item.duplicate_count}" if item.duplicate_count else ""
            sources = f" [{', '.join(item.sources[:3])}]" if item.sources else ""
            source_hint = f" src:{item.source_hint_domain}" if item.source_hint_domain else ""
            lines.append(f"- {item.title} ({item.domain}) score:{item.score}{dup}{source_hint}{sources}")
            lines.append(f"  {item.link}")
        lines.append("")
    if failures:
        lines.append("## Feed Failures")
        for failure in failures[:20]:
            lines.append(f"- {failure['feed']}: {failure['error']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_slack_text(mode: str, counts: dict[str, int], items: list[RankedItem], failures: list[dict[str, str]], max_items: int) -> str:
    lines = [f"RSS Watch — {datetime.now(timezone.utc):%Y-%m-%d} ({mode})"]

    selected = [item for item in items if item.bucket in {"high", "maybe"}][:max_items]
    if selected:
        lines.append("")
        lines.append("Top items")
        for item in selected:
            lines.append(f"- <{item.link}|{item.title}> ({item.domain})")
    else:
        lines.append("")
        lines.append("No high/maybe items in this run.")

    return "\n".join(lines)


def run(args: argparse.Namespace) -> dict:
    opml_path = Path(args.opml)
    state_path = Path(args.state)
    out_path = Path(args.out)
    brief_path = Path(args.brief)
    candidates_path = Path(args.candidates_out)

    state = load_state(state_path)
    now = datetime.now(timezone.utc)
    if args.mode == "update" and state.last_checked:
        since = state.last_checked.astimezone(timezone.utc)
    else:
        since = now - timedelta(hours=args.window_hours)

    feeds = load_feed_urls_from_opml(opml_path)
    if args.max_feeds is not None:
        feeds = feeds[: args.max_feeds]
    all_items: list[FeedItem] = []
    failures: list[dict[str, str]] = []

    def _fetch_and_parse(feed_title: str, feed_url: str) -> tuple[list[FeedItem], dict[str, str] | None]:
        try:
            xml_text = _fetch_url(feed_url, timeout_seconds=args.feed_timeout_seconds, retries=args.feed_retries)
            return _parse_feed_xml(xml_text, default_feed_title=feed_title), None
        except Exception as err:
            return [], {"feed": feed_title, "url": feed_url, "error": str(err)}

    with ThreadPoolExecutor(max_workers=args.parallelism) as pool:
        future_map = {pool.submit(_fetch_and_parse, title, url): (title, url) for title, url in feeds}
        for future in as_completed(future_map):
            items, failure = future.result()
            if items:
                all_items.extend(items)
            if failure:
                failures.append(failure)

    ranked, all_ranked, counts = dedupe_and_rank(
        items=all_items,
        since=since,
        include_seen=args.include_seen,
        seen_ids=set(state.seen_ids),
        max_items=args.max_items,
        max_item_age_days=args.max_item_age_days,
    )

    payload = {
        "mode": args.mode,
        "window_start_utc": since.isoformat(),
        "feed_count": len(feeds),
        "fetch_failures": failures,
        "candidate_count": len(all_ranked),
        "counts": counts,
        "items": [asdict(item) for item in ranked],
        "slack_status": "not_attempted",
        "slack_error": "",
        "state_updated": False,
    }
    candidates_payload = {
        "mode": args.mode,
        "window_start_utc": since.isoformat(),
        "feed_count": len(feeds),
        "fetch_failures": failures,
        "counts_before_trim": {
            "high": sum(1 for item in all_ranked if item.bucket == "high"),
            "maybe": sum(1 for item in all_ranked if item.bucket == "maybe"),
            "low": sum(1 for item in all_ranked if item.bucket == "low"),
        },
        "candidate_count": len(all_ranked),
        "max_items": args.max_items,
        "items": [asdict(item) for item in all_ranked],
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    candidates_path.parent.mkdir(parents=True, exist_ok=True)
    candidates_path.write_text(json.dumps(candidates_payload, indent=2, sort_keys=True), encoding="utf-8")

    briefing = _render_briefing(args.mode, since, ranked, counts, args.include_low, failures)
    brief_path.parent.mkdir(parents=True, exist_ok=True)
    brief_path.write_text(briefing, encoding="utf-8")

    if args.dry_run:
        payload["slack_status"] = "dry_run"
        out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return payload

    if args.slack_webhook:
        slack_text = _render_slack_text(args.mode, counts, ranked, failures, args.slack_max_items)
        try:
            post_to_slack(args.slack_webhook, SlackMessage(text=slack_text))
        except SlackPostError as err:
            payload["slack_status"] = "failed"
            payload["slack_error"] = str(err)
            out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
            print(f"rss-watch: Slack post failed: {err}")
            return payload
        payload["slack_status"] = "sent"

    state.seen_ids.extend([item.item_id for item in ranked])
    state.seen_ids = state.seen_ids[-8000:]
    state.last_checked = now
    save_state(state_path, state)
    payload["state_updated"] = True
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RSS Watch")
    parser.add_argument("--opml", required=True, help="Path to OPML feed export")
    parser.add_argument("--mode", choices=["morning", "update"], default="morning")
    parser.add_argument("--window-hours", type=int, default=24)
    parser.add_argument("--state", default="output/state.json")
    parser.add_argument("--out", default="output/last-run.json")
    parser.add_argument("--candidates-out", default="output/candidates.json")
    parser.add_argument("--brief", default="output/briefing.md")
    parser.add_argument("--max-items", type=int, default=200)
    parser.add_argument(
        "--max-item-age-days",
        type=int,
        default=30,
        help="Exclude items older than this many days to avoid stale resurfaced feed items (0 disables).",
    )
    parser.add_argument("--include-low", action="store_true")
    parser.add_argument("--include-seen", action="store_true")
    parser.add_argument("--feed-timeout-seconds", type=int, default=int(os.environ.get("RSS_WATCH_TIMEOUT_SECONDS", "20")))
    parser.add_argument("--feed-retries", type=int, default=int(os.environ.get("RSS_WATCH_RETRIES", "1")))
    parser.add_argument("--parallelism", type=int, default=int(os.environ.get("RSS_WATCH_PARALLELISM", "8")))
    parser.add_argument("--max-feeds", type=int, help="Optional cap for feed fetches (first N feeds in OPML)")
    parser.add_argument("--slack-webhook", default=os.environ.get("SLACK_WEBHOOK_URL"))
    parser.add_argument("--slack-max-items", type=int, default=10)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
