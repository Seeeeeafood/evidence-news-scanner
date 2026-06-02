import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from news_scanner_v2.fetcher import fetch_brave_news, parse_brave_news_json, parse_feed_xml
from news_scanner_v2.sources import NewsSource, required_category_status


RSS_SAMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item>
      <title>Fed leaves rates unchanged</title>
      <link>https://example.com/markets/fed?utm_source=test</link>
      <pubDate>Thu, 14 May 2026 12:30:00 GMT</pubDate>
      <description>Policy statement text</description>
    </item>
  </channel>
</rss>
"""


ATOM_SAMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Company announces guidance raise</title>
    <link href="https://example.com/company/guidance" />
    <updated>2026-05-14T12:45:00Z</updated>
    <summary>Updated outlook</summary>
  </entry>
</feed>
"""


class FetcherTests(unittest.TestCase):
    def test_parse_rss_sample(self) -> None:
        source = NewsSource(name="sample", category="MACRO", url="https://example.com/rss")
        items = parse_feed_xml(RSS_SAMPLE, source)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Fed leaves rates unchanged")
        self.assertEqual(items[0].canonical_url, "https://example.com/markets/fed")
        self.assertEqual(items[0].published_at, "2026-05-14T12:30:00+00:00")

    def test_parse_atom_sample(self) -> None:
        source = NewsSource(name="sample", category="EARN", url="https://example.com/atom")
        items = parse_feed_xml(ATOM_SAMPLE, source)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].url, "https://example.com/company/guidance")
        self.assertEqual(items[0].published_at, "2026-05-14T12:45:00+00:00")

    def test_required_category_status(self) -> None:
        status = required_category_status({"GEO", "EARN"})
        self.assertTrue(status["GEO"])
        self.assertTrue(status["EARN"])
        self.assertFalse(status["MACRO"])

    def test_parse_brave_news_sample(self) -> None:
        raw = b"""{
          "type": "news",
          "results": [
            {
              "title": "Stocks rise after Fed decision",
              "url": "https://example.com/fed?utm_source=x",
              "description": "Markets moved after the decision.",
              "page_age": "2026-05-14T12:45:00Z"
            }
          ]
        }"""
        source = NewsSource(
            name="brave-news-sample",
            category="MACRO",
            url="https://api.search.brave.com/res/v1/news/search",
            kind="brave_news",
            provider="brave",
            query="Fed stocks",
        )
        items = parse_brave_news_json(raw, source)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].provider, "brave")
        self.assertEqual(items[0].canonical_url, "https://example.com/fed")
        self.assertEqual(items[0].published_at, "2026-05-14T12:45:00+00:00")

    def test_fetch_brave_missing_key_skips_without_network(self) -> None:
        source = NewsSource(
            name="brave-news-sample",
            category="MACRO",
            url="https://api.search.brave.com/res/v1/news/search",
            kind="brave_news",
            provider="brave",
            query="Fed stocks",
        )
        with patch("urllib.request.urlopen") as urlopen:
            result = fetch_brave_news(source, api_key=None)
        self.assertEqual(result.status, "skipped_missing_key")
        self.assertEqual(len(result.items), 0)
        urlopen.assert_not_called()

    def test_fetch_brave_http_errors_are_recorded(self) -> None:
        source = NewsSource(
            name="brave-news-sample",
            category="MACRO",
            url="https://api.search.brave.com/res/v1/news/search",
            kind="brave_news",
            provider="brave",
            query="Fed stocks",
        )
        for code in (401, 403, 429):
            with self.subTest(code=code), patch(
                "urllib.request.urlopen",
                side_effect=HTTPError(source.url, code, "HTTP failure", None, None),
            ):
                result = fetch_brave_news(source, api_key="token")
            self.assertEqual(result.status, "error")
            self.assertEqual(result.http_status, code)
            self.assertIn(f"HTTP {code}", result.error or "")
            self.assertNotIn("token", result.error or "")

    def test_fetch_brave_timeout_is_recorded(self) -> None:
        source = NewsSource(
            name="brave-news-sample",
            category="MACRO",
            url="https://api.search.brave.com/res/v1/news/search",
            kind="brave_news",
            provider="brave",
            query="Fed stocks",
        )
        with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
            result = fetch_brave_news(source, api_key="token")
        self.assertEqual(result.status, "error")
        self.assertIn("TimeoutError", result.error or "")

    def test_fetch_brave_malformed_json_is_recorded(self) -> None:
        class BadResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self, _limit):
                return b"{not-json"

        source = NewsSource(
            name="brave-news-sample",
            category="MACRO",
            url="https://api.search.brave.com/res/v1/news/search",
            kind="brave_news",
            provider="brave",
            query="Fed stocks",
        )
        with patch("urllib.request.urlopen", return_value=BadResponse()):
            result = fetch_brave_news(source, api_key="token")
        self.assertEqual(result.status, "error")
        self.assertIn("JSONDecodeError", result.error or "")

    def test_fetch_brave_url_error_is_recorded(self) -> None:
        source = NewsSource(
            name="brave-news-sample",
            category="MACRO",
            url="https://api.search.brave.com/res/v1/news/search",
            kind="brave_news",
            provider="brave",
            query="Fed stocks",
        )
        with patch("urllib.request.urlopen", side_effect=URLError("network down")):
            result = fetch_brave_news(source, api_key="token")
        self.assertEqual(result.status, "error")
        self.assertIn("URLError", result.error or "")


if __name__ == "__main__":
    unittest.main()
