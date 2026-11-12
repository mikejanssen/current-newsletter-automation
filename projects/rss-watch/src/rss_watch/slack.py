from __future__ import annotations

import json
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class SlackMessage:
    text: str


class SlackPostError(RuntimeError):
    """Raised when Slack rejects or fails to receive a webhook request."""


def post_to_slack(webhook_url: str, message: SlackMessage) -> None:
    payload = json.dumps({"text": message.text}).encode("utf-8")
    req = Request(webhook_url, data=payload, headers={"Content-Type": "application/json"})
    for attempt in range(3):
        try:
            with urlopen(req, timeout=30) as resp:  # nosec B310
                resp.read()
            return
        except HTTPError as err:
            retry_after = (err.headers.get("Retry-After") or "").strip() if err.headers else ""
            if err.code == 429 and attempt < 2 and retry_after.isdecimal() and int(retry_after) <= 60:
                time.sleep(max(1, int(retry_after)))
                continue
            raise SlackPostError(f"HTTP {err.code}: {err.reason}") from err
        except URLError as err:
            raise SlackPostError(str(err.reason)) from err
        except TimeoutError as err:
            raise SlackPostError("Slack webhook timed out") from err
