from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from rss_watch import cli


class ScoringTests(unittest.TestCase):
    def test_pbs_station_archive_legal_dispute_is_high_priority(self) -> None:
        score, reasons = cli._score_item(
            "70 years of St. Louis history is stuck in a Denver data center, PBS station claims - The Denver Post",
            "",
            "news.google.com",
            None,
        )

        self.assertGreaterEqual(score, 8)
        self.assertEqual(reasons[0], "high")
        self.assertIn("public-media-legal-dispute", reasons)

    def test_callsign_station_sale_is_high_priority(self) -> None:
        score, reasons = cli._score_item(
            "WSU considers sale of KWSU-TV license",
            "Washington State University may transfer the public television station to a buyer.",
            "news.wsu.edu",
            None,
        )

        self.assertGreaterEqual(score, 8)
        self.assertEqual(reasons[0], "high")
        self.assertIn("callsign+ownership-disposition", reasons)

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

    def test_pbs_programming_cuts_are_visible(self) -> None:
        score, reasons = cli._score_item(
            "Arkansas TV quietly prunes some PBS programming from lineup",
            "Arkansas TV is cutting PBS news programming to make room for shows filmed in Arkansas.",
            "arktimes.com",
            None,
        )

        self.assertGreaterEqual(score, 5)
        self.assertIn(reasons[0], {"maybe", "high"})
        self.assertIn("weak-brand+station-operations-disruption", reasons)

    def test_uwf_dropping_npr_headline_is_high_priority(self) -> None:
        score, reasons = cli._score_item(
            "UWF Trustees Considering Dropping NPR",
            "",
            "ricksblog.biz",
            None,
        )

        self.assertGreaterEqual(score, 8)
        self.assertEqual(reasons[0], "high")
        self.assertIn("weak-brand+network-disaffiliation", reasons)

    def test_station_ending_pbs_affiliation_headline_is_high_priority(self) -> None:
        score, reasons = cli._score_item(
            "Vincennes University’s WVUT-TV to end PBS affiliation",
            "",
            "news.google.com",
            None,
        )

        self.assertGreaterEqual(score, 8)
        self.assertEqual(reasons[0], "high")
        self.assertIn("weak-brand+network-disaffiliation", reasons)

    def test_station_ending_affiliation_with_npr_is_high_priority(self) -> None:
        score, reasons = cli._score_item(
            "College station ends its affiliation with NPR",
            "",
            "localnews.example",
            None,
        )

        self.assertGreaterEqual(score, 8)
        self.assertEqual(reasons[0], "high")
        self.assertIn("weak-brand+network-disaffiliation", reasons)

    def test_station_cutting_pbs_programming_is_high_priority(self) -> None:
        score, reasons = cli._score_item(
            "Local station cuts PBS programming from its schedule",
            "",
            "localnews.example",
            None,
        )

        self.assertGreaterEqual(score, 8)
        self.assertEqual(reasons[0], "high")
        self.assertIn("weak-brand+network-disaffiliation", reasons)

    def test_generic_funding_cuts_are_not_mislabeled_as_disaffiliation(self) -> None:
        _score, reasons = cli._score_item(
            "Federal funding cuts threaten NPR stations",
            "Local stations say the cuts could lead to layoffs.",
            "localnews.example",
            None,
        )

        self.assertNotIn("weak-brand+network-disaffiliation", reasons)

    def test_npr_source_attribution_is_not_mislabeled_as_disaffiliation(self) -> None:
        _score, reasons = cli._score_item(
            "She criticized the president during the shutdown. Now she's been put on leave - NPR",
            "",
            "news.google.com",
            None,
        )

        self.assertNotIn("weak-brand+network-disaffiliation", reasons)

    def test_npr_source_attribution_after_funding_drop_is_not_disaffiliation(self) -> None:
        _score, reasons = cli._score_item(
            "EPA speeds cleanup despite funding drop - NPR",
            "",
            "news.google.com",
            None,
        )

        self.assertNotIn("weak-brand+network-disaffiliation", reasons)

    def test_person_leaving_pbs_news_is_not_station_disaffiliation(self) -> None:
        _score, reasons = cli._score_item(
            "John Yang will leave PBS News",
            "",
            "news.google.com",
            None,
        )

        self.assertNotIn("weak-brand+network-disaffiliation", reasons)

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

    def test_oeta_publicly_funded_broadcasting_veto_story_is_high_priority(self) -> None:
        score, reasons = cli._score_item(
            "Stitt vetoes OETA, objects to publicly funded broadcasting",
            "The governor objected to state support for the public television authority.",
            "oklahomavoice.com",
            None,
        )

        self.assertGreaterEqual(score, 8)
        self.assertEqual(reasons[0], "high")

    def test_public_tv_operator_selection_story_is_high_priority(self) -> None:
        score, reasons = cli._score_item(
            "2 organizations competing to run NJ public television",
            "A state process will determine who operates the public television service.",
            "subscriber.politicopro.com",
            None,
        )

        self.assertGreaterEqual(score, 8)
        self.assertEqual(reasons[0], "high")
        self.assertIn("public-media-operator-selection", reasons)

    def test_public_tv_franchise_story_is_high_priority(self) -> None:
        score, reasons = cli._score_item(
            "Montclair State will get N.J. public television franchise",
            "The university will take over public television after a competitive bidding process.",
            "newjerseyglobe.com",
            None,
        )

        self.assertGreaterEqual(score, 8)
        self.assertEqual(reasons[0], "high")
        self.assertIn("public-media-operator-selection", reasons)

    def test_weak_brand_rescue_governance_story_is_high_priority(self) -> None:
        score, reasons = cli._score_item(
            "Governor’s plan to save NJ PBS moves toward finish line - New Jersey Monitor",
            "Lawmakers are weighing a plan affecting the state public broadcaster.",
            "news.google.com",
            None,
        )

        self.assertGreaterEqual(score, 8)
        self.assertEqual(reasons[0], "high")
        self.assertIn("weak-brand+rescue-governance", reasons)

    def test_weak_brand_app_promo_with_free_language_stays_low_priority(self) -> None:
        score, reasons = cli._score_item(
            "Download the PBS KIDS Video app and explore America this summer! Always free.",
            "Watch videos with no ads.",
            "instagram.com",
            None,
        )

        self.assertLess(score, 5)
        self.assertEqual(reasons[0], "low")
        self.assertNotIn("weak-brand+rescue-governance", reasons)

    def test_generic_veto_story_without_public_media_context_is_low_priority(self) -> None:
        score, reasons = cli._score_item(
            "Governor vetoes tax credit bill",
            "Lawmakers may try to override the veto before the session ends.",
            "example.test",
            None,
        )

        self.assertLess(score, 5)
        self.assertEqual(reasons[0], "low")

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

    def test_direct_source_suppresses_matching_google_news_copy(self) -> None:
        now = datetime.now(timezone.utc)
        title = "Arkansas TV quietly prunes some PBS programming from lineup"
        items = [
            cli.FeedItem(
                feed_title="Arkansas Times",
                title=title,
                link="https://arktimes.com/arkansas-blog/2026/06/24/arkansas-tv-quietly-prunes-some-pbs-programming-from-lineup",
                summary="Arkansas TV is cutting PBS news programming.",
                published=now,
            ),
            cli.FeedItem(
                feed_title="PBS - Google News",
                title=f"{title} - Arkansas Times",
                link="https://news.google.com/rss/articles/example",
                summary="Arkansas TV is cutting PBS news programming.",
                published=now,
            ),
        ]

        ranked, all_ranked, _counts = cli.dedupe_and_rank(
            items=items,
            since=now - timedelta(hours=1),
            include_seen=True,
            seen_ids=set(),
            max_items=10,
            max_item_age_days=30,
        )

        self.assertEqual(len(ranked), 1)
        self.assertEqual(len(all_ranked), 1)
        self.assertEqual(ranked[0].domain, "arktimes.com")

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
