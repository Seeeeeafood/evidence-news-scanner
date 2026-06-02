import unittest

from news_scanner_v2.earnings_facts import (
    augment_earnings_summary_with_contract,
    build_earnings_fact_contract,
    earnings_fact_fragments,
)


class EarningsFactContractTests(unittest.TestCase):
    def test_builds_contract_from_earnings_evidence(self) -> None:
        row = {
            "event_type": "earnings",
            "subject": "CRM",
            "action": "earnings_report+guidance_update",
            "title": "Salesforce reports Q1 results and FY27 guidance",
            "evidence_items": [
                {
                    "candidate_id": "c1",
                    "source": "marketwatch",
                    "provider": "brave",
                    "url": "https://www.marketwatch.com/example",
                    "title": "Salesforce earnings beat, guidance updated",
                    "summary": (
                        "Salesforce reported adjusted EPS of $3.88 vs $3.12 "
                        "expected and revenue of $11.13 billion vs $11.05 "
                        "billion expected. FY27 revenue guidance of $45.9 "
                        "billion to $46.2 billion."
                    ),
                }
            ],
        }

        contract = build_earnings_fact_contract(row)

        facts = {(fact["kind"], fact["value"]) for fact in contract["facts"]}
        self.assertIn(("eps", "$3.88"), facts)
        self.assertIn(("revenue", "$11.13B"), facts)
        self.assertIn(("guidance_revenue", "$45.9B-$46.2B"), facts)
        self.assertEqual(contract["status"], "ok")
        self.assertEqual(contract["facts"][0]["source_url"], "https://www.marketwatch.com/example")

    def test_fragments_prioritize_actuals_before_guidance(self) -> None:
        row = {
            "event_type": "earnings",
            "action": "earnings_report+guidance_update",
            "evidence_items": [
                {
                    "title": "CRM EPS $3.88 revenue $11.13B FY27 revenue guidance $45.9B-$46.2B",
                }
            ],
        }
        row["earnings_fact_contract"] = build_earnings_fact_contract(row)

        self.assertEqual(
            earnings_fact_fragments(row),
            ["EPS $3.88", "매출 $11.13B", "매출 가이던스 $45.9B-$46.2B"],
        )

    def test_fragments_keep_one_value_per_fact_kind(self) -> None:
        row = {
            "earnings_fact_contract": {
                "facts": [
                    {"kind": "revenue", "label": "매출", "value": "$850.48M"},
                    {
                        "kind": "guidance_revenue",
                        "label": "매출 가이던스",
                        "value": "$3.330B-$3.333B",
                    },
                    {
                        "kind": "guidance_revenue",
                        "label": "매출 가이던스",
                        "value": "$3.33B-$3.34B",
                    },
                    {"kind": "stock_reaction", "label": "주가반응", "value": "-17%"},
                ]
            }
        }

        self.assertEqual(
            earnings_fact_fragments(row),
            ["매출 $850.48M", "매출 가이던스 $3.330B-$3.333B", "주가반응 -17%"],
        )

    def test_augment_summary_uses_only_contract_facts(self) -> None:
        row = {
            "event_type": "earnings",
            "action": "earnings_report",
            "earnings_fact_contract": {
                "version": "earnings_fact_contract_v1",
                "status": "ok",
                "facts": [
                    {
                        "kind": "eps",
                        "label": "EPS",
                        "value": "$0.80",
                    },
                    {
                        "kind": "revenue",
                        "label": "매출",
                        "value": "$2.42B",
                    },
                ],
            },
        }

        summary = augment_earnings_summary_with_contract(
            row,
            "MRVL, 데이터센터 수요로 실적 예상 상회",
        )

        self.assertEqual(
            summary,
            "MRVL, 데이터센터 수요로 실적 예상 상회; EPS $0.80 / 매출 $2.42B",
        )

    def test_contract_requires_subject_anchor(self) -> None:
        row = {
            "event_type": "earnings",
            "subject": "ZS",
            "action": "earnings_report",
            "evidence_items": [
                {
                    "title": "Snowflake stock jumps after earnings",
                    "summary": "Snowflake revenue reached $11.3B and guidance rose to $5.84B.",
                }
            ],
        }

        self.assertEqual(build_earnings_fact_contract(row), {})

    def test_normalizes_and_range_guidance(self) -> None:
        row = {
            "event_type": "earnings",
            "subject": "APPS",
            "action": "earnings_report+guidance_update",
            "evidence_items": [
                {
                    "title": "APPS earnings and FY2027 guidance",
                    "summary": (
                        "Digital Turbine provided guidance for fiscal year 2027, "
                        "projecting revenue between $630 million and $650 million."
                    ),
                }
            ],
        }

        contract = build_earnings_fact_contract(row)

        facts = {(fact["kind"], fact["value"]) for fact in contract["facts"]}
        self.assertIn(("guidance_revenue", "$630M-$650M"), facts)

    def test_revenue_fact_does_not_cross_into_other_metric_amount(self) -> None:
        row = {
            "event_type": "earnings",
            "subject": "ZS",
            "action": "earnings_report",
            "evidence_items": [
                {
                    "title": "Zscaler (ZS) Q3 earnings call transcript",
                    "summary": (
                        "Zscaler reported EPS of $1.11. Zscaler anticipates "
                        "deceleration to 16%-17% ARR and revenue growth rates, "
                        "with elevated CapEx impacting free cash flow margin "
                        "assumptions. Net other income of approximately $24.5 "
                        "million and EPS of $1.08 to $1.09."
                    ),
                }
            ],
        }

        contract = build_earnings_fact_contract(row)

        facts = {(fact["kind"], fact["value"]) for fact in contract["facts"]}
        self.assertNotIn(("revenue", "$24.5M"), facts)
        self.assertNotIn(("eps", "$1.08-$1.09"), facts)
        self.assertIn(("eps", "$1.11"), facts)

    def test_guidance_facts_do_not_cross_title_into_actuals(self) -> None:
        row = {
            "event_type": "earnings",
            "subject": "OKTA",
            "action": "earnings_report+guidance_raise",
            "evidence_items": [
                {
                    "title": (
                        "Okta (OKTA) Stock Soars 27% Following Stellar "
                        "Earnings Beat and Raised Guidance"
                    ),
                    "summary": (
                        "Okta delivered Q1 FY2027 earnings per share of "
                        "$0.91, exceeding analyst expectations of $0.85, "
                        "while revenue reached $765M compared to the $752M "
                        "forecast. Management elevated every metric in its "
                        "full-year FY2027 financial guidance."
                    ),
                }
            ],
        }

        contract = build_earnings_fact_contract(row)

        facts = {(fact["kind"], fact["value"]) for fact in contract["facts"]}
        self.assertIn(("eps", "$0.91"), facts)
        self.assertIn(("revenue", "$765M"), facts)
        self.assertNotIn(("guidance_revenue", "$0.85"), facts)
        self.assertNotIn(("guidance_eps", "$0.91"), facts)

    def test_guidance_revenue_skips_segment_bridge(self) -> None:
        row = {
            "event_type": "earnings",
            "subject": "ZS",
            "action": "earnings_report+guidance_update",
            "evidence_items": [
                {
                    "title": "Zscaler (ZS) Q3 earnings call transcript",
                    "summary": (
                        "Zscaler posted revenue of $850.48 million. We expect Red "
                        "Canary revenue of approximately $137 million in fiscal 2026. "
                        "Full-year fiscal 2026 guidance -- Revenue in the $3.33 "
                        "billion-$3.34 billion range."
                    ),
                }
            ],
        }

        contract = build_earnings_fact_contract(row)

        facts = {(fact["kind"], fact["value"]) for fact in contract["facts"]}
        self.assertIn(("revenue", "$850.48M"), facts)
        self.assertIn(("guidance_revenue", "$3.33B-$3.34B"), facts)
        self.assertNotIn(("guidance_revenue", "$137M"), facts)

    def test_guidance_revenue_prefers_new_range(self) -> None:
        row = {
            "event_type": "earnings",
            "subject": "ZS",
            "action": "earnings_report+guidance_update",
            "evidence_items": [
                {
                    "title": "Zscaler (ZS) raises fiscal 2026 revenue outlook",
                    "summary": (
                        "Zscaler raised its fiscal 2026 revenue outlook from a "
                        "range of $3.31 billion to $3.32 billion to a new range "
                        "of $3.330 billion to $3.333 billion."
                    ),
                }
            ],
        }

        contract = build_earnings_fact_contract(row)

        facts = {(fact["kind"], fact["value"]) for fact in contract["facts"]}
        self.assertIn(("guidance_revenue", "$3.330B-$3.333B"), facts)
        self.assertNotIn(("guidance_revenue", "$3.31B-$3.32B"), facts)

    def test_eps_fact_rejects_company_scale_units(self) -> None:
        row = {
            "event_type": "earnings",
            "subject": "SNOW",
            "action": "earnings_report",
            "evidence_items": [
                {
                    "title": "Snowflake Q1 earnings",
                    "summary": (
                        "Snowflake posted EPS of $1.39 billion in contracted "
                        "backlog and revenue of $1.39 billion."
                    ),
                }
            ],
        }

        contract = build_earnings_fact_contract(row)

        facts = {(fact["kind"], fact["value"]) for fact in contract["facts"]}
        self.assertNotIn(("eps", "$1.39B"), facts)
        self.assertIn(("revenue", "$1.39B"), facts)

    def test_revenue_fact_rejects_contract_commitment_amount(self) -> None:
        row = {
            "event_type": "earnings",
            "subject": "SNOW",
            "action": "earnings_report+guidance_update",
            "evidence_items": [
                {
                    "title": "Snowflake earnings and AWS deal",
                    "summary": (
                        "Snowflake announced a $6 billion AWS cloud contract "
                        "and customer spending commitment. Product revenue "
                        "rose to $1.39 billion."
                    ),
                }
            ],
        }

        contract = build_earnings_fact_contract(row)

        facts = {(fact["kind"], fact["value"]) for fact in contract["facts"]}
        self.assertNotIn(("revenue", "$6B"), facts)
        self.assertIn(("revenue", "$1.39B"), facts)

    def test_live_update_facts_require_subject_context(self) -> None:
        row = {
            "event_type": "earnings",
            "subject": "SNOW",
            "action": "earnings_report+guidance_update",
            "evidence_items": [
                {
                    "title": (
                        "Earnings live updates: Snowflake stock skyrockets on "
                        "AWS deal, Kohl's pops"
                    ),
                    "summary": (
                        "Net sales decreased to $3 billion. Kohl's maintained "
                        "full-year revenue and earnings guidance, with adjusted "
                        "earnings per share of $1.00 to $1.60. Snowflake stock "
                        "also soared on a stronger-than-expected outlook. The "
                        "company forecast product revenue growth to $5.84 billion."
                    ),
                }
            ],
        }

        contract = build_earnings_fact_contract(row)

        facts = {(fact["kind"], fact["value"]) for fact in contract["facts"]}
        self.assertNotIn(("revenue", "$3B"), facts)
        self.assertNotIn(("guidance_eps", "$1.00-$1.60"), facts)
        self.assertIn(("guidance_revenue", "$5.84B"), facts)

    def test_cloud_counterparty_segment_revenue_rejected_for_snowflake(self) -> None:
        row = {
            "event_type": "earnings",
            "subject": "SNOW",
            "action": "earnings_report",
            "event_metadata": {
                "ownership": {
                    "primary_subject": "SNOW",
                    "related_entities": ["AMZN"],
                }
            },
            "evidence_items": [
                {
                    "title": (
                        "Snowflake Explodes 37% on $6 Billion Amazon Deal as "
                        "CEO Calls Q1 an AI Inflection Point"
                    ),
                    "summary": (
                        "Amazon stock is also catching a bid on AWS infrastructure "
                        "revenue. AWS posted Q1 2026 revenue of $37.59 billion. "
                        "Snowflake reported Q1 results. Product revenue came in "
                        "at $1.39 billion."
                    ),
                }
            ],
        }

        contract = build_earnings_fact_contract(row)

        facts = {(fact["kind"], fact["value"]) for fact in contract["facts"]}
        self.assertNotIn(("revenue", "$37.59B"), facts)
        self.assertIn(("revenue", "$1.39B"), facts)

    def test_revenue_fact_rejects_marketplace_lifetime_sales(self) -> None:
        row = {
            "event_type": "earnings",
            "subject": "SNOW",
            "action": "earnings_report",
            "evidence_items": [
                {
                    "title": "Snowflake stock is soaring because of Q1 earnings",
                    "summary": (
                        "Amazon's sales force can cross-sell Snowflake via the "
                        "AWS Marketplace, where it has already hit over "
                        "$7 billion in lifetime sales. Revenue jumped 33% "
                        "year-over-year to $1.39 billion."
                    ),
                }
            ],
        }

        contract = build_earnings_fact_contract(row)

        facts = {(fact["kind"], fact["value"]) for fact in contract["facts"]}
        self.assertNotIn(("revenue", "$7B"), facts)
        self.assertIn(("revenue", "$1.39B"), facts)

    def test_stock_reaction_rejects_revenue_growth_percent(self) -> None:
        row = {
            "event_type": "earnings",
            "subject": "SNOW",
            "action": "earnings_report",
            "evidence_items": [
                {
                    "title": "Snowflake stock is soaring because of Q1 earnings",
                    "summary": (
                        "Snowflake stock is soaring after earnings. Revenue "
                        "jumped 33% year-over-year to $1.39 billion. Shares "
                        "jumped 36% after hours."
                    ),
                }
            ],
        }

        contract = build_earnings_fact_contract(row)

        facts = {(fact["kind"], fact["value"]) for fact in contract["facts"]}
        self.assertNotIn(("stock_reaction", "+33%"), facts)
        self.assertIn(("stock_reaction", "+36%"), facts)

    def test_revenue_fact_skips_eps_per_share_amount_before_revenue_amount(self) -> None:
        row = {
            "event_type": "earnings",
            "subject": "DELL",
            "action": "earnings_report+guidance_update",
            "evidence_items": [
                {
                    "title": (
                        "Dell Technologies jumps as Q1 results, guidance top "
                        "estimates, led by AI"
                    ),
                    "summary": (
                        "Dell reported earnings and revenue far above analyst "
                        "expectations, with $4.86 per share and $43.8 billion "
                        "revenue. Dell also raised full-year guidance."
                    ),
                }
            ],
        }

        contract = build_earnings_fact_contract(row)

        facts = {(fact["kind"], fact["value"]) for fact in contract["facts"]}
        self.assertNotIn(("revenue", "$4.86"), facts)
        self.assertIn(("eps", "$4.86"), facts)
        self.assertIn(("revenue", "$43.8B"), facts)

    def test_verified_dell_summary_rejects_prior_year_and_ai_segment_amounts(self) -> None:
        row = {
            "event_type": "earnings",
            "subject": "DELL",
            "action": "earnings_report+guidance_update",
            "evidence_items": [
                {
                    "title": (
                        "Dell Technologies Q1 FY27 results top estimates as AI "
                        "server demand lifts guidance"
                    ),
                    "summary": (
                        "Dell reported 88% year-over-year revenue growth, topping "
                        "analysts estimates. Dell said its AI server revenue increased "
                        "757% from a year earlier to $16.1 billion. For the full "
                        "year, Dell now expects AI revenue of $60 billion. Dell "
                        "reported net income in the latest quarter more than tripled "
                        "to $3.44 billion, or $5.24 per share, from $965 million, "
                        "or $1.37 per share, a year earlier."
                    ),
                }
            ],
        }

        contract = build_earnings_fact_contract(row)

        facts = {(fact["kind"], fact["value"]) for fact in contract["facts"]}
        self.assertIn(("eps", "$5.24"), facts)
        self.assertIn(("ai_revenue", "$16.1B"), facts)
        self.assertIn(("ai_guidance_revenue", "$60B"), facts)
        self.assertNotIn(("eps", "$1.37"), facts)
        self.assertNotIn(("revenue", "$16.1B"), facts)
        self.assertNotIn(("guidance_revenue", "$60B"), facts)


if __name__ == "__main__":
    unittest.main()
