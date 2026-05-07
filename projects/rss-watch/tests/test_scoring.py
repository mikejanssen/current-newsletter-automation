from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

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

    def test_radioink_lineup_item_is_not_promoted_by_html_attrs(self) -> None:
        score, reasons = cli._score_item(
            "Derek Fisher & Cody Decker Lead 97.1 The Fan’s LA Lineup",
            """<img align="left" alt="The Fan LA" />
            Audacy's new LA all-sports station, 97.1 The Fan (KNX-FM), has its opening lineup.""",
            "radioink.com",
            None,
        )

        self.assertLess(score, 5)
        self.assertEqual(reasons[0], "low")

    def test_music_industry_filing_item_is_not_promoted_by_html_attrs(self) -> None:
        score, reasons = cli._score_item(
            "Filing: music industry organizations oppose a proposed 43% increase to copyright fees",
            """<A HREF="https://www.musicbusinessworldwide.com/">Music Business Worldwide</A>
            A group of music industry organizations formally opposed a proposed fee increase.""",
            "mediagazer.com",
            None,
        )

        self.assertLess(score, 5)
        self.assertEqual(reasons[0], "low")

    def test_public_media_commentary_remains_visible(self) -> None:
        score, reasons = cli._score_item(
            "Commentary: Why public radio still matters after federal funding cuts",
            "The column argues that public radio remains essential for local journalism and civic life.",
            "example.test",
            None,
        )

        self.assertGreaterEqual(score, 5)
        self.assertIn(reasons[0], {"maybe", "high"})

    def test_public_media_leadership_change_remains_visible(self) -> None:
        score, reasons = cli._score_item(
            "VTDigger names Vermont public media executive Brendan Kinney as its new CEO",
            "The public media leader will become CEO of the nonprofit news outlet.",
            "news.google.com",
            None,
        )

        self.assertGreaterEqual(score, 5)
        self.assertIn(reasons[0], {"maybe", "high"})

    def test_public_media_appointment_without_ceo_term_remains_visible(self) -> None:
        score, reasons = cli._score_item(
            "Kimberly Adams Succeeds Brancaccio at APM’s Marketplace",
            "The public media program named Kimberly Adams as the new host.",
            "radioink.com",
            None,
        )

        self.assertGreaterEqual(score, 5)
        self.assertIn(reasons[0], {"maybe", "high"})

    def test_social_source_public_media_commentary_is_not_suppressed(self) -> None:
        now = datetime.now(timezone.utc)
        items = [
            cli.FeedItem(
                feed_title="Google News",
                title="Commentary: National Public Radio deserves support after federal funding cuts - facebook.com",
                link="https://news.google.com/rss/articles/example?utm_source=x",
                summary="The post argues about public radio funding and local journalism.",
                published=now,
            )
        ]

        ranked, _all_ranked, _counts = cli.dedupe_and_rank(
            items=items,
            since=now - timedelta(hours=1),
            include_seen=True,
            seen_ids=set(),
            max_items=10,
            max_item_age_days=30,
        )

        self.assertEqual(len(ranked), 1)
        self.assertGreaterEqual(ranked[0].score, 5)
        self.assertIn(ranked[0].bucket, {"maybe", "high"})

    def test_technical_pbs_acronym_item_is_low_priority(self) -> None:
        score, reasons = cli._score_item(
            "PBS 4.2 Move groups and namespaces within a datastore for easier backup reorganization",
            "A Proxmox support forum thread about backup tooling.",
            "forum.example",
            None,
        )

        self.assertLess(score, 5)
        self.assertEqual(reasons[0], "low")
        self.assertNotIn("public-media-commentary", reasons)


if __name__ == "__main__":
    unittest.main()
