import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from news_scanner_v2.body_fetcher import (
    BodyFetchResult,
    enrich_candidate_records_with_bodies,
    extract_article_text,
    fetch_article_body,
    prioritized_send_candidate_ids,
    send_candidate_ids,
)


class BodyFetcherTests(unittest.TestCase):
    def test_extract_article_text_prefers_paragraph_blocks(self) -> None:
        paragraph = " ".join(["Iran war pressure affects energy markets."] * 25)
        raw = f"""
        <html>
          <body>
            <nav>menu menu menu</nav>
            <article>
              <h1>Trump Xi summit</h1>
              <p>{paragraph}</p>
            </article>
          </body>
        </html>
        """.encode()

        text = extract_article_text(raw, content_type="text/html; charset=utf-8")

        self.assertIn("Iran war pressure", text)
        self.assertNotIn("menu menu", text)
        self.assertGreaterEqual(len(text), 300)

    def test_fetch_article_body_records_http_errors_without_body_text(self) -> None:
        with patch(
            "news_scanner_v2.body_fetcher.request.urlopen",
            side_effect=HTTPError("https://blocked.example/news", 403, "no", None, None),
        ):
            result = fetch_article_body("https://reuters.com/news")

        self.assertEqual(result.status, "error_http")
        self.assertEqual(result.http_status, 403)
        self.assertEqual(result.body_text, "")

    def test_fetch_article_body_skips_fixture_urls_without_network(self) -> None:
        with patch("news_scanner_v2.body_fetcher.request.urlopen") as urlopen:
            result = fetch_article_body("https://example.com/news")

        self.assertEqual(result.status, "skipped_fixture_url")
        urlopen.assert_not_called()

    def test_send_candidate_ids_preserves_order_and_filters_reviews(self) -> None:
        decisions = [
            {
                "decision": "review",
                "payload": {"candidate_ids": ["ignored"]},
            },
            {
                "decision": "send_candidate",
                "payload": {"candidate_ids": ["a", "b", "a"]},
            },
            {
                "decision": "send_candidate",
                "payload": {"candidate_ids": ["c"]},
            },
        ]

        self.assertEqual(send_candidate_ids(decisions), ["a", "b", "c"])

    def test_prioritized_send_candidate_ids_puts_unsent_events_first(self) -> None:
        decisions = [
            {
                "event_signature": "sent-high-score",
                "decision": "send_candidate",
                "score": 99,
                "payload": {
                    "event": {"event_type": "earnings", "subject": "nvda"},
                    "candidate_ids": ["old-a", "old-b"],
                },
            },
            {
                "event_signature": "unsent-low-score",
                "decision": "send_candidate",
                "score": 80,
                "payload": {
                    "event": {"event_type": "earnings", "subject": "amd"},
                    "candidate_ids": ["new-a"],
                },
            },
            {
                "event_signature": "unsent-high-score",
                "decision": "send_candidate",
                "score": 90,
                "payload": {
                    "event": {"event_type": "geo", "subject": "iran"},
                    "candidate_ids": ["new-b"],
                },
            },
        ]

        self.assertEqual(
            prioritized_send_candidate_ids(
                decisions,
                sent_event_signatures={"sent-high-score"},
            ),
            ["new-b", "new-a", "old-a", "old-b"],
        )

    def test_enrich_candidate_records_fetches_selected_ids_only(self) -> None:
        records = [
            {"id": "a", "url": "https://source.test/a", "title": "A"},
            {"id": "b", "url": "https://source.test/b", "title": "B"},
            {"id": "c", "url": "https://source.test/c", "title": "C"},
        ]

        with patch(
            "news_scanner_v2.body_fetcher.fetch_article_body",
            side_effect=[
                BodyFetchResult(
                    url="https://source.test/a",
                    status="full",
                    fetched_at="2026-05-15T00:00:00+00:00",
                    body_text="body " * 300,
                    text_chars=1500,
                    http_status=200,
                ),
                BodyFetchResult(
                    url="https://source.test/b",
                    status="error_http",
                    fetched_at="2026-05-15T00:00:00+00:00",
                    http_status=401,
                    error="HTTP 401",
                ),
            ],
        ) as fetch:
            enriched, stats = enrich_candidate_records_with_bodies(
                records,
                candidate_ids=["a", "b", "c"],
                max_fetches=2,
            )

        self.assertEqual(fetch.call_count, 2)
        self.assertIn("body_text", enriched[0])
        self.assertNotIn("body_text", enriched[1])
        self.assertNotIn("body_fetch", enriched[2])
        self.assertEqual(stats["body_fetch_candidates"], 3)
        self.assertEqual(stats["body_fetch_attempts"], 2)
        self.assertEqual(stats["body_fetch_full"], 1)
        self.assertEqual(stats["body_fetch_errors"], 1)
        self.assertEqual(stats["body_fetch_skipped"], 1)


if __name__ == "__main__":
    unittest.main()
