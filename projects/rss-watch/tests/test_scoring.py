from __future__ import annotations

import unittest

from rss_watch import cli


class ScoringTests(unittest.TestCase):
    def test_public_radio_funding_story_is_high_priority(self) -> None:
        score, reasons = cli._score_item(
            "Public radio station faces federal funding cuts",
            "The station says layoffs are possible if CPB funding is reduced.",
            "localnews.example",
            None,
        )

        self.assertGreaterEqual(score, 8)
        self.assertEqual(reasons[0], "high")

    def test_weak_brand_entertainment_story_is_low_priority(self) -> None:
        score, reasons = cli._score_item(
            "NPR Tiny Desk concert clip goes viral",
            "A musician shared a YouTube performance on TikTok.",
            "example.test",
            None,
        )

        self.assertLess(score, 5)
        self.assertEqual(reasons[0], "low")


if __name__ == "__main__":
    unittest.main()
