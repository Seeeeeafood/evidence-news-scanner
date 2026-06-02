from datetime import datetime
import unittest
from zoneinfo import ZoneInfo

from news_scanner_v2.news_seed import build_news_seeds, summarize_news_seeds


AS_OF = datetime(2026, 5, 20, 15, 0, tzinfo=ZoneInfo("Asia/Seoul"))


def _item(
    item_id: str,
    title: str,
    *,
    category: str = "STRAT",
    source: str = "fixture",
    provider: str = "brave",
    url: str = "https://reuters.com/story",
    summary: str = "",
) -> dict:
    return {
        "id": item_id,
        "item_hash": f"hash-{item_id}",
        "source": source,
        "provider": provider,
        "category": category,
        "title": title,
        "normalized_title": title.lower(),
        "url": url,
        "canonical_url": url,
        "published_at": "2026-05-20T04:30:00+00:00",
        "summary": summary,
        "body_text": "",
    }


class NewsSeedTests(unittest.TestCase):
    def test_builds_ai_infra_seed_from_single_strategic_item(self) -> None:
        seeds = build_news_seeds(
            raw_items=[
                _item(
                    "a",
                    (
                        "Google and Blackstone launch $5B TPU cloud AI "
                        "infrastructure joint venture to rival CoreWeave"
                    ),
                    url="https://reuters.com/google-blackstone-tpu",
                )
            ],
            as_of=AS_OF,
        )

        self.assertEqual(len(seeds), 1)
        seed = seeds[0]
        self.assertEqual(seed["seed_type"], "strategic_theme")
        self.assertEqual(seed["subject"], "AI_INFRA")
        self.assertEqual(seed["theme"], "ai_infrastructure_jv")
        self.assertEqual(seed["source_tier"], "trusted")
        self.assertEqual(seed["evidence_count"], 1)
        atom_texts = {atom["text"] for atom in seed["claim_atoms"]}
        self.assertIn("Google/Alphabet AI infrastructure involvement", atom_texts)
        self.assertIn("Blackstone investment or partnership", atom_texts)
        self.assertIn("accelerator or AI compute capacity", atom_texts)
        self.assertIn("data center or cloud capacity expansion", atom_texts)
        self.assertIn("amount mentioned: $5B", atom_texts)

    def test_does_not_build_ai_infra_seed_from_stock_pick_noise(self) -> None:
        seeds = build_news_seeds(
            raw_items=[
                _item(
                    "a",
                    "ALAB vs AVGO: Which AI Infrastructure Stock Is the Better Buy Now?",
                    category="STRAT",
                    url="https://finance.yahoo.com/ai-infra-stock-pick",
                    summary=(
                        "Both companies benefit from AI infrastructure demand, "
                        "but this is a valuation comparison rather than a new "
                        "capacity, capex, JV, or deal event."
                    ),
                ),
                _item(
                    "b",
                    "AMD stock slips after analyst upgrades and AI accelerator chatter",
                    category="ANAL",
                    url="https://blockonomi.com/amd-analyst-upgrade",
                    summary="Market chatter mentions AI accelerator chips and Anthropic.",
                ),
            ],
            as_of=AS_OF,
        )

        self.assertNotIn("ai_infrastructure_jv", {seed["theme"] for seed in seeds})

    def test_builds_ai_infra_seed_from_data_center_launch(self) -> None:
        seeds = build_news_seeds(
            raw_items=[
                _item(
                    "a",
                    "Microsoft's biggest India data center on track to go live in mid-2026",
                    category="STRAT",
                    url="https://finance.yahoo.com/microsoft-india-data-center",
                    summary=(
                        "Microsoft is expanding Azure cloud capacity as AI demand "
                        "grows in India."
                    ),
                )
            ],
            as_of=AS_OF,
        )

        self.assertEqual(len(seeds), 1)
        seed = seeds[0]
        self.assertEqual(seed["theme"], "ai_infrastructure_jv")
        atom_texts = {atom["text"] for atom in seed["claim_atoms"]}
        self.assertIn("Big Tech AI infrastructure involvement", atom_texts)
        self.assertIn("data center or cloud capacity expansion", atom_texts)

    def test_does_not_build_ai_infra_seed_from_single_untrusted_source(self) -> None:
        seeds = build_news_seeds(
            raw_items=[
                _item(
                    "a",
                    "NextEra bets $66.8B on AI power boom with Dominion acquisition",
                    category="MA",
                    url="https://foxbusiness.com/nextera-dominion-ai-power",
                    summary=(
                        "NextEra plans to acquire Dominion Energy in a $66.8B "
                        "deal tied to AI-driven electricity demand."
                    ),
                )
            ],
            as_of=AS_OF,
        )

        self.assertNotIn("ai_infrastructure_jv", {seed["theme"] for seed in seeds})

    def test_does_not_build_ai_infra_seed_when_amount_only_from_untrusted_syndication(
        self,
    ) -> None:
        seeds = build_news_seeds(
            raw_items=[
                _item(
                    "a",
                    "AI infrastructure power demand becomes a market theme",
                    category="STRAT",
                    url="https://cnbc.com/ai-infrastructure-power-demand",
                    summary=(
                        "AI data center electricity demand is rising, but this "
                        "item does not identify a new deal, capex plan, or JV."
                    ),
                ),
                _item(
                    "b",
                    "NextEra eyes $67B utility acquisition for AI data center power",
                    category="MA",
                    url="https://aol.com/nextera-dominion-ai-power",
                    summary=(
                        "A syndicated item says NextEra may pursue a $67B "
                        "utility acquisition tied to AI power demand."
                    ),
                ),
                _item(
                    "c",
                    "Data center power deal chatter spreads across AI infrastructure blogs",
                    category="MA",
                    url="https://fourweekmba.com/ai-power-deal",
                    summary=(
                        "The blog repeats the $67B utility acquisition chatter "
                        "without trusted primary confirmation."
                    ),
                ),
            ],
            as_of=AS_OF,
        )

        self.assertNotIn("ai_infrastructure_jv", {seed["theme"] for seed in seeds})

    def test_builds_semiconductor_pressure_seed_from_multiple_items(self) -> None:
        seeds = build_news_seeds(
            raw_items=[
                _item(
                    "a",
                    "NVDA drops as investors brace for earnings and chip weakness",
                    category="MOVE",
                    url="https://cnbc.com/nvda-chip-pressure",
                ),
                _item(
                    "b",
                    "Semiconductor stocks slip as Treasury yields pressure valuations",
                    category="ANAL",
                    url="https://finance.yahoo.com/semis-yields",
                ),
            ],
            as_of=AS_OF,
        )

        self.assertEqual(len(seeds), 1)
        seed = seeds[0]
        self.assertEqual(seed["seed_type"], "sector_pressure")
        self.assertEqual(seed["subject"], "SEMIS")
        self.assertEqual(seed["theme"], "semiconductor_pressure")
        self.assertEqual(seed["evidence_count"], 2)
        self.assertGreaterEqual(len(seed["claim_atoms"]), 3)

    def test_summarize_news_seeds_counts_type_and_theme(self) -> None:
        seeds = build_news_seeds(
            raw_items=[
                _item(
                    "a",
                    "Google and Blackstone launch TPU cloud AI infrastructure JV",
                ),
                _item(
                    "b",
                    "NVDA drops as chip stocks face selling pressure",
                    category="MOVE",
                ),
                _item(
                    "c",
                    "Semiconductor stocks slip as yields pressure valuations",
                    category="ANAL",
                ),
            ],
            as_of=AS_OF,
        )

        summary = summarize_news_seeds(seeds)
        self.assertEqual(summary["news_seeds_built"], 2)
        self.assertEqual(
            summary["news_seeds_by_theme"],
            {
                "ai_infrastructure_jv": 1,
                "semiconductor_pressure": 1,
            },
        )


if __name__ == "__main__":
    unittest.main()
