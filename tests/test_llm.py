import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from news_scanner_v2.composer import compose_digest_message
from news_scanner_v2.llm import (
    annotate_rows,
    build_annotation_payload,
    evidence_hash,
    edit_rows,
    edit_theme_candidates,
    normalize_annotation,
    validate_annotation,
    validate_editorial,
    validate_theme_editorial,
)
from news_scanner_v2.reports import load_decision_rows
from test_reports import _build_report_fixture


class FakeLLMClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def create_annotation(self, payload):
        self.calls.append(payload)
        return dict(self.payload)


class FakeEditorClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def create_editorial(self, payload):
        self.calls.append(payload)
        return dict(self.payload)


class FakeThemeEditorClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def create_theme_editorial(self, payload):
        self.calls.append(payload)
        return dict(self.payload)


class LLMAnnotationTests(unittest.TestCase):
    def test_edit_rows_persists_send_editorial_and_sets_digest_annotation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config, summary = _build_report_fixture(Path(tmp))
            report = load_decision_rows(
                config.db_path,
                run_id=summary["run_id"],
                decisions={"send_candidate"},
            )
            rows = report["rows"]
            client = FakeEditorClient(
                {
                    "decision": "send",
                    "grade": "A",
                    "drop_reason": "",
                    "summary_ko": "NVDA, AI 데이터센터 수요 확대로 실적 후 가이던스 상향",
                    "market_marker": "green",
                    "confidence": "high",
                    "basis": "snippet",
                    "reason_ko": "실적 이후 가이던스 상향 근거가 명확",
                    "source_hint": "",
                    "risk_flags": [],
                }
            )

            result = edit_rows(
                rows,
                db_path=config.db_path,
                enabled=True,
                api_key=None,
                client=client,
            )
            message = compose_digest_message(rows, run=report["run"])

            self.assertEqual(result["accepted"], 1)
            self.assertEqual(result["decisions"], {"send": 1, "drop": 0, "hold": 0})
            self.assertEqual(rows[0]["grade"], "A")
            self.assertEqual(rows[0]["llm_editorial"]["decision"], "send")
            self.assertTrue(rows[0]["llm_annotation"]["_from_editorial"])
            self.assertIn("🟢 NVDA, AI 데이터센터 수요", message["text"])
            self.assertNotIn("(Reuters)", message["text"])

            con = sqlite3.connect(config.db_path)
            stored = con.execute(
                """
                select annotation_type, status, payload_json
                from llm_annotations
                """
            ).fetchone()
            self.assertEqual(stored[0], "editorial")
            self.assertEqual(stored[1], "ok")
            payload = json.loads(stored[2])
            self.assertEqual(payload["decision"], "send")
            reloaded = load_decision_rows(
                config.db_path,
                run_id=summary["run_id"],
                decisions={"send_candidate"},
            )
            self.assertEqual(reloaded["rows"][0]["llm_editorial"]["decision"], "send")
            self.assertTrue(reloaded["rows"][0]["llm_annotation"]["_from_editorial"])

    def test_edit_rows_accepts_drop_editorial_without_summary_annotation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config, summary = _build_report_fixture(Path(tmp))
            report = load_decision_rows(
                config.db_path,
                run_id=summary["run_id"],
                decisions={"send_candidate"},
            )
            rows = report["rows"]
            client = FakeEditorClient(
                {
                    "decision": "drop",
                    "grade": "C",
                    "drop_reason": "soft_analysis",
                    "summary_ko": "NVDA 관련 분석성 기사로 전송 제외",
                    "market_marker": "none",
                    "confidence": "medium",
                    "basis": "snippet",
                    "reason_ko": "구체적 신규 이벤트보다 분석성 설명에 가까움",
                    "source_hint": "",
                    "risk_flags": ["soft_analysis"],
                }
            )

            result = edit_rows(
                rows,
                db_path=config.db_path,
                enabled=True,
                api_key=None,
                client=client,
            )

            self.assertEqual(result["accepted"], 1)
            self.assertEqual(result["decisions"], {"send": 0, "drop": 1, "hold": 0})
            self.assertEqual(rows[0]["llm_editorial"]["decision"], "drop")
            self.assertIsNone(rows[0]["llm_annotation"])

    def test_validate_editorial_rejects_drop_without_reason(self) -> None:
        valid, reason = validate_editorial(
            {
                "decision": "drop",
                "grade": "C",
                "drop_reason": "",
                "summary_ko": "NVDA 관련 분석성 기사로 전송 제외",
                "market_marker": "none",
                "confidence": "medium",
                "basis": "title",
                "reason_ko": "근거가 약해 제외",
                "source_hint": "",
                "risk_flags": [],
            },
            row={"title": "Nvidia raises guidance after Q1 earnings"},
        )

        self.assertFalse(valid)
        self.assertEqual(reason, "drop_reason_required")

    def test_validate_editorial_rejects_source_hint_not_in_evidence(self) -> None:
        row = {
            "title": "Workday shares jump 10.8% after earnings beat",
            "url": "https://www.chartmill.com/news/WDAY/workday-earnings",
            "evidence_items": [
                {
                    "title": "Workday shares jump 10.8% after earnings beat",
                    "url": "https://www.chartmill.com/news/WDAY/workday-earnings",
                    "summary": (
                        "Workday shares jumped 10.8% after an earnings beat "
                        "despite a slight revenue miss."
                    ),
                }
            ],
        }
        editorial = {
            "decision": "send",
            "grade": "A",
            "drop_reason": "",
            "summary_ko": "WDAY, 이익 예상 상회·매출 소폭 미달에도 주가 10.8% 급등",
            "market_marker": "green",
            "confidence": "medium",
            "basis": "snippet",
            "reason_ko": "실적과 주가 반응이 함께 확인됨",
            "source_hint": "CNBC 5/21",
            "risk_flags": [],
        }

        valid, reason = validate_editorial(editorial, row=row)

        self.assertFalse(valid)
        self.assertEqual(reason, "source_hint_not_in_evidence")

    def test_validate_editorial_rejects_generic_provider_source_hint(self) -> None:
        row = {
            "title": "Brent rises as Iran talks stall",
            "url": "https://www.reuters.com/markets/commodities/oil-iran-talks",
            "evidence_items": [
                {
                    "title": "Brent rises as Iran talks stall",
                    "url": "https://www.reuters.com/markets/commodities/oil-iran-talks",
                    "summary": "Brent rose 3.4% as Iran talks stalled.",
                }
            ],
        }
        editorial = {
            "decision": "send",
            "grade": "A",
            "drop_reason": "",
            "summary_ko": "Brent, 이란 협상 교착으로 3.4% 상승",
            "market_marker": "red",
            "confidence": "high",
            "basis": "snippet",
            "reason_ko": "유가 상승과 지정학 리스크가 확인됨",
            "source_hint": "brave 5/21",
            "risk_flags": [],
        }

        valid, reason = validate_editorial(editorial, row=row)

        self.assertFalse(valid)
        self.assertEqual(reason, "source_hint_generic_provider")

    def test_validate_editorial_accepts_source_hint_from_evidence_domain(self) -> None:
        row = {
            "title": "Workday shares jump 10.8% after earnings beat",
            "url": "https://www.chartmill.com/news/WDAY/workday-earnings",
            "evidence_items": [
                {
                    "title": "Workday shares jump 10.8% after earnings beat",
                    "url": "https://www.chartmill.com/news/WDAY/workday-earnings",
                    "summary": (
                        "Workday shares jumped 10.8% after an earnings beat "
                        "despite a slight revenue miss."
                    ),
                }
            ],
        }
        editorial = {
            "decision": "send",
            "grade": "A",
            "drop_reason": "",
            "summary_ko": "WDAY, 이익 예상 상회·매출 소폭 미달에도 주가 10.8% 급등",
            "market_marker": "green",
            "confidence": "medium",
            "basis": "snippet",
            "reason_ko": "실적과 주가 반응이 함께 확인됨",
            "source_hint": "ChartMill 5/21",
            "risk_flags": [],
        }

        valid, reason = validate_editorial(editorial, row=row)

        self.assertTrue(valid)
        self.assertEqual(reason, "")

    def test_annotate_rows_persists_valid_summary_and_digest_uses_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config, summary = _build_report_fixture(Path(tmp))
            report = load_decision_rows(
                config.db_path,
                run_id=summary["run_id"],
                decisions={"send_candidate"},
            )
            rows = report["rows"]
            client = FakeLLMClient(
                {
                    "summary_ko": "NVDA, AI 데이터센터 수요 확대로 실적 후 가이던스 상향",
                    "market_marker": "green",
                    "confidence": "high",
                    "basis": "snippet",
                    "reason_ko": "가이던스 상향과 AI 수요가 근거",
                    "source_quote": "raised outlook after Q1 earnings",
                }
            )

            result = annotate_rows(
                rows,
                db_path=config.db_path,
                enabled=True,
                api_key=None,
                client=client,
            )
            message = compose_digest_message(rows, run=report["run"])

            self.assertEqual(result["accepted"], 1)
            self.assertEqual(result["rejected"], 0)
            self.assertEqual(len(client.calls), 1)
            self.assertIn("🟢 NVDA, AI 데이터센터 수요", message["text"])
            self.assertEqual(message["summary_basis_counts"], {"llm_snippet": 1})

            con = sqlite3.connect(config.db_path)
            stored = con.execute(
                "select status, payload_json from llm_annotations"
            ).fetchone()
            self.assertEqual(stored[0], "ok")
            payload = json.loads(stored[1])
            self.assertEqual(payload["confidence"], "high")

    def test_annotate_rows_rejects_generic_summary_and_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config, summary = _build_report_fixture(Path(tmp))
            report = load_decision_rows(
                config.db_path,
                run_id=summary["run_id"],
                decisions={"send_candidate"},
            )
            rows = report["rows"]
            client = FakeLLMClient(
                {
                    "summary_ko": "NVDA 관련 정책·지정학 이벤트 진행",
                    "market_marker": "none",
                    "confidence": "low",
                    "basis": "title",
                    "reason_ko": "불명확",
                    "source_quote": "Nvidia",
                }
            )

            result = annotate_rows(
                rows,
                db_path=config.db_path,
                enabled=True,
                api_key=None,
                client=client,
            )
            message = compose_digest_message(rows, run=report["run"])

            self.assertEqual(result["accepted"], 0)
            self.assertEqual(result["rejected"], 1)
            self.assertIn("가이던스 상향", message["text"])
            self.assertNotIn("정책·지정학 이벤트 진행", message["text"])

            con = sqlite3.connect(config.db_path)
            stored = con.execute("select status, error from llm_annotations").fetchone()
            self.assertEqual(stored[0], "rejected_validation")
            self.assertEqual(stored[1], "summary_too_generic")

    def test_annotate_rows_strips_source_prefix_before_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config, summary = _build_report_fixture(Path(tmp))
            report = load_decision_rows(
                config.db_path,
                run_id=summary["run_id"],
                decisions={"send_candidate"},
            )
            rows = report["rows"]
            client = FakeLLMClient(
                {
                    "summary_ko": (
                        "Reuters: NVDA, AI 데이터센터 수요 확대로 "
                        "실적 후 가이던스 상향"
                    ),
                    "market_marker": "green",
                    "confidence": "high",
                    "basis": "snippet",
                    "reason_ko": "가이던스 상향과 AI 수요가 근거",
                    "source_quote": "raised outlook after Q1 earnings",
                }
            )

            result = annotate_rows(
                rows,
                db_path=config.db_path,
                enabled=True,
                api_key=None,
                client=client,
            )
            message = compose_digest_message(rows, run=report["run"])

            self.assertEqual(result["accepted"], 1)
            self.assertIn("🟢 NVDA, AI 데이터센터 수요", message["text"])
            self.assertNotIn("Reuters:", message["text"])

            con = sqlite3.connect(config.db_path)
            payload = json.loads(
                con.execute("select payload_json from llm_annotations").fetchone()[0]
            )
            self.assertFalse(payload["summary_ko"].startswith("Reuters:"))

    def test_normalize_annotation_cleans_amount_words_and_ticker_colon(self) -> None:
        payload = normalize_annotation(
            {
                "summary_ko": (
                    "TSLA: 테슬라가 실적에서 스페이스X에 "
                    "$2 Billion을 투자했고 MU가 $1 trillion 시총에 진입"
                ),
                "market_marker": "none",
                "confidence": "medium",
                "basis": "title",
                "reason_ko": "투자 규모",
                "source_quote": "Invested $2 Billion Into SpaceX; $1 trillion market value",
            }
        )

        self.assertEqual(
            payload["summary_ko"],
            "TSLA, 테슬라가 실적에서 스페이스X에 $2B을 투자했고 MU가 $1T 시총에 진입",
        )

    def test_normalize_annotation_replaces_theme_company_alias_without_subject_ticker(self) -> None:
        payload = normalize_annotation(
            {
                "summary_ko": "Micron/MU가 AI 수요 기반 메모리 랠리 중심으로 보도됐다",
                "market_marker": "green",
                "confidence": "high",
                "basis": "snippet",
                "reason_ko": "메모리 랠리",
                "source_quote": "Micron/MU AI memory rally",
            }
        )

        self.assertEqual(
            payload["summary_ko"],
            "MU가 AI 수요 기반 메모리 랠리 중심으로 보도됐다",
        )

    def test_normalize_annotation_replaces_subject_company_alias_with_ticker(self) -> None:
        payload = normalize_annotation(
            {
                "summary_ko": "Intel, 예상치를 크게 웃돈 매출 전망 이후 채권 투자자 대상 콜 예정",
                "market_marker": "green",
                "confidence": "high",
                "basis": "title",
                "reason_ko": "매출 전망 호조",
                "source_quote": "Blowout Sales Outlook",
            },
            row={"subject": "intc"},
        )

        self.assertEqual(
            payload["summary_ko"],
            "INTC, 예상치를 크게 웃돈 매출 전망 이후 채권 투자자 대상 콜 예정",
        )
        valid, reason = validate_annotation(
            payload,
            row={
                "subject": "intc",
                "title": "Intel to Hold Fixed-Income Calls Following Blowout Sales Outlook",
            },
        )
        self.assertTrue(valid, reason)

    def test_normalize_annotation_translates_geopolitical_terms(self) -> None:
        payload = normalize_annotation(
            {
                "summary_ko": (
                    "IRAN, Trump, Middle East 동맹 요청에 화요일 예정된 "
                    "US 군사공격 보류"
                ),
                "market_marker": "green",
                "confidence": "high",
                "basis": "body",
                "reason_ko": "군사공격 보류",
                "source_quote": "Trump cancelled the US strike on Iran",
            }
        )

        self.assertEqual(
            payload["summary_ko"],
            "이란, 트럼프, 중동 동맹 요청에 화요일 예정된 미국 군사공격 보류",
        )
        valid, reason = validate_annotation(
            payload,
            row={
                "title": "Trump cancelled the US strike on Iran after Middle East allies requested talks",
            },
        )
        self.assertTrue(valid, reason)

    def test_normalize_annotation_translates_geopolitical_terms_before_korean_suffix(
        self,
    ) -> None:
        payload = normalize_annotation(
            {
                "summary_ko": (
                    "IRAN의 호르무즈 압박과 U.S.의 제재 집행으로 "
                    "CHINA와 Taiwan 리스크 부각"
                ),
                "market_marker": "red",
                "confidence": "medium",
                "basis": "title",
                "reason_ko": "지정학 리스크",
                "source_quote": "Iran, U.S., China and Taiwan risks",
            }
        )

        self.assertEqual(
            payload["summary_ko"],
            "이란의 호르무즈 압박과 미국의 제재 집행으로 중국과 대만 리스크 부각",
        )
        valid, reason = validate_annotation(
            payload,
            row={"title": "Iran, U.S., China and Taiwan risks"},
        )
        self.assertTrue(valid, reason)

    def test_annotate_rows_normalizes_company_alias_before_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config, summary = _build_report_fixture(Path(tmp))
            report = load_decision_rows(
                config.db_path,
                run_id=summary["run_id"],
                decisions={"send_candidate"},
            )
            rows = report["rows"]
            client = FakeLLMClient(
                {
                    "summary_ko": "Nvidia, AI 데이터센터 수요 확대로 실적 후 가이던스 상향",
                    "market_marker": "green",
                    "confidence": "high",
                    "basis": "snippet",
                    "reason_ko": "가이던스 상향과 AI 수요가 근거",
                    "source_quote": "raised outlook after Q1 earnings",
                }
            )

            result = annotate_rows(
                rows,
                db_path=config.db_path,
                enabled=True,
                api_key=None,
                client=client,
            )
            message = compose_digest_message(rows, run=report["run"])

            self.assertEqual(result["accepted"], 1)
            self.assertEqual(result["rejected"], 0)
            self.assertIn("🟢 NVDA, AI 데이터센터 수요", message["text"])

    def test_validate_annotation_rejects_numbers_not_in_evidence(self) -> None:
        row = {"title": "Nvidia raises guidance after Q1 earnings"}
        valid, reason = validate_annotation(
            {
                "summary_ko": "NVDA, 실적 후 가이던스 20% 상향",
                "market_marker": "green",
                "confidence": "medium",
                "basis": "title",
                "reason_ko": "가이던스 상향",
                "source_quote": "raises guidance",
            },
            row=row,
        )

        self.assertFalse(valid)
        self.assertEqual(reason, "summary_number_not_in_evidence")

    def test_validate_annotation_accepts_numbers_from_evidence_item_body_text(self) -> None:
        row = {
            "title": "Stocks Fall and Oil Prices Gain After Trump Warns Iran",
            "evidence_items": [
                {
                    "title": "Wall Street edges lower after Iran warning",
                    "summary": "",
                    "body_text": (
                        "Futures for the S&P 500 were down 0.3% before the "
                        "opening bell as oil prices grew volatile."
                    ),
                }
            ],
        }

        valid, reason = validate_annotation(
            {
                "summary_ko": "미국-이란 협상 교착 속 S&P 500 선물 0.3% 하락",
                "market_marker": "red",
                "confidence": "high",
                "basis": "body",
                "reason_ko": "선물 하락",
                "source_quote": "S&P 500 were down 0.3%",
            },
            row=row,
        )

        self.assertTrue(valid, reason)

    def test_edit_theme_candidates_rejects_send_when_trusted_rescue_required(self) -> None:
        candidate = {
            "id": "theme-hd",
            "theme_type": "earnings_result",
            "theme_key": "hd_earnings_result",
            "subject": "HD",
            "action": "earnings_result",
            "grade": "B",
            "market_marker": "none",
            "source_tier": "low_quality",
            "requires_trusted_rescue": True,
            "preview_only": False,
            "summary_seed": "HD earnings or guidance update requires trusted rescue",
            "claim_atoms": [{"text": "guidance data mentioned", "evidence_id": "hd-a"}],
            "evidence": [
                {
                    "candidate_id": "hd-a",
                    "source": "brave-news-earnings-guidance",
                    "provider": "brave",
                    "domain": "marketbeat.com",
                    "title": "Home Depot Updates FY 2026 Earnings Guidance",
                    "summary": "Home Depot provided EPS guidance of 14.690-15.278.",
                }
            ],
        }
        client = FakeThemeEditorClient(
            {
                "decision": "send",
                "grade": "B",
                "drop_reason": "",
                "summary_ko": "HD, FY2026 EPS 가이던스 14.690-15.278 제시",
                "market_marker": "none",
                "confidence": "medium",
                "basis": "snippet",
                "reason_ko": "가이던스 수치가 제시됨",
                "source_hint": "MarketBeat",
                "evidence_ids": ["hd-a"],
                "claim_atoms": [
                    {"text": "FY2026 EPS guidance", "evidence_id": "hd-a"}
                ],
                "risk_flags": [],
            }
        )

        result = edit_theme_candidates(
            [candidate],
            enabled=True,
            api_key=None,
            client=client,
        )

        self.assertEqual(result["accepted"], 0)
        self.assertEqual(result["rejected"], 1)
        self.assertEqual(
            result["validation_errors"],
            {"trusted_rescue_required": 1},
        )
        self.assertEqual(
            candidate["llm_editorial"]["validation_error"],
            "trusted_rescue_required",
        )

    def test_validate_theme_editorial_rejects_generic_sector_pressure_summary(
        self,
    ) -> None:
        candidate = {
            "id": "theme-semi",
            "theme_type": "sector_pressure",
            "theme_key": "semiconductor_pressure",
            "subject": "semiconductors",
            "action": "sector_pressure",
            "source_tier": "mixed",
            "requires_trusted_rescue": False,
            "preview_only": False,
            "summary_seed": "Semiconductor pressure from chip weakness",
            "evidence": [
                {
                    "candidate_id": "semi-a",
                    "source": "Yahoo Finance",
                    "provider": "brave",
                    "domain": "finance.yahoo.com",
                    "title": "Nvidia weakness weighs on chip stocks",
                    "summary": "Nvidia shares were weaker after earnings.",
                },
                {
                    "candidate_id": "semi-b",
                    "source": "TradingView",
                    "provider": "brave",
                    "domain": "tradingview.com",
                    "title": "Chip stocks remain under pressure",
                    "summary": "Chip stocks remained under pressure.",
                },
            ],
        }
        editorial = {
            "decision": "send",
            "grade": "B",
            "drop_reason": "",
            "summary_ko": (
                "NVDA 약세와 실적 경계감에 반도체/칩 압박 신호가 "
                "겹치며 섹터 전반에 부담으로 부각"
            ),
            "market_marker": "red",
            "confidence": "medium",
            "basis": "snippet",
            "reason_ko": "복수 근거가 반도체 압박을 지지",
            "source_hint": "Yahoo/TradingView",
            "evidence_ids": ["semi-a", "semi-b"],
            "claim_atoms": [
                {"text": "NVDA 약세", "evidence_id": "semi-a"},
                {"text": "칩 압박", "evidence_id": "semi-b"},
            ],
            "risk_flags": ["theme_synthesis"],
        }

        valid, reason = validate_theme_editorial(editorial, candidate=candidate)

        self.assertFalse(valid)
        self.assertEqual(reason, "theme_summary_too_generic")

    def test_validate_theme_editorial_rejects_ai_infra_amount_without_trusted_evidence(
        self,
    ) -> None:
        candidate = {
            "id": "theme-ai-infra",
            "theme_type": "strategic_theme",
            "theme_key": "ai_infrastructure_jv",
            "subject": "AI_INFRA",
            "action": "ai_infrastructure_jv",
            "source_tier": "trusted",
            "requires_trusted_rescue": False,
            "preview_only": False,
            "summary_seed": "AI infrastructure power demand theme",
            "evidence": [
                {
                    "candidate_id": "trusted-a",
                    "source": "CNBC",
                    "provider": "brave",
                    "domain": "cnbc.com",
                    "title": "AI infrastructure power demand becomes a market theme",
                    "summary": (
                        "AI data center electricity demand is rising, but no "
                        "new transaction amount is confirmed."
                    ),
                },
                {
                    "candidate_id": "weak-b",
                    "source": "AOL",
                    "provider": "brave",
                    "domain": "aol.com",
                    "title": "NextEra eyes $67B utility acquisition for AI power",
                    "summary": "The syndicated item repeats a $67B deal figure.",
                },
            ],
        }
        editorial = {
            "decision": "send",
            "grade": "B",
            "drop_reason": "",
            "summary_ko": (
                "AI 전력 수요 대응 인수설 $67B가 데이터센터 전력 테마로 확산"
            ),
            "market_marker": "green",
            "confidence": "medium",
            "basis": "snippet",
            "reason_ko": "전력 수요와 인수설 금액이 근거로 제시됨",
            "source_hint": "CNBC",
            "evidence_ids": ["trusted-a", "weak-b"],
            "claim_atoms": [
                {"text": "AI 전력 수요", "evidence_id": "trusted-a"},
                {"text": "$67B 인수설", "evidence_id": "weak-b"},
            ],
            "risk_flags": ["theme_synthesis"],
        }

        valid, reason = validate_theme_editorial(editorial, candidate=candidate)

        self.assertFalse(valid)
        self.assertEqual(reason, "ai_infra_amount_needs_trusted_evidence")

    def test_validate_theme_editorial_accepts_ai_infra_amount_from_trusted_evidence(
        self,
    ) -> None:
        candidate = {
            "id": "theme-ai-infra",
            "theme_type": "strategic_theme",
            "theme_key": "ai_infrastructure_jv",
            "subject": "AI_INFRA",
            "action": "ai_infrastructure_jv",
            "source_tier": "trusted",
            "requires_trusted_rescue": False,
            "preview_only": False,
            "summary_seed": "AI infrastructure power acquisition",
            "evidence": [
                {
                    "candidate_id": "trusted-a",
                    "source": "Bloomberg",
                    "provider": "brave",
                    "domain": "bloomberg.com",
                    "title": "Utility group pursues $67B deal for AI data center power",
                    "summary": (
                        "The utility group is pursuing a $67B acquisition to "
                        "meet AI data center power demand."
                    ),
                }
            ],
        }
        editorial = {
            "decision": "send",
            "grade": "B",
            "drop_reason": "",
            "summary_ko": (
                "AI 데이터센터 전력 수요 대응 인수 $67B 추진으로 유틸리티 재편 기대"
            ),
            "market_marker": "green",
            "confidence": "medium",
            "basis": "snippet",
            "reason_ko": "신뢰 근거가 거래 금액과 전력 수요를 함께 제시함",
            "source_hint": "Bloomberg 5/22",
            "evidence_ids": ["trusted-a"],
            "claim_atoms": [
                {"text": "AI 데이터센터 전력 수요", "evidence_id": "trusted-a"},
                {"text": "$67B 인수", "evidence_id": "trusted-a"},
            ],
            "risk_flags": ["theme_synthesis"],
        }

        valid, reason = validate_theme_editorial(editorial, candidate=candidate)

        self.assertTrue(valid)
        self.assertEqual(reason, "")

    def test_evidence_hash_includes_evidence_item_body_text(self) -> None:
        base = {
            "event_signature": "event-1",
            "title": "Stocks Fall After Iran Warning",
            "evidence_items": [
                {
                    "title": "Wall Street edges lower",
                    "summary": "",
                    "body_text": "Futures for the S&P 500 were down 0.3%.",
                }
            ],
        }
        changed = {
            **base,
            "evidence_items": [
                {
                    "title": "Wall Street edges lower",
                    "summary": "",
                    "body_text": "Futures for the S&P 500 were down 0.4%.",
                }
            ],
        }

        self.assertNotEqual(evidence_hash(base), evidence_hash(changed))

    def test_annotation_payload_requires_exact_numeric_tokens(self) -> None:
        payload = build_annotation_payload(
            {
                "event_signature": "event-1",
                "event_type": "earnings",
                "subject": "GEV",
                "action": "guidance_raise",
                "score": 70,
                "evidence_count": 1,
                "providers": ["archive"],
                "title": (
                    "GE Vernova Raises FY2026 Sales Guidance from "
                    "$44.000B-$45.000B to $44.500B-$45.500B vs $44.474B Est"
                ),
            }
        )

        self.assertIn("Copy numeric tokens exactly", payload["output_rules"]["summary_ko"])
        self.assertIn("do not convert", payload["output_rules"]["summary_ko"])
        self.assertEqual(payload["company_name_rule"]["event_subject_ticker"], "GEV")
        self.assertIn(
            "ge vernova",
            payload["company_name_rule"]["raw_english_aliases_to_avoid"],
        )

    def test_annotation_payload_includes_earnings_fact_contract(self) -> None:
        payload = build_annotation_payload(
            {
                "event_signature": "event-1",
                "event_type": "earnings",
                "subject": "MRVL",
                "action": "earnings_report",
                "score": 80,
                "evidence_count": 1,
                "providers": ["brave"],
                "title": "Marvell Q1 results",
                "earnings_fact_contract": {
                    "version": "earnings_fact_contract_v1",
                    "status": "ok",
                    "facts": [
                        {"kind": "eps", "label": "EPS", "value": "$0.80"},
                    ],
                },
            }
        )

        self.assertEqual(
            payload["event"]["earnings_fact_contract"]["facts"][0]["value"],
            "$0.80",
        )
        self.assertIn("required numeric anchors", payload["output_rules"]["summary_ko"])

    def test_annotation_payload_includes_merged_event_context(self) -> None:
        payload = build_annotation_payload(
            {
                "event_signature": "event-1",
                "merged_event_signatures": ["event-1", "event-2"],
                "event_type": "earnings",
                "subject": "GEV",
                "action": "earnings_report+guidance_raise",
                "merged_actions": ["earnings_report", "guidance_raise"],
                "score": 86,
                "evidence_count": 2,
                "providers": ["archive"],
                "title": "GE Vernova Q1 beat and FY2026 guidance raise",
            }
        )

        self.assertEqual(
            payload["event"]["merged_event_signatures"],
            ["event-1", "event-2"],
        )
        self.assertEqual(
            payload["event"]["merged_actions"],
            ["earnings_report", "guidance_raise"],
        )

    def test_validate_annotation_rejects_source_name_after_normalization(self) -> None:
        payload = normalize_annotation(
            {
                "summary_ko": "Reuters: NVDA, AI 데이터센터 수요 확대로 실적 후 가이던스 상향",
                "market_marker": "green",
                "confidence": "high",
                "basis": "snippet",
                "reason_ko": "가이던스 상향",
                "source_quote": "raised guidance",
            }
        )

        valid, reason = validate_annotation(
            {
                **payload,
                "summary_ko": "NVDA, Reuters 보도 이후 AI 데이터센터 수요 부각",
            },
            row={"title": "Nvidia raises guidance after Q1 earnings"},
        )

        self.assertEqual(payload["summary_ko"], "NVDA, AI 데이터센터 수요 확대로 실적 후 가이던스 상향")
        self.assertFalse(valid)
        self.assertEqual(reason, "summary_has_source_name")

    def test_validate_annotation_rejects_raw_english_entities(self) -> None:
        valid, reason = validate_annotation(
            {
                "summary_ko": (
                    "Trump-Xi 베이징 정상회담은 성공 평가에도 "
                    "Iran·Taiwan 이견과 무역 교착 가능성이 부각"
                ),
                "market_marker": "none",
                "confidence": "medium",
                "basis": "snippet",
                "reason_ko": "이견 부각",
                "source_quote": "Trump-Xi summit, Iran, Taiwan",
            },
            row={"title": "Trump-Xi summit tests Iran, Taiwan tensions"},
        )

        self.assertFalse(valid)
        self.assertEqual(reason, "summary_has_raw_english")


if __name__ == "__main__":
    unittest.main()
