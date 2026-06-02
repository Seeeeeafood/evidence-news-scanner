from datetime import datetime
import unittest
from zoneinfo import ZoneInfo

from news_scanner_v2.extractor import (
    extract_event_from_candidate,
    extract_events,
    extract_events_from_candidate,
)


AS_OF = datetime(2026, 5, 14, 17, 0, tzinfo=ZoneInfo("Asia/Seoul"))


def candidate(
    title: str,
    category: str,
    candidate_id: str = "c1",
    *,
    provider: str = "fixture",
    source: str = "unit",
    summary: str = "",
    body_text: str = "",
    published_at: str = "2026-05-14T12:45:00+00:00",
) -> dict:
    return {
        "id": candidate_id,
        "source": source,
        "provider": provider,
        "category": category,
        "title": title,
        "normalized_title": title.lower(),
        "url": "https://example.com/news",
        "canonical_url": "https://example.com/news",
        "published_at": published_at,
        "summary": summary,
        "body_text": body_text,
    }


class ExtractorTests(unittest.TestCase):
    def test_extract_earnings_company_event(self) -> None:
        event = extract_event_from_candidate(
            candidate("Nvidia (NVDA) raises guidance after Q1 earnings", "EARN"),
            as_of=AS_OF,
        )
        self.assertIsNotNone(event)
        assert event is not None
        payload = event.event.signature_payload()
        self.assertEqual(payload["event_type"], "earnings")
        self.assertEqual(payload["subject"], "nvda")
        self.assertEqual(payload["action"], "guidance_raise")
        self.assertEqual(payload["effective_date"], "2026-05-14")

    def test_earnings_title_aliases_prevent_fiscal_token_subjects(self) -> None:
        zscaler = extract_event_from_candidate(
            candidate(
                "Zscaler Earnings: Sales Shakeup And Weak Guidance Overshadow AI Narrative | Trefis",
                "EARN",
            ),
            as_of=AS_OF,
        )
        best_buy = extract_event_from_candidate(
            candidate(
                "Best Buy reiterates FY27 guidance after Q1 revenue and earnings rise",
                "EARN",
            ),
            as_of=AS_OF,
        )

        self.assertIsNotNone(zscaler)
        self.assertIsNotNone(best_buy)
        assert zscaler is not None
        assert best_buy is not None
        self.assertEqual(zscaler.event.signature_payload()["subject"], "zs")
        self.assertEqual(best_buy.event.signature_payload()["subject"], "bby")

    def test_earnings_title_owner_blocks_unrelated_body_ticker_rescue(self) -> None:
        events = extract_events_from_candidate(
            candidate(
                "HP Stock Faces Weekend Setup as AI-PC Gains Run Into Cost Pressures",
                "EARN",
                summary="CRM Q1 earnings guidance was raised in a separate article module.",
            ),
            as_of=AS_OF,
        )

        self.assertEqual(events, [])

    def test_earnings_live_update_can_extract_company_from_summary(self) -> None:
        event = extract_event_from_candidate(
            candidate(
                "Earnings live updates: Intuit stock tumbles after announcing job cuts",
                "EARN",
                summary=(
                    "For Q1, Nvidia reported EPS of $1.87 on revenue of "
                    "$81.62 billion and gave Q2 revenue guidance "
                    "between $89.1 billion and $92.8 billion."
                ),
                published_at="2026-05-21T13:54:00+00:00",
            ),
            as_of=datetime(2026, 5, 21, 22, 30, tzinfo=ZoneInfo("Asia/Seoul")),
        )

        self.assertIsNotNone(event)
        assert event is not None
        payload = event.event.signature_payload()
        self.assertEqual(payload["event_type"], "earnings")
        self.assertEqual(payload["subject"], "nvda")
        self.assertEqual(payload["action"], "guidance_update")

    def test_earnings_title_ignores_parenthetical_publisher(self) -> None:
        event = extract_event_from_candidate(
            candidate(
                "Nvidia's post-earnings gains may hinge on sales to China. Here's why (CNBC)",
                "EARN",
                source="breaking-hints-earn",
                provider="breaking_hint",
                published_at="2026-05-21T00:36:00+09:00",
            ),
            as_of=datetime(2026, 5, 21, 22, 30, tzinfo=ZoneInfo("Asia/Seoul")),
        )

        self.assertIsNotNone(event)
        assert event is not None
        payload = event.event.signature_payload()
        self.assertEqual(payload["event_type"], "earnings")
        self.assertEqual(payload["subject"], "nvda")
        self.assertEqual(payload["action"], "earnings_report")

    def test_salesforce_earnings_title_prefers_company_alias_over_pm_time_token(
        self,
    ) -> None:
        event = extract_event_from_candidate(
            candidate(
                "Salesforce Delivers Record First Quarter Fiscal 2027 Results",
                "EARN",
                summary=(
                    "Salesforce leaders will participate in a webinar on Friday, "
                    "May 29, 2026, at 11:00 AM PT / 2:00 PM ET. Revenue and EPS "
                    "beat expectations."
                ),
                published_at="2026-05-27T20:01:00+00:00",
            ),
            as_of=datetime(2026, 5, 28, 11, 0, tzinfo=ZoneInfo("Asia/Seoul")),
        )

        self.assertIsNotNone(event)
        assert event is not None
        payload = event.event.signature_payload()
        self.assertEqual(payload["event_type"], "earnings")
        self.assertEqual(payload["subject"], "crm")
        self.assertEqual(payload["action"], "earnings_report")

    def test_marvell_breaking_hint_extracts_mrvl_from_company_alias(self) -> None:
        event = extract_event_from_candidate(
            candidate(
                "Marvell's stock falls despite exceptional AI demand driving a stronger growth outlook",
                "EARN",
                source="breaking-hints-earn",
                provider="breaking_hint",
                published_at="2026-05-28T09:32:00+09:00",
            ),
            as_of=datetime(2026, 5, 28, 11, 0, tzinfo=ZoneInfo("Asia/Seoul")),
        )

        self.assertIsNotNone(event)
        assert event is not None
        payload = event.event.signature_payload()
        self.assertEqual(payload["event_type"], "earnings")
        self.assertEqual(payload["subject"], "mrvl")
        self.assertEqual(payload["action"], "guidance_update")

    def test_move_extracts_micron_as_mu_from_memory_rally_title(self) -> None:
        event = extract_event_from_candidate(
            candidate(
                "Micron joins $1 trillion club as AI race powers memory chip boom | Reuters",
                "MOVE",
                summary=(
                    "Micron Technology shares rallied as HBM demand remained strong "
                    "and memory chip stocks gained."
                ),
                published_at="2026-05-26T19:48:00+00:00",
            ),
            as_of=datetime(2026, 5, 27, 5, 0, tzinfo=ZoneInfo("Asia/Seoul")),
        )

        self.assertIsNotNone(event)
        assert event is not None
        payload = event.event.signature_payload()
        self.assertEqual(payload["event_type"], "mover")
        self.assertEqual(payload["subject"], "mu")
        self.assertEqual(payload["action"], "shares_up")

    def test_move_prefers_early_company_alias_over_late_noisy_parenthetical_ticker(self) -> None:
        event = extract_event_from_candidate(
            candidate(
                (
                    "Nasdaq 100 Hits 30,000 On Micron's 18% AI-Driven Rally - "
                    "American Airlines Group (NASDAQ:AAL), Allegro Mi - Benzinga"
                ),
                "MOVE",
                summary="Micron shares jumped on AI memory demand.",
                published_at="2026-05-26T19:49:00+00:00",
            ),
            as_of=datetime(2026, 5, 27, 5, 0, tzinfo=ZoneInfo("Asia/Seoul")),
        )

        self.assertIsNotNone(event)
        assert event is not None
        payload = event.event.signature_payload()
        self.assertEqual(payload["event_type"], "mover")
        self.assertEqual(payload["subject"], "mu")
        self.assertEqual(payload["action"], "shares_up")

    def test_geo_ai_chip_company_policy_risk_reroutes_to_company_event(self) -> None:
        events = extract_events_from_candidate(
            candidate(
                "Nvidia says it has 'largely conceded' China's AI chip market to Huawei",
                "GEO",
                summary=(
                    "Nvidia CEO Jensen Huang said the company has largely conceded "
                    "China's artificial intelligence chip market to Huawei as U.S. "
                    "export restrictions continue. Revenue surged 85% to $81.62 "
                    "billion, but H200 approvals remain a policy risk."
                ),
                published_at="2026-05-21T00:54:10+00:00",
            ),
            as_of=datetime(2026, 5, 22, 15, 39, tzinfo=ZoneInfo("Asia/Seoul")),
        )

        self.assertEqual(len(events), 1)
        payload = events[0].event.signature_payload()
        self.assertEqual(payload["event_type"], "strategic")
        self.assertEqual(payload["subject"], "nvda")
        self.assertEqual(payload["action"], "policy_risk")
        self.assertEqual(payload["scope"], "company")
        record = events[0].event.as_record()
        metadata = record["metadata"]
        self.assertEqual(metadata["source_category"], "GEO")
        self.assertEqual(metadata["extracted_from"], "company_policy_risk")

    def test_strat_ai_chip_company_policy_risk_uses_policy_risk_action(self) -> None:
        events = extract_events_from_candidate(
            candidate(
                "Nvidia says it has 'largely conceded' China's AI chip market to Huawei",
                "STRAT",
                summary=(
                    "Nvidia CEO Jensen Huang said the company has largely conceded "
                    "China's artificial intelligence chip market to Huawei as U.S. "
                    "export restrictions continue."
                ),
                published_at="2026-05-21T00:54:10+00:00",
            ),
            as_of=datetime(2026, 5, 22, 16, 30, tzinfo=ZoneInfo("Asia/Seoul")),
        )

        self.assertEqual(len(events), 1)
        payload = events[0].event.signature_payload()
        self.assertEqual(payload["event_type"], "strategic")
        self.assertEqual(payload["subject"], "nvda")
        self.assertEqual(payload["action"], "policy_risk")
        self.assertEqual(events[0].event.as_record()["metadata"]["source_category"], "STRAT")

    def test_geo_sanctions_article_does_not_use_distant_google_chrome_as_company(self) -> None:
        events = extract_events_from_candidate(
            candidate(
                "Details emerge of a potential Iran deal after Trump claims progress | AP News",
                "GEO",
                summary=(
                    "The U.S. would allow Iran to sell its oil through sanctions "
                    "waivers if Tehran complies with the proposed deal."
                ),
                body_text=(
                    "A U.S. official said if Iran does not give up its stockpile, "
                    "there will be no sanctions relief. "
                    + ("Middle East diplomacy continues. " * 70)
                    + "Google announces slew of AI advances, including a personal "
                    "AI assistant coming soon."
                ),
                published_at="2026-05-25T11:11:00+00:00",
            ),
            as_of=datetime(2026, 5, 25, 22, 30, tzinfo=ZoneInfo("Asia/Seoul")),
        )

        self.assertTrue(events)
        for event in events:
            payload = event.event.signature_payload()
            self.assertNotEqual(payload["subject"], "googl")
            self.assertFalse(
                payload["event_type"] == "strategic"
                and payload["action"] == "policy_risk"
            )

    def test_geo_sanctions_article_does_not_use_prime_minister_pm_as_ticker(self) -> None:
        events = extract_events_from_candidate(
            candidate(
                "US-Iran war news LIVE: Peace deal close, toll-free Strait of Hormuz likely to open",
                "GEO",
                summary=(
                    "Israeli PM Benjamin Netanyahu is set to preside over a security "
                    "meeting. Iran's calls for sanctions relief remain unresolved."
                ),
                published_at="2026-05-25T11:11:00+00:00",
            ),
            as_of=datetime(2026, 5, 25, 22, 30, tzinfo=ZoneInfo("Asia/Seoul")),
        )

        self.assertTrue(events)
        for event in events:
            payload = event.event.signature_payload()
            self.assertNotEqual(payload["subject"], "pm")
            self.assertFalse(
                payload["event_type"] == "strategic"
                and payload["action"] == "policy_risk"
            )

    def test_chips_act_support_is_strategic_not_earnings(self) -> None:
        events = extract_events_from_candidate(
            candidate(
                "IBM wins support for quantum chip foundry expansion",
                "EARN",
                summary=(
                    "IBM won Commerce Department CHIPS Act funding support for a "
                    "quantum semiconductor foundry project."
                ),
                published_at="2026-05-22T01:10:00+00:00",
            ),
            as_of=datetime(2026, 5, 22, 11, 0, tzinfo=ZoneInfo("Asia/Seoul")),
        )

        self.assertEqual(len(events), 1)
        payload = events[0].event.signature_payload()
        self.assertEqual(payload["event_type"], "strategic")
        self.assertEqual(payload["subject"], "ibm")
        self.assertEqual(payload["action"], "policy_support")
        self.assertEqual(payload["scope"], "company")
        metadata = events[0].event.as_record()["metadata"]
        self.assertEqual(metadata["source_category"], "EARN")
        self.assertEqual(metadata["extracted_from"], "company_policy_support")

    def test_stale_earnings_report_date_is_not_extracted_as_fresh_news(self) -> None:
        event = extract_event_from_candidate(
            candidate(
                "UNH stock surges past $394 on Q1 earnings beat",
                "EARN",
                summary=(
                    "UnitedHealth Group reported first-quarter 2026 results on "
                    "April 21 that completely reversed market sentiment. "
                    "Revenue reached $111.7 billion and shares jumped 9% on "
                    "earnings day."
                ),
                published_at="2026-05-18T10:35:20+00:00",
            ),
            as_of=datetime(2026, 5, 18, 22, 30, tzinfo=ZoneInfo("Asia/Seoul")),
        )
        self.assertIsNone(event)

    def test_recent_earnings_report_date_is_still_extracted(self) -> None:
        event = extract_event_from_candidate(
            candidate(
                "UNH stock surges past $394 on Q2 earnings beat",
                "EARN",
                summary=(
                    "UnitedHealth Group reported second-quarter 2026 results on "
                    "May 18 with revenue ahead of consensus."
                ),
                published_at="2026-05-18T10:35:20+00:00",
            ),
            as_of=datetime(2026, 5, 18, 22, 30, tzinfo=ZoneInfo("Asia/Seoul")),
        )
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.event.signature_payload()["effective_date"], "2026-05-18")
        metadata = event.event.as_record()["metadata"]["freshness"]
        self.assertEqual(metadata["event_date"], "2026-05-18")
        self.assertEqual(metadata["status"], "fresh_event_date")

    def test_stale_earnings_article_can_still_extract_fresh_stake_exit(self) -> None:
        records = extract_events(
            [
                candidate(
                    "UNH stock surges past $394 on Q1 earnings beat, Berkshire sells entire 5M share position",
                    "EARN",
                    summary=(
                        "UnitedHealth Group reported first-quarter 2026 results on "
                        "April 21. Berkshire Hathaway dumped its entire 5 million "
                        "share position, raising questions about the recovery."
                    ),
                    published_at="2026-05-18T10:35:20+00:00",
                )
            ],
            as_of=datetime(2026, 5, 18, 22, 30, tzinfo=ZoneInfo("Asia/Seoul")),
        )

        self.assertEqual(len(records), 1)
        event = records[0]["event"]
        self.assertEqual(event["event_type"], "corporate_action")
        self.assertEqual(event["subject"], "unh")
        self.assertEqual(event["payload"]["action"], "stake_exit")

    def test_cross_category_market_story_extracts_company_earnings_result(self) -> None:
        records = extract_events(
            [
                candidate(
                    (
                        "US Stock Market Today: Nvidia posts record $81.6 billion "
                        "quarterly revenue; Dow soars on Iran peace hopes"
                    ),
                    "GEO",
                    "c1",
                )
            ],
            as_of=AS_OF,
        )

        earnings = [
            record["event"]
            for record in records
            if record["event"]["event_type"] == "earnings"
        ]
        self.assertEqual(len(earnings), 1)
        self.assertEqual(earnings[0]["subject"], "nvda")
        self.assertEqual(earnings[0]["payload"]["action"], "earnings_report")
        self.assertEqual(
            earnings[0]["metadata"]["extracted_from"],
            "cross_category_earnings_result",
        )

    def test_ma_category_earnings_results_title_is_not_labeled_ipo(self) -> None:
        records = extract_events(
            [
                candidate(
                    "Autodesk (NASDAQ:ADSK) Releases Earnings Results - Ticker Report",
                    "MA",
                    "adsk-results",
                )
            ],
            as_of=AS_OF,
        )

        earnings = [
            record["event"]
            for record in records
            if record["event"]["event_type"] == "earnings"
        ]
        corporate_actions = [
            record["event"]
            for record in records
            if record["event"]["event_type"] == "corporate_action"
        ]
        self.assertEqual(len(earnings), 1)
        self.assertEqual(earnings[0]["subject"], "adsk")
        self.assertEqual(earnings[0]["payload"]["action"], "earnings_report")
        self.assertEqual(corporate_actions, [])

    def test_cross_category_snowflake_aws_story_uses_snow_as_earnings_owner(
        self,
    ) -> None:
        records = extract_events(
            [
                candidate(
                    "Snowflake rockets 36% on earnings beat and plan to spend $6 billion on Amazon cloud",
                    "STRAT",
                    "snow-aws",
                    summary=(
                        "Snowflake announced strong quarterly results and a "
                        "$6 billion AWS infrastructure partnership with Amazon "
                        "Web Services. Guidance also rose."
                    ),
                )
            ],
            as_of=AS_OF,
        )

        earnings = [
            record["event"]
            for record in records
            if record["event"]["event_type"] == "earnings"
        ]
        self.assertEqual(len(earnings), 1)
        self.assertEqual(earnings[0]["subject"], "snow")
        self.assertNotIn("amzn", [event["subject"] for event in earnings])
        ownership = earnings[0]["metadata"]["ownership"]
        self.assertEqual(ownership["primary_subject"], "SNOW")
        self.assertIn("AMZN", ownership["related_entities"])
        rejected = {
            item["subject"]: item["reason"]
            for item in ownership["rejected_subject_candidates"]
        }
        self.assertEqual(rejected.get("AMZN"), "cloud_provider_counterparty_context")

    def test_aws_customer_commitment_does_not_create_amzn_earnings_event(self) -> None:
        records = extract_events(
            [
                candidate(
                    "Amazon reports $6 billion AWS cloud customer commitment",
                    "STRAT",
                    "aws-only",
                    summary=(
                        "The AWS contract is a cloud spending commitment from an "
                        "enterprise customer, not an Amazon earnings report."
                    ),
                )
            ],
            as_of=AS_OF,
        )

        earnings = [
            record["event"]
            for record in records
            if record["event"]["event_type"] == "earnings"
        ]
        self.assertEqual(earnings, [])

    def test_generic_earnings_live_update_rescues_snowflake_from_summary(self) -> None:
        event = extract_event_from_candidate(
            candidate(
                "Earnings live updates: software stocks move after results",
                "EARN",
                summary=(
                    "Snowflake reported Q1 fiscal 2027 results with product "
                    "revenue guidance raised to $5.84 billion after a $6 billion "
                    "AWS deal."
                ),
            ),
            as_of=AS_OF,
        )

        self.assertIsNotNone(event)
        assert event is not None
        payload = event.event.signature_payload()
        self.assertEqual(payload["event_type"], "earnings")
        self.assertEqual(payload["subject"], "snow")
        ownership = event.event.as_record()["metadata"]["ownership"]
        self.assertEqual(ownership["primary_subject"], "SNOW")
        self.assertIn("AMZN", ownership["related_entities"])

    def test_extract_analyst_event(self) -> None:
        event = extract_event_from_candidate(
            candidate("Analyst upgrades Microsoft (MSFT), raises price target", "ANAL"),
            as_of=AS_OF,
        )
        self.assertIsNotNone(event)
        assert event is not None
        payload = event.event.signature_payload()
        self.assertEqual(payload["event_type"], "analyst")
        self.assertEqual(payload["subject"], "msft")
        self.assertEqual(payload["action"], "upgrade")

    def test_extract_macro_event(self) -> None:
        event = extract_event_from_candidate(
            candidate("Fed leaves rates unchanged as Treasury yields fall", "MACRO"),
            as_of=AS_OF,
        )
        self.assertIsNotNone(event)
        assert event is not None
        payload = event.event.signature_payload()
        self.assertEqual(payload["event_type"], "macro")
        self.assertEqual(payload["subject"], "rates")
        self.assertEqual(payload["action"], "rates_update")

    def test_extract_official_fomc_minutes_full_committee_name(self) -> None:
        event = extract_event_from_candidate(
            candidate(
                "Minutes of the Federal Open Market Committee, April 28-29, 2026",
                "MACRO",
                provider="official_rss",
                source="federal-reserve-press",
                published_at="2026-05-20T18:00:00+00:00",
            ),
            as_of=datetime(2026, 5, 21, 5, 0, tzinfo=ZoneInfo("Asia/Seoul")),
        )

        self.assertIsNotNone(event)
        assert event is not None
        payload = event.event.signature_payload()
        self.assertEqual(payload["event_type"], "macro")
        self.assertEqual(payload["subject"], "fomc")
        self.assertEqual(payload["action"], "fomc_minutes")
        self.assertEqual(payload["effective_date"], "2026-05-21")

    def test_extract_private_company_ipo_event(self) -> None:
        event = extract_event_from_candidate(
            candidate(
                "SpaceX IPO filing brings Musk's interplanetary ambitions to Wall Street",
                "MA",
                source="brave-discovery-2-ma",
            ),
            as_of=AS_OF,
        )

        self.assertIsNotNone(event)
        assert event is not None
        payload = event.event.signature_payload()
        self.assertEqual(payload["event_type"], "corporate_action")
        self.assertEqual(payload["subject"], "spacex")
        self.assertEqual(payload["action"], "ipo")

    def test_tickerless_uber_delivery_hero_ma_hint_is_eventized(self) -> None:
        event = extract_event_from_candidate(
            candidate(
                "🔴 [M&A] Uber weighs higher bid for Delivery Hero after €11.5bn offer rebuffed (Financial Times)",
                "MA",
                source="breaking-hints-ma",
                provider="breaking_hint",
                published_at="2026-05-25T04:47:00+09:00",
            ),
            as_of=datetime(2026, 5, 25, 19, 0, tzinfo=ZoneInfo("Asia/Seoul")),
        )

        self.assertIsNotNone(event)
        assert event is not None
        payload = event.event.signature_payload()
        self.assertEqual(payload["event_type"], "corporate_action")
        self.assertEqual(payload["subject"], "uber")
        self.assertEqual(payload["action"], "ma")

    def test_delivery_hero_headline_prefers_uber_as_us_acquirer(self) -> None:
        event = extract_event_from_candidate(
            candidate(
                "Delivery Hero confirms Uber buyout offer after stake increase to 19.5%",
                "MA",
                source="breaking-hints-ma",
                provider="breaking_hint",
                published_at="2026-05-25T01:00:00+09:00",
            ),
            as_of=datetime(2026, 5, 25, 19, 0, tzinfo=ZoneInfo("Asia/Seoul")),
        )

        self.assertIsNotNone(event)
        assert event is not None
        payload = event.event.signature_payload()
        self.assertEqual(payload["event_type"], "corporate_action")
        self.assertEqual(payload["subject"], "uber")
        self.assertEqual(payload["action"], "ma")

    def test_extract_geo_event(self) -> None:
        event = extract_event_from_candidate(
            candidate("Trump-Xi summit focuses on tariffs and technology", "GEO"),
            as_of=AS_OF,
        )
        self.assertIsNotNone(event)
        assert event is not None
        payload = event.event.signature_payload()
        self.assertEqual(payload["event_type"], "geo")
        self.assertEqual(payload["subject"], "trump_xi")
        self.assertEqual(payload["action"], "tariff_policy")

    def test_geo_warns_does_not_match_war_conflict(self) -> None:
        event = extract_event_from_candidate(
            candidate(
                "Live updates: Xi hails new era in US-China relations, but warns Trump on Taiwan",
                "GEO",
            ),
            as_of=AS_OF,
        )
        self.assertIsNotNone(event)
        assert event is not None
        payload = event.event.signature_payload()
        self.assertEqual(payload["subject"], "trump_xi")
        self.assertEqual(payload["action"], "policy_geo")
        self.assertEqual(payload["object"], "taiwan_warning")

    def test_geo_signature_separates_same_subject_action_by_story_object(self) -> None:
        first = extract_event_from_candidate(
            candidate(
                "Trump-Xi summit opens as Xi warns Trump over Taiwan",
                "GEO",
                "c1",
            ),
            as_of=AS_OF,
        )
        second = extract_event_from_candidate(
            candidate(
                "Trump-Xi summit focuses on Iran and keeping Hormuz shipping open",
                "GEO",
                "c2",
            ),
            as_of=AS_OF,
        )

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        assert first is not None
        assert second is not None
        first_payload = first.event.signature_payload()
        second_payload = second.event.signature_payload()
        self.assertEqual(first_payload["subject"], "trump_xi")
        self.assertEqual(second_payload["subject"], "trump_xi")
        self.assertEqual(first_payload["action"], "diplomacy")
        self.assertEqual(second_payload["action"], "diplomacy")
        self.assertEqual(first_payload["object"], "taiwan_warning")
        self.assertEqual(second_payload["object"], "hormuz_shipping")
        self.assertNotEqual(first.event.signature(), second.event.signature())

    def test_geo_market_impact_angle_gets_distinct_signature(self) -> None:
        conflict_update = extract_event_from_candidate(
            candidate(
                "U.S. and Iran still without deal to end war after Trump says he is not in a hurry",
                "GEO",
                "c1",
            ),
            as_of=AS_OF,
        )
        market_impact = extract_event_from_candidate(
            candidate(
                "Japanese Firms Cut Investment as War in Iran Clouded Outlook (Bloomberg)",
                "GEO",
                "c2",
            ),
            as_of=AS_OF,
        )

        self.assertIsNotNone(conflict_update)
        self.assertIsNotNone(market_impact)
        assert conflict_update is not None
        assert market_impact is not None
        first_payload = conflict_update.event.signature_payload()
        second_payload = market_impact.event.signature_payload()
        self.assertEqual(first_payload["subject"], "iran")
        self.assertEqual(second_payload["subject"], "iran")
        self.assertEqual(first_payload["action"], "conflict")
        self.assertEqual(second_payload["action"], "conflict")
        self.assertEqual(first_payload["object"], "military_escalation")
        self.assertEqual(second_payload["object"], "iran_market_pressure")
        self.assertNotEqual(conflict_update.event.signature(), market_impact.event.signature())

    def test_hormuz_toll_story_uses_stable_policy_signature(self) -> None:
        first = extract_event_from_candidate(
            candidate(
                "Iran and Oman discuss Strait of Hormuz toll regime as markets watch oil",
                "GEO",
                "c1",
            ),
            as_of=AS_OF,
        )
        second = extract_event_from_candidate(
            candidate(
                "US-Iran peace efforts face setbacks over uranium and Hormuz tolls",
                "GEO",
                "c2",
            ),
            as_of=AS_OF,
        )
        third = extract_event_from_candidate(
            candidate(
                "Rubio warns Iran that charging fees in the Strait of Hormuz is unacceptable",
                "GEO",
                "c3",
            ),
            as_of=AS_OF,
        )

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertIsNotNone(third)
        assert first is not None
        assert second is not None
        assert third is not None
        first_payload = first.event.signature_payload()
        second_payload = second.event.signature_payload()
        third_payload = third.event.signature_payload()
        self.assertEqual(first_payload["subject"], "iran")
        self.assertEqual(first_payload["action"], "policy_geo")
        self.assertEqual(first_payload["object"], "hormuz_toll_regime")
        self.assertEqual(second_payload["object"], "hormuz_toll_regime")
        self.assertEqual(third_payload["object"], "hormuz_toll_regime")
        self.assertEqual(first.event.signature(), second.event.signature())
        self.assertEqual(second.event.signature(), third.event.signature())

    def test_iran_deal_conditions_story_uses_stable_policy_signature(self) -> None:
        first = extract_event_from_candidate(
            candidate(
                "Trump adds Abraham Accords as required condition for Iran deal, demanding Saudi, Qatar, Egypt, Jordan, Turkey and Pakistan sign",
                "GEO",
                "c1",
            ),
            as_of=datetime(2026, 5, 25, 22, 30, tzinfo=ZoneInfo("Asia/Seoul")),
        )
        second = extract_event_from_candidate(
            candidate(
                "Iran peace agreement prerequisite requires Gulf states to sign Abraham Accords",
                "GEO",
                "c2",
            ),
            as_of=datetime(2026, 5, 25, 22, 30, tzinfo=ZoneInfo("Asia/Seoul")),
        )

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        assert first is not None
        assert second is not None
        first_payload = first.event.signature_payload()
        second_payload = second.event.signature_payload()
        self.assertEqual(first_payload["event_type"], "geo")
        self.assertEqual(first_payload["subject"], "iran")
        self.assertEqual(first_payload["action"], "policy_geo")
        self.assertEqual(first_payload["object"], "iran_deal_conditions")
        self.assertEqual(second_payload["object"], "iran_deal_conditions")
        self.assertEqual(first.event.signature(), second.event.signature())

    def test_iran_deal_conditions_can_be_promoted_from_body_text(self) -> None:
        event = extract_event_from_candidate(
            candidate(
                "Live Updates: Iran and U.S. agree deal to end war taking shape, but Iran says obstacles remain",
                "GEO",
                body_text=(
                    "Trump says talks are proceeding nicely, and Iran deal should "
                    "see other Gulf allies sign Abraham Accords. He said it should "
                    "be mandatory that Saudi Arabia and Qatar sign as part of the deal."
                ),
            ),
            as_of=datetime(2026, 5, 25, 22, 30, tzinfo=ZoneInfo("Asia/Seoul")),
        )

        self.assertIsNotNone(event)
        assert event is not None
        payload = event.event.signature_payload()
        self.assertEqual(payload["subject"], "iran")
        self.assertEqual(payload["action"], "policy_geo")
        self.assertEqual(payload["object"], "iran_deal_conditions")

    def test_new_geo_issue_uses_canonical_entity_action_object(self) -> None:
        first = extract_event_from_candidate(
            candidate(
                "Venezuela coup fears rise as military factions pressure Maduro",
                "GEO",
                "c1",
            ),
            as_of=AS_OF,
        )
        second = extract_event_from_candidate(
            candidate(
                "Maduro faces coup risk as Venezuela military pressure grows",
                "GEO",
                "c2",
            ),
            as_of=AS_OF,
        )
        third = extract_event_from_candidate(
            candidate(
                "Venezuela election dispute deepens as opposition rejects vote",
                "GEO",
                "c3",
            ),
            as_of=AS_OF,
        )

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertIsNotNone(third)
        assert first is not None
        assert second is not None
        assert third is not None
        self.assertEqual(first.event.signature_payload()["subject"], "venezuela")
        self.assertEqual(first.event.signature_payload()["object"], "venezuela_coup")
        self.assertEqual(second.event.signature_payload()["object"], "venezuela_coup")
        self.assertEqual(third.event.signature_payload()["object"], "venezuela_election_risk")
        self.assertEqual(first.event.signature(), second.event.signature())
        self.assertNotEqual(first.event.signature(), third.event.signature())

    def test_new_maritime_geo_issue_does_not_fall_into_hormuz_bucket(self) -> None:
        first = extract_event_from_candidate(
            candidate(
                "South China Sea blockade disrupts shipping near Spratly Islands",
                "GEO",
                "c1",
            ),
            as_of=AS_OF,
        )
        second = extract_event_from_candidate(
            candidate(
                "Philippines says China blockade in South China Sea threatens cargo routes",
                "GEO",
                "c2",
            ),
            as_of=AS_OF,
        )

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        assert first is not None
        assert second is not None
        self.assertEqual(first.event.signature_payload()["subject"], "south_china_sea")
        self.assertEqual(
            first.event.signature_payload()["object"],
            "south_china_sea_maritime_blockade",
        )
        self.assertEqual(
            second.event.signature_payload()["object"],
            "south_china_sea_maritime_blockade",
        )
        self.assertEqual(first.event.signature(), second.event.signature())

    def test_new_geo_drone_attack_gets_specific_canonical_object(self) -> None:
        first = extract_event_from_candidate(
            candidate("Saudi drone attack hits Aramco oil facility", "GEO", "c1"),
            as_of=AS_OF,
        )
        second = extract_event_from_candidate(
            candidate("Aramco facility struck in Saudi UAV attack", "GEO", "c2"),
            as_of=AS_OF,
        )

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        assert first is not None
        assert second is not None
        self.assertEqual(first.event.signature_payload()["subject"], "saudi")
        self.assertEqual(first.event.signature_payload()["object"], "saudi_drone_attack")
        self.assertEqual(second.event.signature_payload()["object"], "saudi_drone_attack")
        self.assertEqual(first.event.signature(), second.event.signature())

    def test_generic_listicle_is_not_extracted(self) -> None:
        event = extract_event_from_candidate(
            candidate("10 Best Tech Stocks to Buy for 2026", "ANAL"),
            as_of=AS_OF,
        )
        self.assertIsNone(event)

    def test_google_rss_publisher_suffix_is_not_used_as_ticker(self) -> None:
        event = extract_event_from_candidate(
            candidate(
                "Source Energy Services stock: Strategic Investment Guide - AD HOC NEWS",
                "MA",
            ),
            as_of=AS_OF,
        )
        self.assertIsNone(event)

    def test_parenthetical_us_suffix_is_normalized_to_ticker(self) -> None:
        event = extract_event_from_candidate(
            candidate("VNET Group, Inc. (VNET.US) adds strategic investor", "MA"),
            as_of=AS_OF,
        )
        self.assertIsNotNone(event)
        assert event is not None
        payload = event.event.signature_payload()
        self.assertEqual(payload["subject"], "vnet")

    def test_parenthetical_exchange_prefix_is_normalized_to_ticker(self) -> None:
        event = extract_event_from_candidate(
            candidate(
                "Target (NYSE:TGT) Stock Price Expected to Rise, Analyst Says",
                "ANAL",
            ),
            as_of=AS_OF,
        )
        self.assertIsNotNone(event)
        assert event is not None
        payload = event.event.signature_payload()
        self.assertEqual(payload["subject"], "tgt")

    def test_parenthetical_exchange_suffix_is_normalized_to_ticker(self) -> None:
        event = extract_event_from_candidate(
            candidate(
                "Paysafe stock jumps after earnings beat, guidance reaffirmed (PSFE:NYSE)",
                "EARN",
            ),
            as_of=AS_OF,
        )
        self.assertIsNotNone(event)
        assert event is not None
        payload = event.event.signature_payload()
        self.assertEqual(payload["subject"], "psfe")

    def test_reaffirmed_guidance_is_not_guidance_cut(self) -> None:
        event = extract_event_from_candidate(
            candidate(
                "Paysafe stock jumps as earnings beat, guidance reaffirmed, leverage reduced (PSFE:NYSE)",
                "EARN",
            ),
            as_of=AS_OF,
        )
        self.assertIsNotNone(event)
        assert event is not None
        payload = event.event.signature_payload()
        self.assertEqual(payload["subject"], "psfe")
        self.assertEqual(payload["action"], "guidance_update")

    def test_isin_parenthetical_is_not_used_as_ticker(self) -> None:
        event = extract_event_from_candidate(
            candidate(
                "Devon Energy stock (US25179M1036): Coterra merger completed",
                "MA",
            ),
            as_of=AS_OF,
        )
        self.assertIsNone(event)

    def test_isin_parenthetical_can_fall_back_to_company_alias(self) -> None:
        event = extract_event_from_candidate(
            candidate(
                "Abbott Laboratories stock (US0028241000): guidance update after earnings",
                "EARN",
            ),
            as_of=AS_OF,
        )
        self.assertIsNotNone(event)
        assert event is not None
        payload = event.event.signature_payload()
        self.assertEqual(payload["subject"], "abt")

    def test_company_aliases_cover_backlog_earnings_names(self) -> None:
        cases = [
            ("GE Vernova Raises FY2026 Sales Guidance", "EARN", "gev"),
            ("GE Vernov Q1 Sales $9.339B Beat $9.173B Estimate", "EARN", "gev"),
            ("Texas Instruments Sees Q2 GAAP EPS $1.77-$2.05", "EARN", "txn"),
            ("ServiceNow Says Outlook Reflects Geopolitical Headwinds", "EARN", "now"),
            ("Comcast Q1 EPS Beats Estimate", "EARN", "cmcsa"),
            ("Lam Research Q3 Earnings Beat", "EARN", "lrcx"),
            ("Vertiv Raises Full-Year Outlook", "EARN", "vrt"),
            ("IBM Q1 Adj. EPS $1.91 Beats $1.81 Estimate", "EARN", "ibm"),
            ("Intel Delivers Strong AI-Fueled Outlook", "EARN", "intc"),
            ("AT&T Affirms FY2026 Adj EPS Guidance", "EARN", "t"),
            ("T-Mobile USA Increases 2026 Shareholder Return Program", "MA", "tmus"),
        ]
        for title, category, expected in cases:
            with self.subTest(title=title):
                event = extract_event_from_candidate(candidate(title, category), as_of=AS_OF)
                self.assertIsNotNone(event)
                assert event is not None
                payload = event.event.signature_payload()
                self.assertEqual(payload["subject"], expected)

    def test_gaap_is_not_used_as_ticker_when_company_alias_exists(self) -> None:
        event = extract_event_from_candidate(
            candidate(
                "Texas Instruments Sees Q2 GAAP EPS $1.77-$2.05 vs $1.57 Est",
                "EARN",
            ),
            as_of=AS_OF,
        )
        self.assertIsNotNone(event)
        assert event is not None
        payload = event.event.signature_payload()
        self.assertEqual(payload["subject"], "txn")

    def test_reporting_words_are_not_used_as_uppercase_ticker_fallback(self) -> None:
        cases = [
            ("UPDATE: ASM International Sees Q2 Sales $1.057B-$1.168B", "EARN"),
            ("K-Tech Solutions Expects Annual Revenue To Climb To $60M By FY27", "EARN"),
            ("AtlasClear Signs LOI To Acquire Ark Financial", "MA"),
            ("Teck And Anglo American Target $1.4B EBITDA Uplift", "MA"),
        ]
        for title, category in cases:
            with self.subTest(title=title):
                event = extract_event_from_candidate(candidate(title, category), as_of=AS_OF)
                self.assertIsNone(event)

    def test_old_acquisition_lawsuit_settlement_is_not_fresh_ma(self) -> None:
        cases = [
            "Activision shareholders reach $250 million settlement over Microsoft buyout - The Hindu",
            "Microsoft Settles Activision Blizzard Acquisition Lawsuit for $340 Billion - Inven Global",
        ]
        for title in cases:
            with self.subTest(title=title):
                event = extract_event_from_candidate(candidate(title, "MA"), as_of=AS_OF)
                self.assertIsNone(event)

    def test_company_suffix_is_not_used_as_ticker(self) -> None:
        event = extract_event_from_candidate(
            candidate(
                "Nebius Group N.V. Stock 12-Month Price Target Raised to $190",
                "ANAL",
            ),
            as_of=AS_OF,
        )
        self.assertIsNone(event)

    def test_broad_market_mover_without_company_is_not_extracted(self) -> None:
        event = extract_event_from_candidate(
            candidate(
                "Stock market today: Dow, S&P 500, Nasdaq mixed as PPI data comes in hot",
                "MOVE",
            ),
            as_of=AS_OF,
        )
        self.assertIsNone(event)

    def test_low_signal_strategic_commentary_is_not_extracted(self) -> None:
        event = extract_event_from_candidate(
            candidate(
                "NVIDIA (NVDA) Stock Forecast: Analyst Ratings, Predictions & Price Target 2026",
                "STRAT",
            ),
            as_of=AS_OF,
        )
        self.assertIsNone(event)

    def test_investment_story_commentary_is_not_extracted_as_strategic_event(self) -> None:
        event = extract_event_from_candidate(
            candidate(
                "How The Broadcom (AVGO) Investment Story Is Shifting With AI Hopes And Fresh Concerns",
                "STRAT",
            ),
            as_of=AS_OF,
        )
        self.assertIsNone(event)

    def test_stock_pick_commentary_is_not_extracted_as_earnings_event(self) -> None:
        event = extract_event_from_candidate(
            candidate(
                "Sea Limited (SE) Stands Out as a Top Steve Cohen Large-Cap Pick on Robust Revenue Growth",
                "EARN",
            ),
            as_of=AS_OF,
        )
        self.assertIsNone(event)

    def test_macro_quote_page_is_not_extracted(self) -> None:
        event = extract_event_from_candidate(
            candidate(
                "Crude Oil Price Today | WTI OIL PRICE CHART | OIL PRICE PER BARREL",
                "MACRO",
            ),
            as_of=AS_OF,
        )
        self.assertIsNone(event)

    def test_low_signal_official_geo_is_not_extracted(self) -> None:
        event = extract_event_from_candidate(
            candidate(
                "Nominations Sent to the Senate",
                "GEO",
                provider="official_rss",
                source="white-house-presidential-actions",
                summary="Deputy Secretary of the Treasury. The post appeared first on The White House.",
            ),
            as_of=AS_OF,
        )
        self.assertIsNone(event)

    def test_market_signal_official_geo_is_extracted(self) -> None:
        event = extract_event_from_candidate(
            candidate(
                "Executive Order on new tariffs for China technology imports",
                "GEO",
                provider="official_rss",
                source="white-house-presidential-actions",
            ),
            as_of=AS_OF,
        )
        self.assertIsNotNone(event)
        assert event is not None
        payload = event.event.signature_payload()
        self.assertEqual(payload["event_type"], "geo")
        self.assertEqual(payload["action"], "tariff_policy")

    def test_macro_signature_keeps_distinct_titles_separate(self) -> None:
        first = extract_event_from_candidate(
            candidate("Gold rises as dollar softens after CPI data", "MACRO", "c1"),
            as_of=AS_OF,
        )
        second = extract_event_from_candidate(
            candidate(
                "Gold import duty hike rattles jewellery trade",
                "MACRO",
                "c2",
            ),
            as_of=AS_OF,
        )
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        assert first is not None
        assert second is not None
        self.assertNotEqual(first.event.signature(), second.event.signature())

    def test_macro_signature_dedupes_same_title_without_publisher_suffix(self) -> None:
        first = extract_event_from_candidate(
            candidate(
                "10-year Treasury yield rises after hot inflation data - Yahoo Finance",
                "MACRO",
                "c1",
            ),
            as_of=AS_OF,
        )
        second = extract_event_from_candidate(
            candidate(
                "10-year Treasury yield rises after hot inflation data - Reuters",
                "MACRO",
                "c2",
            ),
            as_of=AS_OF,
        )
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        assert first is not None
        assert second is not None
        self.assertEqual(first.event.signature(), second.event.signature())

    def test_official_fed_enforcement_action_is_not_extracted(self) -> None:
        event = extract_event_from_candidate(
            candidate(
                "Federal Reserve Board announces termination of enforcement actions with F & M Holding Company, Inc. and Thread Bancorp, Inc.",
                "MACRO",
                provider="official_rss",
                source="federal-reserve-press",
            ),
            as_of=AS_OF,
        )
        self.assertIsNone(event)

    def test_official_fed_oath_is_not_extracted_as_macro_policy(self) -> None:
        event = extract_event_from_candidate(
            candidate(
                "Kevin Warsh takes oath of office as chairman and a member of the Board of Governors of the Federal Reserve System, and the Federal Open Market Committee unanimously selects Warsh as its chairman",
                "MACRO",
                provider="official_rss",
                source="federal-reserve-press",
                published_at="2026-05-22T20:15:00+00:00",
            ),
            as_of=datetime(2026, 5, 25, 22, 30, tzinfo=ZoneInfo("Asia/Seoul")),
        )

        self.assertIsNone(event)

    def test_official_fed_non_policy_report_is_not_extracted(self) -> None:
        event = extract_event_from_candidate(
            candidate(
                "Federal Reserve Board issues Economic Well-Being of U.S. Households in 2025 report",
                "MACRO",
                provider="official_rss",
                source="federal-reserve-press",
            ),
            as_of=AS_OF,
        )
        self.assertIsNone(event)

    def test_extract_events_dedupes_candidate_links(self) -> None:
        items = [
            candidate("Apple (AAPL) raises guidance after earnings", "EARN", "c1"),
            candidate("Apple (AAPL) raises guidance after earnings", "EARN", "c1"),
        ]
        self.assertEqual(len(extract_events(items, as_of=AS_OF)), 1)


if __name__ == "__main__":
    unittest.main()
