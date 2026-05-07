from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import socket
import time
import urllib.error
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .config import DEFAULT_DIGEST_BASE_URLS, DEFAULT_PUBLIC_FILES_BASE_URL, USER_AGENT


@dataclass(frozen=True)
class ProbeResult:
    url: str
    host: str
    dns_ok: bool
    tcp_ok: bool
    http_ok: bool
    status_code: int | None
    elapsed_ms: int
    error: str | None


def _probe_once(url: str, timeout_seconds: float) -> ProbeResult:
    parsed = urlparse(url)
    host = parsed.netloc
    start = time.monotonic()

    dns_ok = False
    tcp_ok = False
    http_ok = False
    status_code: int | None = None
    error: str | None = None

    try:
        socket.getaddrinfo(host, 443)
        dns_ok = True
    except OSError as err:
        elapsed = int((time.monotonic() - start) * 1000)
        return ProbeResult(
            url=url,
            host=host,
            dns_ok=False,
            tcp_ok=False,
            http_ok=False,
            status_code=None,
            elapsed_ms=elapsed,
            error=f"dns_error: {type(err).__name__}: {err}",
        )

    try:
        with socket.create_connection((host, 443), timeout=timeout_seconds):
            tcp_ok = True
    except OSError as err:
        elapsed = int((time.monotonic() - start) * 1000)
        return ProbeResult(
            url=url,
            host=host,
            dns_ok=dns_ok,
            tcp_ok=False,
            http_ok=False,
            status_code=None,
            elapsed_ms=elapsed,
            error=f"tcp_error: {type(err).__name__}: {err}",
        )

    try:
        req = Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=timeout_seconds) as resp:  # nosec B310
            status_code = getattr(resp, "status", None)
            http_ok = True
    except urllib.error.HTTPError as err:
        # HTTP errors still confirm host/app-layer reachability.
        status_code = err.code
        http_ok = True
    except (TimeoutError, socket.timeout, urllib.error.URLError, OSError) as err:
        error = f"http_error: {type(err).__name__}: {err}"

    elapsed = int((time.monotonic() - start) * 1000)
    return ProbeResult(
        url=url,
        host=host,
        dns_ok=dns_ok,
        tcp_ok=tcp_ok,
        http_ok=http_ok,
        status_code=status_code,
        elapsed_ms=elapsed,
        error=error,
    )


def default_probe_urls(target_date: date) -> list[str]:
    urls = [f"{base.rstrip('/')}/{target_date:%Y/%m/%d}" for base in DEFAULT_DIGEST_BASE_URLS]
    urls.append(DEFAULT_PUBLIC_FILES_BASE_URL)
    return urls


def run_preflight(urls: list[str], timeout_seconds: float, retries: int) -> dict:
    results: list[ProbeResult] = []
    for url in urls:
        last = _probe_once(url, timeout_seconds=timeout_seconds)
        for _ in range(retries):
            if last.http_ok:
                break
            last = _probe_once(url, timeout_seconds=timeout_seconds)
        results.append(last)

    all_reachable = all(r.http_ok for r in results)
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "timeout_seconds": timeout_seconds,
        "retries": retries,
        "all_reachable": all_reachable,
        "results": [asdict(r) for r in results],
    }
