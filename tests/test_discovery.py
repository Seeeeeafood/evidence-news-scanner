from datetime import datetime, timezone
import unittest
from zoneinfo import ZoneInfo

from news_scanner_v2.discovery import (
    build_high_recall_scout_queries,
    build_discovery_payload,
    create_discovery_plan,
    discovery_sources_from_queries,
    normalize_discovery_plan,
    scout_sources_from_queries,
)
from news_scanner_v2.fetcher import FetchResult
from news_scanner_v2.models import CandidateItem
from news_scanner_v2.sources import NewsSource


AS_OF = datetime(2026, 5, 20, 15, 0, tzinfo=ZoneInfo("Asia/Seoul"))


def _source(name: str, category: str, provider: str = "brave") -> NewsSource:
    return NewsSource(
        name=name,
        category=category,
        provider=provider,
        url=f"fixture://{name}",
    )


def _item(source: NewsSource, title: str, summary: str = "") -> CandidateItem:
    return CandidateItem(
        source=source.name,
        provider=source.provider,
        category=source.category,
        title=title,
        url=f"https://example.com/{source.name}",
        published_at="2026-05-20T05:10:00+00:00",
        summary=summary,
    )


def _result(source: NewsSource, *items: CandidateItem) -> FetchResult:
    return FetchResult(
        source=source,
        status="ok",
        started_at=datetime.now(timezone.utc).isoformat(),
        finished_at=datetime.now(timezone.utc).isoformat(),
        items=items,
    )


class FakeDiscoveryClient:
    def __init__(self, response: dict):
        self.response = response
        self.payloads: list[dict] = []

    def create_discovery_queries(self, payload: dict) -> dict:
        self.payloads.append(payload)
        return self.response


class DiscoveryPlannerTests(unittest.TestCase):
    def test_build_discovery_payload_keeps_counts_samples_and_recent_deliveries(self) -> None:
        source = _source("fixture-strat", "STRAT")
        payload = build_discovery_payload(
            fetch_results=(
                _result(
                    source,
                    _item(
                        source,
                        "Google and Blackstone launch AI infrastructure JV",
                        "TPU cloud capacity expansion",
                    ),
                ),
            ),
            as_of=AS_OF,
            recent_delivery_texts=["old live digest"],
            max_queries=3,
            max_results_per_query=10,
        )

        self.assertEqual(payload["as_of"], AS_OF.isoformat())
        self.assertEqual(payload["category_item_counts"], {"STRAT": 1})
        self.assertEqual(payload["provider_source_counts"], {"brave": 1})
        self.assertEqual(payload["recent_delivery_texts"], ["old live digest"])
        self.assertIn("STRAT", payload["sample_items_by_category"])

    def test_normalize_discovery_plan_sanitizes_caps_and_dedupes_queries(self) -> None:
        normalized = normalize_discovery_plan(
            {
                "extra_queries": [
                    {
                        "query": "https://example.com NVDA; rm -rf semiconductor pressure!!!",
                        "category": "STRAT",
                        "reason": "semiconductor weakness clue",
                        "max_results": 99,
                    },
                    {
                        "query": "example.com NVDA rm -rf semiconductor pressure",
                        "category": "STRAT",
                        "reason": "duplicate",
                        "max_results": 5,
                    },
                    {
                        "query": "bad category query",
                        "category": "BAD",
                        "reason": "skip",
                        "max_results": 5,
                    },
                    {
                        "query": "AI infrastructure JV cloud capex",
                        "category": "STRAT",
                        "reason": "AI capex clue",
                        "max_results": 2,
                    },
                ]
            },
            max_queries=2,
            max_results_per_query=10,
        )

        self.assertEqual(len(normalized), 2)
        self.assertEqual(normalized[0]["category"], "STRAT")
        self.assertNotIn("https://", normalized[0]["query"])
        self.assertNotIn(";", normalized[0]["query"])
        self.assertEqual(normalized[0]["max_results"], 10)
        self.assertEqual(normalized[1]["query"], "AI infrastructure JV cloud capex")

    def test_create_discovery_plan_uses_client_and_builds_brave_sources(self) -> None:
        source = _source("fixture-macro", "MACRO")
        client = FakeDiscoveryClient(
            {
                "extra_queries": [
                    {
                        "query": "Brent oil gold dollar market shock",
                        "category": "MACRO",
                        "reason": "macro sample looked thin",
                        "max_results": 4,
                    }
                ]
            }
        )

        plan = create_discovery_plan(
            fetch_results=(_result(source),),
            as_of=AS_OF,
            enabled=True,
            api_key=None,
            max_queries=3,
            max_results_per_query=10,
            client=client,
        )

        self.assertEqual(plan["status"], "ok")
        self.assertEqual(plan["requested"], 3)
        self.assertEqual(len(plan["queries"]), 1)
        self.assertEqual(len(plan["sources"]), 1)
        self.assertTrue(plan["sources"][0].name.startswith("brave-discovery-1-macro"))
        self.assertEqual(plan["sources"][0].count, 4)
        self.assertEqual(len(client.payloads), 1)

    def test_create_discovery_plan_fails_open_without_key(self) -> None:
        plan = create_discovery_plan(
            fetch_results=(),
            as_of=AS_OF,
            enabled=True,
            api_key=None,
            max_queries=3,
        )

        self.assertEqual(plan["status"], "skipped_no_api_key")
        self.assertEqual(plan["queries"], [])
        self.assertEqual(plan["sources"], ())

    def test_discovery_sources_from_queries_preserves_category_and_count(self) -> None:
        sources = discovery_sources_from_queries(
            [
                {
                    "query": "AI infrastructure JV",
                    "category": "STRAT",
                    "max_results": 7,
                }
            ]
        )

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].provider, "brave")
        self.assertEqual(sources[0].kind, "brave_news")
        self.assertEqual(sources[0].category, "STRAT")
        self.assertEqual(sources[0].count, 7)



    def test_high_recall_scout_uses_ai_platform_hint_for_linked_movers(self) -> None:
        queries = build_high_recall_scout_queries(
            as_of=AS_OF,
            hint_texts=[
                "Nvidia GTC AI PC platform launch pressures Intel and Qualcomm while Dell and HP rally",
            ],
            max_queries=1,
            max_results_per_query=6,
        )

        self.assertEqual([query["lane"] for query in queries], ["event_linked_ai_pc_movers"])
        self.assertEqual(queries[0]["category"], "MOVE")
        self.assertIn("Dell", queries[0]["query"])
        self.assertIn("Qualcomm", queries[0]["query"])

    def test_high_recall_scout_uses_breaking_hints_for_rubio_and_kawasaki(self) -> None:
        queries = build_high_recall_scout_queries(
            as_of=AS_OF,
            hint_texts=[
                'Rubio says Hormuz toll would break Iran talks; Pakistan delegation heads to Tehran',
                'Kawasaki Heavy and NVIDIA physical AI partnership with Microsoft and Fujitsu robot center',
            ],
            max_queries=4,
            max_results_per_query=6,
        )

        query_text = " ".join(query["query"] for query in queries)
        lanes = [query["lane"] for query in queries]
        self.assertIn("breaking_geo_policy_speaker", lanes)
        self.assertIn("breaking_strat_industrial_ai", lanes)
        self.assertIn("Rubio", query_text)
        self.assertIn("Kawasaki", query_text)
        self.assertIn("physical AI", query_text)
        self.assertLessEqual(len(queries), 4)

    def test_high_recall_scout_uses_breaking_hints_for_iran_deal_conditions(self) -> None:
        queries = build_high_recall_scout_queries(
            as_of=AS_OF,
            hint_texts=[
                "Trump requires Abraham Accords signatures as prerequisite for Iran deal",
            ],
            max_queries=3,
            max_results_per_query=6,
        )

        query_text = " ".join(query["query"] for query in queries)
        lanes = [query["lane"] for query in queries]
        self.assertIn("breaking_geo_iran_deal_conditions", lanes)
        self.assertIn("Abraham Accords", query_text)
        self.assertIn("Iran deal", query_text)

    def test_high_recall_scout_defaults_to_iran_deal_conditions_lane(self) -> None:
        queries = build_high_recall_scout_queries(
            as_of=AS_OF,
            max_queries=1,
            max_results_per_query=6,
        )

        self.assertEqual([query["lane"] for query in queries], ["geo_iran_deal_conditions"])
        self.assertIn("Abraham Accords", queries[0]["query"])
        self.assertIn("Iran deal", queries[0]["query"])

    def test_scout_sources_from_queries_uses_scout_prefix_and_lane_names(self) -> None:
        sources = scout_sources_from_queries(
            [
                {
                    "lane": "geo_policy_speaker",
                    "query": "Rubio Hormuz Iran talks",
                    "category": "GEO",
                    "max_results": 5,
                }
            ]
        )

        self.assertEqual(len(sources), 1)
        self.assertTrue(sources[0].name.startswith("brave-scout-1-geo-policy"))
        self.assertEqual(sources[0].category, "GEO")
        self.assertEqual(sources[0].count, 5)


if __name__ == "__main__":
    unittest.main()
