from fcc_lms_watch.html_forms import parse_public_notice_date_links


import unittest


class PublicNoticeParserTests(unittest.TestCase):
    def test_parse_public_notice_date_links_dedupes_and_absolutizes(self) -> None:
        html = """
        <a href="/dataentry/public/tv/publicNoticeSearchResult.html?pnDate=05%2F06%2F2026">05/06/2026</a>
        <a href="/dataentry/public/tv/publicNoticeSearchResult.html?pnDate=05%2F06%2F2026">dupe</a>
        <a href="https://enterpriseefiling.fcc.gov/dataentry/public/tv/publicNoticeSearchResult.html?pnDate=05%2F05%2F2026">05/05/2026</a>
        """
        self.assertEqual(
            parse_public_notice_date_links(html),
            [
                "https://enterpriseefiling.fcc.gov/dataentry/public/tv/publicNoticeSearchResult.html?pnDate=05%2F06%2F2026",
                "https://enterpriseefiling.fcc.gov/dataentry/public/tv/publicNoticeSearchResult.html?pnDate=05%2F05%2F2026",
            ],
        )


if __name__ == "__main__":
    unittest.main()
