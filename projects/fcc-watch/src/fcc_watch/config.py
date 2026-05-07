from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


DAILY_DIGEST_BASE_URL = "https://www.fcc.gov/edocs/daily-digest"
PUBLIC_FILES_BASE_URL = "https://publicfiles.fcc.gov"
ECFS_BASE_URL = "https://www.fcc.gov/ecfs/search/api/filings"
MEETING_BASE_URL = "https://www.fcc.gov/news-events/events"
USER_AGENT = "Mozilla/5.0 (compatible; CurrentFCCWatch/1.0; +https://current.org)"

KEYWORDS = [
    "public broadcasting",
    "public television",
    "public radio",
    "npr",
    "nce",
    "pbs",
]

CATEGORY_KEYWORDS = {
    "auctions": ["auction", "auctions"],
    "enforcement": ["enforcement bureau", "notice of apparent liability", "forfeiture"],
    "rulemakings": ["nprm", "notice of proposed rulemaking", "order", "report and order"],
    "public notices": ["public notice"],
}

STOPWORDS = {
    "the",
    "and",
    "of",
    "for",
    "inc",
    "inc.",
    "incorporated",
    "corp",
    "corp.",
    "corporation",
    "company",
    "co",
    "co.",
    "llc",
    "l.l.c.",
    "ltd",
    "ltd.",
}


@dataclass(frozen=True)
class Paths:
    project_root: Path
    output_dir: Path
    cache_dir: Path


DEFAULT_LOOKBACK_DAYS = 2
DEFAULT_DIGEST_TIMEOUT_SECONDS = int(os.environ.get("FCC_WATCH_DIGEST_TIMEOUT_SECONDS", "20"))
DEFAULT_DIGEST_RETRIES = int(os.environ.get("FCC_WATCH_DIGEST_RETRIES", "1"))
DEFAULT_DIGEST_RETRY_BACKOFF_SECONDS = float(os.environ.get("FCC_WATCH_DIGEST_RETRY_BACKOFF_SECONDS", "1.5"))
DEFAULT_DIGEST_MAX_CATCHUP_DAYS = int(os.environ.get("FCC_WATCH_DIGEST_MAX_CATCHUP_DAYS", "14"))
DEFAULT_DIGEST_BASE_URLS = [
    part.strip()
    for part in os.environ.get("FCC_WATCH_DIGEST_BASE_URLS", DAILY_DIGEST_BASE_URL).split(",")
    if part.strip()
]
DEFAULT_PUBLIC_FILES_BASE_URL = os.environ.get("FCC_WATCH_PUBLIC_FILES_BASE_URL", PUBLIC_FILES_BASE_URL).strip()
DEFAULT_PUBLIC_FILES_TIMEOUT_SECONDS = int(os.environ.get("FCC_WATCH_PUBLIC_FILES_TIMEOUT_SECONDS", "20"))
DEFAULT_PUBLIC_FILES_RETRIES = int(os.environ.get("FCC_WATCH_PUBLIC_FILES_RETRIES", "1"))
DEFAULT_PUBLIC_FILES_RETRY_BACKOFF_SECONDS = float(
    os.environ.get("FCC_WATCH_PUBLIC_FILES_RETRY_BACKOFF_SECONDS", "1.5")
)
DEFAULT_PUBLIC_FILES_STATION_LIMIT = int(os.environ.get("FCC_WATCH_PUBLIC_FILES_STATION_LIMIT", "120"))
DEFAULT_PUBLIC_FILES_PRIORITY_MODE = os.environ.get("FCC_WATCH_PUBLIC_FILES_PRIORITY_MODE", "balanced").strip().lower()
DEFAULT_ECFS_BASE_URLS = [
    part.strip()
    for part in os.environ.get("FCC_WATCH_ECFS_BASE_URLS", ECFS_BASE_URL).split(",")
    if part.strip()
]
DEFAULT_ECFS_TIMEOUT_SECONDS = int(os.environ.get("FCC_WATCH_ECFS_TIMEOUT_SECONDS", "20"))
DEFAULT_ECFS_RETRIES = int(os.environ.get("FCC_WATCH_ECFS_RETRIES", "1"))
DEFAULT_ECFS_RETRY_BACKOFF_SECONDS = float(os.environ.get("FCC_WATCH_ECFS_RETRY_BACKOFF_SECONDS", "1.5"))
DEFAULT_ECFS_LIMIT = int(os.environ.get("FCC_WATCH_ECFS_LIMIT", "200"))
DEFAULT_ECFS_DOCKETS_FALLBACK = [
    "MB Docket",
    "MM Docket",
    "noncommercial educational",
    "public broadcasting",
    "public radio",
    "public television",
    "sponsorship identification",
    "political file",
]
DEFAULT_ECFS_DOCKETS = [
    part.strip()
    for part in os.environ.get("FCC_WATCH_ECFS_DOCKETS", ",".join(DEFAULT_ECFS_DOCKETS_FALLBACK)).split(",")
    if part.strip()
]
DEFAULT_MEETING_BASE_URLS = [
    part.strip()
    for part in os.environ.get(
        "FCC_WATCH_MEETING_BASE_URLS",
        ",".join(
            [
                "https://www.fcc.gov/news-events/events/open-commission-meeting",
                MEETING_BASE_URL,
                "https://www.fcc.gov/circulation",
            ]
        ),
    ).split(",")
    if part.strip()
]
DEFAULT_MEETING_TIMEOUT_SECONDS = int(os.environ.get("FCC_WATCH_MEETING_TIMEOUT_SECONDS", "20"))
DEFAULT_MEETING_RETRIES = int(os.environ.get("FCC_WATCH_MEETING_RETRIES", "1"))
DEFAULT_MEETING_RETRY_BACKOFF_SECONDS = float(os.environ.get("FCC_WATCH_MEETING_RETRY_BACKOFF_SECONDS", "1.5"))
DEFAULT_MEETING_MAX_ITEMS = int(os.environ.get("FCC_WATCH_MEETING_MAX_ITEMS", "250"))
DEFAULT_PREFLIGHT_TIMEOUT_SECONDS = float(os.environ.get("FCC_WATCH_PREFLIGHT_TIMEOUT_SECONDS", "4"))
DEFAULT_PREFLIGHT_RETRIES = int(os.environ.get("FCC_WATCH_PREFLIGHT_RETRIES", "0"))
