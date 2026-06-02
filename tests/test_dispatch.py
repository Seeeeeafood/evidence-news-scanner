from __future__ import annotations

from datetime import datetime
import unittest
from zoneinfo import ZoneInfo

from news_scanner_v2.dispatch import (
    REVIEW,
    SEND_CANDIDATE,
    REJECT,
    decide_dispatch,
)
from news_scanner_v2.extractor import extract_events


AS_OF = datetime(2026, 5, 14, 17, 30, tzinfo=ZoneInfo("Asia/Seoul"))


def candidate(
    title: str,
    category: str,
    candidate_id: str,
    *,
    provider: str = "brave",
    source: str = "fixture",
    url: str | None = None,
    summary: str = "",
) -> dict:
    return {
        "id": candidate_id,
        "source": source,
        "provider": provider,
        "category": category,
        "title": title,
        "normalized_title": title.lower(),
        "url": url or f"https://example.com/{candidate_id}",
        "canonical_url": url or f"https://example.com/{candidate_id}",
        "published_at": "2026-05-14T08:25:00+00:00",
        "summary": summary,
    }


def decisions_for(items: list[dict]):
    return decide_dispatch(extract_events(items, as_of=AS_OF), candidates=items)


class DispatchDecisionTests(unittest.TestCase):
    def test_multi_source_guidance_event_becomes_send_candidate(self) -> None:
        items = [
            candidate(
                "Nvidia (NVDA) raises guidance after Q1 earnings",
                "EARN",
                "c1",
                provider="brave",
                source="brave-news-earnings-guidance",
            ),
            candidate(
                "NVIDIA raises guidance after earnings beat",
                "EARN",
                "c2",
                provider="google_rss",
                source="google-news-earnings-guidance",
            ),
        ]

        decisions = decisions_for(items)

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].decision, SEND_CANDIDATE)
        self.assertGreaterEqual(decisions[0].score, 75)
        self.assertEqual(decisions[0].payload["evidence_count"], 2)
        self.assertEqual(decisions[0].payload["providers"], ["brave", "google_rss"])
        self.assertEqual(decisions[0].payload["event"]["object"], "earn")

    def test_preferred_source_orders_candidate_ids_and_title(self) -> None:
        items = [
            candidate(
                "Nvidia posts Q1 revenue $81.6B and Q2 guidance $91B on AI boom",
                "EARN",
                "weak",
                url="https://mashable.com/tech/nvidia-earnings",
            ),
            candidate(
                "Nvidia Q1 earnings: EPS $1.87, revenue $81.62B, Q2 revenue guidance $91B",
                "EARN",
                "trusted",
                url="https://finance.yahoo.com/markets/live/earnings-live-updates.html",
            ),
        ]

        decisions = decisions_for(items)

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].payload["candidate_ids"][0], "weak")
        self.assertEqual(decisions[0].payload["ranked_candidate_ids"][0], "trusted")
        self.assertIn("Q2 revenue guidance", decisions[0].payload["event"]["title"])

    def test_single_source_analyst_item_is_rejected(self) -> None:
        items = [
            candidate(
                "Veteran analyst resets Apple stock price target for 2026",
                "ANAL",
                "c1",
            )
        ]

        decisions = decisions_for(items)

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].decision, REJECT)
        self.assertIn("sensitive_single_source", decisions[0].reason)

    def test_concrete_watchlist_price_target_becomes_b_watch_item(self) -> None:
        items = [
            candidate(
                "HSBC Raises Nvidia (NVDA) Price Target to $325 From $295, Maintains Buy",
                "ANAL",
                "c1",
                source="brave-news-analyst-actions",
            )
        ]

        decisions = decisions_for(items)

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].decision, SEND_CANDIDATE)
        self.assertEqual(decisions[0].payload["grade"], "B")
        self.assertEqual(decisions[0].payload["event_quality"], "watch_item")
        self.assertIn("single_source", decisions[0].payload["risk_flags"])
        self.assertIn("recall_concrete_analyst_target", decisions[0].reason)

    def test_geo_sourced_company_policy_risk_becomes_strategic_send(self) -> None:
        items = [
            candidate(
                "Nvidia says it has 'largely conceded' China's AI chip market to Huawei",
                "GEO",
                "c1",
                source="brave-discovery-1-geo",
                url="https://www.cnbc.com/2026/05/21/nvidia-jensen-huang-china-ai-chip-market-huawei.html",
                summary=(
                    "Nvidia CEO Jensen Huang said the company has largely conceded "
                    "China's artificial intelligence chip market to Huawei as U.S. "
                    "export restrictions continue. Revenue surged 85% to $81.62 "
                    "billion, but H200 approvals remain a policy risk."
                ),
            )
        ]

        decisions = decisions_for(items)

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].decision, SEND_CANDIDATE)
        self.assertEqual(decisions[0].payload["event"]["event_type"], "strategic")
        self.assertEqual(decisions[0].payload["event"]["subject"], "nvda")
        self.assertEqual(decisions[0].payload["event"]["action"], "policy_risk")
        self.assertEqual(decisions[0].payload["grade"], "B")
        self.assertIn("recall_material_strategic", decisions[0].reason)

    def test_strat_sourced_company_policy_risk_becomes_strategic_send(self) -> None:
        items = [
            candidate(
                "Nvidia says it has 'largely conceded' China's AI chip market to Huawei",
                "STRAT",
                "c1",
                source="brave-discovery-2-strat",
                url="https://www.cnbc.com/2026/05/21/nvidia-jensen-huang-china-ai-chip-market-huawei.html",
                summary=(
                    "Nvidia CEO Jensen Huang said the company has largely conceded "
                    "China's artificial intelligence chip market to Huawei as U.S. "
                    "export restrictions continue."
                ),
            )
        ]

        decisions = decisions_for(items)

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].decision, SEND_CANDIDATE)
        self.assertEqual(decisions[0].payload["event"]["action"], "policy_risk")
        self.assertEqual(decisions[0].payload["event"]["metadata"]["source_category"], "STRAT")
        self.assertIn("recall_material_strategic", decisions[0].reason)

    def test_recall_first_single_source_earnings_guidance_becomes_b_send(self) -> None:
        items = [
            candidate(
                "GE Vernova Raises FY2026 Sales Guidance from $44.000B-$45.000B to $44.500B-$45.500B vs $44.474B Est",
                "EARN",
                "c1",
                source="Bloomberg",
                url="https://www.bloomberg.com/news/ge-vernova-guidance",
            )
        ]

        decisions = decisions_for(items)

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].decision, SEND_CANDIDATE)
        self.assertEqual(decisions[0].payload["grade"], "B")
        self.assertIn("recall_earnings_guidance", decisions[0].reason)

    def test_recall_first_concrete_earnings_report_becomes_b_send(self) -> None:
        items = [
            candidate(
                "Texas Instruments Sees Q2 GAAP EPS $1.77-$2.05 vs $1.57 Est; Sees Sales $5.000B-$5.400B vs $4.859B Est",
                "EARN",
                "c1",
                source="CNBC",
                url="https://www.cnbc.com/news/txn-q2-outlook",
            )
        ]

        decisions = decisions_for(items)

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].decision, SEND_CANDIDATE)
        self.assertEqual(decisions[0].payload["grade"], "B")
        self.assertIn("recall_concrete_earnings", decisions[0].reason)

    def test_single_source_untrusted_earnings_is_review_not_send(self) -> None:
        items = [
            candidate(
                "UNH stock surges past $394 on Q1 earnings beat",
                "EARN",
                "c1",
                source="brave-news-earnings-guidance",
                url="https://eciks.org/unh-q1-earnings-replay",
            )
        ]

        decisions = decisions_for(items)

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].decision, REVIEW)
        self.assertIn("single_low_quality_earnings_source", decisions[0].reason)

    def test_recall_first_material_corporate_action_becomes_b_send(self) -> None:
        items = [
            candidate(
                "Meta Tells Staff It Will Cut 10% of Jobs in Push for Efficiency",
                "MA",
                "c1",
                source="Bloomberg",
                url="https://www.bloomberg.com/news/meta-job-cuts",
            )
        ]

        decisions = decisions_for(items)

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].decision, SEND_CANDIDATE)
        self.assertEqual(decisions[0].payload["grade"], "B")
        self.assertIn("recall_material_corporate_action", decisions[0].reason)
        self.assertEqual(decisions[0].payload["event_quality"], "hard_event")

    def test_spacex_ipo_single_source_becomes_b_send(self) -> None:
        items = [
            candidate(
                "SpaceX IPO filing brings Musk's interplanetary ambitions to Wall Street",
                "MA",
                "c1",
                source="Reuters",
                url="https://www.reuters.com/business/finance/spacex-ipo-filing",
            )
        ]

        decisions = decisions_for(items)

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].decision, SEND_CANDIDATE)
        self.assertEqual(decisions[0].payload["grade"], "B")
        self.assertIn("recall_corporate_transaction", decisions[0].reason)

    def test_tsla_spacex_merger_buzz_is_rejected_as_low_signal(self) -> None:
        items = [
            candidate(
                "TSLA Stock Rises Overnight: Elon Musk Drops Quiet Period Hint Amid Tesla-SpaceX Merger Buzz",
                "MA",
                "c1",
                source="brave-news-ma-buyback",
                url="https://stocktwits.com/news/tsla-spacex-merger-buzz",
            )
        ]

        decisions = decisions_for(items)

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].decision, REJECT)
        self.assertIn("low_signal_title", decisions[0].reason)

    def test_single_source_untrusted_buyback_enters_hard_event_lane(self) -> None:
        items = [
            candidate(
                "Cognizant Technology Solutions (CTSH) Expands Stock Repurchase Program by $2 Billion",
                "MA",
                "c1",
                source="brave-news-ma-buyback",
                url=(
                    "https://www.gurufocus.com/news/8866507/"
                    "cognizant-technology-solutions-ctsh-expands-stock-"
                    "repurchase-program-by-2-billion"
                ),
            )
        ]

        decisions = decisions_for(items)

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].decision, SEND_CANDIDATE)
        self.assertEqual(decisions[0].payload["grade"], "B")
        self.assertEqual(decisions[0].payload["source_tier"], "untrusted")
        self.assertEqual(decisions[0].payload["event_quality"], "hard_event")
        self.assertIn("single_source_untrusted", decisions[0].payload["risk_flags"])
        self.assertIn("recall_corporate_transaction", decisions[0].reason)

    def test_recall_first_material_strategic_investment_becomes_b_send(self) -> None:
        items = [
            candidate(
                "Google Plans to Invest Up to $40 Billion in Anthropic",
                "STRAT",
                "c1",
                source="Bloomberg",
                url="https://www.bloomberg.com/news/google-anthropic-investment",
            )
        ]

        decisions = decisions_for(items)

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].decision, SEND_CANDIDATE)
        self.assertEqual(decisions[0].payload["grade"], "B")
        self.assertIn("recall_material_strategic", decisions[0].reason)
        self.assertEqual(decisions[0].payload["event_quality"], "hard_event")

    def test_recall_first_physical_ai_partnership_becomes_b_send(self) -> None:
        items = [
            candidate(
                (
                    "Kawasaki Heavy To Partner With NVIDIA On Physical AI, Open "
                    "U.S. Robot Center; Joint Development Includes Microsoft, Fujitsu "
                    "- Nikkei Asia (Benzinga)"
                ),
                "STRAT",
                "c1",
                provider="breaking_hint",
                source="breaking-hints-strat",
                url="breaking-hint://breaking_2026-05-22.md:8",
            )
        ]

        decisions = decisions_for(items)

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].decision, SEND_CANDIDATE)
        self.assertEqual(decisions[0].payload["grade"], "B")
        self.assertEqual(decisions[0].payload["event"]["subject"], "nvda")
        self.assertEqual(decisions[0].payload["source_tier"], "trusted")
        self.assertEqual(decisions[0].payload["event_quality"], "hard_event")
        self.assertNotIn("single_source_untrusted", decisions[0].payload["risk_flags"])
        self.assertIn("recall_material_strategic", decisions[0].reason)

    def test_market_leader_platform_launch_becomes_material_strategic_send(self) -> None:
        items = [
            candidate(
                (
                    "Nvidia Computex 2026 keynote as it happened: "
                    "RTX Spark announced to take on Apple, Intel, and Qualcomm"
                ),
                "STRAT",
                "gtc-1",
                source="brave-discovery-2-strat",
                url="https://techradar.com/news/live/nvidia-computex-2026",
            ),
            candidate(
                "Nvidia RTX Spark: New AI Superchip Unveiled at Computex 2026",
                "STRAT",
                "gtc-2",
                source="brave-discovery-2-strat",
                url=(
                    "https://bmmagazine.co.uk/news/"
                    "nvidia-rtx-spark-superchip-personal-ai-pc-computex-2026"
                ),
            ),
            candidate(
                "Nvidia-powered Windows PCs to make debut at Computex 2026: Report",
                "STRAT",
                "gtc-3",
                source="brave-discovery-2-strat",
                url=(
                    "https://indianexpress.com/article/technology/"
                    "nvidia-windows-pcs-computex-2026"
                ),
            ),
            candidate(
                (
                    "Nvidia, Broadcom and Micron Shares on the Move: "
                    "What COMPUTEX 2026 Means for AI Chip Stocks"
                ),
                "STRAT",
                "gtc-4",
                source="brave-discovery-2-strat",
                url=(
                    "https://stocksdownunder.com/"
                    "nvidia-broadcom-micron-ai-chip-stocks-computex"
                ),
            ),
        ]

        decisions = decisions_for(items)

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].decision, SEND_CANDIDATE)
        self.assertEqual(decisions[0].payload["event"]["subject"], "nvda")
        self.assertEqual(decisions[0].payload["event_quality"], "hard_event")
        self.assertEqual(decisions[0].payload["grade"], "B")
        self.assertIn("recall_material_strategic", decisions[0].reason)

    def test_official_market_leader_platform_launch_single_source_becomes_send(self) -> None:
        items = [
            candidate(
                (
                    "NVIDIA and Microsoft Reinvent Windows PCs for the "
                    "Age of Personal AI With RTX Spark Launch"
                ),
                "STRAT",
                "official-gtc",
                source="nvidia-newsroom",
                url=(
                    "https://investor.nvidia.com/news/press-release-details/"
                    "2026/nvidia-rtx-spark-launch/default.aspx"
                ),
            )
        ]

        decisions = decisions_for(items)

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].decision, SEND_CANDIDATE)
        self.assertEqual(decisions[0].payload["source_tier"], "trusted")
        self.assertEqual(decisions[0].payload["event_quality"], "hard_event")
        self.assertIn("recall_material_strategic", decisions[0].reason)
        self.assertNotIn("single_source_untrusted", decisions[0].payload["risk_flags"])

    def test_material_platform_launch_requires_actual_actor_not_mentioned_peer(self) -> None:
        items = [
            candidate(
                (
                    "Watch out, Apple - Nvidia just unveiled its RTX Spark "
                    "Arm superchip to take on the M5 at Computex 2026"
                ),
                "STRAT",
                "aapl-peer",
                source="brave-discovery-2-strat",
            )
        ]

        decisions = decisions_for(items)

        self.assertEqual(len(decisions), 1)
        self.assertNotEqual(decisions[0].decision, SEND_CANDIDATE)
        self.assertEqual(decisions[0].payload["event"]["subject"], "aapl")

    def test_platform_keynote_preview_without_announcement_is_not_material_send(self) -> None:
        items = [
            candidate(
                "How to Watch Intel's Computex 2026 Keynote",
                "STRAT",
                "intc-preview",
                source="brave-discovery-2-strat",
            )
        ]

        decisions = decisions_for(items)

        self.assertEqual(len(decisions), 1)
        self.assertNotEqual(decisions[0].decision, SEND_CANDIDATE)
        self.assertEqual(decisions[0].payload["event"]["subject"], "intc")

    def test_materiality_router_keeps_tam_overtake_analysis_out(self) -> None:
        items = [
            candidate(
                (
                    "Nvidia's Hidden $60 Billion Business Is About to "
                    "Overtake Broadcom"
                ),
                "STRAT",
                "nvda-analysis",
                source="brave-discovery-2-strat",
                url=(
                    "https://247wallst.com/investing/2026/05/23/"
                    "nvidias-hidden-60-billion-business-is-about-to-overtake-broadcom"
                ),
            ),
            candidate(
                (
                    "NVIDIA Earnings: New Segments And A $200B CPU TAM "
                    "Reveal A Business Beyond Hyperscale"
                ),
                "STRAT",
                "nvda-tam",
                source="brave-discovery-2-strat",
                url="https://trefis.com/stock/nvda/articles/600335/nvidia-earnings-new-segments",
            ),
        ]

        decisions = decisions_for(items)

        self.assertTrue(all(decision.decision != SEND_CANDIDATE for decision in decisions))

    def test_soft_strategic_analysis_is_not_a_send_candidate(self) -> None:
        items = [
            candidate(
                "How The Broadcom (AVGO) Investment Story Is Shifting With AI Hopes And Fresh Concerns",
                "STRAT",
                "c1",
                source="brave-news-megacap-strategic",
                url=(
                    "https://finance.yahoo.com/markets/stocks/articles/"
                    "broadcom-avgo-investment-story-shifting-171005750.html"
                ),
            )
        ]

        decisions = decisions_for(items)

        self.assertEqual(decisions, [])

    def test_official_macro_single_source_is_review_not_send(self) -> None:
        items = [
            candidate(
                "Federal Reserve Board issues rate policy statement",
                "MACRO",
                "c1",
                provider="official_rss",
                source="federal-reserve-press",
            )
        ]

        decisions = decisions_for(items)

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].decision, REVIEW)
        self.assertIn("official_source", decisions[0].reason)

    def test_high_impact_geo_conflict_from_trusted_domain_can_be_send(self) -> None:
        items = [
            candidate(
                "World markets feel the strain as US-Iran war grinds on",
                "GEO",
                "c1",
                url="https://www.reuters.com/world/middle-east/iran-war",
            )
        ]

        decisions = decisions_for(items)

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].decision, SEND_CANDIDATE)
        self.assertGreaterEqual(decisions[0].score, 75)
        self.assertEqual(decisions[0].payload["trusted_domains"], ["reuters.com"])

    def test_high_impact_geo_conflict_from_untrusted_single_source_is_review(self) -> None:
        items = [
            candidate(
                "World markets feel the strain as US-Iran war grinds on",
                "GEO",
                "c1",
                url="https://example.com/iran-war",
            )
        ]

        decisions = decisions_for(items)

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].decision, REVIEW)
        self.assertIn("single_source_untrusted", decisions[0].reason)

    def test_low_quality_single_source_is_review_even_when_score_is_high(self) -> None:
        items = [
            candidate(
                "For Chinese exporters, Iran worries eclipse tariff woes as Trump, Xi prepare to meet",
                "GEO",
                "c1",
                url="https://www.indiavision.com/business/exporters-tariff",
            )
        ]

        decisions = decisions_for(items)

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].decision, REVIEW)
        self.assertIn("single_low_quality_source", decisions[0].reason)
        self.assertEqual(
            decisions[0].payload["low_quality_domains"],
            ["indiavision.com"],
        )

    def test_reuters_byline_counts_as_trusted_source(self) -> None:
        items = [
            candidate(
                "World markets feel the strain as US-Iran war grinds on By Reuters",
                "GEO",
                "c1",
                url="https://investing.com/news/economy-news/iran-war",
            )
        ]

        decisions = decisions_for(items)

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].decision, SEND_CANDIDATE)
        self.assertEqual(decisions[0].payload["trusted_source_count"], 1)

    def test_marketwatch_breaking_hint_counts_as_trusted_source(self) -> None:
        items = [
            candidate(
                (
                    "Marvell's stock falls despite 'exceptional' AI demand "
                    "driving a stronger growth outlook (MarketWatch)"
                ),
                "EARN",
                "c1",
                provider="breaking_hint",
                source="breaking-hints-earn",
                url="breaking-hint://breaking_2026-05-28.md:4",
            )
        ]

        decisions = decisions_for(items)

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].decision, SEND_CANDIDATE)
        self.assertEqual(decisions[0].payload["event"]["subject"], "mrvl")
        self.assertEqual(decisions[0].payload["source_tier"], "trusted")
        self.assertEqual(decisions[0].payload["trusted_source_count"], 1)
        self.assertNotIn(
            "single_source_untrusted",
            decisions[0].payload["risk_flags"],
        )

    def test_low_signal_title_is_rejected_even_with_high_score(self) -> None:
        items = [
            candidate(
                "Gold Price Flashes Warning at $4,700: A Major Crash Coming?",
                "MACRO",
                "c1",
                provider="brave",
                source="brave-news-macro-markets",
            ),
            candidate(
                "Gold Price Flashes Warning at $4,700: A Major Crash Coming?",
                "MACRO",
                "c2",
                provider="google_rss",
                source="google-news-macro-markets",
            ),
        ]

        decisions = decisions_for(items)

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].decision, REJECT)
        self.assertIn("low_signal_title", decisions[0].reason)

    def test_generic_subject_is_rejected(self) -> None:
        extracted = [
            {
                "id": "link",
                "candidate_id": "c1",
                "event": {
                    "signature": "sig",
                    "event_type": "macro",
                    "subject": "macro",
                    "effective_date": "2026-05-14",
                    "payload": {
                        "event_type": "macro",
                        "subject": "macro",
                        "effective_date": "2026-05-14",
                        "scope": "market",
                        "period": "",
                        "action": "macro_update",
                        "object": "macro",
                        "stage": "candidate",
                    },
                    "title": "Generic market update",
                    "url": "https://example.com/generic",
                },
                "extractor": "unit",
                "confidence": 0.9,
                "reason": "unit",
            }
        ]
        items = [candidate("Generic market update", "MACRO", "c1")]

        decisions = decide_dispatch(extracted, candidates=items)

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].decision, REJECT)
        self.assertIn("generic_subject", decisions[0].reason)


    def test_geo_fresh_delta_rescue_sends_single_source_talks_halt(self) -> None:
        items = [
            candidate(
                "Iran negotiating team halts indirect messages with U.S. mediators after Lebanon attack",
                "GEO",
                "c1",
                source="brave-news-geo-policy",
                summary=(
                    "Iran's negotiating team stopped exchanging indirect messages "
                    "with U.S. mediators after the Lebanon attack, leaving ceasefire "
                    "talks stalled and Hormuz reopening uncertain."
                ),
            )
        ]

        decisions = decisions_for(items)

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].decision, SEND_CANDIDATE)
        self.assertEqual(decisions[0].payload["grade"], "B")
        self.assertEqual(
            decisions[0].payload["rescue_type"],
            "geo_fresh_delta",
        )
        self.assertTrue(decisions[0].payload["atomic_digest"])
        self.assertIn("rescue_geo_fresh_delta", decisions[0].reason)

    def test_event_linked_mover_rescue_requires_numeric_move(self) -> None:
        items = [
            candidate(
                "Dell shares jump 10% as Nvidia AI PC launch boosts OEM demand",
                "MOVE",
                "c1",
                source="brave-news-largecap-movers",
                summary=(
                    "Dell stock rose 10% after Nvidia's AI PC platform launch "
                    "boosted expectations for OEM demand."
                ),
            )
        ]

        decisions = decisions_for(items)

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].decision, SEND_CANDIDATE)
        self.assertEqual(decisions[0].payload["event"]["event_type"], "mover")
        self.assertEqual(decisions[0].payload["event"]["subject"], "dell")
        self.assertEqual(decisions[0].payload["rescue_type"], "event_linked_mover")
        self.assertTrue(decisions[0].payload["atomic_digest"])
        self.assertTrue(decisions[0].payload["requires_numeric_fact"])

    def test_event_linked_mover_rescue_handles_negative_reaction(self) -> None:
        items = [
            candidate(
                "Qualcomm falls 7.3% as Nvidia AI PC chips raise competitive pressure",
                "MOVE",
                "c1",
                source="google-news-largecap-movers",
                summary=(
                    "Qualcomm shares fell 7.3% as investors weighed Nvidia's "
                    "AI PC chip roadmap and the risk of more competition."
                ),
            )
        ]

        decisions = decisions_for(items)

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].decision, SEND_CANDIDATE)
        self.assertEqual(decisions[0].payload["event"]["subject"], "qcom")
        self.assertEqual(decisions[0].payload["rescue_type"], "event_linked_mover")

    def test_event_linked_mover_rescue_blocks_soft_stock_pick(self) -> None:
        items = [
            candidate(
                "Best AI stock to buy: Dell could surge as TAM expands",
                "MOVE",
                "c1",
                source="brave-news-largecap-movers",
                summary="Analysts say Dell could benefit from a larger AI PC TAM.",
            )
        ]

        decisions = decisions_for(items)

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].decision, REJECT)
        self.assertIn("soft_analysis", decisions[0].reason)


if __name__ == "__main__":
    unittest.main()
