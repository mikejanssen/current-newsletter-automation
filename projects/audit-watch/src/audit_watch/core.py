from __future__ import annotations

import csv
import html
import json
import re
import ssl
import subprocess
import time
from base64 import b64decode
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, quote_plus, unquote, urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen

from .models import AuditDocument, StationRecord


class ValidationError(Exception):
    pass


REQUIRED_STATION_COLUMNS = {"station_id", "station_name"}
DOC_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx"}
IGNORABLE_DOCUMENT_HOSTS = {
    "mediad.publicbroadcasting.net",
    "ww2.kedm.org",
}
DEFAULT_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

# Some station sites block bare-bones clients on deeper paths unless requests
# look more like a full browser navigation.
FALLBACK_REQUEST_HEADERS = {
    **DEFAULT_REQUEST_HEADERS,
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Referer": "https://www.google.com/",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}
AUDIT_HINTS = (
    "audit",
    "financial statement",
    "financial report",
    "single audit",
    "independent auditor",
    "annual report",
    "cafr",
    "acfr",
)
UNUSUAL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bmaterial weakness(?:es)?\b", re.IGNORECASE), "Material weakness noted"),
    (re.compile(r"\bsignificant deficienc(?:y|ies)\b", re.IGNORECASE), "Significant deficiency noted"),
    (re.compile(r"\bgoing concern\b", re.IGNORECASE), "Going concern language"),
    (re.compile(r"\bqualified opinion\b", re.IGNORECASE), "Qualified audit opinion"),
    (re.compile(r"\badverse opinion\b", re.IGNORECASE), "Adverse audit opinion"),
    (re.compile(r"\bdisclaimer of opinion\b", re.IGNORECASE), "Disclaimer of opinion"),
    (re.compile(r"\bquestioned costs?\b", re.IGNORECASE), "Questioned costs reported"),
    (re.compile(r"\bnoncompliance\b", re.IGNORECASE), "Noncompliance finding"),
]

SEARCH_ENGINE_HOSTS = {
    "www.bing.com",
    "bing.com",
    "duckduckgo.com",
    "www.duckduckgo.com",
    "google.com",
    "www.google.com",
}

LOW_VALUE_HOST_SUBSTRINGS = (
    "nonprofitfacts.com",
    "dnb.com",
    "bizprofile.net",
    "intellispect.co",
    "projects.propublica.org",
    "sec.gov",
    "incfact.com",
    "cacompanyregistry.com",
    "instrumentl.com",
    "taxexemptworld.com",
    "docslib.org",
    "grantwatch.com",
    "globaldatabase.com",
    "investor.gov",
    "investors.kenvue.com",
    "annualreports.com",
    "secfilingdata.com",
    "990finder.trantorfintech.com",
    "causeiq.com",
    "charity.ehawaii.gov",
    "nonprofitlight.com",
    "bloomberg.com",
    "charitynavigator.org",
    "guidestar.org",
    "support.google.com",
    "support.microsoft.com",
    "imdb.com",
    "merriam-webster.com",
    "britannica.com",
    "metmuseum.org",
    "northwest.bank",
    "discovery.patsnap.com",
    "californiaisforadventure.com",
    "roadtrippingcalifornia.com",
    "theatlasheart.com",
    "lonelyplanet.com",
    "visitcalifornia.com",
    "san.org",
    "city-data.com",
    "nwiforum.org",
    "portal.kansas.gov",
    "dictionary.cambridge.org",
    "wiktionary.org",
    "reverso.net",
    "collinsdictionary.com",
    "dictionary.com",
    "wallstreetoasis.com",
    "wallstreetoasis.org",
    "zoominfo.com",
    "opencorporates.com",
    "github.com",
    "linkedin.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "wikipedia.org",
    "bizapedia.com",
    "govtribe.com",
    "naicslist.com",
    "radiostationusa.fm",
    "www.radiostationusa.fm",
    "app.candid.org",
    "play.google.com",
    "givefreely.com",
    "app.milliegiving.com",
    "mightycause.com",
    "nonprofitlocator.org",
    "growjo.com",
    "raddioapp.com",
    "radiostay.com",
    "liveonlineradio.net",
    "publicmedianow.org",
    "publicmedia.co",
    "tunein.com",
    "graymedia.com",
    "chicago.suntimes.com",
    "current.org",
    "texas-biz.com",
    "colorado-corp.com",
    "lynncanalpost12.org",
    "hoopavalleypud.com",
    "berriencountymi.gov",
    "sites.google.com",
    "us-radio.com",
    "radio.net",
    "chicoer.com",
    "ourquadcities.com",
    "carbondale.com",
    "kitimatrecycle.ca",
    "governor.hawaii.gov",
    "cbinsights.com",
    "govcb.com",
    "mapquest.com",
    "highergov.com",
    "tvb.org",
    "ecfa.org",
    "publicfiles.fcc.gov",
    "pbs.org",
    "opaguam.org",
    "auditor.hawaii.gov",
    "utah.gov",
    "unalaska.gov",
    "pickclickgive.org",
    "radiostation.info",
    "pioneer.org",
    "givemn.org",
    "gnof.org",
    "elkhart.k12.in.us",
    "tonation-nsn.gov",
    "tcusd.org",
    "nezpercetribe.news",
    "video.wtjx.org",
    "video.westtnpbs.org",
    "vids.kvie.org",
    "mediaplayer.whro.org",
    "bethel.online",
    "jacksonvillefreepress.com",
    "everything.explained.today",
    "uppercumberlandreporter.com",
    "corporationwiki.com",
    "stateuniversity.com",
    "salary.com",
    "allbiz.com",
    "idealist.org",
    "visitbloomington.com",
    "local.yahoo.com",
    "member.quadcitieschamber.com",
    "quadcitieschamber.com",
    "guampdn.com",
    "wsbt.com",
    "npr.org",
    "prod-www.npr.org",
    "protectmypublicmedia.org",
    "findyournews.org",
    "nonprofitinfomart.org",
    "communitycomm.com",
    "givegab.com",
    "lowercapenews.org",
    "interlochen.org",
    "umojaradioapp.com",
    "wordpress.com",
    "whiteearth.com",
    "boisforte.com",
    "thenezpercetribe.com",
    "csuchico.edu",
    "arts.ca.gov",
    "filmtvproduction.net",
    "voiceoflaguna.com",
    "coloradomediaproject.com",
    "nfcb.org",
    "caltribalfamilies.org",
    "appalshop.org",
    "prx.org",
    "bluelake.org",
    "mnr.org",
    "democraticmedia.org",
    "leechlakenews.com",
    "nwavecorp.com",
    "americanpublicmedia.org",
    "wctrib.com",
    "yourbasin.com",
    "lehighvalleynews.com",
    "start.cortera.com",
    "coloradogives.org",
    "editorandpublisher.com",
    "kwqc.com",
    "mihsb.org",
    "tentribespartnership.org",
    "schoolinsites.com",
    "thegallupchamber.com",
    "mapleknoll.org",
    "sacapa.org",
    "nativepublicmedia.org",
    "communitiesinschools.org",
    "marfacc.com",
    "communitywireless.com",
    "givenative.org",
    "nezpercegis.org",
    "chamberofcommerce.com",
    "afge.org",
    "downtowndayton.org",
    "seventhgeneration.com",
    "newsmemory.com",
    "singleaudit.org",
    "lowercapetv.org",
    "traversecity.com",
    "newwavemedia.com",
    "americangeneralmedia.com",
    "hispanicchambercincinnati.com",
    "ohio-corp.com",
    "culturaltrust.org",
    "native-land.ca",
    "elkhornmediagroup.com",
    "lakotaonline.com",
    "nonprofitinfomart.com",
    "actionnewsnow.com",
    "nptweekly.org",
    "publicmedia.tech",
    "fcc.report",
    "kstp.com",
    "ushe.edu",
    "viconsortium.com",
    "wikidata.org",
    "iheart.com",
    "northshoregiveweek.org",
    "quadcities.com",
    "americanpublicmediagroup.org",
    "transmissionproject.org",
    "outercapecommunitysolutions.org",
    "newwavecorporated.com",
    "jicarillahunt.com",
    "ramahnavajo.online",
    "ca.go.ke",
    "mytuner-radio.com",
    "radioworld.com",
    "grangerchamber.net",
    "countyofbathchamber.org",
    "gallup.com",
    "groundworksnm.org",
    "nativeshop.org",
    "seattleschools.org",
    "ohioserves.org",
    "sos.oregon.gov",
    "galaxydigital.com",
    "capitolbroadcasting.com",
    "jacksonholechamber.com",
    "createtv.com",
)

GENERIC_NAME_TOKENS = {
    "public",
    "broadcasting",
    "corporation",
    "inc",
    "incorporated",
    "foundation",
    "communications",
    "communication",
    "media",
    "television",
    "radio",
    "network",
    "networks",
    "authority",
    "board",
    "boards",
    "educational",
    "service",
    "services",
    "regional",
    "metro",
    "metropolitan",
    "north",
    "south",
    "east",
    "west",
    "city",
    "county",
    "association",
    "council",
    "company",
}

DISCOVERY_HUB_PATHS = (
    "/",
    "/about",
    "/about-us",
    "/about/public-files",
    "/about/public-records",
    "/about/reports",
    "/about/financials",
    "/public-files",
    "/public-records",
    "/public-documents",
    "/reports",
    "/financials",
    "/financial-reports",
    "/compliance",
)

LOW_VALUE_PATH_HINTS = (
    "/live",
    "/watch",
    "/tv-schedule",
    "/schedule",
    "/events",
    "/news",
    "/podcast",
    "/episode",
    "/story",
    "/shows",
    "/show/",
    "/passport",
    "/video",
    "/videos",
)


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_anchor = False
        self._href = ""
        self._text: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self._in_anchor = True
            self._href = dict(attrs).get("href") or ""
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._in_anchor:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_anchor:
            text = " ".join(" ".join(self._text).split())
            self.links.append((text, self._href))
            self._in_anchor = False
            self._href = ""
            self._text = []


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise ValidationError(f"File not found: {path}")
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValidationError(f"CSV has no headers: {path}")
        cleaned: list[dict[str, str]] = []
        for row in reader:
            fixed: dict[str, str] = {}
            for key, value in row.items():
                k = (key or "").lstrip("\ufeff")
                v = value if isinstance(value, str) else ""
                fixed[k] = v.strip()
            cleaned.append(fixed)
        return cleaned


def load_stations(path: Path) -> list[StationRecord]:
    rows = _read_csv_rows(path)
    if not rows:
        return []
    missing = REQUIRED_STATION_COLUMNS - set(rows[0].keys())
    if missing:
        raise ValidationError(f"Missing station columns: {', '.join(sorted(missing))}")

    out: list[StationRecord] = []
    for idx, row in enumerate(rows, start=2):
        for col in REQUIRED_STATION_COLUMNS:
            if not row.get(col):
                raise ValidationError(f"{path}:{idx} missing required value for '{col}'")
        enabled_raw = (row.get("enabled") or "1").strip().lower()
        enabled = enabled_raw not in {"0", "false", "no", "n"}
        out.append(
            StationRecord(
                station_id=row["station_id"],
                station_name=row["station_name"],
                page_url=row.get("page_url", ""),
                notes=row.get("notes", ""),
                enabled=enabled,
            )
        )
    return out


def load_state(path: Path) -> set[str]:
    if not path.exists():
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    ids = payload.get("seen_doc_ids")
    if not isinstance(ids, list):
        return set()
    return {str(v) for v in ids}


def save_state(path: Path, seen_doc_ids: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": utc_now_iso(),
        "seen_doc_ids": sorted(seen_doc_ids),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_station_records(stations: list[StationRecord]) -> dict:
    duplicate_ids = sorted(
        station_id
        for station_id in {s.station_id for s in stations}
        if sum(1 for s in stations if s.station_id == station_id) > 1
    )
    malformed_urls = [
        {
            "station_id": s.station_id,
            "station_name": s.station_name,
            "page_url": s.page_url,
            "reason": "page_url must start with http:// or https://",
        }
        for s in stations
        if s.page_url.strip() and urlparse(s.page_url).scheme not in {"http", "https"}
    ]
    disabled_with_page_url = [
        {"station_id": s.station_id, "station_name": s.station_name, "page_url": s.page_url}
        for s in stations
        if not s.enabled and s.page_url.strip()
    ]
    enabled_without_page_url = [
        {"station_id": s.station_id, "station_name": s.station_name}
        for s in stations
        if s.enabled and not s.page_url.strip()
    ]
    return {
        "station_count": len(stations),
        "enabled_count": sum(1 for s in stations if s.enabled),
        "enabled_with_page_url_count": sum(1 for s in stations if s.enabled and s.page_url.strip()),
        "disabled_count": sum(1 for s in stations if not s.enabled),
        "duplicate_station_ids": duplicate_ids,
        "malformed_urls": malformed_urls,
        "disabled_with_page_url": disabled_with_page_url,
        "enabled_without_page_url": enabled_without_page_url,
        "issue_count": len(duplicate_ids) + len(malformed_urls) + len(enabled_without_page_url),
    }


def summarize_failures(failures: list[dict[str, str]]) -> dict:
    by_station: dict[str, dict] = {}
    by_type: dict[str, int] = {}
    for failure in failures:
        station_id = failure.get("station_id", "unknown")
        station_name = failure.get("station_name") or station_id
        error = failure.get("error", "")
        if "HTTP Error 404" in error:
            failure_type = "http_404"
        elif "CERTIFICATE_VERIFY_FAILED" in error:
            failure_type = "ssl_certificate"
        elif "timed out" in error.lower() or "timeout" in error.lower():
            failure_type = "timeout"
        elif "download/archive failed" in error:
            failure_type = "archive"
        else:
            failure_type = "other"
        by_type[failure_type] = by_type.get(failure_type, 0) + 1
        entry = by_station.setdefault(
            station_id,
            {
                "station_id": station_id,
                "station_name": station_name,
                "count": 0,
                "types": {},
                "last_error": "",
                "page_url": failure.get("page_url", ""),
            },
        )
        entry["count"] += 1
        entry["types"][failure_type] = entry["types"].get(failure_type, 0) + 1
        entry["last_error"] = error
        if failure.get("page_url"):
            entry["page_url"] = failure["page_url"]
    stations = sorted(by_station.values(), key=lambda i: (-i["count"], i["station_name"]))
    return {
        "failure_count": len(failures),
        "station_failure_count": len(by_station),
        "by_type": dict(sorted(by_type.items())),
        "by_station": stations,
    }


def build_health_payload(
    *,
    run_payload: dict,
    failures_payload: dict,
    risk_payload: dict,
    started_at: str,
    finished_at: str,
    scan_status: str,
    risk_status: str,
    slack_status: str,
    risk_error: str = "",
    slack_error: str = "",
) -> dict:
    counts = run_payload.get("counts") or {}
    failures = failures_payload.get("failures") or run_payload.get("failures") or []
    return {
        "updated_at": finished_at,
        "last_run_date": run_payload.get("run_date"),
        "started_at": started_at,
        "finished_at": finished_at,
        "scan_status": scan_status,
        "risk_status": risk_status,
        "risk_error": risk_error,
        "slack_status": slack_status,
        "slack_error": slack_error,
        "counts": {
            "new_documents": counts.get("new_documents", 0),
            "flagged_documents": counts.get("flagged_documents", 0),
            "stations_with_failures": counts.get("stations_with_failures", 0),
            "strict_risk_stations": risk_payload.get("strict_station_count", 0) or 0,
            "watchlist_risk_stations": risk_payload.get("watchlist_station_count", 0) or 0,
        },
        "failure_summary": summarize_failures(failures),
    }


def _open_url(url: str, *, timeout_seconds: int):
    req = Request(url, headers=DEFAULT_REQUEST_HEADERS)
    try:
        return urlopen(req, timeout=timeout_seconds)
    except HTTPError as exc:
        if exc.code == 403:
            retry_req = Request(url, headers=FALLBACK_REQUEST_HEADERS)
            return urlopen(retry_req, timeout=timeout_seconds)
        if exc.code in {307, 308}:
            location = exc.headers.get("Location", "").strip()
            if location:
                redirected = urljoin(url, location)
                redirected_req = Request(redirected, headers=DEFAULT_REQUEST_HEADERS)
                try:
                    return urlopen(redirected_req, timeout=timeout_seconds)
                except HTTPError as redirected_exc:
                    if redirected_exc.code == 403:
                        redirected_retry = Request(redirected, headers=FALLBACK_REQUEST_HEADERS)
                        return urlopen(redirected_retry, timeout=timeout_seconds)
                    raise
        raise


def _open_url_insecure_html(url: str, *, timeout_seconds: int):
    context = ssl._create_unverified_context()
    req = Request(url, headers=DEFAULT_REQUEST_HEADERS)
    try:
        return urlopen(req, timeout=timeout_seconds, context=context)
    except HTTPError as exc:
        if exc.code == 403:
            retry_req = Request(url, headers=FALLBACK_REQUEST_HEADERS)
            return urlopen(retry_req, timeout=timeout_seconds, context=context)
        raise


def _read_http_error_html(exc: HTTPError) -> str | None:
    content_type = (exc.headers.get("Content-Type") or "").lower()
    body = exc.read()
    if exc.code == 404 and "html" in content_type and body.strip():
        return body.decode("utf-8", errors="replace")
    return None


def _fetch_text(url: str, timeout_seconds: int) -> str:
    for attempt in range(3):
        try:
            with _open_url(url, timeout_seconds=timeout_seconds) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            html_body = _read_http_error_html(exc)
            if html_body is not None:
                return html_body
            raise
        except URLError as exc:
            message = str(exc)
            if "CERTIFICATE_VERIFY_FAILED" in message:
                with _open_url_insecure_html(url, timeout_seconds=timeout_seconds) as resp:
                    return resp.read().decode("utf-8", errors="replace")
            if attempt < 2 and "nodename nor servname" in message:
                time.sleep(0.5 * (attempt + 1))
                continue
            raise
    raise RuntimeError(f"Failed to fetch {url}")


def _fetch_html(url: str, timeout_seconds: int) -> str:
    return _fetch_text(url, timeout_seconds)


def _normalize_title(title: str, url: str) -> str:
    clean = " ".join(title.split()).strip()
    if clean:
        return clean
    name = Path(urlparse(url).path).name
    return name or url


def _guess_confidence(title: str, url: str) -> str:
    blob = f"{title} {url}".lower()
    strong = ("audit", "single-audit", "financial-statement", "independent-auditor")
    if any(k in blob for k in strong):
        return "high"
    if any(k in blob for k in ("financial", "report", "annual")):
        return "medium"
    return "low"


def _is_candidate(title: str, absolute_url: str) -> bool:
    path = urlparse(absolute_url).path.lower()
    ext = Path(path).suffix.lower()
    if ext not in DOC_EXTENSIONS:
        return False
    blob = f"{title} {absolute_url}".lower()
    return any(h in blob for h in AUDIT_HINTS)


def discover_station_docs(
    station: StationRecord,
    *,
    timeout_seconds: int,
    html_override: str | None = None,
) -> list[AuditDocument]:
    html = html_override if html_override is not None else _fetch_text(station.page_url, timeout_seconds)
    parser = _AnchorParser()
    parser.feed(html)

    docs: list[AuditDocument] = []
    for text, href in parser.links:
        if not href:
            continue
        abs_url = urljoin(station.page_url, href)
        if _should_ignore_document_url(abs_url):
            continue
        if not _is_candidate(text, abs_url):
            continue
        path = urlparse(abs_url).path
        ext = Path(path).suffix.lower()
        title = _normalize_title(text, abs_url)
        confidence = _guess_confidence(title, abs_url)
        docs.append(
            AuditDocument(
                station_id=station.station_id,
                station_name=station.station_name,
                discovered_date=date.today(),
                page_url=station.page_url,
                document_url=abs_url,
                title=title,
                file_ext=ext,
                status="discovered",
                confidence=confidence,
            )
        )

    by_id = {d.doc_id: d for d in docs}
    return sorted(by_id.values(), key=lambda d: (d.station_id, d.document_url))


def unresolved_stations(stations: list[StationRecord]) -> list[StationRecord]:
    return [s for s in stations if not s.page_url.strip()]


def _extract_http_links(raw_html: str) -> list[str]:
    parser = _AnchorParser()
    parser.feed(raw_html)
    candidates = [href for _text, href in parser.links if href]
    out: list[str] = []
    seen: set[str] = set()
    for href in candidates:
        clean = html.unescape(href).strip()
        if not clean:
            continue
        if clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
    return out


def _unwrap_search_redirect(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    qs = parse_qs(parsed.query)

    # DuckDuckGo result redirects use uddg=<encoded target>.
    if host.endswith("duckduckgo.com") and "uddg" in qs and qs["uddg"]:
        return unquote(qs["uddg"][0])

    # Bing sometimes embeds target in u=... (occasionally base64-prefixed).
    if host.endswith("bing.com") and "u" in qs and qs["u"]:
        raw = qs["u"][0]
        if raw.startswith("a1"):
            raw = raw[2:]
            try:
                decoded = b64decode(raw + "==").decode("utf-8", errors="ignore")
                if decoded.startswith("http"):
                    return decoded
            except Exception:
                pass
        if raw.startswith("http"):
            return raw
    return url


def _station_name_tokens(station_name: str) -> list[str]:
    return [
        t
        for t in re.split(r"[^a-z0-9]+", station_name.lower())
        if len(t) >= 4 and t not in GENERIC_NAME_TOKENS
    ]


def _host_is_low_value(host: str) -> bool:
    if host in SEARCH_ENGINE_HOSTS:
        return True
    return any(s in host for s in LOW_VALUE_HOST_SUBSTRINGS)


def _score_page_candidate(station_name: str, url: str, *, preferred_hosts: set[str] | None = None) -> int:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    blob = f"{host}{path}"
    if _host_is_low_value(host):
        return -100
    if path.endswith((".pdf", ".doc", ".docx", ".xls", ".xlsx")):
        return -20
    if any(h in path for h in LOW_VALUE_PATH_HINTS):
        return -100
    if re.search(r"/20\d{2}([/-]\d{2})", path):
        return -100

    score = 0
    keywords = (
        "financial",
        "audit",
        "audited",
        "annual-report",
        "annual-reports",
        "public-files",
        "public-file",
        "public-documents",
        "reports",
        "compliance",
        "about",
    )
    keyword_hits = 0
    for kw in keywords:
        if kw in blob:
            score += 2
            keyword_hits += 1

    name_tokens = _station_name_tokens(station_name)
    matches = sum(1 for t in name_tokens if t in blob)
    score += min(matches * 2, 6)

    if preferred_hosts and host in preferred_hosts:
        score += 5

    # Reject pages that have neither audit-ish keywords nor meaningful station-name signal.
    if keyword_hits == 0 and matches == 0:
        return -100

    if path.count("/") >= 2:
        score += 1
    if parsed.scheme == "https":
        score += 1
    return score


def _canonical_root(url: str) -> str:
    parsed = urlparse(url)
    scheme = parsed.scheme or "https"
    host = parsed.netloc.lower()
    return f"{scheme}://{host}"


def _discover_candidate_domains(station_name: str, *, timeout_seconds: int) -> list[str]:
    query = f"{station_name} public media official site"
    search_urls = [
        f"https://duckduckgo.com/html/?q={quote_plus(query)}",
        f"https://www.bing.com/search?q={quote_plus(query)}&count=12",
    ]
    links: list[str] = []
    for search_url in search_urls:
        try:
            raw_html = _fetch_html(search_url, timeout_seconds)
        except Exception:
            continue
        for href in _extract_http_links(raw_html):
            absolute = href if href.startswith("http") else urljoin(search_url, href)
            absolute = _unwrap_search_redirect(absolute)
            links.append(absolute)
        if links:
            break

    tokens = _station_name_tokens(station_name)
    scored: list[tuple[int, str]] = []
    seen_roots: set[str] = set()
    for link in links:
        parsed = urlparse(link)
        host = parsed.netloc.lower()
        if not host or _host_is_low_value(host):
            continue
        root = _canonical_root(link)
        if root in seen_roots:
            continue
        seen_roots.add(root)
        host_blob = host
        token_hits = sum(1 for t in tokens if t in host_blob)
        score = token_hits * 3
        if any(k in host_blob for k in ("pbs", "npr", "public", "radio", "tv")):
            score += 2
        if parsed.scheme == "https":
            score += 1
        scored.append((score, root))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [root for score, root in scored[:4] if score > 0]


def _collect_in_domain_links(root_url: str, *, timeout_seconds: int) -> list[str]:
    root = _canonical_root(root_url)
    root_host = urlparse(root).netloc.lower()
    candidates: list[str] = []
    seen: set[str] = set()

    to_fetch = [urljoin(root, p) for p in DISCOVERY_HUB_PATHS]
    for page_url in to_fetch:
        try:
            raw_html = _fetch_html(page_url, timeout_seconds)
        except Exception:
            continue

        if page_url not in seen:
            seen.add(page_url)
            candidates.append(page_url)

        parser = _AnchorParser()
        parser.feed(raw_html)
        for _text, href in parser.links:
            if not href:
                continue
            abs_url = href if href.startswith("http") else urljoin(page_url, href)
            parsed = urlparse(abs_url)
            if parsed.netloc.lower() != root_host:
                continue
            clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            if clean in seen:
                continue
            seen.add(clean)
            candidates.append(clean)
    return candidates


def discover_page_candidates(
    station: StationRecord,
    *,
    timeout_seconds: int,
    max_candidates: int = 5,
) -> list[dict[str, str]]:
    domains = _discover_candidate_domains(station.station_name, timeout_seconds=timeout_seconds)
    links: list[str] = []
    preferred_hosts: set[str] = set()
    for domain in domains:
        preferred_hosts.add(urlparse(domain).netloc.lower())
        links.extend(_collect_in_domain_links(domain, timeout_seconds=timeout_seconds))

    if not links:
        # Soft fallback to search result links if domain crawl yielded nothing.
        query = f"{station.station_name} audited financial statements public files"
        fallback_searches = [
            f"https://duckduckgo.com/html/?q={quote_plus(query)}",
            f"https://www.bing.com/search?q={quote_plus(query)}&count=12",
        ]
        for fallback_search in fallback_searches:
            try:
                raw_html = _fetch_html(fallback_search, timeout_seconds)
            except Exception:
                continue
            for href in _extract_http_links(raw_html):
                absolute = href if href.startswith("http") else urljoin(fallback_search, href)
                links.append(_unwrap_search_redirect(absolute))
            if links:
                break

    scored: list[tuple[int, str]] = []
    for link in links:
        score = _score_page_candidate(station.station_name, link, preferred_hosts=preferred_hosts)
        if score < 1:
            continue
        scored.append((score, link))
    scored.sort(key=lambda x: (-x[0], x[1]))

    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for score, url in scored:
        if url in seen:
            continue
        seen.add(url)
        out.append(
            {
                "station_id": station.station_id,
                "station_name": station.station_name,
                "candidate_url": url,
                "score": str(score),
            }
        )
        if len(out) >= max_candidates:
            break
    return out


def _safe_filename(url: str) -> str:
    path = Path(urlparse(_normalize_document_url(url)).path)
    name = path.name or "document"
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


def _normalize_document_url(url: str) -> str:
    parsed = urlparse(url)
    scheme = parsed.scheme
    host = parsed.netloc.lower()
    # Some station pages still publish legacy http S3 links that reject download;
    # upgrade known-safe storage hosts to https before fetching.
    if scheme == "http" and (
        host.endswith(".amazonaws.com")
        or host in {"pbs.bento.storage.s3.amazonaws.com"}
    ):
        scheme = "https"
    path = quote(parsed.path, safe="/._-~()%")
    query = quote(parsed.query, safe="=&._-~%")
    return urlunparse((scheme, parsed.netloc, path, parsed.params, query, parsed.fragment))


def _should_ignore_document_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return host in IGNORABLE_DOCUMENT_HOSTS


def _download_bytes(url: str, timeout_seconds: int) -> bytes:
    with _open_url(_normalize_document_url(url), timeout_seconds=timeout_seconds) as resp:
        return resp.read()


def _extract_pdf_text(pdf_path: Path) -> str:
    try:
        proc = subprocess.run(
            ["pdftotext", "-q", str(pdf_path), "-"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout or ""


def _flags_for_text(text: str) -> list[str]:
    flags: list[str] = []
    for pattern, label in UNUSUAL_PATTERNS:
        if pattern.search(text):
            flags.append(label)
    return flags


def archive_document(
    doc: AuditDocument,
    *,
    archive_root: Path,
    timeout_seconds: int,
) -> AuditDocument:
    folder = archive_root / doc.station_id / doc.discovered_date.isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    filename = _safe_filename(doc.document_url)
    out_path = folder / filename

    raw = _download_bytes(doc.document_url, timeout_seconds)
    out_path.write_bytes(raw)
    digest = __import__("hashlib").sha256(raw).hexdigest()

    text_blob = f"{doc.title}\n{doc.document_url}"
    if doc.file_ext == ".pdf":
        extracted = _extract_pdf_text(out_path)
        if extracted:
            text_blob += "\n" + extracted[:100000]

    flags = _flags_for_text(text_blob)
    summary = "No unusual keywords detected."
    if flags:
        summary = " | ".join(flags)

    return AuditDocument(
        station_id=doc.station_id,
        station_name=doc.station_name,
        discovered_date=doc.discovered_date,
        page_url=doc.page_url,
        document_url=doc.document_url,
        title=doc.title,
        file_ext=doc.file_ext,
        status="downloaded",
        confidence=doc.confidence,
        downloaded_path=str(out_path),
        content_sha256=digest,
        flags="; ".join(flags),
        summary=summary,
    )


def build_payload(
    new_docs: list[AuditDocument],
    failures: list[dict[str, str]],
    *,
    started_at: str | None = None,
    finished_at: str | None = None,
    stations_total: int | None = None,
    stations_scanned: int | None = None,
    stations_skipped: int | None = None,
    documents_discovered: int | None = None,
    documents_archived: int | None = None,
    scan_status: str = "completed",
) -> dict:
    flagged = [d for d in new_docs if d.flags.strip()]
    payload = {
        "run_date": date.today().isoformat(),
        "started_at": started_at,
        "finished_at": finished_at,
        "counts": {
            "new_documents": len(new_docs),
            "flagged_documents": len(flagged),
            "stations_with_failures": len({f["station_id"] for f in failures}),
            "stations_total": stations_total,
            "stations_scanned": stations_scanned,
            "stations_skipped": stations_skipped,
            "documents_discovered": documents_discovered,
            "documents_archived": documents_archived,
        },
        "scan_status": scan_status,
        "new_documents": [
            {
                "doc_id": d.doc_id,
                "station_id": d.station_id,
                "station_name": d.station_name,
                "title": d.title,
                "document_url": d.document_url,
                "downloaded_path": d.downloaded_path,
                "status": d.status,
                "flags": d.flags,
                "summary": d.summary,
                "confidence": d.confidence,
            }
            for d in new_docs
        ],
        "failures": failures,
    }
    if started_at and finished_at:
        try:
            started = datetime.fromisoformat(started_at)
            finished = datetime.fromisoformat(finished_at)
            payload["duration_seconds"] = round((finished - started).total_seconds(), 3)
        except ValueError:
            payload["duration_seconds"] = None
    return payload


def write_brief(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    counts = payload.get("counts", {})
    lines = [
        "# Audit Watch Briefing",
        "",
        f"Run date: {payload.get('run_date', 'unknown')}",
        "",
        "## Snapshot",
        f"- New documents: {counts.get('new_documents', 0)}",
        f"- Flagged documents: {counts.get('flagged_documents', 0)}",
        f"- Stations with fetch failures: {counts.get('stations_with_failures', 0)}",
        "",
        "## New Documents",
    ]
    docs = payload.get("new_documents") or []
    if docs:
        for doc in docs:
            lines.append(f"- {doc['station_name']}: {doc['title']}")
            lines.append(f"  URL: {doc['document_url']}")
            lines.append(f"  Saved: {doc['downloaded_path']}")
            lines.append(f"  Notes: {doc['summary']}")
    else:
        lines.append("- No new audit documents detected.")
    lines.append("")
    lines.append("## Fetch Failures")
    failures = payload.get("failures") or []
    if failures:
        for failure in failures:
            lines.append(
                f"- {failure.get('station_name', failure.get('station_id', 'unknown'))}: {failure.get('error', 'unknown error')}"
            )
    else:
        lines.append("- No fetch failures.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
