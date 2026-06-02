import unittest

from news_scanner_v2.evidence_contract import build_evidence_contract


def row(**overrides):
    base = {
        "event_type": "geo",
        "subject": "iran",
        "action": "conflict",
        "effective_date": "2026-05-18",
        "evidence_count": 1,
        "source_tier": "trusted",
        "risk_flags": [],
        "title": "World markets feel the strain as US-Iran war grinds on",
        "body_text": "Iran war pressure hits global markets. " * 20,
        "evidence_items": [],
        "event_metadata": {"published_date": "2026-05-18"},
    }
    base.update(overrides)
    return base


class EvidenceContractTests(unittest.TestCase):
    def test_geo_trusted_body_basis_passes(self) -> None:
        contract = build_evidence_contract(row())

        self.assertEqual(contract["status"], "pass")
        self.assertTrue(contract["delivery_eligible"])
        self.assertEqual(contract["basis_level"], "body")
        self.assertEqual(contract["price_reaction"]["status"], "not_applicable")

    def test_low_quality_source_fails_delivery(self) -> None:
        contract = build_evidence_contract(row(source_tier="low_quality"))

        self.assertEqual(contract["status"], "fail")
        self.assertFalse(contract["delivery_eligible"])
        self.assertIn("low_quality_source", contract["failures"])

    def test_stale_earnings_fails_delivery(self) -> None:
        contract = build_evidence_contract(
            row(
                event_type="earnings",
                subject="unh",
                action="earnings_report",
                source_tier="trusted",
                event_metadata={
                    "freshness": {
                        "status": "stale_event_date",
                        "event_date": "2026-04-21",
                        "event_date_source": "body_report_date",
                        "event_age_days": 27,
                        "stale": True,
                        "max_age_days": 7,
                    }
                },
            )
        )

        self.assertEqual(contract["status"], "fail")
        self.assertFalse(contract["delivery_eligible"])
        self.assertIn("stale_event", contract["failures"])

    def test_single_untrusted_earnings_fails_delivery(self) -> None:
        contract = build_evidence_contract(
            row(
                event_type="earnings",
                subject="dlr",
                action="earnings_report",
                source_tier="untrusted",
                evidence_count=1,
                event_metadata={
                    "freshness": {
                        "status": "fresh_event_date",
                        "event_date": "2026-05-18",
                        "event_date_source": "body_report_date",
                        "stale": False,
                    }
                },
            )
        )

        self.assertEqual(contract["status"], "fail")
        self.assertFalse(contract["delivery_eligible"])
        self.assertIn("single_source_untrusted_earnings", contract["failures"])
        self.assertIn("single_source_untrusted_company_event", contract["failures"])

    def test_unknown_single_source_earnings_event_date_fails_delivery(self) -> None:
        contract = build_evidence_contract(
            row(
                event_type="earnings",
                subject="nvda",
                action="earnings_report",
                source_tier="trusted",
                evidence_count=1,
                url="https://finance.yahoo.com/markets/stocks/articles/nvda-commentary",
                event_metadata={
                    "freshness": {
                        "status": "unknown_event_date",
                        "event_date": "2026-05-25",
                        "event_date_source": "published_at",
                        "stale": False,
                    }
                },
                evidence_items=[
                    {
                        "url": "https://finance.yahoo.com/markets/stocks/articles/nvda-commentary",
                        "summary": "Commentary mentions Nvidia's earlier earnings print.",
                    }
                ],
            )
        )

        self.assertEqual(contract["status"], "fail")
        self.assertFalse(contract["delivery_eligible"])
        self.assertIn("unknown_earnings_event_date", contract["warnings"])
        self.assertIn(
            "unknown_earnings_event_date_single_source",
            contract["failures"],
        )

    def test_unknown_single_source_earnings_event_date_allows_wire_source(self) -> None:
        contract = build_evidence_contract(
            row(
                event_type="earnings",
                subject="nvda",
                action="earnings_report",
                source_tier="trusted",
                evidence_count=1,
                url="https://www.reuters.com/technology/nvidia-results-2026-05-25/",
                event_metadata={
                    "freshness": {
                        "status": "unknown_event_date",
                        "event_date": "2026-05-25",
                        "event_date_source": "published_at",
                        "stale": False,
                    }
                },
                evidence_items=[
                    {
                        "url": "https://www.reuters.com/technology/nvidia-results-2026-05-25/",
                        "summary": "Reuters reports Nvidia quarterly results.",
                    }
                ],
            )
        )

        self.assertEqual(contract["status"], "warn")
        self.assertTrue(contract["delivery_eligible"])
        self.assertIn("unknown_earnings_event_date", contract["warnings"])
        self.assertNotIn(
            "unknown_earnings_event_date_single_source",
            contract["failures"],
        )

    def test_unknown_single_source_earnings_event_date_allows_marketwatch_hint(self) -> None:
        contract = build_evidence_contract(
            row(
                event_type="earnings",
                subject="mrvl",
                action="guidance_update",
                source_tier="trusted",
                evidence_count=1,
                url="breaking-hint://breaking_2026-05-28.md:4",
                event_metadata={
                    "freshness": {
                        "status": "unknown_event_date",
                        "event_date": "2026-05-28",
                        "event_date_source": "published_at",
                        "stale": False,
                    }
                },
                evidence_items=[
                    {
                        "url": "breaking-hint://breaking_2026-05-28.md:4",
                        "title": (
                            "Marvell's stock falls despite exceptional AI demand "
                            "driving a stronger growth outlook (MarketWatch)"
                        ),
                        "summary": (
                            "Marvell's stock falls despite exceptional AI demand "
                            "driving a stronger growth outlook (MarketWatch)"
                        ),
                    }
                ],
            )
        )

        self.assertEqual(contract["status"], "warn")
        self.assertTrue(contract["delivery_eligible"])
        self.assertIn("unknown_earnings_event_date", contract["warnings"])
        self.assertNotIn(
            "unknown_earnings_event_date_single_source",
            contract["failures"],
        )
        self.assertEqual(contract["price_reaction"]["status"], "missing_optional")

    def test_unknown_single_source_earnings_event_date_allows_cnbc_verification(self) -> None:
        contract = build_evidence_contract(
            row(
                event_type="earnings",
                subject="dell",
                action="guidance_update",
                source_tier="trusted",
                evidence_count=1,
                url="https://www.cnbc.com/2026/05/28/dell-technologies-q1-fy27-results.html",
                event_metadata={
                    "freshness": {
                        "status": "unknown_event_date",
                        "event_date": "2026-05-28",
                        "event_date_source": "published_at",
                        "stale": False,
                    }
                },
                evidence_items=[
                    {
                        "url": (
                            "https://www.cnbc.com/2026/05/28/"
                            "dell-technologies-q1-fy27-results.html"
                        ),
                        "title": (
                            "Dell Technologies Q1 FY27 results top estimates "
                            "as AI server demand lifts guidance"
                        ),
                        "summary": (
                            "CNBC reports Dell revenue and EPS topped expectations."
                        ),
                    }
                ],
            )
        )

        self.assertEqual(contract["status"], "warn")
        self.assertTrue(contract["delivery_eligible"])
        self.assertIn("unknown_earnings_event_date", contract["warnings"])
        self.assertNotIn(
            "unknown_earnings_event_date_single_source",
            contract["failures"],
        )

    def test_single_untrusted_company_event_fails_delivery(self) -> None:
        contract = build_evidence_contract(
            row(
                event_type="corporate_action",
                subject="gpro",
                action="ma",
                source_tier="untrusted",
                evidence_count=1,
            )
        )

        self.assertEqual(contract["status"], "fail")
        self.assertFalse(contract["delivery_eligible"])
        self.assertIn(
            "single_source_untrusted_company_event",
            contract["failures"],
        )

    def test_single_untrusted_hard_event_with_price_move_is_warning_not_failure(self) -> None:
        contract = build_evidence_contract(
            row(
                event_type="corporate_action",
                subject="ctsh",
                action="buyback",
                source_tier="untrusted",
                evidence_count=1,
                event_quality="hard_event",
                price_reaction={
                    "status": "ok",
                    "price_as_of": "2026-05-18",
                    "price_as_of_at": "2026-05-18T23:55:00+00:00",
                    "pct_change": 8.78,
                    "direction": "up",
                    "session": "intraday_5min",
                    "stale": False,
                },
            )
        )

        self.assertEqual(contract["status"], "warn")
        self.assertTrue(contract["delivery_eligible"])
        self.assertTrue(contract["hard_event_lane"])
        self.assertNotIn(
            "single_source_untrusted_company_event",
            contract["failures"],
        )
        self.assertIn(
            "single_source_untrusted_company_event",
            contract["warnings"],
        )

    def test_single_untrusted_hard_event_with_small_price_move_stays_blocked(self) -> None:
        contract = build_evidence_contract(
            row(
                event_type="corporate_action",
                subject="ctsh",
                action="buyback",
                source_tier="untrusted",
                evidence_count=1,
                event_quality="hard_event",
                price_reaction={
                    "status": "ok",
                    "price_as_of": "2026-05-18",
                    "price_as_of_at": "2026-05-18T23:55:00+00:00",
                    "pct_change": 0.8,
                    "direction": "up",
                    "session": "intraday_5min",
                    "stale": False,
                },
            )
        )

        self.assertEqual(contract["status"], "fail")
        self.assertFalse(contract["delivery_eligible"])
        self.assertFalse(contract["hard_event_lane"])
        self.assertIn(
            "single_source_untrusted_company_event",
            contract["failures"],
        )

    def test_trusted_company_without_price_reaction_passes_delivery(self) -> None:
        contract = build_evidence_contract(
            row(
                event_type="earnings",
                subject="nvda",
                action="guidance_raise",
                source_tier="trusted",
                evidence_count=2,
                event_metadata={
                    "freshness": {
                        "status": "fresh_event_date",
                        "event_date": "2026-05-18",
                        "event_date_source": "body_report_date",
                        "stale": False,
                    }
                },
            )
        )

        self.assertEqual(contract["status"], "pass")
        self.assertTrue(contract["delivery_eligible"])
        self.assertEqual(contract["price_reaction"]["status"], "missing_optional")
        self.assertFalse(contract["price_reaction"]["required"])

    def test_company_with_valid_price_reaction_passes(self) -> None:
        contract = build_evidence_contract(
            row(
                event_type="earnings",
                subject="nvda",
                action="guidance_raise",
                source_tier="trusted",
                evidence_count=2,
                event_metadata={
                    "freshness": {
                        "status": "fresh_event_date",
                        "event_date": "2026-05-18",
                        "event_date_source": "body_report_date",
                        "stale": False,
                    }
                },
                price_reaction={
                    "status": "ok",
                    "price_as_of": "2026-05-18",
                    "price_as_of_at": "2026-05-18T15:30:00+00:00",
                    "pct_change": 2.4,
                    "direction": "up",
                    "session": "intraday_5min",
                    "stale": False,
                },
            )
        )

        self.assertEqual(contract["status"], "pass")
        self.assertTrue(contract["delivery_eligible"])
        self.assertEqual(contract["price_reaction"]["direction"], "up")

    def test_title_only_company_event_fails_delivery(self) -> None:
        contract = build_evidence_contract(
            row(
                event_type="strategic",
                subject="amd",
                action="investment",
                source_tier="trusted",
                evidence_count=1,
                body_text="",
                evidence_items=[],
                title="AMD to invest $10bn in Taiwan AI infrastructure partnership",
            )
        )

        self.assertEqual(contract["status"], "fail")
        self.assertFalse(contract["delivery_eligible"])
        self.assertIn("title_only_basis", contract["warnings"])
        self.assertIn("title_only_company_event", contract["failures"])


if __name__ == "__main__":
    unittest.main()
