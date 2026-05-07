from __future__ import annotations

import json
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
    try:
        with urlopen(req, timeout=30) as resp:  # nosec B310
            resp.read()
    except HTTPError as err:
        raise SlackPostError(f"HTTP {err.code}: {err.reason}") from err
    except URLError as err:
        raise SlackPostError(str(err.reason)) from err
