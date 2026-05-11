from __future__ import annotations

import unittest

from audit_watch import cli


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


if __name__ == "__main__":
    unittest.main()
