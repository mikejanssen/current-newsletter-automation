from __future__ import annotations

from argparse import Namespace
from datetime import date
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from audit_watch import cli
from audit_watch.models import AuditDocument


class CliTests(unittest.TestCase):
    def test_slack_text_includes_failures_and_risks(self) -> None:
        text = cli._slack_text(
            run_payload={
                "run_date": "2026-05-11",
                "counts": {"new_documents": 1, "flagged_documents": 1, "stations_with_failures": 1},
                "new_documents": [
                    {
                        "station_name": "Sample Station",
                        "title": "FY2025 Audit",
                        "document_url": "https://example.org/audit.pdf",
                        "flags": "Going concern language",
                    }
                ],
            },
            failures_payload={
                "failures": [
                    {
                        "station_name": "Broken Station",
                        "page_url": "https://broken.example/audits",
                        "error": "HTTP Error 404: Not Found",
                    }
                ]
            },
            risk_payload={
                "strict_station_count": 1,
                "watchlist_station_count": 1,
                "strict_highlights": [{"pattern": "material weakness", "station_name": "Strict", "title": "Audit"}],
                "watchlist_highlights": [{"pattern": "going concern", "station_name": "Watch", "title": "Audit"}],
            },
            max_new_docs=5,
            max_failures=5,
            max_strict=5,
            max_watchlist=5,
        )

        self.assertIn("*Audit Watch* (2026-05-11)", text)
        self.assertIn("Sample Station: FY2025 Audit", text)
        self.assertIn("Broken Station", text)
        self.assertIn("Strict risk highlights", text)
        self.assertIn("Watchlist highlights", text)

    def test_run_daily_scan_no_archive_discovers_without_state_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stations = root / "stations.csv"
            stations.write_text(
                "station_id,station_name,page_url,enabled\n"
                "one,One,https://one.example/audits,1\n",
                encoding="utf-8",
            )
            doc = AuditDocument(
                station_id="one",
                station_name="One",
                discovered_date=date(2026, 5, 11),
                page_url="https://one.example/audits",
                document_url="https://one.example/audit.pdf",
                title="Audit",
                file_ext=".pdf",
                status="discovered",
                confidence="high",
            )
            args = Namespace(
                stations=str(stations),
                state=str(root / "state.json"),
                out=str(root / "last-run.json"),
                brief=str(root / "briefing.md"),
                failures_out=str(root / "failures.json"),
                archive_root=str(root / "audits"),
                timeout_seconds=1,
                dry_run=False,
                health_out=str(root / "health.json"),
                max_stations=None,
                workers=4,
                discover_only=False,
                no_archive=True,
            )

            with patch.object(cli, "discover_station_docs", return_value=[doc]), patch.object(cli, "archive_document") as archive:
                result = cli._run_daily_scan(args)

            self.assertFalse(result["archive_enabled"])
            self.assertEqual(result["documents_discovered"], 1)
            self.assertEqual(result["payload"]["scan_status"], "discovered")
            self.assertEqual(result["payload"]["counts"]["documents_archived"], 0)
            self.assertFalse((root / "state.json").exists())
            archive.assert_not_called()


if __name__ == "__main__":
    unittest.main()
