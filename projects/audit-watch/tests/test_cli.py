from __future__ import annotations

from argparse import Namespace
import csv
from datetime import date
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import redirect_stdout

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
                        "document_year": 2025,
                        "is_latest_for_station": True,
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
        self.assertIn("(2025) [latest]", text)
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
                archive_scope="all",
            )

            with patch.object(cli, "discover_station_docs", return_value=[doc]), patch.object(cli, "archive_document") as archive:
                result = cli._run_daily_scan(args)

            self.assertFalse(result["archive_enabled"])
            self.assertEqual(result["documents_discovered"], 1)
            self.assertEqual(result["payload"]["scan_status"], "discovered")
            self.assertEqual(result["payload"]["counts"]["documents_archived"], 0)
            self.assertFalse((root / "state.json").exists())
            archive.assert_not_called()

    def test_parse_launchd_print_extracts_status_fields(self) -> None:
        parsed = cli._parse_launchd_print(
            """
            path = /Users/jansen/Library/LaunchAgents/com.current.audit-watch.weekly.plist
            state = not running
            environment = {
                AUDIT_WATCH_ARCHIVE_SCOPE => latest
            }
            runs = 3
            last exit code = 0
            descriptor = {
                "Minute" => 10
                "Hour" => 9
                "Weekday" => 1
            }
            """
        )

        self.assertEqual(parsed["state"], "not running")
        self.assertEqual(parsed["runs"], "3")
        self.assertEqual(parsed["last_exit_code"], "0")
        self.assertEqual(parsed["archive_scope"], "latest")
        self.assertEqual(parsed["schedule"], "weekday 1 at 09:10")

    def test_review_disabled_scans_disabled_rows_with_urls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stations = root / "stations.csv"
            stations.write_text(
                "station_id,station_name,page_url,enabled,notes\n"
                "enabled,Enabled,https://enabled.example/audits,1,\n"
                "disabled-url,Disabled URL,https://disabled.example/audits,0,review me\n"
                "disabled-blank,Disabled Blank,,0,\n",
                encoding="utf-8",
            )
            doc = AuditDocument(
                station_id="disabled-url",
                station_name="Disabled URL",
                discovered_date=date(2026, 5, 11),
                page_url="https://disabled.example/audits",
                document_url="https://disabled.example/audit.pdf",
                title="FY2025 Audit",
                file_ext=".pdf",
                status="discovered",
                confidence="high",
            )
            args = Namespace(
                stations=str(stations),
                out=str(root / "disabled-review.json"),
                csv_out=str(root / "disabled-review.csv"),
                failures_out=str(root / "disabled-review-failures.json"),
                timeout_seconds=1,
                limit=25,
                offset=0,
                workers=2,
            )

            with patch.object(cli, "discover_station_docs", return_value=[doc]) as discover:
                code = cli._cmd_review_disabled(args)

            self.assertEqual(code, 0)
            discover.assert_called_once()
            payload = json.loads((root / "disabled-review.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["disabled_with_urls"], 1)
            self.assertEqual(payload["stations_scanned"], 1)
            self.assertEqual(payload["stations_with_candidate_documents"], 1)
            self.assertEqual(payload["candidate_documents"], 1)
            self.assertIn("FY2025 Audit", (root / "disabled-review.csv").read_text(encoding="utf-8"))

    def test_status_prints_health_failures_and_launchd_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            health = root / "health.json"
            failures = root / "failures.json"
            health.write_text(
                json.dumps(
                    {
                        "last_run_date": "2026-05-13",
                        "started_at": "2026-05-13T14:00:00+00:00",
                        "finished_at": "2026-05-13T14:05:00+00:00",
                        "scan_status": "completed",
                        "risk_status": "completed",
                        "slack_status": "dry_run",
                        "counts": {
                            "new_documents": 1,
                            "flagged_documents": 0,
                            "stations_with_failures": 1,
                            "strict_risk_stations": 0,
                            "watchlist_risk_stations": 0,
                        },
                    }
                ),
                encoding="utf-8",
            )
            failures.write_text(
                json.dumps(
                    {
                        "failure_count": 1,
                        "skipped_count": 2,
                        "skipped_by_reason": {"disabled entry": 2},
                        "failures": [
                            {
                                "station_name": "Mountain Lake",
                                "page_url": "https://mountainlake.org/about/",
                                "error": "read timeout",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            args = Namespace(
                health=str(health),
                failures=str(failures),
                limit_failures=5,
                launchd_label="com.current.audit-watch.weekly",
                no_launchd=False,
            )

            with patch.object(
                cli,
                "_launchd_status",
                return_value=({"state": "not running", "runs": "3", "last_exit_code": "0", "archive_scope": "latest"}, ""),
            ):
                out = io.StringIO()
                with redirect_stdout(out):
                    code = cli._cmd_status(args)

            self.assertEqual(code, 0)
            text = out.getvalue()
            self.assertIn("Last run: 2026-05-13", text)
            self.assertIn("new_docs=1", text)
            self.assertIn("Mountain Lake", text)
            self.assertIn("LaunchAgent archive scope: latest", text)

    def test_run_daily_scan_reports_specific_skipped_reasons(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stations = root / "stations.csv"
            stations.write_text(
                "station_id,station_name,page_url,enabled\n"
                "ready,Ready,https://ready.example/audits,1\n"
                "disabled,Disabled,https://disabled.example/audits,0\n"
                "missing,Missing,,1\n",
                encoding="utf-8",
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
                archive_scope="all",
            )

            with patch.object(cli, "discover_station_docs", return_value=[]):
                result = cli._run_daily_scan(args)

            failures_payload = result["failures_payload"]
            self.assertEqual(failures_payload["skipped_count"], 2)
            self.assertEqual(
                failures_payload["skipped_by_reason"],
                {"disabled entry": 1, "enabled without page_url": 1},
            )
            self.assertEqual(
                {row["station_id"]: row["reason"] for row in failures_payload["skipped"]},
                {"disabled": "disabled entry", "missing": "enabled without page_url"},
            )

    def test_run_daily_scan_latest_archive_scope_archives_only_current_docs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stations = root / "stations.csv"
            stations.write_text(
                "station_id,station_name,page_url,enabled\n"
                "one,One,https://one.example/audits,1\n",
                encoding="utf-8",
            )
            old_doc = AuditDocument(
                station_id="one",
                station_name="One",
                discovered_date=date(2026, 5, 11),
                page_url="https://one.example/audits",
                document_url="https://one.example/fy2023-audit.pdf",
                title="FY2023 Audit",
                file_ext=".pdf",
                status="discovered",
                confidence="high",
            )
            latest_doc = AuditDocument(
                station_id="one",
                station_name="One",
                discovered_date=date(2026, 5, 11),
                page_url="https://one.example/audits",
                document_url="https://one.example/fy2025-audit.pdf",
                title="FY2025 Audit",
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
                no_archive=False,
                archive_scope="latest",
            )

            def fake_archive(doc: AuditDocument, archive_root: Path, timeout_seconds: int) -> AuditDocument:
                return AuditDocument(
                    station_id=doc.station_id,
                    station_name=doc.station_name,
                    discovered_date=doc.discovered_date,
                    page_url=doc.page_url,
                    document_url=doc.document_url,
                    title=doc.title,
                    file_ext=doc.file_ext,
                    status="downloaded",
                    confidence=doc.confidence,
                    downloaded_path=str(archive_root / doc.station_id / "fake.pdf"),
                )

            with patch.object(cli, "discover_station_docs", return_value=[old_doc, latest_doc]), patch.object(
                cli, "archive_document", side_effect=fake_archive
            ) as archive:
                result = cli._run_daily_scan(args)

            self.assertEqual(result["documents_discovered"], 2)
            self.assertEqual(result["documents_archive_candidates"], 1)
            self.assertEqual(result["payload"]["counts"]["documents_skipped_by_archive_scope"], 1)
            self.assertEqual([doc["title"] for doc in result["payload"]["new_documents"]], ["FY2025 Audit"])
            archive.assert_called_once()

    def test_run_daily_scan_dry_run_does_not_archive_or_update_state(self) -> None:
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
                document_url="https://one.example/fy2025-audit.pdf",
                title="FY2025 Audit",
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
                dry_run=True,
                health_out=str(root / "health.json"),
                max_stations=None,
                workers=4,
                discover_only=False,
                no_archive=False,
                archive_scope="latest",
            )

            with patch.object(cli, "discover_station_docs", return_value=[doc]), patch.object(cli, "archive_document") as archive:
                result = cli._run_daily_scan(args)

            self.assertFalse(result["archive_enabled"])
            self.assertEqual(result["payload"]["scan_status"], "discovered")
            self.assertEqual(result["payload"]["counts"]["documents_archived"], 0)
            self.assertFalse((root / "state.json").exists())
            archive.assert_not_called()

    def test_run_daily_scan_retries_transient_station_failures(self) -> None:
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
                document_url="https://one.example/fy2025-audit.pdf",
                title="FY2025 Audit",
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
                failure_retry_passes=1,
                failure_retry_workers=1,
                failure_retry_timeout_multiplier=2.0,
                discover_only=False,
                no_archive=True,
                archive_scope="all",
            )
            calls = {"count": 0}

            def fake_discover(*_args, **_kwargs):
                calls["count"] += 1
                if calls["count"] == 1:
                    raise TimeoutError("The read operation timed out")
                return [doc]

            with patch.object(cli, "discover_station_docs", side_effect=fake_discover):
                result = cli._run_daily_scan(args)

            self.assertEqual(calls["count"], 2)
            self.assertEqual(result["payload"]["retry_summary"]["station_retry_attempts"], 1)
            self.assertEqual(result["payload"]["counts"]["new_documents"], 1)
            self.assertEqual(result["failures_payload"]["failure_count"], 0)

    def test_recent_docs_reports_recent_archive_files_without_upload_year_false_positives(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "audits"
            recent_dir = archive / "one" / "2026-05-21"
            recent_dir.mkdir(parents=True)
            (recent_dir / "FY25-Audit.pdf").write_text("x", encoding="utf-8")
            (recent_dir / "FY2023-Audit.pdf").write_text("x", encoding="utf-8")
            upload_only_dir = archive / "two" / "2026-05-21"
            upload_only_dir.mkdir(parents=True)
            (upload_only_dir / "Audited-Financial-Statements.pdf").write_text("x", encoding="utf-8")
            old_archive_dir = archive / "three" / "2026-03-03"
            old_archive_dir.mkdir(parents=True)
            (old_archive_dir / "FY25-Audit.pdf").write_text("x", encoding="utf-8")
            stations = root / "stations.csv"
            stations.write_text(
                "station_id,station_name,page_url,enabled\n"
                "one,One,https://one.example/audits,1\n"
                "two,Two,https://two.example/audits,1\n"
                "three,Three,https://three.example/audits,1\n",
                encoding="utf-8",
            )
            args = Namespace(
                archive_root=str(archive),
                run_json=[],
                stations=str(stations),
                since_year=2025,
                after_archive_date="2026-03-04",
                out=str(root / "recent.csv"),
            )

            code = cli._cmd_recent_docs(args)

            self.assertEqual(code, 0)
            with (root / "recent.csv").open(encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["station_id"], "one")
            self.assertEqual(rows[0]["document_year"], "2025")
            self.assertEqual(rows[0]["title"], "FY25-Audit")


if __name__ == "__main__":
    unittest.main()
