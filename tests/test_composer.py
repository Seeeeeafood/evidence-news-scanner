from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from news_scanner_v2.cli import main
from news_scanner_v2.composer import (
    compose_digest_message,
    compose_message,
    load_message_preview,
    render_message_preview,
)
from test_reports import _build_report_fixture


class MessagePreviewTests(unittest.TestCase):
    def test_policy_geo_action_is_rendered_in_korean(self) -> None:
        message = compose_message(
            {
                "decision_id": "d1",
                "run_id": "r1",
                "event_signature": "e1",
                "decision": "send_candidate",
                "score": 89,
                "event_type": "geo",
                "subject": "IRAN",
                "action": "policy_geo",
                "effective_date": "2026-05-14",
                "title": "Iran oil flows remain disrupted",
                "url": "https://example.com/news",
                "evidence_count": 2,
                "providers": ["fixture"],
            },
            index=1,
        )

        self.assertEqual(message["market_marker"], "")
        self.assertIn("[지정학] 이란 - 정책/지정학", message["text"])
        self.assertIn("이란 원유", message["text"])
        self.assertNotIn("policy_geo", message["text"])
        self.assertNotIn("정책·지정학 이벤트", message["text"])



    def test_digest_message_does_not_dedupe_atomic_geo_rescue_rows(self) -> None:
        message = compose_digest_message(
            [
                {
                    "decision_id": "d1",
                    "run_id": "r1",
                    "event_signature": "iran-talks-halt",
                    "decision": "send_candidate",
                    "score": 78,
                    "event_type": "geo",
                    "subject": "iran",
                    "action": "diplomacy",
                    "object": "ceasefire_talks",
                    "effective_date": "2026-05-14",
                    "title": "Iran negotiating team halts indirect messages with U.S. mediators",
                    "url": "https://example.com/iran-talks",
                    "evidence_count": 1,
                    "providers": ["brave"],
                    "atomic_digest": True,
                    "rescue_type": "geo_fresh_delta",
                },
                {
                    "decision_id": "d2",
                    "run_id": "r1",
                    "event_signature": "iran-kuwait-missile",
                    "decision": "send_candidate",
                    "score": 76,
                    "event_type": "geo",
                    "subject": "iran",
                    "action": "conflict",
                    "object": "ceasefire_talks",
                    "effective_date": "2026-05-14",
                    "title": "Kuwait says missile fired after Iran ceasefire talks stall",
                    "url": "https://example.com/kuwait-missile",
                    "evidence_count": 1,
                    "providers": ["brave"],
                    "atomic_digest": True,
                    "rescue_type": "geo_fresh_delta",
                },
            ]
        )

        self.assertEqual(message["text"].count("• ["), 2)
        self.assertIn("신규총:2", message["text"])

    def test_digest_message_groups_rows_and_keeps_title_context(self) -> None:
        message = compose_digest_message(
            [
                {
                    "decision_id": "d1",
                    "run_id": "r1",
                    "event_signature": "e1",
                    "decision": "send_candidate",
                    "score": 95,
                    "event_type": "geo",
                    "subject": "IRAN",
                    "action": "conflict",
                    "effective_date": "2026-05-15",
                    "title": "World markets feel the strain as US-Iran war grinds on By Reuters",
                    "url": "https://example.com/news",
                    "evidence_count": 3,
                    "providers": ["brave"],
                },
                {
                    "decision_id": "d2",
                    "run_id": "r1",
                    "event_signature": "e2",
                    "decision": "send_candidate",
                    "score": 76,
                    "event_type": "geo",
                    "subject": "IRAN",
                    "action": "policy_geo",
                    "effective_date": "2026-05-15",
                    "title": "‘Unblock Hormuz Or…’: Marco Rubio Urges China To Act Against Iran Or Face Export Collapse - Times of India",
                    "url": "https://example.com/news2",
                    "evidence_count": 1,
                    "providers": ["brave"],
                },
            ],
            run={"id": "r1", "as_of": "2026-05-15T11:00:00+09:00"},
            skipped_previously_sent=4,
        )

        text = message["text"]
        self.assertTrue(text.startswith("📰 미국증시 뉴스 (11:00 KST)"))
        self.assertIn("• [A] 🔴 미-이란 전쟁 장기화", text)
        self.assertIn("• [B] 루비오, 중국에 호르무즈 압박 요구", text)
        self.assertIn("[QC] V2 A:1 B:1 신규총:2 중복-제외:4", text)
        self.assertEqual(message["summary_basis_counts"], {"title": 2})
        self.assertNotIn("https://example.com", text)
        self.assertNotIn("관련 정책·지정학 이벤트 진행", text)

    def test_digest_message_uses_source_metadata_for_hint(self) -> None:
        message = compose_digest_message(
            [
                {
                    "decision_id": "d1",
                    "run_id": "r1",
                    "event_signature": "e1",
                    "decision": "send_candidate",
                    "score": 76,
                    "grade": "B",
                    "event_type": "earnings",
                    "subject": "GEV",
                    "action": "guidance_raise",
                    "effective_date": "2026-04-22",
                    "title": "GE Vernova Raises FY2026 Sales Guidance",
                    "url": "https://example.com/news",
                    "evidence_count": 1,
                    "providers": ["archive"],
                    "sources": ["Bloomberg"],
                }
            ],
            run={"id": "r1", "as_of": "2026-04-22T22:30:00+09:00"},
        )

        text = message["text"]
        self.assertIn("[B]", text)
        self.assertIn("(Bloomberg)", text)

    def test_digest_message_uses_evidence_url_for_source_hint(self) -> None:
        message = compose_digest_message(
            [
                {
                    "decision_id": "d1",
                    "run_id": "r1",
                    "event_signature": "e1",
                    "decision": "send_candidate",
                    "score": 76,
                    "grade": "B",
                    "event_type": "earnings",
                    "subject": "NVDA",
                    "action": "guidance_update",
                    "effective_date": "2026-05-21",
                    "title": "Earnings live updates",
                    "evidence_count": 1,
                    "providers": ["brave"],
                    "evidence_items": [
                        {
                            "source": "brave-news-earnings-guidance",
                            "url": "https://finance.yahoo.com/markets/live/earnings-live-updates.html",
                            "title": "Earnings live updates",
                            "summary": "Nvidia revenue $81.62B and Q2 guidance $91B.",
                        }
                    ],
                    "llm_annotation": {
                        "summary_ko": "NVDA, 매출 $81.62B 기록 후 Q2 가이던스 $91B 제시",
                        "market_marker": "green",
                        "confidence": "high",
                        "basis": "snippet",
                    },
                }
            ],
            run={"id": "r1", "as_of": "2026-05-21T22:30:00+09:00"},
        )

        self.assertIn("(Yahoo)", message["text"])

    def test_digest_message_keeps_tickerless_ma_deal_context(self) -> None:
        message = compose_digest_message(
            [
                {
                    "decision_id": "d1",
                    "run_id": "r1",
                    "event_signature": "e1",
                    "decision": "send_candidate",
                    "score": 76,
                    "grade": "B",
                    "event_type": "corporate_action",
                    "subject": "UBER",
                    "action": "ma",
                    "effective_date": "2026-05-25",
                    "title": "🔴 [M&A] Uber weighs higher bid for Delivery Hero after €11.5bn offer rebuffed (Financial Times)",
                    "evidence_count": 1,
                    "providers": ["breaking_hint"],
                    "sources": ["breaking-hints-ma"],
                }
            ],
            run={"id": "r1", "as_of": "2026-05-25T19:00:00+09:00"},
        )

        text = message["text"]
        self.assertIn("Uber, Delivery Hero 인수 제안 상향 검토", text)
        self.assertIn("EUR11.5B", text)
        self.assertIn("(FT)", text)
        self.assertNotIn("UBER M&A 또는 전략 거래 발표", text)

    def test_digest_message_keeps_iran_deal_conditions_context(self) -> None:
        message = compose_digest_message(
            [
                {
                    "decision_id": "d1",
                    "run_id": "r1",
                    "event_signature": "e1",
                    "decision": "send_candidate",
                    "score": 88,
                    "grade": "A",
                    "event_type": "geo",
                    "subject": "IRAN",
                    "action": "policy_geo",
                    "object": "iran_deal_conditions",
                    "effective_date": "2026-05-25",
                    "title": "Trump adds Abraham Accords as required condition for Iran deal, demanding Saudi, Qatar, Egypt, Jordan, Turkey and Pakistan sign",
                    "evidence_count": 1,
                    "providers": ["brave"],
                    "sources": ["brave-scout-1-geo-iran-deal-conditions"],
                }
            ],
            run={"id": "r1", "as_of": "2026-05-25T22:30:00+09:00"},
        )

        text = message["text"]
        self.assertIn("트럼프, 이란딜 조건에 아브라함 협정 서명 요구", text)
        self.assertIn("중동 6개국 참여 조건", text)
        self.assertNotIn("관련 정책·지정학 이벤트", text)

    def test_digest_message_can_summarize_iran_deal_conditions_from_body(self) -> None:
        message = compose_digest_message(
            [
                {
                    "decision_id": "d1",
                    "run_id": "r1",
                    "event_signature": "e1",
                    "decision": "send_candidate",
                    "score": 88,
                    "grade": "A",
                    "event_type": "geo",
                    "subject": "IRAN",
                    "action": "policy_geo",
                    "object": "iran_deal_conditions",
                    "effective_date": "2026-05-25",
                    "title": "Live Updates: Iran and U.S. agree deal to end war taking shape, but Iran says obstacles remain",
                    "body_text": (
                        "Trump says talks are proceeding nicely, and Iran deal should "
                        "see other Gulf allies sign Abraham Accords. It should be "
                        "mandatory that Saudi Arabia and Qatar sign as part of the deal."
                    ),
                    "evidence_items": [
                        {
                            "source": "brave-news-geo-policy",
                            "url": "https://www.cbsnews.com/live-updates/iran-us-war-trump-deal-obstacles-remain/",
                            "title": "Live Updates: Iran and U.S. agree deal to end war taking shape",
                            "summary": "Iran deal should see other Gulf allies sign Abraham Accords.",
                            "body_text": (
                                "Iran deal should see other Gulf allies sign Abraham Accords. "
                                "Saudi Arabia and Qatar should sign as part of the deal."
                            ),
                        }
                    ],
                    "evidence_count": 1,
                    "providers": ["brave"],
                    "sources": ["brave-news-geo-policy"],
                }
            ],
            run={"id": "r1", "as_of": "2026-05-25T22:30:00+09:00"},
        )

        text = message["text"]
        self.assertIn("트럼프, 이란딜 조건에 아브라함 협정 서명 요구", text)
        self.assertIn("(CBS)", text)
        self.assertNotIn("이란 이슈 — 협상·유가·호르무즈 변수", text)

    def test_digest_message_dedupes_same_geo_story_object(self) -> None:
        rows = [
            {
                "decision_id": "old",
                "run_id": "r1",
                "event_signature": "old",
                "decision": "send_candidate",
                "score": 84,
                "grade": "B",
                "event_type": "geo",
                "subject": "IRAN",
                "action": "policy_geo",
                "object": "iran_deal_conditions",
                "effective_date": "2026-05-24",
                "title": "Details emerge of a potential Iran deal after Trump claims progress | AP News",
                "evidence_count": 1,
                "providers": ["brave"],
                "sources": ["brave-news-geo-policy"],
            },
            {
                "decision_id": "new",
                "run_id": "r1",
                "event_signature": "new",
                "decision": "send_candidate",
                "score": 94,
                "grade": "A",
                "event_type": "geo",
                "subject": "IRAN",
                "action": "policy_geo",
                "object": "iran_deal_conditions",
                "effective_date": "2026-05-25",
                "title": "What we know and don't know about the emerging deal to end the Iran war",
                "body_text": (
                    "Iran deal should see other Gulf allies sign Abraham Accords. "
                    "Saudi Arabia and Qatar should sign as part of the deal."
                ),
                "evidence_items": [
                    {
                        "url": "https://www.cbsnews.com/live-updates/iran-us-war-trump-deal-obstacles-remain/",
                        "summary": "Iran deal should see other Gulf allies sign Abraham Accords.",
                        "body_text": "Iran deal should see other Gulf allies sign Abraham Accords.",
                    }
                ],
                "evidence_count": 3,
                "providers": ["brave"],
                "sources": ["brave-news-geo-policy"],
            },
        ]

        message = compose_digest_message(
            rows,
            run={"id": "r1", "as_of": "2026-05-25T22:30:00+09:00"},
        )

        text = message["text"]
        self.assertEqual(
            text.count("트럼프, 이란딜 조건에 아브라함 협정 서명 요구"),
            1,
        )
        self.assertIn("[QC] V2 A:1 B:0 신규총:1 중복-제외:1", text)
        self.assertEqual(len(message["summary_evidence"]), 1)

    def test_digest_message_prunes_broad_iran_geo_when_specific_story_exists(self) -> None:
        rows = [
            {
                "decision_id": "specific",
                "run_id": "r1",
                "event_signature": "specific",
                "decision": "send_candidate",
                "score": 94,
                "grade": "A",
                "event_type": "geo",
                "subject": "IRAN",
                "action": "policy_geo",
                "object": "iran_deal_conditions",
                "effective_date": "2026-05-25",
                "title": "What we know and don't know about the emerging deal to end the Iran war",
                "body_text": "Iran deal should see other Gulf allies sign Abraham Accords.",
                "evidence_count": 3,
                "providers": ["brave"],
                "sources": ["brave-news-geo-policy"],
            },
            {
                "decision_id": "broad",
                "run_id": "r1",
                "event_signature": "broad",
                "decision": "send_candidate",
                "score": 76,
                "grade": "B",
                "event_type": "geo",
                "subject": "IRAN",
                "action": "policy_geo",
                "object": "hormuz_ceasefire_talks",
                "effective_date": "2026-05-25",
                "title": "Live updates: Iran peace deal and Strait of Hormuz agreement still a work in progress, says Rubio | CNN",
                "evidence_count": 1,
                "providers": ["brave"],
                "sources": ["brave-scout-2-geo-hormuz-detail"],
            },
        ]

        message = compose_digest_message(
            rows,
            run={"id": "r1", "as_of": "2026-05-25T22:30:00+09:00"},
        )

        text = message["text"]
        self.assertIn("트럼프, 이란딜 조건에 아브라함 협정 서명 요구", text)
        self.assertNotIn("협상·유가·호르무즈 변수가 시장 부담", text)
        self.assertIn("[QC] V2 A:1 B:0 신규총:1 중복-제외:1", text)

    def test_digest_message_keeps_specific_iran_energy_summary(self) -> None:
        message = compose_digest_message(
            [
                {
                    "decision_id": "oil",
                    "run_id": "r1",
                    "event_signature": "oil",
                    "decision": "send_candidate",
                    "score": 86,
                    "grade": "B",
                    "event_type": "geo",
                    "subject": "IRAN",
                    "action": "policy_geo",
                    "object": "iran_energy_supply",
                    "effective_date": "2026-05-25",
                    "title": "Crude Oil Drops as US Inches Toward Iran Deal to Reopen Strait (Bloomberg)",
                    "evidence_count": 1,
                    "providers": ["brave"],
                    "sources": ["brave-news-geo-policy"],
                }
            ],
            run={"id": "r1", "as_of": "2026-05-25T22:30:00+09:00"},
        )

        self.assertIn("미국-이란 딜 기대에 원유 하락", message["text"])

    def test_digest_message_ignores_generic_editorial_source_hint(self) -> None:
        message = compose_digest_message(
            [
                {
                    "decision_id": "d1",
                    "run_id": "r1",
                    "event_signature": "e1",
                    "decision": "send_candidate",
                    "score": 76,
                    "grade": "B",
                    "event_type": "earnings",
                    "subject": "IBM",
                    "action": "earnings_report",
                    "effective_date": "2026-05-22",
                    "title": "IBM wins CHIPS Act support, Yahoo Finance reports",
                    "evidence_count": 1,
                    "providers": ["brave"],
                    "llm_annotation": {
                        "summary_ko": "IBM, 양자 칩 파운드리 지원금 수령 소식에 주가 상승",
                        "market_marker": "green",
                        "confidence": "medium",
                        "basis": "snippet",
                    },
                    "llm_editorial": {
                        "decision": "send",
                        "source_hint": "EARN 5/20",
                    },
                }
            ],
            run={"id": "r1", "as_of": "2026-05-22T11:00:00+09:00"},
        )

        text = message["text"]
        self.assertIn("(Yahoo)", text)
        self.assertNotIn("(EARN 5/20)", text)

    def test_digest_message_ignores_provider_source_hint_with_date(self) -> None:
        message = compose_digest_message(
            [
                {
                    "decision_id": "d1",
                    "run_id": "r1",
                    "event_signature": "e1",
                    "decision": "send_candidate",
                    "score": 86,
                    "grade": "B",
                    "event_type": "macro",
                    "subject": "OIL",
                    "action": "oil_update",
                    "effective_date": "2026-05-22",
                    "title": "Brent rises as Iran talks stall - Reuters",
                    "url": "https://www.reuters.com/markets/commodities/oil-iran-talks",
                    "evidence_count": 1,
                    "providers": ["brave"],
                    "llm_annotation": {
                        "summary_ko": "Brent 원유, 이란 협상 교착으로 3.4% 상승",
                        "market_marker": "red",
                        "confidence": "high",
                        "basis": "snippet",
                    },
                    "llm_editorial": {
                        "decision": "send",
                        "source_hint": "brave 5/21",
                    },
                }
            ],
            run={"id": "r1", "as_of": "2026-05-22T01:00:00+09:00"},
        )

        text = message["text"]
        self.assertIn("(Reuters)", text)
        self.assertNotIn("(brave 5/21)", text)

    def test_digest_message_detects_nikkei_source_hint_from_title(self) -> None:
        message = compose_digest_message(
            [
                {
                    "decision_id": "d1",
                    "run_id": "r1",
                    "event_signature": "e1",
                    "decision": "send_candidate",
                    "score": 64,
                    "grade": "B",
                    "event_type": "strategic",
                    "subject": "NVDA",
                    "action": "partnership",
                    "effective_date": "2026-05-22",
                    "title": (
                        "Kawasaki Heavy To Partner With NVIDIA On Physical AI, "
                        "Open U.S. Robot Center - Nikkei Asia (Benzinga)"
                    ),
                    "url": "breaking-hint://breaking_2026-05-22.md:8",
                    "evidence_count": 1,
                    "providers": ["breaking_hint"],
                }
            ],
            run={"id": "r1", "as_of": "2026-05-22T01:00:00+09:00"},
        )

        self.assertIn("(Nikkei Asia)", message["text"])

    def test_empty_digest_explains_exclusion_counts(self) -> None:
        message = compose_digest_message(
            [],
            run={"id": "r1", "as_of": "2026-05-21T22:30:00+09:00"},
            skipped_previously_sent=2,
            exclusion_counts={
                "contract_blocked": 1,
                "editorial_dropped": 2,
                "summary_rejected": 1,
            },
        )

        text = message["text"]
        self.assertIn("✅ 특이사항 없음 (검증 통과 신규 기준)", text)
        self.assertIn("↳ 제외: 중복 2 · 계약 1 · 편집 2 · 요약검증 1", text)
        self.assertEqual(message["exclusion_counts"]["duplicate"], 2)
        self.assertEqual(message["exclusion_counts"]["contract_blocked"], 1)

    def test_digest_message_includes_market_snapshot_block(self) -> None:
        message = compose_digest_message(
            [],
            run={"id": "r1", "as_of": "2026-05-21T22:30:00+09:00"},
            market_snapshot={
                "status": "ok",
                "values": {
                    "sp500": {"status": "ok", "value": 7432.97, "change_pct": 1.0792},
                    "nasdaq": {"status": "ok", "value": 26270.36, "change_pct": 1.5448},
                    "dow": {"status": "ok", "value": 50009.35, "change_pct": 1.30755},
                    "wti": {"status": "ok", "value": 97.75},
                    "brent": {"status": "ok", "value": 104.17},
                    "gold": {"status": "ok", "value": 4533.8},
                    "dxy": {"status": "ok", "value": 99.075},
                    "ten_year": {"status": "ok", "value": 4.57},
                    "vix": {"status": "ok", "value": 17.19},
                    "usd_krw": {"status": "ok", "value": 1503.68},
                },
            },
        )

        text = message["text"]
        self.assertIn("✅ 특이사항 없음", text)
        self.assertIn(
            "📊 지수: S&P 7,433 (+1.08%) | NASDAQ 26,270 (+1.54%) | DOW 50,009 (+1.31%)",
            text,
        )
        self.assertIn(
            "💰 매크로: WTI $97.8 | Brent $104.2 | 금 $4,534 | DXY 99.1 | 10Y 4.57% | VIX 17.2",
            text,
        )
        self.assertIn("💱 환율: USD/KRW 1,504", text)
        self.assertLess(text.index("💱 환율"), text.index("[QC]"))
        self.assertEqual(message["market_snapshot"]["status"], "ok")

    def test_digest_message_guards_oil_summary_against_snapshot_direction(self) -> None:
        message = compose_digest_message(
            [
                {
                    "decision_id": "d1",
                    "run_id": "r1",
                    "event_signature": "e1",
                    "decision": "send_candidate",
                    "score": 76,
                    "grade": "B",
                    "event_type": "geo",
                    "subject": "IRAN",
                    "action": "policy_geo",
                    "effective_date": "2026-05-20",
                    "title": "Oil prices slide after Trump says Iran talks are in final stages",
                    "url": "https://example.com/news",
                    "evidence_count": 1,
                    "providers": ["brave"],
                    "llm_annotation": {
                        "summary_ko": "이란 협상 진전 기대에 유가 하락",
                        "market_marker": "green",
                        "confidence": "medium",
                        "basis": "snippet",
                    },
                }
            ],
            run={"id": "r1", "as_of": "2026-05-21T22:30:00+09:00"},
            market_snapshot={
                "values": {
                    "brent": {
                        "status": "ok",
                        "value": 108.62,
                        "change_pct": 3.42792,
                    }
                }
            },
        )

        text = message["text"]
        self.assertIn("현재 유가 반등", text)
        self.assertNotIn("유가 하락", text)

    def test_digest_message_does_not_append_unreviewed_numbers_to_llm_summary(self) -> None:
        message = compose_digest_message(
            [
                {
                    "decision_id": "d1",
                    "run_id": "r1",
                    "event_signature": "e1",
                    "decision": "send_candidate",
                    "score": 76,
                    "grade": "B",
                    "event_type": "earnings",
                    "subject": "NVDA",
                    "action": "earnings_report+guidance_update",
                    "effective_date": "2026-05-21",
                    "title": "Nvidia earnings and guidance beat expectations",
                    "url": "https://example.com/news",
                    "evidence_count": 2,
                    "providers": ["brave"],
                    "llm_annotation": {
                        "summary_ko": (
                            "NVDA, 1분기 매출·EPS 예상 상회와 강한 "
                            "2분기 매출 가이던스 제시; 자사주 매입 승인 $80B 확대"
                        ),
                        "market_marker": "none",
                        "confidence": "medium",
                        "basis": "body",
                    },
                    "evidence_items": [
                        {
                            "title": (
                                "Nvidia posts record $81.6 billion quarterly "
                                "revenue and expands buyback by $80 billion"
                            ),
                            "summary": "",
                            "body_text": "",
                        }
                    ],
                }
            ],
            run={"id": "r1", "as_of": "2026-05-21T11:00:00+09:00"},
        )

        text = message["text"]
        self.assertIn("$80B", text)
        self.assertNotIn("매출 $81.6B", text)

    def test_digest_message_appends_reviewed_earnings_contract_numbers(self) -> None:
        message = compose_digest_message(
            [
                {
                    "decision_id": "d1",
                    "run_id": "r1",
                    "event_signature": "e1",
                    "decision": "send_candidate",
                    "score": 76,
                    "grade": "B",
                    "event_type": "earnings",
                    "subject": "MRVL",
                    "action": "earnings_report",
                    "effective_date": "2026-05-28",
                    "title": "Marvell Q1 results beat expectations",
                    "evidence_count": 1,
                    "providers": ["brave"],
                    "llm_annotation": {
                        "summary_ko": "MRVL, AI 데이터센터 수요로 실적 예상 상회",
                        "market_marker": "green",
                        "confidence": "high",
                        "basis": "snippet",
                    },
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
            ],
            run={"id": "r1", "as_of": "2026-05-28T11:00:00+09:00"},
        )

        text = message["text"]
        self.assertIn("MRVL, AI 데이터센터 수요로 실적 예상 상회; EPS $0.80 / 매출 $2.42B", text)

    def test_digest_message_summarizes_merged_company_actions(self) -> None:
        message = compose_digest_message(
            [
                {
                    "decision_id": "d1",
                    "run_id": "r1",
                    "event_signature": "e1",
                    "merged_event_signatures": ["e1", "e2"],
                    "merged_actions": ["earnings_report", "guidance_raise"],
                    "decision": "send_candidate",
                    "score": 86,
                    "grade": "B",
                    "event_type": "earnings",
                    "subject": "GEV",
                    "action": "earnings_report+guidance_raise",
                    "effective_date": "2026-04-22",
                    "title": "GE Vernova Raises FY2026 Sales Guidance",
                    "merged_titles": [
                        "GE Vernova Q1 EPS Beats Estimate",
                        "GE Vernova Raises FY2026 Sales Guidance",
                    ],
                    "url": "https://example.com/news",
                    "evidence_count": 2,
                    "providers": ["archive"],
                }
            ],
            run={"id": "r1", "as_of": "2026-04-22T22:30:00+09:00"},
        )

        text = message["text"]
        self.assertIn("GEV 실적 발표·가이던스 상향", text)
        self.assertIn("신규총:1", text)

    def test_digest_message_prefers_snippet_evidence_when_available(self) -> None:
        message = compose_digest_message(
            [
                {
                    "decision_id": "d1",
                    "run_id": "r1",
                    "event_signature": "e1",
                    "decision": "send_candidate",
                    "score": 76,
                    "event_type": "geo",
                    "subject": "IRAN",
                    "action": "policy_geo",
                    "effective_date": "2026-05-15",
                    "title": "Iran policy update",
                    "url": "https://example.com/news",
                    "evidence_count": 2,
                    "providers": ["brave"],
                    "evidence_items": [
                        {
                            "provider": "brave",
                            "source": "brave-news-geo-policy",
                            "summary": (
                                "US Secretary of State Marco Rubio said Washington "
                                "wants China to push Iran into making concessions "
                                "around the Strait of Hormuz before the Trump-Xi "
                                "meeting in Beijing."
                            ),
                        }
                    ],
                }
            ],
            run={"id": "r1", "as_of": "2026-05-15T11:00:00+09:00"},
        )

        text = message["text"]
        self.assertIn("루비오, 중국에 호르무즈 압박 요구", text)
        self.assertEqual(message["summary_basis_counts"], {"snippet": 1})
        self.assertEqual(message["summary_evidence"][0]["basis"], "snippet")
        self.assertGreaterEqual(message["summary_evidence"][0]["basis_chars"], 120)
        self.assertNotIn("Iran policy update", text)

    def test_digest_message_does_not_emit_raw_english_snippet(self) -> None:
        message = compose_digest_message(
            [
                {
                    "decision_id": "d1",
                    "run_id": "r1",
                    "event_signature": "e1",
                    "decision": "send_candidate",
                    "score": 84,
                    "event_type": "geo",
                    "subject": "TRUMP_XI",
                    "action": "diplomacy",
                    "effective_date": "2026-05-15",
                    "title": (
                        "Trump-Xi Beijing summit tests leverage on Iran, "
                        "trade, Taiwan | The Jerusalem Post"
                    ),
                    "url": "https://example.com/news",
                    "evidence_count": 2,
                    "providers": ["brave"],
                    "evidence_items": [
                        {
                            "provider": "brave",
                            "source": "brave-news-geo-policy",
                            "summary": (
                                "That inconsistency is especially visible on Iran. "
                                "The war has moved from being a Middle Eastern "
                                "crisis into a global pressure point affecting "
                                "energy markets, sanctions enforcement, maritime "
                                "security, and China’s relationship with both "
                                "Tehran and Washington."
                            ),
                        }
                    ],
                }
            ],
            run={"id": "r1", "as_of": "2026-05-15T13:16:00+09:00"},
        )

        text = message["text"]
        self.assertIn("트럼프-시진핑 회담", text)
        self.assertIn("이란전·무역·대만", text)
        self.assertEqual(message["summary_basis_counts"], {"snippet": 1})
        self.assertNotIn("That inconsistency", text)
        self.assertNotIn("global pressure point", text)

    def test_digest_message_does_not_emit_raw_english_title_fallback(self) -> None:
        message = compose_digest_message(
            [
                {
                    "decision_id": "d1",
                    "run_id": "r1",
                    "event_signature": "e1",
                    "decision": "send_candidate",
                    "score": 81,
                    "event_type": "geo",
                    "subject": "TRUMP_XI",
                    "action": "policy_geo",
                    "effective_date": "2026-05-15",
                    "title": (
                        "Trump and Xi Play Up Stability Without Resolving Major "
                        "Tensions During China Visit: Live Updates - The New York Times"
                    ),
                    "url": "https://example.com/news",
                    "evidence_count": 2,
                    "providers": ["brave"],
                    "evidence_items": [
                        {
                            "provider": "brave",
                            "source": "brave-news-geo-policy",
                            "summary": (
                                "President Trump and China’s leader, Xi Jinping, "
                                "emphasized stability as they concluded a high-stakes "
                                "summit in Beijing, without clear resolutions on "
                                "trade, Taiwan, or the war in Iran."
                            ),
                        }
                    ],
                }
            ],
            run={"id": "r1", "as_of": "2026-05-15T16:44:00+09:00"},
        )

        text = message["text"]
        self.assertIn("안정 메시지에도 무역·대만·이란 이견은 미해결", text)
        self.assertNotIn("Play Up Stability", text)
        self.assertNotIn("Without Resolving", text)

    def test_digest_message_records_body_basis_when_full_body_available(self) -> None:
        message = compose_digest_message(
            [
                {
                    "decision_id": "d1",
                    "run_id": "r1",
                    "event_signature": "e1",
                    "decision": "send_candidate",
                    "score": 84,
                    "event_type": "geo",
                    "subject": "TRUMP_XI",
                    "action": "diplomacy",
                    "effective_date": "2026-05-15",
                    "title": "Trump-Xi Beijing summit tests leverage on Iran, trade, Taiwan",
                    "url": "https://example.com/news",
                    "evidence_count": 2,
                    "providers": ["brave"],
                    "body_text": (
                        "Iran has become a global pressure point affecting energy "
                        "markets, sanctions enforcement, maritime security, and "
                        "China's relationship with Tehran and Washington. "
                    )
                    * 5,
                }
            ],
            run={"id": "r1", "as_of": "2026-05-15T13:16:00+09:00"},
        )

        text = message["text"]
        self.assertIn("미중 정상회담, 이란전·에너지·제재 리스크", text)
        self.assertEqual(message["summary_basis_counts"], {"body": 1})
        self.assertEqual(message["summary_evidence"][0]["basis"], "body")

    def test_llm_none_marker_renders_yellow_in_digest_and_card(self) -> None:
        row = {
            "decision_id": "d1",
            "run_id": "r1",
            "event_signature": "e1",
            "decision": "send_candidate",
            "score": 76,
            "event_type": "geo",
            "subject": "TRUMP_XI",
            "action": "diplomacy",
            "effective_date": "2026-05-15",
            "title": "Trump-Xi summit",
            "url": "https://example.com/news",
            "evidence_count": 2,
            "providers": ["brave"],
            "llm_annotation": {
                "summary_ko": (
                    "트럼프-시진핑 회담에서 대만·호르무즈·이란 무기 지원 "
                    "문제가 함께 논의됨"
                ),
                "market_marker": "none",
                "confidence": "medium",
                "basis": "snippet",
            },
        }

        digest = compose_digest_message(
            [row],
            run={"id": "r1", "as_of": "2026-05-15T19:00:00+09:00"},
        )
        card = compose_message(row, index=1)

        self.assertIn("• [B] 🟡 트럼프-시진핑 회담", digest["text"])
        self.assertTrue(card["text"].startswith("🟡 1. [지정학]"))
        self.assertEqual(card["market_marker"], "🟡")

    def test_company_marker_conflict_with_price_reaction_renders_yellow(self) -> None:
        row = {
            "decision_id": "d1",
            "run_id": "r1",
            "event_signature": "e1",
            "decision": "send_candidate",
            "score": 86,
            "grade": "B",
            "event_type": "earnings",
            "subject": "UNH",
            "action": "guidance_raise",
            "effective_date": "2026-05-18",
            "title": "UNH raises guidance after Q1 earnings",
            "evidence_count": 2,
            "providers": ["brave"],
            "price_reaction": {
                "status": "ok",
                "ticker": "UNH",
                "direction": "down",
                "pct_change": -2.1,
                "session": "intraday_5min",
            },
        }

        digest = compose_digest_message(
            [row],
            run={"id": "r1", "as_of": "2026-05-18T22:30:00+09:00"},
        )
        card = compose_message(row, index=1)

        self.assertIn("• [B] 🟡 UNH 실적 이후 가이던스 상향", digest["text"])
        self.assertEqual(card["market_marker"], "🟡")

    def test_llm_marker_is_not_overridden_by_small_price_reaction(self) -> None:
        row = {
            "decision_id": "d1",
            "run_id": "r1",
            "event_signature": "e1",
            "decision": "send_candidate",
            "score": 76,
            "grade": "B",
            "event_type": "earnings",
            "subject": "NVDA",
            "action": "earnings_report",
            "effective_date": "2026-05-21",
            "title": "Nvidia forecasts quarterly revenue above estimates",
            "evidence_count": 2,
            "providers": ["brave"],
            "llm_annotation": {
                "summary_ko": (
                    "NVDA, 2분기 매출 전망 $91B로 예상치 $86.84B 상회; "
                    "$80B 자사주 매입 발표"
                ),
                "market_marker": "green",
                "confidence": "high",
                "basis": "snippet",
            },
            "price_reaction": {
                "status": "ok",
                "ticker": "NVDA",
                "direction": "down",
                "pct_change": -0.886,
                "session": "intraday_5min",
            },
        }

        digest = compose_digest_message(
            [row],
            run={"id": "r1", "as_of": "2026-05-21T22:30:00+09:00"},
        )
        card = compose_message(row, index=1)

        self.assertIn("• [B] 🟢 NVDA, 2분기 매출 전망 $91B", digest["text"])
        self.assertEqual(card["market_marker"], "🟢")

    def test_llm_red_marker_with_positive_price_reaction_renders_yellow(self) -> None:
        row = {
            "decision_id": "d1",
            "run_id": "r1",
            "event_signature": "e1",
            "decision": "send_candidate",
            "score": 76,
            "grade": "B",
            "event_type": "earnings",
            "subject": "INTU",
            "action": "earnings_report",
            "effective_date": "2026-05-22",
            "title": "Intuit reports earnings and guidance update",
            "evidence_count": 2,
            "providers": ["brave"],
            "llm_annotation": {
                "summary_ko": "INTU, 실적 발표 후 전망 불확실성이 부각됨",
                "market_marker": "red",
                "confidence": "medium",
                "basis": "snippet",
            },
            "price_reaction": {
                "status": "ok",
                "ticker": "INTU",
                "direction": "up",
                "pct_change": 0.3371,
                "session": "intraday_5min",
            },
        }

        digest = compose_digest_message(
            [row],
            run={"id": "r1", "as_of": "2026-05-23T01:00:00+09:00"},
        )
        card = compose_message(row, index=1)

        self.assertIn("• [B] 🟡 INTU, 실적 발표 후 전망 불확실성", digest["text"])
        self.assertEqual(card["market_marker"], "🟡")

    def test_merged_geo_actions_do_not_render_raw_action_labels(self) -> None:
        digest = compose_digest_message(
            [
                {
                    "decision_id": "d1",
                    "run_id": "r1",
                    "event_signature": "e1",
                    "decision": "send_candidate",
                    "score": 95,
                    "grade": "A",
                    "event_type": "geo",
                    "subject": "IRAN",
                    "action": "conflict+diplomacy+policy_geo",
                    "effective_date": "2026-05-22",
                    "title": "Iran war keeps oil markets pressured as talks continue",
                    "evidence_count": 3,
                    "providers": ["brave"],
                }
            ],
            run={"id": "r1", "as_of": "2026-05-23T01:00:00+09:00"},
        )

        self.assertIn("이란 이슈 — 협상·유가·호르무즈 변수", digest["text"])
        self.assertNotIn("conflict+diplomacy", digest["text"])
        self.assertNotIn("policy geo", digest["text"])

    def test_policy_risk_digest_is_strategic_not_geo(self) -> None:
        row = {
            "decision_id": "d1",
            "run_id": "r1",
            "event_signature": "e1",
            "decision": "send_candidate",
            "score": 76,
            "grade": "B",
            "event_type": "strategic",
            "subject": "NVDA",
            "action": "policy_risk",
            "effective_date": "2026-05-21",
            "title": "Nvidia says it has 'largely conceded' China's AI chip market to Huawei",
            "url": "https://www.cnbc.com/2026/05/21/nvidia-jensen-huang-china-ai-chip-market-huawei.html",
            "evidence_count": 1,
            "providers": ["brave"],
            "llm_annotation": {
                "summary_ko": (
                    "엔비디아 CEO, 중국 AI 칩 시장을 화웨이에 대체로 양보했다고 언급"
                ),
                "market_marker": "red",
                "confidence": "high",
                "basis": "body",
            },
            "sources": ["CNBC"],
        }

        digest = compose_digest_message(
            [row],
            run={"id": "r1", "as_of": "2026-05-22T15:39:00+09:00"},
        )
        card = compose_message(row, index=1)

        self.assertIn("• [B] 🔴 엔비디아 CEO, 중국 AI 칩 시장", digest["text"])
        self.assertIn("EVENTS: 전략:1", digest["text"])
        self.assertNotIn("EVENTS: 지정학:1", digest["text"])
        self.assertTrue(card["text"].startswith("🔴 1. [전략] NVDA - 정책 리스크"))

    def test_llm_none_marker_is_not_flipped_red_by_price_reaction(self) -> None:
        row = {
            "decision_id": "d1",
            "run_id": "r1",
            "event_signature": "e1",
            "decision": "send_candidate",
            "score": 76,
            "grade": "B",
            "event_type": "earnings",
            "subject": "NVDA",
            "action": "earnings_report",
            "effective_date": "2026-05-21",
            "title": "Nvidia forecasts quarterly revenue above estimates",
            "evidence_count": 2,
            "providers": ["brave"],
            "llm_annotation": {
                "summary_ko": (
                    "NVDA, 2분기 매출 전망 $91B로 예상치 $86.84B 상회; "
                    "$80B 자사주 매입 발표"
                ),
                "market_marker": "none",
                "confidence": "medium",
                "basis": "snippet",
            },
            "price_reaction": {
                "status": "ok",
                "ticker": "NVDA",
                "direction": "down",
                "pct_change": -0.886,
                "session": "intraday_5min",
            },
        }

        digest = compose_digest_message(
            [row],
            run={"id": "r1", "as_of": "2026-05-21T22:30:00+09:00"},
        )
        card = compose_message(row, index=1)

        self.assertIn("• [B] 🟡 NVDA, 2분기 매출 전망 $91B", digest["text"])
        self.assertEqual(card["market_marker"], "🟡")

    def test_earnings_report_marker_uses_price_reaction_as_market_verdict(self) -> None:
        row = {
            "decision_id": "d1",
            "run_id": "r1",
            "event_signature": "e1",
            "decision": "send_candidate",
            "score": 90,
            "grade": "A",
            "event_type": "earnings",
            "subject": "DLR",
            "action": "earnings_report",
            "effective_date": "2026-05-18",
            "title": "DLR reports Q1 results",
            "evidence_count": 2,
            "providers": ["brave"],
            "price_reaction": {
                "status": "ok",
                "ticker": "DLR",
                "direction": "down",
                "pct_change": -1.7,
                "session": "intraday_5min",
            },
        }

        digest = compose_digest_message(
            [row],
            run={"id": "r1", "as_of": "2026-05-18T22:30:00+09:00"},
        )

        self.assertIn("• [A] 🔴 DLR", digest["text"])

    def test_message_preview_defaults_to_send_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config, summary = _build_report_fixture(Path(tmp))

            preview = load_message_preview(config.db_path)

            self.assertEqual(preview["run"]["id"], summary["run_id"])
            self.assertEqual(preview["message_count"], 1)
            message = preview["messages"][0]
            self.assertEqual(message["decision"], "send_candidate")
            self.assertEqual(message["subject"], "nvda")
            self.assertIn("[실적] NVDA", message["text"])
            self.assertIn("가이던스 상향", message["text"])
            self.assertEqual(message["summary_basis"], "snippet")
            self.assertGreaterEqual(message["summary_basis_chars"], 120)
            self.assertNotIn("score", message["text"])
            self.assertNotIn("evidence", message["text"])
            self.assertNotIn("providers", message["text"])
            self.assertNotIn("date ", message["text"])
            self.assertNotIn("https://example.com", message["text"])

    def test_message_preview_markdown_and_json_render(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config, _summary = _build_report_fixture(Path(tmp))
            preview = load_message_preview(config.db_path)

            markdown = render_message_preview(preview, output_format="markdown")
            json_text = render_message_preview(preview, output_format="json")

            self.assertIn("# News Scanner V2 Message Preview", markdown)
            self.assertIn("```text", markdown)
            parsed = json.loads(json_text)
            self.assertEqual(parsed["message_count"], 1)
            self.assertEqual(parsed["messages"][0]["subject"], "nvda")

    def test_cli_report_messages_writes_output_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config, _summary = _build_report_fixture(root)
            output = root / "preview" / "messages.md"

            exit_code = main(
                [
                    "report",
                    "messages",
                    "--db-path",
                    str(config.db_path),
                    "--output",
                    str(output),
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertIn("# News Scanner V2 Message Preview", output.read_text())

    def test_cli_report_messages_missing_db_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stderr = io.StringIO()
            stdout = io.StringIO()

            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main(
                    [
                        "report",
                        "messages",
                        "--db-path",
                        str(Path(tmp) / "missing.sqlite"),
                    ]
                )

            self.assertEqual(exit_code, 1)
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn("DB does not exist", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
