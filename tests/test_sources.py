import unittest

from news_scanner_v2.sources import BRAVE_NEWS_SOURCES, RSS_FALLBACK_SOURCES


class SourceTests(unittest.TestCase):
    def test_ma_queries_cover_offer_bid_and_stake_deal_language(self) -> None:
        brave_ma = next(source for source in BRAVE_NEWS_SOURCES if source.name == "brave-news-ma-buyback")
        rss_ma = next(source for source in RSS_FALLBACK_SOURCES if source.name == "google-news-ma-buyback")

        brave_query = brave_ma.query.lower()
        rss_url = rss_ma.url.lower()

        for term in ("buyout offer", "takeover bid", "binding offer", "stake deal"):
            self.assertIn(term, brave_query)
            self.assertIn(term.replace(" ", "+"), rss_url)


if __name__ == "__main__":
    unittest.main()
