from __future__ import annotations

import argparse
import json
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError

from rss_watch import cli, slack
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
        since_last_checked=False,
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
    def test_unlimited_slack_batches_cover_items_beyond_report_limit(self) -> None:
        with TemporaryDirectory() as tmpdir:
            args = make_args(Path(tmpdir), dry_run=False, slack_webhook="https://example.test/webhook")
            args.slack_max_items = 0
            args.max_items = 2
            item = feed_items()[0]
            items = [replace(item, link=f"https://example.test/{index}/" + "a" * 450) for index in range(12)]
            with patch.object(cli, "_fetch_url", return_value="<rss />"), patch.object(
                cli, "_parse_feed_xml", return_value=items
            ), patch.object(cli, "post_to_slack") as post, patch.object(cli.time, "sleep"):
                report = cli.run(args)
            self.assertEqual(len(report["items"]), 2)
            self.assertEqual(set(report["slack_item_ids"]), {item.link for item in items})
            self.assertGreater(post.call_count, 1)
            self.assertTrue(all(len(call.args[1].text) <= cli.SLACK_BATCH_CHARS for call in post.call_args_list))

    def test_failed_second_batch_retries_only_unsent_stories(self) -> None:
        with TemporaryDirectory() as tmpdir:
            args = make_args(Path(tmpdir), dry_run=False, slack_webhook="https://example.test/webhook")
            args.slack_max_items = 0
            item = feed_items()[0]
            items = [replace(item, link=f"https://example.test/{index}/" + "a" * 450) for index in range(12)]
            with patch.object(cli, "_fetch_url", return_value="<rss />"), patch.object(
                cli, "_parse_feed_xml", return_value=items
            ), patch.object(cli.time, "sleep"):
                with patch.object(cli, "post_to_slack", side_effect=[None, SlackPostError("unavailable")]):
                    first = cli.run(args)
                checkpoint = json.loads(Path(args.state).read_text())
                self.assertIsNone(checkpoint["last_checked"])
                with patch.object(cli, "post_to_slack"):
                    retry = cli.run(args)
            self.assertEqual(first["slack_status"], "partial")
            self.assertTrue(first["slack_item_ids"])
            self.assertFalse(set(first["slack_item_ids"]) & set(retry["slack_item_ids"]))
            self.assertEqual(set(first["slack_item_ids"] + retry["slack_item_ids"]), {item.link for item in items})

    def test_slack_retries_rate_limit_after_requested_delay(self) -> None:
        url = "https://example.test/webhook"
        rate_limit = HTTPError(url, 429, "Too Many Requests", {"Retry-After": "2"}, None)
        with patch.object(slack, "urlopen") as request, patch.object(slack.time, "sleep") as sleep:
            response = request.return_value
            request.side_effect = [rate_limit, response]
            slack.post_to_slack(url, slack.SlackMessage("example"))
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once_with(2)

    def test_overlap_picks_up_late_item_without_resending_seen_item(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            args = make_args(tmp, dry_run=True)
            args.since_last_checked = True
            args.overlap_hours = 72
            now = datetime.now(timezone.utc)
            first = replace(feed_items()[0], published=now - timedelta(hours=10))
            seen = replace(first, link="https://example.test/seen")
            (tmp / "state.json").write_text(json.dumps({
                "last_checked": (now - timedelta(hours=2)).isoformat(), "seen_ids": [seen.link],
            }), encoding="utf-8")
            with patch.object(cli, "_fetch_url", return_value="<rss />"), patch.object(
                cli, "_parse_feed_xml", return_value=[first, seen]
            ):
                payload = cli.run(args)
            self.assertEqual(payload["slack_item_ids"], ["https://example.test/story"])

    def test_persistent_feed_failure_is_reported_and_clears_on_recovery(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            args = make_args(tmp, dry_run=False, slack_webhook="https://example.test/webhook")
            with patch.object(cli, "_fetch_url", return_value="<html />"), patch.object(cli, "post_to_slack") as post:
                self.assertFalse(cli.run(args)["persistent_feed_failures"])
                self.assertEqual(len(cli.run(args)["persistent_feed_failures"]), 1)
            self.assertIn("Failed on consecutive scans: Feed", post.call_args.args[1].text)
            with patch.object(cli, "_fetch_url", return_value="<rss />"), patch.object(cli, "post_to_slack"):
                self.assertFalse(cli.run(args)["persistent_feed_failures"])
            self.assertEqual(json.loads((tmp / "state.json").read_text())["feed_failure_streaks"], {})

    def test_slack_overflow_remains_eligible_for_next_scan(self) -> None:
        with TemporaryDirectory() as tmpdir:
            args = make_args(Path(tmpdir), dry_run=False, slack_webhook="https://example.test/webhook")
            args.slack_max_items = 1
            items = feed_items()
            items.append(replace(items[0], link="https://example.test/second",
                                 published=items[0].published - timedelta(minutes=1)))
            with patch.object(cli, "_fetch_url", return_value="<rss />"), patch.object(
                cli, "_parse_feed_xml", return_value=items
            ), patch.object(cli, "post_to_slack"):
                first = cli.run(args)["slack_item_ids"]
                second = cli.run(args)["slack_item_ids"]
            self.assertEqual(first, ["https://example.test/story"])
            self.assertEqual(second, ["https://example.test/second"])

    def test_additional_opml_feeds_are_combined_and_deduplicated(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            primary = tmp / "primary.opml"
            additional = tmp / "additional.opml"
            primary.write_text(
                '<opml version="2.0"><body><outline text="Primary" xmlUrl="https://example.test/feed.xml"/></body></opml>',
                encoding="utf-8",
            )
            additional.write_text(
                '<opml version="2.0"><body>'
                '<outline text="Duplicate" xmlUrl="https://example.test/feed.xml"/>'
                '<outline text="Active watch" xmlUrl="https://example.test/active.xml"/>'
                '</body></opml>',
                encoding="utf-8",
            )

            feeds = cli.load_feed_urls_from_opml_files([primary, additional])

            self.assertEqual(
                feeds,
                [
                    ("Primary", "https://example.test/feed.xml"),
                    ("Active watch", "https://example.test/active.xml"),
                ],
            )

    def test_morning_can_resume_from_last_checked(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            args = make_args(tmp, dry_run=True)
            args.since_last_checked = True
            previous = datetime(2026, 8, 17, 12, 30, tzinfo=timezone.utc)
            (tmp / "state.json").write_text(
                json.dumps({"last_checked": previous.isoformat(), "seen_ids": []}),
                encoding="utf-8",
            )

            with patch.object(cli, "_fetch_url", return_value="<rss />"), patch.object(
                cli, "_parse_feed_xml", return_value=[]
            ):
                payload = cli.run(args)

            self.assertEqual(payload["window_start_utc"], previous.isoformat())

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
