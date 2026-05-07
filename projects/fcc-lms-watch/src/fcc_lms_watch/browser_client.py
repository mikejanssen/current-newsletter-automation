from __future__ import annotations

import os
import urllib.parse
from dataclasses import dataclass


DEFAULT_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/135.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class BrowserResponse:
    ok: bool
    status: int
    body: bytes
    error: str | None = None


class BrowserClient:
    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    @staticmethod
    def available() -> bool:
        try:
            import playwright.sync_api  # noqa: F401
        except Exception:
            return False
        return True

    def _ensure_started(self) -> None:
        if self._page is not None:
            return
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        browser_name = os.environ.get("FCC_LMS_BROWSER_ENGINE", "chromium").strip().lower() or "chromium"
        launcher = getattr(self._playwright, browser_name, None)
        if launcher is None:
            raise RuntimeError(f"Unsupported browser engine: {browser_name}")
        self._browser = launcher.launch(headless=True)
        self._context = self._browser.new_context(
            user_agent=os.environ.get("FCC_LMS_BROWSER_USER_AGENT", DEFAULT_BROWSER_USER_AGENT),
            locale="en-US",
        )
        self._page = self._context.new_page()

    def close(self) -> None:
        if self._page is not None:
            self._page.close()
            self._page = None
        if self._context is not None:
            self._context.close()
            self._context = None
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None

    def fetch(self, url: str, *, data: dict[str, str] | None = None, referer: str | None = None) -> BrowserResponse:
        self._ensure_started()
        assert self._page is not None

        try:
            if data is None:
                assert self._context is not None
                page = self._context.new_page()
                try:
                    response = page.goto(url, wait_until="domcontentloaded", referer=referer, timeout=15000)
                    page.wait_for_timeout(1000)
                    status = response.status if response is not None else 0
                    body = page.content().encode("utf-8", errors="ignore")
                    return BrowserResponse(ok=200 <= status < 400, status=status, body=body)
                finally:
                    page.close()

            # Prime same-origin session/cookies before fetch POSTs.
            warm_url = referer or url
            self._page.goto(warm_url, wait_until="domcontentloaded")
            payload = urllib.parse.urlencode(data)
            result = self._page.evaluate(
                """async ({url, payload, referer}) => {
                    const response = await fetch(url, {
                        method: 'POST',
                        credentials: 'include',
                        headers: {
                            'Content-Type': 'application/x-www-form-urlencoded',
                            ...(referer ? {'Referer': referer} : {})
                        },
                        body: payload
                    });
                    return {
                        status: response.status,
                        text: await response.text()
                    };
                }""",
                {"url": url, "payload": payload, "referer": referer},
            )
            status = int(result.get("status", 0))
            text = result.get("text", "")
            return BrowserResponse(ok=200 <= status < 400, status=status, body=text.encode("utf-8", errors="ignore"))
        except Exception as err:
            return BrowserResponse(ok=False, status=0, body=b"", error=str(err))

    def submit_public_notice_search(
        self,
        url: str,
        *,
        from_date: str,
        to_date: str,
        call_sign: str | None = None,
    ) -> BrowserResponse:
        self._ensure_started()
        assert self._page is not None

        try:
            response = self._page.goto(url, wait_until="domcontentloaded")
            if response is not None and not (200 <= response.status < 400):
                return BrowserResponse(ok=False, status=response.status, body=b"", error=f"HTTP {response.status}")
            self._page.fill('input[name="txt-fromDate"]', from_date)
            self._page.fill('input[name="txt-toDate"]', to_date)
            if call_sign:
                self._page.fill('input[name="txt-callSign"]', call_sign.strip().upper())
            try:
                with self._page.expect_navigation(wait_until="domcontentloaded", timeout=15000):
                    self._page.click('input[name="j_idt99"]')
            except Exception:
                # LMS sometimes completes the JSF submit without a normal navigation event.
                pass
            body = self._page.content().encode("utf-8", errors="ignore")
            return BrowserResponse(ok=True, status=200, body=body)
        except Exception as err:
            return BrowserResponse(ok=False, status=0, body=b"", error=str(err))

    def fetch_public_notice_date_pages(
        self,
        url: str,
        *,
        from_date: str,
        to_date: str,
        call_sign: str | None = None,
        max_pages: int = 3,
    ) -> list[BrowserResponse]:
        self._ensure_started()
        assert self._page is not None

        try:
            response = self._page.goto(url, wait_until="domcontentloaded", timeout=45000)
            if response is not None and not (200 <= response.status < 400):
                return [BrowserResponse(ok=False, status=response.status, body=b"", error=f"HTTP {response.status}")]
            self._page.fill('input[name="txt-fromDate"]', from_date)
            self._page.fill('input[name="txt-toDate"]', to_date)
            if call_sign:
                self._page.fill('input[name="txt-callSign"]', call_sign.strip().upper())
            try:
                with self._page.expect_navigation(wait_until="domcontentloaded", timeout=15000):
                    self._page.click('input[name="j_idt99"]')
            except Exception:
                pass
            links = self._page.locator('a[href*="publicNoticeSearchResult.html"]').evaluate_all(
                """(els, args) => {
                    const parse = (s) => {
                        const m = (s || '').match(/^(\\d{2})\\/(\\d{2})\\/(\\d{4})$/);
                        if (!m) return null;
                        return `${m[3]}-${m[1]}-${m[2]}`;
                    };
                    const from = parse(args.fromDate);
                    const to = parse(args.toDate);
                    const out = [];
                    for (const a of els) {
                        const value = parse((a.textContent || '').trim());
                        if (!value || (from && value < from) || (to && value > to)) continue;
                        out.push(a.href);
                        if (out.length >= args.maxPages) break;
                    }
                    return out;
                }""",
                {"maxPages": max_pages, "fromDate": from_date, "toDate": to_date},
            )
            responses: list[BrowserResponse] = []
            for link in links:
                try:
                    resp = self._page.goto(link, wait_until="domcontentloaded", timeout=45000)
                    self._page.wait_for_timeout(1000)
                    status = resp.status if resp is not None else 0
                    responses.append(
                        BrowserResponse(
                            ok=200 <= status < 400,
                            status=status,
                            body=self._page.content().encode("utf-8", errors="ignore"),
                        )
                    )
                except Exception as err:
                    try:
                        self._page.wait_for_load_state("domcontentloaded", timeout=3000)
                    except Exception:
                        pass
                    try:
                        body = self._page.content().encode("utf-8", errors="ignore")
                    except Exception:
                        body = b""
                    if b"table-apps" in body or b"Search Results" in body:
                        responses.append(BrowserResponse(ok=True, status=200, body=body))
                    else:
                        responses.append(BrowserResponse(ok=False, status=0, body=b"", error=str(err)))
            return responses
        except Exception as err:
            return [BrowserResponse(ok=False, status=0, body=b"", error=str(err))]
