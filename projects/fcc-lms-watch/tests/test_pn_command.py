from __future__ import annotations

import argparse
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fcc_lms_watch import cli
from fcc_lms_watch.lms_client import CsvResult


CPB_CSV = "Grantee Name,Grantee Type,Grantee City,Grantee State,Licensee Name\nWRVO,Radio,Oswego,NY,STATE UNIVERSITY OF NEW YORK\n"


class FakePnClient:
    def consume_request_log(self) -> list:
        return []

    def fetch_public_notice_rows(self, *, notice_type, from_date, to_date, call_sign=None, debug_dir=None) -> CsvResult:
        if notice_type == "Action":
            return CsvResult(rows=[], source_mode="pn_action_html_empty")
        return CsvResult(
            rows=[
                {
                    "Service": "Full Power FM",
                    "File Number": "0000296158",
                    "Call Sign": "WRVO",
                    "Facility ID": "63115",
                    "Applicant": "STATE UNIVERSITY OF NEW YORK",
                    "Status": "Accepted for Filing",
                    "Public Notice Date URL": "https://example.test/pn",
                }
            ],
            source_mode="pn_application_browser_date_pages",
        )


def make_args(tmp: Path, *, dry_run: bool) -> argparse.Namespace:
    cpb = tmp / "cpb.csv"
    cpb.write_text(CPB_CSV, encoding="utf-8")
    return argparse.Namespace(
        cpb=str(cpb),
        cpb_aliases="",
        state=str(tmp / "pn-state.json"),
        out=str(tmp / "pn-last-run.json"),
        lookback_days=3,
        from_date="2026-05-01",
        to_date="2026-05-06",
        call_sign=None,
        notice_type=["Application"],
        debug_export_dir=None,
        date_page_fallback=True,
        dry_run=dry_run,
    )


class PnCommandTests(unittest.TestCase):
    def test_pn_dry_run_does_not_write_state(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            args = make_args(tmp, dry_run=True)

            with patch.object(cli, "LmsClient", FakePnClient), patch.object(cli, "post_to_slack") as post:
                payload = cli.run_pn(args)

            self.assertEqual(payload["slack_status"], "dry_run")
            self.assertFalse(payload["state_updated"])
            self.assertEqual(payload["matched_counts"]["Application"], 1)
            self.assertEqual(
                payload["alerts"][0]["detail_url"],
                "https://enterpriseefiling.fcc.gov/dataentry/public/tv/publicFacilityDetails.html?facilityId=63115",
            )
            self.assertFalse((tmp / "pn-state.json").exists())
            post.assert_not_called()

    def test_pn_success_writes_separate_state(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            args = make_args(tmp, dry_run=False)

            with patch.dict(os.environ, {"SLACK_WEBHOOK_URL": "https://example.test/webhook"}), patch.object(
                cli, "LmsClient", FakePnClient
            ), patch.object(cli, "post_to_slack"):
                payload = cli.run_pn(args)

            self.assertEqual(payload["slack_status"], "sent")
            self.assertTrue(payload["state_updated"])
            state = json.loads((tmp / "pn-state.json").read_text(encoding="utf-8"))
            self.assertEqual(
                state["seen_items"],
                ["Application PN Search:0000296158::63115:application_public_notice"],
            )

    def test_pn_keeps_direct_application_detail_url_when_present(self) -> None:
        row = {
            "Service": "Full Power FM",
            "File Number": "0000296158",
            "Call Sign": "WRVO",
            "Facility ID": "63115",
            "Detail URL": "https://enterpriseefiling.fcc.gov/dataentry/views/public/fmDraftCopy?appKey=abc&id=abc",
        }

        alerts = cli._build_pn_alerts([row], "Application PN Search", "application_public_notice")

        self.assertEqual(
            alerts[0].detail_url,
            "https://enterpriseefiling.fcc.gov/dataentry/views/public/fmDraftCopy?appKey=abc&id=abc",
        )


if __name__ == "__main__":
    unittest.main()
