from collections import Counter
from datetime import datetime
import re
import unittest
from zoneinfo import ZoneInfo

from news_scanner_v2.extractor import extract_event_from_candidate, extract_events


AS_OF = datetime(2026, 5, 14, 17, 30, tzinfo=ZoneInfo("Asia/Seoul"))
BAD_SUBJECTS = {"nyse", "nasdaq", "ppi", "p500", "ad", "n-v"}
ISIN_RE = re.compile(r"^[a-z]{2}[a-z0-9]{9}[0-9]$")


def candidate(
    title: str,
    category: str,
    candidate_id: str,
    *,
    provider: str = "brave",
    source: str = "replay",
    summary: str = "",
) -> dict:
    return {
        "id": candidate_id,
        "source": source,
        "provider": provider,
        "category": category,
        "title": title,
        "normalized_title": title.lower(),
        "url": f"https://example.com/{candidate_id}",
        "canonical_url": f"https://example.com/{candidate_id}",
        "published_at": "2026-05-14T08:25:00+00:00",
        "summary": summary,
    }


POSITIVE_CASES = (
    (
        "Veteran analyst resets Apple stock price target for 2026",
        "ANAL",
        "analyst",
        "aapl",
        "price_target",
    ),
    (
        "BofA Hikes NVIDIA Price Target to $320 on Massive $1.7 Trillion AI Data Center Forecast",
        "ANAL",
        "analyst",
        "nvda",
        "price_target",
    ),
    (
        "Target (NYSE:TGT) Stock Price Expected to Rise, Truist Financial Analyst Says",
        "ANAL",
        "analyst",
        "tgt",
        "analyst_action",
    ),
    (
        "CDW adds $1B to share repurchase authorization | CDW Stock News",
        "MA",
        "corporate_action",
        "cdw",
        "buyback",
    ),
    (
        "GoPro brings in global investment bank to explore possible sale - Stock Titan",
        "MA",
        "corporate_action",
        "gpro",
        "ma",
    ),
    (
        "VNET Group, Inc. (VNET) Stock: New Strategic Investors Back China Data Center Expansion",
        "MA",
        "corporate_action",
        "vnet",
        "corporate_transaction",
    ),
    (
        "Abbott Laboratories stock (US0028241000): shares rebound after earnings and guidance update",
        "EARN",
        "earnings",
        "abt",
        "guidance_update",
    ),
    (
        "Capricor Therapeutics, Inc. (NASDAQ:CAPR) Q1 2026 Earnings Call Transcript",
        "EARN",
        "earnings",
        "capr",
        "earnings_report",
    ),
    (
        "Cisco Systems (NASDAQ:CSCO) Issues FY 2026 Earnings Guidance",
        "EARN",
        "earnings",
        "csco",
        "guidance_update",
    ),
    (
        "Trump-Xi summit live: US, China leaders holding talks on trade, tech, Iran",
        "GEO",
        "geo",
        "trump_xi",
        "diplomacy",
    ),
    (
        "Treasury Warns of Sanctions Risks Linked to China-Based Independent Teapot Oil Refineries",
        "GEO",
        "geo",
        "china",
        "sanctions",
    ),
    (
        "World markets feel the strain as US-Iran war grinds on By Reuters",
        "GEO",
        "geo",
        "iran",
        "conflict",
    ),
    (
        "DXY Extends Gains After Hot PPI Report - TradingView News",
        "MACRO",
        "macro",
        "dxy",
        "macro_update",
    ),
    (
        "10-year Treasury yield rises to highest level in 10 months on hotter-than-expected inflation data",
        "MACRO",
        "macro",
        "rates",
        "rates_update",
    ),
    (
        "South Korean Won under pressure as volatility persists, says OCBC - MEXC",
        "MACRO",
        "macro",
        "usd_krw",
        "volatility_update",
    ),
    (
        "Today's Market Movers: Micron, Nvidia, and Alibaba Lead Wednesday's Premarket Action",
        "MOVE",
        "mover",
        "mu",
        "mover",
    ),
    (
        "Why Broadcom (AVGO) Is Becoming a Bigger Force in Custom AI Silicon",
        "STRAT",
        "strategic",
        "avgo",
        "strategic_update",
    ),
)


NEGATIVE_CASES = (
    ("Nebius Group N.V. Stock 12-Month Price Target Raised to $190", "ANAL"),
    ("How High Can AMD's Stock Price Rise? - TipRanks.com", "ANAL"),
    (
        "Source Energy Services stock (CA84852H1038): Strategic Investment Guide - AD HOC NEWS",
        "MA",
    ),
    (
        "Devon Energy stock (US25179M1036): Coterra merger completed, Q1 production hits guidance",
        "MA",
    ),
    (
        "Cornerstone Strategic Investment Fund, Inc. (CLM) to Issue Monthly Dividend of $0.12",
        "MA",
    ),
    (
        "Stock market today: Dow, S&P 500, Nasdaq mixed as PPI inflation data comes in hot",
        "MOVE",
    ),
    ("Crude Oil Price Today | WTI OIL PRICE CHART | OIL PRICE PER BARREL", "MACRO"),
    (
        "NVIDIA (NVDA) Stock Forecast: Analyst Ratings, Predictions & Price Target 2026",
        "STRAT",
    ),
    ("Beyond NVDA: A 4-Name Semi Buy List for the 2026 AI Build-Out", "STRAT"),
    ("Trade NVIDIA (NVDA) Stock Pre-Market on Public.com", "STRAT"),
    (
        "Nominations and Withdrawal Sent to the Senate",
        "GEO",
        "official_rss",
        "white-house-presidential-actions",
        "Deputy Secretary of the Treasury. The post appeared first on The White House.",
    ),
)


class ExtractorReplayTests(unittest.TestCase):
    def test_replay_positive_live_patterns(self) -> None:
        for idx, (title, category, event_type, subject, action) in enumerate(
            POSITIVE_CASES
        ):
            with self.subTest(title=title):
                event = extract_event_from_candidate(
                    candidate(title, category, f"p{idx}"),
                    as_of=AS_OF,
                )
                self.assertIsNotNone(event)
                assert event is not None
                payload = event.event.signature_payload()
                self.assertEqual(payload["event_type"], event_type)
                self.assertEqual(payload["subject"], subject)
                self.assertEqual(payload["action"], action)

    def test_replay_negative_live_noise_patterns(self) -> None:
        for idx, case in enumerate(NEGATIVE_CASES):
            title = case[0]
            category = case[1]
            provider = case[2] if len(case) > 2 else "brave"
            source = case[3] if len(case) > 3 else "replay"
            summary = case[4] if len(case) > 4 else ""
            with self.subTest(title=title):
                event = extract_event_from_candidate(
                    candidate(
                        title,
                        category,
                        f"n{idx}",
                        provider=provider,
                        source=source,
                        summary=summary,
                    ),
                    as_of=AS_OF,
                )
                self.assertIsNone(event)

    def test_replay_batch_counts_and_blocks_structural_false_subjects(self) -> None:
        items = [
            candidate(title, category, f"p{idx}")
            for idx, (title, category, *_rest) in enumerate(POSITIVE_CASES)
        ]
        items.extend(
            candidate(case[0], case[1], f"n{idx}")
            for idx, case in enumerate(NEGATIVE_CASES)
            if len(case) == 2
        )

        events = extract_events(items, as_of=AS_OF)
        counts = Counter(item["event"]["event_type"] for item in events)
        subjects = {item["event"]["subject"] for item in events}

        self.assertEqual(len(events), len(POSITIVE_CASES))
        self.assertEqual(
            counts,
            {
                "analyst": 3,
                "corporate_action": 3,
                "earnings": 3,
                "geo": 3,
                "macro": 3,
                "mover": 1,
                "strategic": 1,
            },
        )
        self.assertFalse(subjects & BAD_SUBJECTS)
        self.assertFalse(any(ISIN_RE.match(subject) for subject in subjects))


if __name__ == "__main__":
    unittest.main()
