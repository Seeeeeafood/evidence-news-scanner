from datetime import datetime, timezone
import unittest

from news_scanner_v2.fetcher import FetchResult
from news_scanner_v2.models import CandidateItem
from news_scanner_v2.sources import BRAVE_NEWS_ENDPOINT, NewsSource
from news_scanner_v2.verification import verify_hard_event_records


def _record(
    *,
    subject: str = "ctsh",
    action: str = "buyback",
    pct_change: float = 8.78,
    title: str = (
        "Cognizant Technology Solutions (CTSH) Expands Stock Repurchase "
        "Program by $2 Billion"
    ),
) -> dict:
    return {
        "decision": "send_candidate",
        "reason": "send_candidate:recall_corporate_transaction",
        "payload": {
            "event_quality": "hard_event",
            "source_tier": "untrusted",
            "evidence_count": 1,
            "trusted_source_count": 0,
            "grade": "B",
            "risk_flags": ["single_source", "single_source_untrusted"],
            "event": {
                "event_type": "corporate_action",
                "subject": subject,
                "action": action,
                "title": title,
                "url": "https://stockstory.example/ctsh-buyback",
            },
            "price_reaction": {
                "status": "ok",
                "ticker": "CTSH",
                "direction": "up",
                "pct_change": pct_change,
                "price_as_of": "2026-05-19",
            },
        },
    }


def _dell_review_record(
    *,
    score: float = 56.0,
    source_tier: str = "low_quality",
    evidence_count: int = 2,
    subject: str = "dell",
    action: str = "guidance_update",
    title: str = (
        "Dell Technologies jumps as Q1 results, guidance top "
        "estimates, led by AI (DELL:NYSE) | Seeking Alpha"
    ),
) -> dict:
    return {
        "decision": "review",
        "score": score,
        "reason": (
            "review:score;type:earnings:50;action:guidance_update:8;"
            "low_quality_source:1:-15"
        ),
        "payload": {
            "event_quality": "",
            "source_tier": source_tier,
            "evidence_count": evidence_count,
            "trusted_source_count": 0,
            "low_quality_domains": ["marketbeat.com"] if source_tier == "low_quality" else [],
            "grade": "C",
            "risk_flags": ["low_quality_source"] if source_tier == "low_quality" else [],
            "event": {
                "event_type": "earnings",
                "subject": subject,
                "action": action,
                "title": title,
                "url": (
                    "https://seekingalpha.com/news/4597974-dell-technologies-"
                    "jumps-as-q1-results-guidance-top-estimates-led-by-ai"
                ),
            },
            "price_reaction": {},
        },
    }


def _source(name: str = "brave-news-verification-1") -> NewsSource:
    return NewsSource(
        name=name,
        category="VERIFY",
        url=BRAVE_NEWS_ENDPOINT,
        kind="brave_news",
        provider="brave",
        query="CTSH buyback",
    )


def _fetch_result(source: NewsSource, *items: CandidateItem) -> FetchResult:
    return FetchResult(
        source=source,
        status="ok",
        started_at=datetime.now(timezone.utc).isoformat(),
        finished_at=datetime.now(timezone.utc).isoformat(),
        items=items,
    )


class VerificationTests(unittest.TestCase):
    def test_promotes_review_earnings_rescue_when_trusted_result_matches(self) -> None:
        calls = []

        def fetcher(source: NewsSource, *, api_key: str) -> FetchResult:
            calls.append(source.query)
            item = CandidateItem(
                source=source.name,
                provider=source.provider,
                category=source.category,
                title=(
                    "Dell Technologies Q1 FY27 results top estimates as AI "
                    "server demand lifts guidance"
                ),
                url="https://www.cnbc.com/2026/05/28/dell-technologies-q1-fy27-results.html",
                published_at="2026-05-28T21:00:00+00:00",
                summary=(
                    "Dell Technologies reported revenue of $43.8 billion and "
                    "adjusted EPS of $4.86 per share, and raised guidance."
                ),
            )
            return _fetch_result(source, item)

        updated, stats, fetch_results = verify_hard_event_records(
            [_dell_review_record()],
            enabled=True,
            api_key="brave-key",
            max_requests=2,
            fetcher=fetcher,
        )

        payload = updated[0]["payload"]
        self.assertEqual(stats["verification_candidates"], 1)
        self.assertEqual(stats["verification_attempted"], 1)
        self.assertEqual(stats["verification_verified"], 1)
        self.assertEqual(len(fetch_results), 1)
        self.assertIn("DELL guidance update", calls[0])
        self.assertEqual(updated[0]["decision"], "send_candidate")
        self.assertIn("verified_earnings_rescue", updated[0]["reason"])
        self.assertEqual(payload["source_tier"], "trusted")
        self.assertEqual(payload["grade"], "A")
        self.assertEqual(payload["verification_status"], "verified")
        self.assertIn("cnbc.com", payload["trusted_domains"])
        self.assertNotIn("low_quality_source", payload["risk_flags"])

    def test_does_not_verify_low_score_single_low_quality_earnings_review(self) -> None:
        def fetcher(source: NewsSource, *, api_key: str) -> FetchResult:
            raise AssertionError("fetcher should not be called")

        updated, stats, fetch_results = verify_hard_event_records(
            [_dell_review_record(score=51.0, evidence_count=1)],
            enabled=True,
            api_key="brave-key",
            max_requests=2,
            fetcher=fetcher,
        )

        self.assertEqual(updated[0]["decision"], "review")
        self.assertEqual(stats["verification_candidates"], 0)
        self.assertEqual(stats["verification_attempted"], 0)
        self.assertEqual(fetch_results, ())

    def test_prioritizes_review_earnings_rescue_before_hard_event_limit(self) -> None:
        calls = []

        def fetcher(source: NewsSource, *, api_key: str) -> FetchResult:
            calls.append(source.query)
            if "DELL" not in source.query:
                raise AssertionError(f"unexpected first verification query: {source.query}")
            item = CandidateItem(
                source=source.name,
                provider=source.provider,
                category=source.category,
                title="Dell Technologies Q1 FY27 results top estimates",
                url="https://www.cnbc.com/2026/05/28/dell-technologies-q1-fy27-results.html",
                published_at="2026-05-28T21:00:00+00:00",
                summary="Dell Technologies revenue and EPS topped estimates.",
            )
            return _fetch_result(source, item)

        updated, stats, _ = verify_hard_event_records(
            [_record(), _dell_review_record()],
            enabled=True,
            api_key="brave-key",
            max_requests=1,
            fetcher=fetcher,
        )

        self.assertEqual(stats["verification_candidates"], 2)
        self.assertEqual(stats["verification_attempted"], 1)
        self.assertEqual(stats["verification_skipped_limit"], 1)
        self.assertEqual(stats["verification_brave_max_requests"], 1)
        self.assertEqual(stats["verification_dynamic_budget_added"], 0)
        self.assertIn("DELL guidance update", calls[0])
        self.assertNotIn("verification_status", updated[0]["payload"])
        self.assertEqual(updated[1]["decision"], "send_candidate")
        self.assertEqual(updated[1]["payload"]["verification_status"], "verified")

    def test_dynamic_budget_expands_for_review_earnings_rescue_candidates(self) -> None:
        calls = []

        def fetcher(source: NewsSource, *, api_key: str) -> FetchResult:
            calls.append(source.query)
            if "DELL" in source.query:
                item = CandidateItem(
                    source=source.name,
                    provider=source.provider,
                    category=source.category,
                    title="Dell Technologies Q1 FY27 results top estimates",
                    url=(
                        "https://www.cnbc.com/2026/05/28/"
                        "dell-technologies-q1-fy27-results.html"
                    ),
                    published_at="2026-05-28T21:00:00+00:00",
                    summary="Dell Technologies revenue and EPS topped estimates.",
                )
            else:
                item = CandidateItem(
                    source=source.name,
                    provider=source.provider,
                    category=source.category,
                    title=(
                        "Cognizant Increases 2026 Share Repurchase Target "
                        "by $1 Billion to $2 Billion"
                    ),
                    url="https://news.cognizant.com/2026-05-19-ctsh-repurchase",
                    published_at="2026-05-19T12:45:00+00:00",
                    summary="Cognizant said its repurchase target is $2 billion.",
                )
            return _fetch_result(source, item)

        records = [_dell_review_record() for _ in range(4)] + [_record()]
        updated, stats, fetch_results = verify_hard_event_records(
            records,
            enabled=True,
            api_key="brave-key",
            max_requests=2,
            fetcher=fetcher,
        )

        self.assertEqual(stats["verification_candidates"], 5)
        self.assertEqual(stats["verification_earnings_rescue_candidates"], 4)
        self.assertEqual(stats["verification_brave_base_max_requests"], 2)
        self.assertEqual(stats["verification_brave_max_requests"], 5)
        self.assertEqual(stats["verification_dynamic_budget_added"], 3)
        self.assertEqual(stats["verification_attempted"], 5)
        self.assertEqual(stats["verification_skipped_limit"], 0)
        self.assertEqual(len(fetch_results), 5)
        self.assertEqual(len(calls), 5)
        self.assertTrue(all(record["decision"] == "send_candidate" for record in updated))

    def test_rejects_rescue_match_when_subject_is_different_company(self) -> None:
        cases = [
            (
                _dell_review_record(),
                "Dell Technologies Q1 FY27 results top estimates",
                "https://www.cnbc.com/2026/05/28/dell-technologies-q1-fy27-results.html",
                "Dell Technologies revenue and EPS topped estimates.",
                True,
            ),
            (
                _dell_review_record(),
                "Marvell Technology valuation after AI driven earnings beat",
                "https://finance.yahoo.com/news/marvell-technology-earnings.html",
                "Marvell raised its multi-year outlook after earnings.",
                False,
            ),
            (
                _dell_review_record(subject="F", title="F stock rallies 9% as Ford beats Q1 earnings, raises 2026 guidance"),
                "Gap Inc. beats EPS in Q1 2026, stock rises",
                "https://www.investing.com/news/transcripts/gap-inc-q1-2026",
                "Gap reported earnings and raised guidance.",
                False,
            ),
            (
                _dell_review_record(subject="GE", title="GE HealthCare stock guidance cut puts earnings quality in focus"),
                "GeneDx Holdings drop triggers investor scrutiny",
                "https://www.prnewswire.com/news-releases/genedx-holdings-drop.html",
                "GeneDx discussed growth and guidance.",
                False,
            ),
            (
                _dell_review_record(
                    subject="CRM",
                    title=(
                        "Salesforce Tops Estimates In Q1, Shares Wobble On Soft "
                        "Revenue Outlook - Salesforce (NYSE:CRM) - Benzinga"
                    ),
                ),
                "Jim Cramer was on the fence with Salesforce. Now ready to act",
                "https://www.cnbc.com/2026/05/29/cramer-salesforce.html",
                (
                    "Snowflake delivered a beat-and-raise quarter by leaning into AI. "
                    "Jim Cramer Charitable Trust is long CRM."
                ),
                False,
            ),
            (
                _dell_review_record(
                    subject="TTWO",
                    title=(
                        "Take-Two Interactive Software (NASDAQ:TTWO) Surges on "
                        "Fiscal Q4 Earnings Beat and Record-Breaking Guidance"
                    ),
                ),
                "Trusts tied to Take-Two (NASDAQ: TTWO) sell 70K, gift 40K",
                "https://www.stocktitan.net/news/TTWO/trusts-tied-to-take-two-sell.html",
                (
                    "Take-Two Interactive Software, Inc. Reports Results for "
                    "Fourth Quarter and Fiscal Year 2026."
                ),
                False,
            ),
        ]

        for record, title, url, summary, should_verify in cases:
            def fetcher(source: NewsSource, *, api_key: str) -> FetchResult:
                item = CandidateItem(
                    source=source.name,
                    provider=source.provider,
                    category=source.category,
                    title=title,
                    url=url,
                    published_at="2026-05-28T21:00:00+00:00",
                    summary=summary,
                )
                return _fetch_result(source, item)

            updated, stats, _ = verify_hard_event_records(
                [record],
                enabled=True,
                api_key="brave-key",
                max_requests=2,
                fetcher=fetcher,
            )

            if should_verify:
                self.assertEqual(stats["verification_verified"], 1)
                self.assertEqual(updated[0]["decision"], "send_candidate")
            else:
                self.assertEqual(stats["verification_verified"], 0)
                self.assertEqual(updated[0]["decision"], "review")

    def test_promotes_single_source_hard_event_when_issuer_result_matches(self) -> None:
        calls = []

        def fetcher(source: NewsSource, *, api_key: str) -> FetchResult:
            calls.append((source.query, api_key))
            item = CandidateItem(
                source=source.name,
                provider=source.provider,
                category=source.category,
                title=(
                    "Cognizant Increases 2026 Share Repurchase Target "
                    "by $1 Billion to $2 Billion"
                ),
                url="https://news.cognizant.com/2026-05-19-ctsh-repurchase",
                published_at="2026-05-19T12:45:00+00:00",
                summary=(
                    "Cognizant said its stock repurchase authorization now totals "
                    "$2 billion for 2026."
                ),
            )
            return _fetch_result(source, item)

        original = _record()
        updated, stats, fetch_results = verify_hard_event_records(
            [original],
            enabled=True,
            api_key="brave-key",
            max_requests=2,
            fetcher=fetcher,
        )

        payload = updated[0]["payload"]
        self.assertEqual(stats["verification_candidates"], 1)
        self.assertEqual(stats["verification_attempted"], 1)
        self.assertEqual(stats["verification_verified"], 1)
        self.assertEqual(stats["verification_brave_requests_used"], 1)
        self.assertEqual(len(fetch_results), 1)
        self.assertIn("CTSH buyback", calls[0][0])
        self.assertEqual(payload["source_tier"], "trusted")
        self.assertEqual(payload["grade"], "A")
        self.assertEqual(payload["verification_status"], "verified")
        self.assertIn("news.cognizant.com", payload["trusted_domains"])
        self.assertNotIn("single_source_untrusted", payload["risk_flags"])
        self.assertIn("verified_single_source", payload["risk_flags"])
        self.assertEqual(original["payload"]["source_tier"], "untrusted")

    def test_leaves_unverified_candidate_at_b_grade_when_match_is_weak(self) -> None:
        def fetcher(source: NewsSource, *, api_key: str) -> FetchResult:
            item = CandidateItem(
                source=source.name,
                provider=source.provider,
                category=source.category,
                title="Market commentary says CTSH shares moved after rumors",
                url="https://blog.example/ctsh-rumor",
                published_at="2026-05-19T12:45:00+00:00",
                summary="No issuer or trusted-source confirmation of a buyback amount.",
            )
            return _fetch_result(source, item)

        updated, stats, _ = verify_hard_event_records(
            [_record()],
            enabled=True,
            api_key="brave-key",
            max_requests=2,
            fetcher=fetcher,
        )

        payload = updated[0]["payload"]
        self.assertEqual(stats["verification_unverified"], 1)
        self.assertEqual(payload["source_tier"], "untrusted")
        self.assertEqual(payload["grade"], "B")
        self.assertEqual(payload["verification_status"], "unverified")

    def test_does_not_promote_from_trusted_byline_on_untrusted_domain(self) -> None:
        def fetcher(source: NewsSource, *, api_key: str) -> FetchResult:
            item = CandidateItem(
                source=source.name,
                provider=source.provider,
                category=source.category,
                title=(
                    "Cognizant Increases Stock Repurchase Program by "
                    "$2 Billion By Reuters"
                ),
                url="https://stockstotrade.example/ctsh-reuters-copy",
                published_at="2026-05-19T12:45:00+00:00",
                summary="Cognizant said its stock repurchase authorization changed.",
            )
            return _fetch_result(source, item)

        updated, stats, _ = verify_hard_event_records(
            [_record()],
            enabled=True,
            api_key="brave-key",
            max_requests=2,
            fetcher=fetcher,
        )

        payload = updated[0]["payload"]
        self.assertEqual(stats["verification_unverified"], 1)
        self.assertEqual(payload["source_tier"], "untrusted")
        self.assertEqual(payload["grade"], "B")
        self.assertEqual(payload["verification_status"], "unverified")

    def test_does_not_treat_generic_stock_token_as_issuer_domain(self) -> None:
        title = "United Airlines Stock Slips As Softer 2026 Outlook Overshadows Q1 Earnings Beat"

        def fetcher(source: NewsSource, *, api_key: str) -> FetchResult:
            item = CandidateItem(
                source=source.name,
                provider=source.provider,
                category=source.category,
                title=title,
                url="https://stockstotrade.example/united-airlines-outlook",
                published_at="2026-05-19T12:45:00+00:00",
                summary="United Airlines shares slipped after a softer outlook.",
            )
            return _fetch_result(source, item)

        updated, stats, _ = verify_hard_event_records(
            [_record(subject="ual", action="guidance_update", title=title)],
            enabled=True,
            api_key="brave-key",
            max_requests=2,
            fetcher=fetcher,
        )

        payload = updated[0]["payload"]
        self.assertEqual(stats["verification_unverified"], 1)
        self.assertEqual(payload["source_tier"], "untrusted")
        self.assertEqual(payload["grade"], "B")
        self.assertEqual(payload["verification_status"], "unverified")

    def test_skips_verification_when_price_reaction_is_too_small(self) -> None:
        def fetcher(source: NewsSource, *, api_key: str) -> FetchResult:
            raise AssertionError("fetcher should not be called")

        updated, stats, fetch_results = verify_hard_event_records(
            [_record(pct_change=1.5)],
            enabled=True,
            api_key="brave-key",
            max_requests=2,
            fetcher=fetcher,
        )

        self.assertEqual(updated[0]["payload"]["grade"], "B")
        self.assertEqual(stats["verification_candidates"], 0)
        self.assertEqual(stats["verification_attempted"], 0)
        self.assertEqual(fetch_results, ())

    def test_enforces_verification_request_limit(self) -> None:
        def fetcher(source: NewsSource, *, api_key: str) -> FetchResult:
            return _fetch_result(source)

        updated, stats, fetch_results = verify_hard_event_records(
            [
                _record(title="Cognizant Technology Solutions (CTSH) buyback $2 Billion"),
                _record(title="Cognizant Technology Solutions (CTSH) buyback $3 Billion"),
            ],
            enabled=True,
            api_key="brave-key",
            max_requests=1,
            fetcher=fetcher,
        )

        self.assertEqual(stats["verification_candidates"], 2)
        self.assertEqual(stats["verification_attempted"], 1)
        self.assertEqual(stats["verification_skipped_limit"], 1)
        self.assertEqual(len(fetch_results), 1)
        self.assertEqual(updated[0]["payload"]["verification_status"], "unverified")
        self.assertNotIn("verification_status", updated[1]["payload"])


if __name__ == "__main__":
    unittest.main()
