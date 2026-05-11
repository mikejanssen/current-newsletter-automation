from __future__ import annotations

import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

from audit_watch import core
from audit_watch.models import StationRecord


class CoreTests(unittest.TestCase):
    def test_discover_station_docs_finds_audit_links_and_ignores_noise(self) -> None:
        station = StationRecord(
            station_id="sample-station",
            station_name="Sample Station",
            page_url="https://example.org/about/financials",
        )
        html = """
        <a href="/files/FY2025-audited-financial-statements.pdf">FY2025 Audited Financial Statements</a>
        <a href="/files/program-schedule.pdf">Program Schedule</a>
        <a href="https://cdn.example.org/reports/annual-report-2025.xlsx">Annual Report 2025</a>
        """

        docs = core.discover_station_docs(station, timeout_seconds=1, html_override=html)

        self.assertEqual(len(docs), 2)
        self.assertEqual({d.file_ext for d in docs}, {".pdf", ".xlsx"})
        self.assertTrue(all(d.station_id == "sample-station" for d in docs))

    def test_validate_station_records_reports_config_issues(self) -> None:
        stations = [
            StationRecord("one", "One", "https://one.example/audits", enabled=True),
            StationRecord("one", "Duplicate", "https://dup.example/audits", enabled=True),
            StationRecord("bad-url", "Bad URL", "ftp://bad.example/audits", enabled=True),
            StationRecord("missing-url", "Missing URL", "", enabled=True),
            StationRecord("disabled-ready", "Disabled Ready", "https://ready.example/audits", enabled=False),
        ]

        payload = core.validate_station_records(stations)

        self.assertEqual(payload["station_count"], 5)
        self.assertEqual(payload["enabled_count"], 4)
        self.assertEqual(payload["issue_count"], 3)
        self.assertEqual(payload["duplicate_station_ids"], ["one"])
        self.assertEqual(len(payload["malformed_urls"]), 1)
        self.assertEqual(len(payload["enabled_without_page_url"]), 1)
        self.assertEqual(len(payload["disabled_with_page_url"]), 1)

    def test_fetch_text_accepts_html_body_from_404_page(self) -> None:
        error = HTTPError(
            "https://example.org/reports",
            404,
            "Not Found",
            {"Content-Type": "text/html; charset=UTF-8"},
            BytesIO(b"<html><a href='/audit.pdf'>Audit</a></html>"),
        )

        with patch.object(core, "_open_url", side_effect=error):
            text = core._fetch_text("https://example.org/reports", timeout_seconds=1)

        self.assertIn("audit.pdf", text)

    def test_summarize_failures_groups_types_and_stations(self) -> None:
        failures = [
            {"station_id": "a", "station_name": "A", "error": "HTTP Error 404: Not Found"},
            {"station_id": "a", "station_name": "A", "error": "HTTP Error 404: Not Found"},
            {"station_id": "b", "station_name": "B", "error": "certificate verify failed: CERTIFICATE_VERIFY_FAILED"},
        ]

        summary = core.summarize_failures(failures)

        self.assertEqual(summary["failure_count"], 3)
        self.assertEqual(summary["station_failure_count"], 2)
        self.assertEqual(summary["by_type"]["http_404"], 2)
        self.assertEqual(summary["by_type"]["ssl_certificate"], 1)
        self.assertEqual(summary["by_station"][0]["station_id"], "a")

    def test_build_health_payload_preserves_statuses(self) -> None:
        run_payload = {
            "run_date": "2026-05-11",
            "counts": {"new_documents": 2, "flagged_documents": 1, "stations_with_failures": 1},
        }
        failures_payload = {"failures": [{"station_id": "a", "station_name": "A", "error": "HTTP Error 404"}]}
        risk_payload = {"strict_station_count": 1, "watchlist_station_count": 2}

        health = core.build_health_payload(
            run_payload=run_payload,
            failures_payload=failures_payload,
            risk_payload=risk_payload,
            started_at="2026-05-11T10:00:00+00:00",
            finished_at="2026-05-11T10:01:00+00:00",
            scan_status="completed",
            risk_status="completed",
            slack_status="failed",
            slack_error="HTTP Error 404",
        )

        self.assertEqual(health["scan_status"], "completed")
        self.assertEqual(health["risk_status"], "completed")
        self.assertEqual(health["slack_status"], "failed")
        self.assertEqual(health["counts"]["strict_risk_stations"], 1)
        self.assertEqual(health["failure_summary"]["failure_count"], 1)

    def test_state_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            core.save_state(path, {"b", "a"})
            payload = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual(payload["seen_doc_ids"], ["a", "b"])
            self.assertEqual(core.load_state(path), {"a", "b"})


if __name__ == "__main__":
    unittest.main()
