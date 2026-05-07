from __future__ import annotations

import argparse
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from rss_watch import cli
from rss_watch.slack import SlackPostError


def make_args(tmp: Path, *, dry_run: bool, slack_webhook: str | None = None) -> argparse.Namespace:
    opml = tmp / "feeds.opml"
    opml.write_text(
        """<?xml version="1.0"?>
<opml version="2.0"><body><outline text="Feed" xmlUrl="https://example.test/feed.xml"/></body></opml>
""",
        encoding="utf-8",
    )
    return argparse.Namespace(
        opml=str(opml),
        mode="morning",
        window_hours=24,
        state=str(tmp / "state.json"),
        out=str(tmp / "last-run.json"),
        candidates_out=str(tmp / "candidates.json"),
        brief=str(tmp / "briefing.md"),
        max_items=200,
        max_item_age_days=30,
        include_low=False,
        include_seen=False,
        feed_timeout_seconds=1,
        feed_retries=0,
        parallelism=1,
        max_feeds=None,
        slack_webhook=slack_webhook,
        slack_max_items=10,
        dry_run=dry_run,
    )


def feed_items() -> list[cli.FeedItem]:
    return [
        cli.FeedItem(
            feed_title="Feed",
            title="Public radio station faces funding cuts",
            link="https://example.test/story?utm_source=x",
            summary="A public radio station faces federal funding cuts.",
            published=datetime.now(timezone.utc),
        )
    ]


class StateSafetyTests(unittest.TestCase):
    def test_dry_run_does_not_write_state(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            args = make_args(tmp, dry_run=True)

            with patch.object(cli, "_fetch_url", return_value="<rss />"), patch.object(
                cli, "_parse_feed_xml", return_value=feed_items()
            ), patch.object(cli, "post_to_slack") as post:
                payload = cli.run(args)

            self.assertEqual(payload["slack_status"], "dry_run")
            self.assertFalse(payload["state_updated"])
            self.assertFalse((tmp / "state.json").exists())
            post.assert_not_called()

    def test_slack_failure_does_not_write_state(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            args = make_args(tmp, dry_run=False, slack_webhook="https://example.test/webhook")

            with patch.object(cli, "_fetch_url", return_value="<rss />"), patch.object(
                cli, "_parse_feed_xml", return_value=feed_items()
            ), patch.object(cli, "post_to_slack", side_effect=SlackPostError("boom")), patch("builtins.print"):
                payload = cli.run(args)

            self.assertEqual(payload["slack_status"], "failed")
            self.assertFalse(payload["state_updated"])
            self.assertFalse((tmp / "state.json").exists())

    def test_success_writes_state(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            args = make_args(tmp, dry_run=False, slack_webhook="https://example.test/webhook")

            with patch.object(cli, "_fetch_url", return_value="<rss />"), patch.object(
                cli, "_parse_feed_xml", return_value=feed_items()
            ), patch.object(cli, "post_to_slack"):
                payload = cli.run(args)

            self.assertEqual(payload["slack_status"], "sent")
            self.assertTrue(payload["state_updated"])
            state = json.loads((tmp / "state.json").read_text(encoding="utf-8"))
            self.assertTrue(state["seen_ids"])
            self.assertTrue(state["last_checked"])


if __name__ == "__main__":
    unittest.main()
