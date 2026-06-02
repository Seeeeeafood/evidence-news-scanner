from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from news_scanner_v2.db import connect, finish_run, init_db, insert_news_seeds, insert_run
from news_scanner_v2.market_theme import (
    build_market_theme_candidates,
    load_news_seed_candidates_for_run,
)
from news_scanner_v2.news_seed import build_news_seeds


class MarketThemeCandidateTests(unittest.TestCase):
    def test_builds_2230_semiconductor_pressure_and_hd_rescue_candidates(self) -> None:
        raw_items = [
            {
                "id": "move-yahoo",
                "category": "MOVE",
                "provider": "brave",
                "source": "brave-news-largecap-movers",
                "title": (
                    "Stock market today: Dow, S&P 500, Nasdaq futures slide as "
                    "rising yields keep up pressure"
                ),
                "url": "https://finance.yahoo.com/markets/live/stock-market-today.html",
                "published_at": "2026-05-19T12:48:34+00:00",
                "summary": (
                    "Nvidia fell again in premarket trading, extending losses, "
                    "while South Korea's Kospi sank and Samsung Electronics and "
                    "SK Hynix tracked losses in tech shares."
                ),
            },
            {
                "id": "move-cnbc",
                "category": "MOVE",
                "provider": "brave",
                "source": "brave-news-largecap-movers",
                "title": "Stock market today: Live updates",
                "url": "https://cnbc.com/2026/05/18/stock-market-today-live-updates.html",
                "published_at": "2026-05-18T22:01:21+00:00",
                "summary": (
                    "Investors are using semiconductors as a short-term ATM and "
                    "strategically taking profits after a parabolic surge in chipmakers."
                ),
            },
            {
                "id": "earn-hd-marketbeat",
                "category": "EARN",
                "provider": "brave",
                "source": "brave-news-earnings-guidance",
                "title": "Home Depot (NYSE:HD) Updates FY 2026 Earnings Guidance",
                "url": "https://www.marketbeat.com/instant-alerts/home-depot-nysehd-updates-fy-2026-earnings-guidance-2026-05-19/",
                "published_at": "2026-05-19T11:11:43+00:00",
                "summary": (
                    "Home Depot updated its FY 2026 earnings guidance. The company "
                    "provided EPS guidance of 14.690-15.278 for the period."
                ),
            },
            {
                "id": "earn-hd-preview",
                "category": "EARN",
                "provider": "brave",
                "source": "brave-news-earnings-guidance",
                "title": (
                    "Home Depot (HD) Stock: What Wall Street Expects from Q1 "
                    "Earnings Today - CoinCentral"
                ),
                "url": "https://coincentral.com/home-depot-hd-stock-what-wall-street-expects-from-q1-earnings-today/",
                "published_at": "2026-05-19T09:48:47+00:00",
                "summary": "Wall Street is forecasting EPS of $3.41 and revenue of $41.6 billion.",
            },
            {
                "id": "earn-ecx",
                "category": "EARN",
                "provider": "brave",
                "source": "brave-news-earnings-guidance",
                "title": (
                    "ECARX Holdings Inc. (NASDAQ:ECX) Q1 2026 Earnings: EPS Beat "
                    "Offsets Revenue Miss, Stock Jumps 5.8% Pre-Market | ChartMill.com"
                ),
                "url": "https://www.chartmill.com/news/ECX/example",
                "published_at": "2026-05-19T12:20:43+00:00",
                "summary": "Single-source ECX article.",
            },
        ]

        themes = build_market_theme_candidates(raw_items=raw_items)

        by_key = {theme["theme_key"]: theme for theme in themes}
        self.assertIn("semiconductor_pressure", by_key)
        self.assertIn("hd_earnings_result", by_key)
        self.assertNotIn("ecx_earnings_result", by_key)

        semi = by_key["semiconductor_pressure"]
        self.assertEqual(semi["theme_type"], "sector_pressure")
        self.assertEqual(semi["market_marker"], "red")
        self.assertFalse(semi["requires_verification"])
        self.assertGreaterEqual(len(semi["evidence_ids"]), 2)
        self.assertIn("move-yahoo", semi["evidence_ids"])
        self.assertIn("move-cnbc", semi["evidence_ids"])
        self.assertTrue(
            any(atom["text"] == "semiconductor or chip pressure" for atom in semi["claim_atoms"])
        )

        hd = by_key["hd_earnings_result"]
        self.assertEqual(hd["theme_type"], "earnings_result")
        self.assertEqual(hd["subject"], "HD")
        self.assertTrue(hd["requires_trusted_rescue"])
        self.assertEqual(hd["source_tier"], "low_quality")
        self.assertIn("earn-hd-marketbeat", hd["evidence_ids"])
        self.assertTrue(
            any(atom["text"] == "guidance data mentioned" for atom in hd["claim_atoms"])
        )

    def test_theme_id_uses_stable_item_hash_not_run_candidate_id(self) -> None:
        first_run = [
            {
                "id": "run-a-1",
                "item_hash": "stable-yahoo",
                "category": "MOVE",
                "provider": "brave",
                "title": "Nvidia fell as rising yields pressured chipmakers",
                "url": "https://finance.yahoo.com/a",
                "summary": "Nvidia fell and chipmakers were under pressure.",
            },
            {
                "id": "run-a-2",
                "item_hash": "stable-cnbc",
                "category": "MOVE",
                "provider": "brave",
                "title": "Investors take profits in semiconductors",
                "url": "https://www.cnbc.com/a",
                "summary": "Semiconductors saw profit-taking after a surge.",
            },
        ]
        second_run = [dict(item, id=item["id"].replace("run-a", "run-b")) for item in first_run]

        first = build_market_theme_candidates(raw_items=first_run)
        second = build_market_theme_candidates(raw_items=second_run)

        self.assertEqual(first[0]["id"], second[0]["id"])

    def test_builds_memory_sector_rally_candidate_from_micron_hbm_evidence(self) -> None:
        raw_items = [
            {
                "id": "move-reuters-mu",
                "item_hash": "stable-reuters-mu",
                "category": "MOVE",
                "provider": "brave",
                "source": "brave-discovery-2-move",
                "title": "Micron joins $1 trillion club as AI race powers memory chip boom | Reuters",
                "url": "https://www.reuters.com/technology/micron-memory-chip-boom",
                "published_at": "2026-05-26T19:48:00+00:00",
                "summary": (
                    "Micron Technology shares rallied as HBM4 production and "
                    "DRAM demand were cited in the AI race."
                ),
            },
            {
                "id": "move-investopedia-mu",
                "item_hash": "stable-investopedia-mu",
                "category": "MOVE",
                "provider": "brave",
                "source": "brave-news-largecap-movers",
                "title": (
                    "Stock Market Today: Nasdaq, S&P 500 Hit New All-Time Highs "
                    "as Micron Soars 20%; Dow Pulls Back"
                ),
                "url": "https://www.investopedia.com/stock-market-today-micron-soars",
                "published_at": "2026-05-26T20:02:00+00:00",
                "summary": "Micron soars as memory stocks gain on AI demand.",
            },
            {
                "id": "move-warning",
                "category": "MOVE",
                "provider": "brave",
                "source": "brave-discovery-2-move",
                "title": "Beware the boom and bust cycle of memory stocks, investors warn",
                "url": "https://example.com/memory-warning",
                "published_at": "2026-05-26T20:05:00+00:00",
                "summary": "Memory stocks may face a boom and bust cycle.",
            },
        ]

        themes = build_market_theme_candidates(raw_items=raw_items)
        by_key = {theme["theme_key"]: theme for theme in themes}

        self.assertIn("memory_sector_rally", by_key)
        memory = by_key["memory_sector_rally"]
        self.assertEqual(memory["theme_type"], "sector_rally")
        self.assertEqual(memory["subject"], "memory_semiconductors")
        self.assertEqual(memory["market_marker"], "green")
        self.assertEqual(memory["source_tier"], "trusted")
        self.assertIn("move-reuters-mu", memory["evidence_ids"])
        self.assertIn("move-investopedia-mu", memory["evidence_ids"])
        self.assertNotIn("move-warning", memory["evidence_ids"])
        self.assertTrue(
            any(atom["text"] == "AI demand is cited as a driver" for atom in memory["claim_atoms"])
        )

    def test_loads_news_seeds_as_theme_editor_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "news.sqlite"
            init_db(db_path)
            raw_items = [
                {
                    "id": "ai-a",
                    "category": "STRAT",
                    "provider": "brave",
                    "source": "brave-news-megacap-strategic",
                    "title": (
                        "Google and Blackstone launch $5B TPU cloud AI "
                        "infrastructure joint venture to rival CoreWeave"
                    ),
                    "url": "https://www.reuters.com/technology/google-blackstone-tpu",
                    "published_at": "2026-05-20T05:00:00+00:00",
                    "summary": "The partnership expands AI compute capacity.",
                }
            ]
            seeds = build_news_seeds(
                raw_items=raw_items,
                as_of=datetime(2026, 5, 20, 15, 0, tzinfo=ZoneInfo("Asia/Seoul")),
            )
            with connect(db_path) as conn:
                insert_run(
                    conn,
                    run_id="seed-run",
                    started_at="2026-05-20T15:00:00+09:00",
                    as_of="2026-05-20T15:00:00+09:00",
                    mode="live",
                    dispatch_enabled=True,
                    llm_enabled=True,
                    legacy_prompt_hash=None,
                    legacy_snapshot={},
                )
                insert_news_seeds(
                    conn,
                    run_id="seed-run",
                    created_at="2026-05-20T15:00:00+09:00",
                    seeds=seeds,
                )
                finish_run(
                    conn,
                    run_id="seed-run",
                    status="ok",
                    finished_at="2026-05-20T15:00:00+09:00",
                )

            candidates = load_news_seed_candidates_for_run(db_path, run_id="seed-run")

            self.assertEqual(len(candidates), 1)
            candidate = candidates[0]
            self.assertEqual(candidate["policy"], "news_seed_theme_v1")
            self.assertEqual(candidate["theme_key"], "ai_infrastructure_jv")
            self.assertEqual(candidate["theme_type"], "strategic_theme")
            self.assertEqual(candidate["subject"], "AI_INFRA")
            self.assertEqual(candidate["market_marker"], "green")
            self.assertFalse(candidate["requires_trusted_rescue"])
            self.assertEqual(candidate["evidence"][0]["candidate_id"], "ai-a")
            self.assertTrue(candidate["claim_atoms"])


if __name__ == "__main__":
    unittest.main()
