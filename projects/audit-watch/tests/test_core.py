from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

from audit_watch import core
from audit_watch.models import AuditDocument, StationRecord


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
        <a href="/files/eeo-audit-letter.pdf">EEO audit letter</a>
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
            {"station_id": "c", "station_name": "C", "error": "<urlopen error [Errno 54] Connection reset by peer>"},
        ]

        summary = core.summarize_failures(failures)

        self.assertEqual(summary["failure_count"], 4)
        self.assertEqual(summary["station_failure_count"], 3)
        self.assertEqual(summary["by_type"]["http_404"], 2)
        self.assertEqual(summary["by_type"]["ssl_certificate"], 1)
        self.assertEqual(summary["by_type"]["connection_reset"], 1)
        self.assertEqual(summary["transient_failure_count"], 1)
        self.assertEqual(summary["persistent_failure_count"], 3)
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

    def test_build_payload_marks_latest_documents_by_station(self) -> None:
        docs = [
            AuditDocument(
                station_id="one",
                station_name="One",
                discovered_date=date(2026, 5, 12),
                page_url="https://one.example",
                document_url="https://one.example/fy2023-audit.pdf",
                title="FY2023 Audit",
                file_ext=".pdf",
                status="discovered",
                confidence="high",
            ),
            AuditDocument(
                station_id="one",
                station_name="One",
                discovered_date=date(2026, 5, 12),
                page_url="https://one.example",
                document_url="https://one.example/fy2025-audit.pdf",
                title="FY2025 Audit",
                file_ext=".pdf",
                status="discovered",
                confidence="high",
            ),
            AuditDocument(
                station_id="two",
                station_name="Two",
                discovered_date=date(2026, 5, 12),
                page_url="https://two.example",
                document_url="https://two.example/report-2024.pdf",
                title="Annual Report",
                file_ext=".pdf",
                status="discovered",
                confidence="medium",
            ),
            AuditDocument(
                station_id="three",
                station_name="Three",
                discovered_date=date(2026, 5, 12),
                page_url="https://three.example",
                document_url="https://three.example/uploads/2026/report.pdf",
                title="FY2025 Audited Financial Statements",
                file_ext=".pdf",
                status="discovered",
                confidence="high",
            ),
            AuditDocument(
                station_id="three",
                station_name="Three",
                discovered_date=date(2026, 5, 12),
                page_url="https://three.example",
                document_url="https://three.example/annualreport2026.pdf",
                title="2026 Annual Report",
                file_ext=".pdf",
                status="discovered",
                confidence="medium",
            ),
        ]

        payload = core.build_payload(docs, [])
        out = payload["new_documents"]

        self.assertEqual(out[0]["title"], "FY2025 Audit")
        self.assertEqual(out[0]["document_year"], 2025)
        self.assertEqual(out[0]["document_kind"], "audited_financial")
        self.assertTrue(out[0]["is_latest_for_station"])
        self.assertEqual(
            next(doc for doc in out if doc["title"] == "FY2025 Audited Financial Statements")["document_year"],
            2025,
        )
        self.assertTrue(
            next(doc for doc in out if doc["title"] == "FY2025 Audited Financial Statements")[
                "is_latest_for_station"
            ]
        )
        self.assertFalse(next(doc for doc in out if doc["title"] == "2026 Annual Report")["is_latest_for_station"])
        self.assertFalse(next(doc for doc in out if doc["title"] == "FY2023 Audit")["is_latest_for_station"])

    def test_extract_document_year_accepts_two_digit_fiscal_years(self) -> None:
        self.assertEqual(core.extract_document_year("Report of Independent Auditors FY25", "https://example.org/a.pdf"), 2025)
        self.assertEqual(core.extract_document_year("FYE22 Financial Statement", "https://example.org/a.pdf"), 2022)
        self.assertEqual(core.extract_document_year("Annual Audited Financial Statement", "https://example.org/file_FY24.pdf"), 2024)
        self.assertEqual(core.extract_document_year("Audited Financials 6-30-24", "https://example.org/a.pdf"), 2024)
        self.assertEqual(core.extract_document_year("Audit", "https://example.org/Audit-Final-20250630.pdf"), 2025)
        self.assertEqual(core.extract_document_year("Audit", "https://example.org/FS-WICN-06302025-FINAL.pdf"), 2025)

    def test_extract_document_year_ignores_upload_path_years(self) -> None:
        self.assertEqual(
            core.extract_document_year(
                "RCR Financial Statements",
                "https://example.org/wp-content/uploads/2026/01/RCR-FY2023-Audited-Financial-Statements.pdf",
            ),
            2023,
        )
        self.assertIsNone(
            core.extract_document_year(
                "RCR Financial Statements",
                "https://example.org/wp-content/uploads/2026/01/RCR-Audited-Financial-Statements.pdf",
            )
        )

    def test_flags_ignore_standard_going_concern_boilerplate(self) -> None:
        text = (
            "Management is required to evaluate whether there are conditions or events, "
            "considered in the aggregate, that raise substantial doubt about the Organization's "
            "ability to continue as a going concern for one year after the date that the "
            "financial statements are available to be issued. Conclude whether, in our judgment, "
            "there are conditions or events, considered in the aggregate, that raise substantial "
            "doubt about the Organization's ability to continue as a going concern for a "
            "reasonable period of time."
        )

        self.assertEqual(core._flags_for_text(text), [])

    def test_flags_keep_actual_going_concern_findings(self) -> None:
        text = (
            "The accompanying financial statements have been prepared assuming the Organization "
            "will continue as a going concern. The Organization has suffered recurring losses "
            "that raise substantial doubt about its ability to continue as a going concern."
        )

        self.assertEqual(core._flags_for_text(text), ["Going concern finding"])

    def test_flags_ignore_no_noncompliance_boilerplate(self) -> None:
        text = (
            "The grants are subject to audit and if found to be in error or noncompliance, "
            "could result in refunds. We performed tests of compliance with provisions, "
            "noncompliance with which could have a direct and material effect. The results "
            "of our tests disclosed no instances of noncompliance or other matters that are "
            "required to be reported under Government Auditing Standards."
        )

        self.assertEqual(core._flags_for_text(text), [])

    def test_flags_keep_actual_noncompliance_findings(self) -> None:
        self.assertEqual(
            core._flags_for_text("The audit disclosed instances of noncompliance required to be reported."),
            ["Noncompliance finding"],
        )

    def test_flags_ignore_internal_control_definitions_and_negative_findings(self) -> None:
        text = (
            "A material weakness is a deficiency, or a combination of deficiencies, in internal control "
            "such that there is a reasonable possibility that a material misstatement will not be prevented. "
            "A significant deficiency is a deficiency, or a combination of deficiencies, in internal control "
            "that is less severe than a material weakness, yet important enough to merit attention by those "
            "charged with governance. Given these limitations, during our audit we did not identify any "
            "deficiencies in internal control that we consider to be material weaknesses. However, material "
            "weaknesses or significant deficiencies may exist that have not been identified."
        )

        self.assertEqual(core._flags_for_text(text), [])

    def test_flags_keep_actual_internal_control_findings(self) -> None:
        self.assertEqual(
            core._flags_for_text(
                "Our audit identified a material weakness in internal control and a significant deficiency."
            ),
            ["Material weakness noted", "Significant deficiency noted"],
        )


if __name__ == "__main__":
    unittest.main()
