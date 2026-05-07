from __future__ import annotations

import argparse
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fcc_watch import cli
from fcc_watch.slack import SlackPostError


CPB_CSV = "Grantee Name,Grantee Type,Grantee City,Grantee State,Licensee Name\nWABC-FM,Radio,Test,NY,Test Licensee\n"


def make_args(tmp: Path, *, dry_run: bool) -> argparse.Namespace:
    cpb = tmp / "cpb.csv"
    cpb.write_text(CPB_CSV, encoding="utf-8")
    return argparse.Namespace(
        cpb=str(cpb),
        state=str(tmp / "state.json"),
        out=str(tmp / "last-run.json"),
        lookback_days=2,
        max_catchup_days=14,
        skip_digest=False,
        dry_run=dry_run,
    )


def digest_alerts(*args, **kwargs):
    return (
        [
            cli.DigestAlert(
                title="Public broadcasting item",
                link="https://www.fcc.gov/item",
                categories=["public notices"],
                keywords=["public broadcasting"],
            )
        ],
        [],
        ["2026-05-07"],
        ["2026-05-07"],
    )


def no_public_files(*args, **kwargs):
    return [], []


class DailyStateTests(unittest.TestCase):
    def test_dry_run_does_not_write_state(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            args = make_args(tmp, dry_run=True)

            with patch.object(cli, "build_digest_alerts", digest_alerts), patch.object(
                cli, "build_public_files_alerts", no_public_files
            ), patch.object(cli, "build_ecfs_alerts", no_public_files), patch.object(
                cli, "build_meeting_alerts", no_public_files
            ), patch.object(cli, "post_to_slack") as post:
                payload = cli.run_daily(args)

            self.assertEqual(payload["slack_status"], "dry_run")
            self.assertFalse(payload["state_updated"])
            self.assertFalse((tmp / "state.json").exists())
            post.assert_not_called()

    def test_slack_failure_does_not_write_state(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            args = make_args(tmp, dry_run=False)

            with patch.dict("os.environ", {"SLACK_WEBHOOK_URL": "https://example.test/webhook"}), patch.object(
                cli, "build_digest_alerts", digest_alerts
            ), patch.object(cli, "build_public_files_alerts", no_public_files), patch.object(
                cli, "build_ecfs_alerts", no_public_files
            ), patch.object(cli, "build_meeting_alerts", no_public_files), patch.object(
                cli, "post_to_slack", side_effect=SlackPostError("boom")
            ), patch("builtins.print"):
                payload = cli.run_daily(args)

            self.assertEqual(payload["slack_status"], "failed")
            self.assertFalse(payload["state_updated"])
            self.assertFalse((tmp / "state.json").exists())

    def test_success_writes_state_after_slack(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            args = make_args(tmp, dry_run=False)

            with patch.dict("os.environ", {"SLACK_WEBHOOK_URL": "https://example.test/webhook"}), patch.object(
                cli, "build_digest_alerts", digest_alerts
            ), patch.object(cli, "build_public_files_alerts", no_public_files), patch.object(
                cli, "build_ecfs_alerts", no_public_files
            ), patch.object(cli, "build_meeting_alerts", no_public_files), patch.object(cli, "post_to_slack"):
                payload = cli.run_daily(args)

            self.assertEqual(payload["slack_status"], "sent")
            self.assertTrue(payload["state_updated"])
            state = json.loads((tmp / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["seen_items"], ["digest:https://www.fcc.gov/item"])
            self.assertEqual(state["last_successful_digest_date"], "2026-05-07")


if __name__ == "__main__":
    unittest.main()
