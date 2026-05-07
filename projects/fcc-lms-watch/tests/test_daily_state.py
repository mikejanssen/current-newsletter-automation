from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fcc_lms_watch import cli
from fcc_lms_watch.lms_client import CsvResult
from fcc_lms_watch.slack import SlackPostError


CPB_CSV = "Grantee Name,Grantee Type,Grantee City,Grantee State,Licensee Name\nKUON-TV,TV,Lincoln,NE,Nebraska Public Media\n"


class FakeLmsClient:
    def __init__(self) -> None:
        self.search_calls = 0

    def consume_request_log(self) -> list:
        return []

    def fetch_search_results_html(self, *args, **kwargs) -> str:
        self.search_calls += 1
        return "<html></html>"

    def export_csv(self, *args, **kwargs) -> CsvResult:
        if kwargs.get("debug_label") == "assignment":
            return CsvResult(rows=[], source_mode="csv_export")
        return CsvResult(
            rows=[
                {
                    "Call Sign": "KUON-TV",
                    "Facility ID": "66589",
                    "File Number": "0000296088",
                    "Service": "Full Service Television",
                    "Purpose": "Engineering STA",
                }
            ],
            source_mode="csv_export",
        )

    def fetch_public_notice_rows(self, *args, **kwargs) -> CsvResult:
        return CsvResult(rows=[], source_mode="pn_test_empty")


def make_args(tmp: Path, *, dry_run: bool) -> argparse.Namespace:
    cpb = tmp / "cpb.csv"
    cpb.write_text(CPB_CSV, encoding="utf-8")
    return argparse.Namespace(
        cpb=str(cpb),
        cpb_aliases="",
        state=str(tmp / "state.json"),
        out=str(tmp / "last-run.json"),
        from_date="2026-05-03",
        to_date="2026-05-06",
        lookback_days=3,
        call_sign=None,
        debug_export_dir=None,
        dry_run=dry_run,
    )


class DailyStateTests(unittest.TestCase):
    def test_dry_run_does_not_write_state_or_post_slack(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            args = make_args(tmp, dry_run=True)

            with patch.object(cli, "LmsClient", FakeLmsClient), patch.object(cli, "post_to_slack") as post:
                payload = cli.run_daily(args)

            self.assertEqual(payload["slack_status"], "dry_run")
            self.assertFalse(payload["state_updated"])
            self.assertEqual(len(payload["alerts"]), 1)
            self.assertFalse((tmp / "state.json").exists())
            post.assert_not_called()

    def test_slack_failure_does_not_mark_alert_seen(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            args = make_args(tmp, dry_run=False)

            with patch.dict(os.environ, {"SLACK_WEBHOOK_URL": "https://example.test/webhook"}), patch.object(
                cli, "LmsClient", FakeLmsClient
            ), patch.object(cli, "post_to_slack", side_effect=SlackPostError("nope")):
                with contextlib.redirect_stdout(io.StringIO()):
                    payload = cli.run_daily(args)

            self.assertEqual(payload["slack_status"], "failed")
            self.assertFalse(payload["state_updated"])
            self.assertFalse((tmp / "state.json").exists())

    def test_successful_slack_marks_alert_seen(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            args = make_args(tmp, dry_run=False)

            with patch.dict(os.environ, {"SLACK_WEBHOOK_URL": "https://example.test/webhook"}), patch.object(
                cli, "LmsClient", FakeLmsClient
            ), patch.object(cli, "post_to_slack"):
                payload = cli.run_daily(args)

            self.assertEqual(payload["slack_status"], "sent")
            self.assertTrue(payload["state_updated"])
            state = json.loads((tmp / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(
                state["seen_items"],
                ["Application Search:0000296088:None:sta_silent"],
            )


if __name__ == "__main__":
    unittest.main()
