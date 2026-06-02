from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from news_scanner_v2.cli import main
from news_scanner_v2.db import (
    connect,
    finish_run,
    init_db,
    insert_candidate_items,
    insert_market_snapshot,
    insert_news_seeds,
    insert_run,
)
from news_scanner_v2.delivery import (
    DeliverySafetyError,
    TELEGRAM_TEXT_MAX_CHARS,
    _filter_final_publish_ready_rows,
    _filter_snapshot_conflicting_rows,
    _filter_stale_company_preview_rows,
    build_dry_run_deliveries,
    build_live_deliveries,
    create_dry_run_deliveries,
    create_live_deliveries,
    send_telegram_message,
    select_digest_rows,
    sort_delivery_rows,
)
from news_scanner_v2.models import CandidateItem
from news_scanner_v2.news_seed import build_news_seeds
from test_reports import _build_report_fixture


def _message_row(**overrides):
    row = {
        "decision_id": "decision-1",
        "event_signature": "event-1",
        "run_id": "run-1",
        "decision": "send_candidate",
        "score": 90.0,
        "event_type": "geo",
        "subject": "iran",
        "action": "conflict",
        "effective_date": "2026-05-14",
        "evidence_count": 1,
        "providers": ["brave"],
        "title": "World markets feel the strain as US-Iran war grinds on",
        "url": "https://example.com/news",
    }
    row.update(overrides)
    return row


def _delivery_rows(db_path: Path) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return list(conn.execute("SELECT * FROM deliveries ORDER BY created_at, id"))
    finally:
        conn.close()


class FinalPublishGateTests(unittest.TestCase):
    def test_earnings_summary_missing_available_numbers_is_blocked(self) -> None:
        row = _message_row(
            event_type="earnings",
            subject="NVDA",
            action="earnings_report+guidance_update",
            title="Nvidia revenue $81.6B EPS $1.87 guidance $91B buyback $80B",
            llm_annotation={
                "summary_ko": "NVDA, 실적 예상 상회와 강한 가이던스 제시",
                "market_marker": "green",
                "confidence": "high",
                "basis": "snippet",
            },
            evidence_items=[
                {
                    "title": "Nvidia revenue $81.6B EPS $1.87 guidance $91B buyback $80B",
                    "summary": "Revenue $81.6B, EPS $1.87, guidance $91B and buyback $80B.",
                }
            ],
        )

        ready, dropped = _filter_final_publish_ready_rows([row])

        self.assertEqual(ready, [])
        self.assertEqual(dropped[0]["final_publish_blocked"]["reason"], "summary_missing_earnings_result_numbers")

    def test_earnings_summary_with_contract_numbers_is_ready(self) -> None:
        row = _message_row(
            event_type="earnings",
            subject="MRVL",
            action="earnings_report",
            title="Marvell EPS $0.80 revenue $2.42B",
            llm_annotation={
                "summary_ko": "MRVL, AI 데이터센터 수요로 실적 예상 상회",
                "market_marker": "green",
                "confidence": "high",
                "basis": "snippet",
            },
            evidence_items=[
                {
                    "title": "Marvell EPS $0.80 revenue $2.42B",
                    "summary": "Adjusted EPS $0.80 and revenue $2.42B.",
                }
            ],
            earnings_fact_contract={
                "version": "earnings_fact_contract_v1",
                "status": "ok",
                "facts": [
                    {"kind": "eps", "label": "EPS", "value": "$0.80"},
                    {"kind": "revenue", "label": "매출", "value": "$2.42B"},
                ],
            },
        )

        ready, dropped = _filter_final_publish_ready_rows([row])

        self.assertEqual(len(ready), 1)
        self.assertEqual(dropped, [])

    def test_earnings_summary_with_only_buyback_number_still_requires_result_numbers(self) -> None:
        row = _message_row(
            event_type="earnings",
            subject="NVDA",
            action="earnings_report+guidance_update",
            title="Nvidia revenue $81.6B EPS $1.87 guidance $91B buyback $80B",
            llm_annotation={
                "summary_ko": "NVDA, 매출·EPS 예상 상회와 강한 2분기 가이던스 제시; 자사주 매입 승인 $80B 확대",
                "market_marker": "green",
                "confidence": "high",
                "basis": "snippet",
            },
            evidence_items=[
                {
                    "title": "Nvidia revenue $81.6B EPS $1.87 guidance $91B buyback $80B",
                    "summary": "Revenue $81.6B, EPS $1.87, guidance $91B and buyback $80B.",
                }
            ],
        )

        ready, dropped = _filter_final_publish_ready_rows([row])

        self.assertEqual(ready, [])
        self.assertEqual(dropped[0]["final_publish_blocked"]["reason"], "summary_missing_earnings_result_numbers")

    def test_earnings_summary_with_available_numbers_is_ready(self) -> None:
        row = _message_row(
            event_type="earnings",
            subject="NVDA",
            action="earnings_report+guidance_update",
            title="Nvidia revenue $81.6B EPS $1.87 guidance $91B buyback $80B",
            llm_annotation={
                "summary_ko": "NVDA, 매출 $81.6B·EPS $1.87 기록 후 가이던스 $91B 제시·자사주 $80B 확대",
                "market_marker": "green",
                "confidence": "high",
                "basis": "snippet",
            },
            evidence_items=[
                {
                    "title": "Nvidia revenue $81.6B EPS $1.87 guidance $91B buyback $80B",
                    "summary": "Revenue $81.6B, EPS $1.87, guidance $91B and buyback $80B.",
                }
            ],
        )

        ready, dropped = _filter_final_publish_ready_rows([row])

        self.assertEqual(len(ready), 1)
        self.assertEqual(dropped, [])

    def test_analyst_target_summary_missing_available_target_is_blocked(self) -> None:
        row = _message_row(
            event_type="analyst",
            subject="NVDA",
            action="price_target",
            title="HSBC raises Nvidia price target to $325 from $295",
            llm_annotation={
                "summary_ko": "NVDA, 주요 증권사가 투자의견을 긍정적으로 유지",
                "market_marker": "green",
                "confidence": "medium",
                "basis": "snippet",
            },
            evidence_items=[
                {
                    "title": "HSBC raises Nvidia price target to $325 from $295",
                    "summary": "HSBC raised its price target on Nvidia to $325 from $295.",
                }
            ],
        )

        ready, dropped = _filter_final_publish_ready_rows([row])

        self.assertEqual(ready, [])
        self.assertEqual(dropped[0]["final_publish_blocked"]["reason"], "summary_missing_analyst_target")

    def test_generic_company_summary_is_blocked(self) -> None:
        row = _message_row(
            event_type="earnings",
            subject="INTU",
            action="earnings_report",
            title="Intuit reports Q3 results",
            llm_annotation={
                "summary_ko": "INTU 실적 관련 신규 이슈",
                "market_marker": "none",
                "confidence": "medium",
                "basis": "title",
            },
        )

        ready, dropped = _filter_final_publish_ready_rows([row])

        self.assertEqual(ready, [])
        self.assertEqual(dropped[0]["final_publish_blocked"]["reason"], "generic_final_summary")


class FakeLLMClient:
    def create_annotation(self, payload):
        return {
            "summary_ko": "NVDA, AI 데이터센터 수요 확대로 실적 후 가이던스 상향",
            "market_marker": "green",
            "confidence": "high",
            "basis": "snippet",
            "reason_ko": "가이던스 상향과 AI 수요가 근거",
            "source_quote": "raised guidance after earnings",
        }


class FakeInvalidSummaryClient:
    def create_annotation(self, payload):
        return {
            "summary_ko": "NVDA, 실적 후 가이던스 20% 상향",
            "market_marker": "green",
            "confidence": "medium",
            "basis": "title",
            "reason_ko": "증거에 없는 수치를 넣은 잘못된 요약",
            "source_quote": "raises guidance after earnings",
        }


class FakeDropEditorClient:
    def create_editorial(self, payload):
        return {
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


class FakeThemeEditorClient:
    def __init__(self):
        self.calls = []

    def create_theme_editorial(self, payload):
        self.calls.append(payload)
        evidence_ids = [
            item["evidence_id"]
            for item in payload["evidence_items"]
            if item.get("evidence_id")
        ][:2]
        return {
            "decision": "send",
            "grade": "B",
            "drop_reason": "",
            "summary_ko": (
                "반도체주 하락세 지속 — NVDA 약세와 차익실현, "
                "금리 부담이 섹터 압박으로 확산"
            ),
            "market_marker": "red",
            "confidence": "medium",
            "basis": "snippet",
            "reason_ko": "두 개 이상 근거가 반도체 약세와 금리 부담을 동시에 지지",
            "source_hint": "Yahoo/CNBC",
            "evidence_ids": evidence_ids,
            "claim_atoms": [
                {"text": "NVDA 약세", "evidence_id": evidence_ids[0]},
                {"text": "반도체 차익실현", "evidence_id": evidence_ids[1]},
            ],
            "risk_flags": ["theme_synthesis"],
        }


class FakeSeedThemeEditorClient:
    def __init__(self):
        self.calls = []

    def create_theme_editorial(self, payload):
        self.calls.append(payload)
        evidence_ids = [
            item["evidence_id"]
            for item in payload["evidence_items"]
            if item.get("evidence_id")
        ][:1]
        return {
            "decision": "send",
            "grade": "B",
            "drop_reason": "",
            "summary_ko": (
                "AI 인프라 투자 확대 — GOOGL·블랙스톤 클라우드 합작으로 "
                "코어위브 경쟁 구도 부각"
            ),
            "market_marker": "green",
            "confidence": "medium",
            "basis": "snippet",
            "reason_ko": "시드 근거가 AI 인프라 JV와 TPU 클라우드를 지지",
            "source_hint": "Reuters",
            "evidence_ids": evidence_ids,
            "claim_atoms": [
                {"text": "AI infrastructure JV", "evidence_id": evidence_ids[0]}
            ],
            "risk_flags": ["seed_synthesis"],
        }


class FakeInvalidThemeEditorClient:
    def create_theme_editorial(self, payload):
        evidence_ids = [
            item["evidence_id"]
            for item in payload["evidence_items"]
            if item.get("evidence_id")
        ][:1]
        return {
            "decision": "send",
            "grade": "B",
            "drop_reason": "",
            "summary_ko": "AI infrastructure JV가 클라우드 compute 경쟁을 확대",
            "market_marker": "green",
            "confidence": "medium",
            "basis": "snippet",
            "reason_ko": "영문 원문이 남아 있어 검증에서 탈락해야 함",
            "source_hint": "Reuters",
            "evidence_ids": evidence_ids,
            "claim_atoms": [
                {"text": "AI infrastructure JV", "evidence_id": evidence_ids[0]}
            ],
            "risk_flags": [],
        }


def _build_theme_only_fixture(root: Path) -> tuple[Path, str]:
    db_path = root / "state" / "news_scanner_v2.sqlite"
    init_db(db_path)
    run_id = "theme-run"
    started_at = "2026-05-19T22:30:00+09:00"
    with connect(db_path) as conn:
        insert_run(
            conn,
            run_id=run_id,
            started_at=started_at,
            as_of=started_at,
            mode="live",
            dispatch_enabled=True,
            llm_enabled=True,
            legacy_prompt_hash=None,
            legacy_snapshot={},
        )
        source_a = CandidateItem(
            source="brave-news-largecap-movers",
            provider="brave",
            category="MOVE",
            title=(
                "Stock market today: Dow, S&P 500, Nasdaq futures slide as "
                "rising yields keep up pressure"
            ),
            url="https://finance.yahoo.com/markets/live/stock-market-today.html",
            published_at="2026-05-19T12:48:34+00:00",
            summary=(
                "Nvidia fell again in premarket trading, extending losses, "
                "while Samsung Electronics and SK Hynix tracked losses."
            ),
        )
        source_b = CandidateItem(
            source="brave-news-largecap-movers",
            provider="brave",
            category="MOVE",
            title="Stock market today: Live updates",
            url="https://www.cnbc.com/2026/05/18/stock-market-today-live-updates.html",
            published_at="2026-05-18T22:01:21+00:00",
            summary=(
                "Investors are using semiconductors as a short-term ATM and "
                "strategically taking profits after a parabolic surge in chipmakers."
            ),
        )
        insert_candidate_items(
            conn,
            run_id=run_id,
            fetched_at=started_at,
            items=[source_a.as_record(run_id), source_b.as_record(run_id)],
        )
        finish_run(conn, run_id=run_id, status="ok", finished_at=started_at)
    return db_path, run_id


def _build_seed_only_fixture(root: Path) -> tuple[Path, str]:
    db_path = root / "state" / "news_scanner_v2.sqlite"
    init_db(db_path)
    run_id = "seed-run"
    started_at = "2026-05-20T15:00:00+09:00"
    raw_items = [
        {
            "id": "ai-a",
            "category": "STRAT",
            "provider": "brave",
            "source": "brave-discovery-1-strat",
            "title": (
                "Google and Blackstone launch $5B TPU cloud AI infrastructure "
                "joint venture to rival CoreWeave"
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
            run_id=run_id,
            started_at=started_at,
            as_of=started_at,
            mode="live",
            dispatch_enabled=True,
            llm_enabled=True,
            legacy_prompt_hash=None,
            legacy_snapshot={},
        )
        insert_news_seeds(
            conn,
            run_id=run_id,
            created_at=started_at,
            seeds=seeds,
        )
        finish_run(conn, run_id=run_id, status="ok", finished_at=started_at)
    return db_path, run_id


class DryRunDeliveryTests(unittest.TestCase):
    def test_create_dry_run_delivery_records_send_candidates_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config, summary = _build_report_fixture(Path(tmp))

            result = create_dry_run_deliveries(config.db_path)

            self.assertEqual(result["run"]["id"], summary["run_id"])
            self.assertEqual(result["mode"], "dry-run")
            self.assertEqual(result["channel"], "telegram")
            self.assertEqual(result["requested"], 1)
            self.assertEqual(result["inserted"], 1)
            self.assertEqual(result["skipped_existing"], 0)
            rows = _delivery_rows(config.db_path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["run_id"], summary["run_id"])
            self.assertEqual(rows[0]["channel"], "telegram")
            self.assertEqual(rows[0]["status"], "dry_run")
            self.assertIsNone(rows[0]["message_id"])
            payload = json.loads(rows[0]["payload_json"])
            self.assertEqual(payload["mode"], "dry-run")
            self.assertEqual(payload["decision"], "send_candidate")
            self.assertEqual(payload["safety"]["status"], "ok")
            self.assertIsNone(payload["message"]["parse_mode"])
            self.assertIn("[실적] NVDA", payload["message"]["text"])
            self.assertIn("가이던스 상향", payload["message"]["text"])
            self.assertNotIn("https://example.com", payload["message"]["text"])

    def test_create_dry_run_delivery_records_are_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config, _summary = _build_report_fixture(Path(tmp))

            first = create_dry_run_deliveries(config.db_path)
            second = create_dry_run_deliveries(config.db_path)

            self.assertEqual(first["inserted"], 1)
            self.assertEqual(second["requested"], 1)
            self.assertEqual(second["inserted"], 0)
            self.assertEqual(second["skipped_existing"], 1)
            self.assertEqual(len(_delivery_rows(config.db_path)), 1)

    def test_create_dry_run_applies_digest_cap_after_loading_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "delivery.sqlite"
            init_db(db_path)
            started_at = "2026-05-29T17:00:00+09:00"
            with connect(db_path) as conn:
                insert_run(
                    conn,
                    run_id="run-1",
                    started_at=started_at,
                    as_of=started_at,
                    mode="dry-run",
                    dispatch_enabled=True,
                    llm_enabled=False,
                    legacy_prompt_hash=None,
                    legacy_snapshot={},
                )
                finish_run(conn, run_id="run-1", status="ok", finished_at=started_at)
            rows = [
                _message_row(
                    event_signature="iran-1",
                    subject="iran",
                    action="conflict",
                    object="military_escalation",
                    score=100,
                ),
                _message_row(
                    event_signature="iran-2",
                    subject="iran",
                    action="policy_geo",
                    object="hormuz_toll_regime",
                    score=99,
                ),
                _message_row(
                    event_signature="iran-3",
                    subject="iran",
                    action="sanctions",
                    object="sanctions_enforcement",
                    score=98,
                ),
                _message_row(
                    event_signature="iran-4",
                    subject="iran",
                    action="diplomacy",
                    object="summit_diplomacy",
                    score=97,
                ),
                _message_row(
                    event_signature="dell-earnings",
                    event_type="earnings",
                    subject="dell",
                    action="guidance_update",
                    title="Dell Q1 results and guidance top estimates",
                    score=86,
                ),
            ]
            report = {
                "run": {
                    "id": "run-1",
                    "as_of": "2026-05-29T17:00:00+09:00",
                    "status": "ok",
                },
                "rows": rows,
            }

            with patch(
                "news_scanner_v2.delivery.load_decision_rows", return_value=report
            ), patch(
                "news_scanner_v2.delivery._filter_contract_eligible_rows",
                return_value=(rows, []),
            ), patch(
                "news_scanner_v2.delivery._theme_rows_for_run",
                return_value=([], {"status": "disabled"}),
            ):
                result = create_dry_run_deliveries(db_path, limit=5)

            self.assertEqual(result["requested"], 4)
            payloads = [json.loads(row["payload_json"]) for row in _delivery_rows(db_path)]
            messages = [payload["message"] for payload in payloads]
            signatures = [message["event_signature"] for message in messages]
            self.assertEqual(len([sig for sig in signatures if sig.startswith("iran-")]), 3)
            self.assertIn("dell-earnings", signatures)
            self.assertNotIn("iran-4", signatures)

    def test_create_dry_run_blocks_failed_evidence_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config, _summary = _build_report_fixture(Path(tmp))
            con = sqlite3.connect(config.db_path)
            try:
                row = con.execute(
                    """
                    SELECT id, payload_json
                    FROM dispatch_decisions
                    WHERE decision = 'send_candidate'
                    LIMIT 1
                    """
                ).fetchone()
                payload = json.loads(row[1])
                payload["source_tier"] = "low_quality"
                payload["low_quality_domains"] = ["example.com"]
                con.execute(
                    "UPDATE dispatch_decisions SET payload_json = ? WHERE id = ?",
                    (json.dumps(payload), row[0]),
                )
                con.commit()
            finally:
                con.close()

            result = create_dry_run_deliveries(config.db_path)

            self.assertEqual(result["requested"], 0)
            self.assertEqual(result["inserted"], 0)
            self.assertEqual(result["contract"]["blocked"], 1)
            self.assertEqual(
                result["contract"]["failure_counts"],
                {"low_quality_source": 1},
            )
            self.assertEqual(len(_delivery_rows(config.db_path)), 0)

    def test_cli_deliver_dry_run_writes_summary_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config, _summary = _build_report_fixture(root)
            output = root / "deliveries" / "summary.json"

            exit_code = main(
                [
                    "deliver",
                    "--mode",
                    "dry-run",
                    "--db-path",
                    str(config.db_path),
                    "--output",
                    str(output),
                ]
            )

            self.assertEqual(exit_code, 0)
            summary = json.loads(output.read_text())
            self.assertEqual(summary["inserted"], 1)
            self.assertEqual(len(_delivery_rows(config.db_path)), 1)

    def test_cli_deliver_missing_db_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stderr = io.StringIO()
            stdout = io.StringIO()

            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main(
                    [
                        "deliver",
                        "--mode",
                        "dry-run",
                        "--db-path",
                        str(Path(tmp) / "missing.sqlite"),
                    ]
                )

            self.assertEqual(exit_code, 1)
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn("DB does not exist", stderr.getvalue())

    def test_delivery_rejects_unsupported_channel(self) -> None:
        with self.assertRaisesRegex(DeliverySafetyError, "unsupported"):
            build_dry_run_deliveries([_message_row()], channel="email")

    def test_delivery_rejects_message_over_telegram_limit(self) -> None:
        long_subject = "A" * (TELEGRAM_TEXT_MAX_CHARS + 1)

        with self.assertRaisesRegex(DeliverySafetyError, "too long"):
            build_dry_run_deliveries([_message_row(subject=long_subject)])

    def test_delivery_rejects_control_characters(self) -> None:
        with self.assertRaisesRegex(DeliverySafetyError, "control"):
            build_dry_run_deliveries([_message_row(subject="bad\x00subject")])

    def test_build_live_deliveries_uses_event_level_ids_and_single_digest(self) -> None:
        first = build_live_deliveries([_message_row(run_id="run-a")])
        second = build_live_deliveries([_message_row(run_id="run-b")])

        self.assertEqual(first[0].status, "sent")
        self.assertEqual(first[0].id, second[0].id)
        self.assertTrue(
            first[0].payload["message"]["text"].startswith("📰 미국증시 뉴스")
        )
        self.assertIn("• [A] 🔴", first[0].payload["message"]["text"])
        self.assertIn("미-이란 전쟁 장기화", first[0].payload["message"]["text"])
        self.assertIn("[QC] V2", first[0].payload["message"]["text"])

    def test_build_live_deliveries_groups_multiple_events_into_same_message(self) -> None:
        rows = [
            _message_row(event_signature="event-1"),
            _message_row(
                decision_id="decision-2",
                event_signature="event-2",
                score=76,
                action="policy_geo",
                title="‘Unblock Hormuz Or…’: Marco Rubio Urges China To Act Against Iran Or Face Export Collapse - Times of India",
            ),
        ]

        deliveries = build_live_deliveries(
            rows,
            run={"id": "run-1", "as_of": "2026-05-15T11:00:00+09:00"},
            skipped_previously_sent=3,
        )

        self.assertEqual(len(deliveries), 2)
        self.assertNotEqual(deliveries[0].id, deliveries[1].id)
        self.assertEqual(
            deliveries[0].payload["message"]["text"],
            deliveries[1].payload["message"]["text"],
        )
        text = deliveries[0].payload["message"]["text"]
        self.assertIn("📰 미국증시 뉴스 (11:00 KST)", text)
        self.assertIn("• [A] 🔴 미-이란 전쟁 장기화", text)
        self.assertIn("• [B] 루비오, 중국에 호르무즈 압박 요구", text)
        self.assertIn("중복-제외:3", text)

    def test_build_live_deliveries_sends_empty_digest_for_no_new_events(self) -> None:
        deliveries = build_live_deliveries(
            [],
            run={"id": "run-1", "as_of": "2026-05-15T15:00:00+09:00"},
            skipped_previously_sent=7,
        )

        self.assertEqual(len(deliveries), 1)
        self.assertEqual(deliveries[0].run_id, "run-1")
        self.assertIsNone(deliveries[0].event_signature)
        self.assertEqual(deliveries[0].payload["digest_size"], 0)
        text = deliveries[0].payload["message"]["text"]
        self.assertIn("📰 미국증시 뉴스 (15:00 KST)", text)
        self.assertIn("✅ 특이사항 없음", text)
        self.assertIn("[QC] V2 A:0 B:0 신규총:0 중복-제외:7", text)

    def test_build_live_deliveries_adds_market_snapshot_to_digest(self) -> None:
        deliveries = build_live_deliveries(
            [],
            run={"id": "run-1", "as_of": "2026-05-21T22:30:00+09:00"},
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

        text = deliveries[0].payload["message"]["text"]
        self.assertIn("📊 지수: S&P 7,433", text)
        self.assertIn("💰 매크로: WTI $97.8", text)
        self.assertIn("💱 환율: USD/KRW 1,504", text)

    def test_create_live_deliveries_does_not_promote_market_snapshot_only_move(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "state" / "news_scanner_v2.sqlite"
            init_db(db_path)
            run_id = "snapshot-run"
            as_of = "2026-05-21T22:30:00+09:00"
            with connect(db_path) as conn:
                insert_run(
                    conn,
                    run_id=run_id,
                    started_at=as_of,
                    as_of=as_of,
                    mode="live",
                    dispatch_enabled=True,
                    llm_enabled=False,
                    legacy_prompt_hash=None,
                    legacy_snapshot={},
                )
                insert_market_snapshot(
                    conn,
                    run_id=run_id,
                    as_of=as_of,
                    created_at=as_of,
                    snapshot={
                        "as_of": as_of,
                        "status": "ok",
                        "providers": ["fmp"],
                        "values": {
                            "brent": {
                                "status": "ok",
                                "provider": "fmp",
                                "value": 97.49,
                                "change_pct": -5.84315,
                            }
                        },
                    },
                )
                finish_run(conn, run_id=run_id, status="ok", finished_at=as_of)

            with patch(
                "news_scanner_v2.delivery.send_telegram_message",
                return_value="1001",
            ) as send:
                result = create_live_deliveries(
                    db_path,
                    bot_token="token",
                    chat_id="123456789",
                    run_id=run_id,
                )

            self.assertEqual(result["requested"], 0)
            self.assertEqual(result["selected"], 0)
            self.assertEqual(result["market_snapshot_hard_events"], 0)
            text = send.call_args.kwargs["message"]["text"]
            self.assertIn("✅ 특이사항 없음", text)
            self.assertIn("Brent $97.5", text)
            self.assertNotIn("시장 데이터 단독 변동", text)
            self.assertNotIn("뉴스 촉매 미확인", text)

    def test_snapshot_conflict_filter_drops_oil_direction_mismatch(self) -> None:
        rows = [
            _message_row(
                event_signature="old-oil-slide",
                event_type="geo",
                subject="iran",
                action="policy_geo",
                title="Oil prices slide after Trump says Iran talks are in final stages",
                llm_annotation={
                    "summary_ko": "이란 협상 진전 기대에 유가 하락",
                    "market_marker": "green",
                    "confidence": "medium",
                    "basis": "snippet",
                },
            ),
            _message_row(
                event_signature="iran-talks",
                event_type="geo",
                subject="iran",
                action="policy_geo",
                title="Iran reviews U.S. peace proposal as Trump waits a few days",
            ),
        ]

        kept, dropped = _filter_snapshot_conflicting_rows(
            rows,
            {
                "values": {
                    "brent": {
                        "status": "ok",
                        "value": 108.62,
                        "change_pct": 3.42,
                    }
                }
            },
        )

        self.assertEqual([row["event_signature"] for row in kept], ["iran-talks"])
        self.assertEqual(
            [row["event_signature"] for row in dropped],
            ["old-oil-slide"],
        )

    def test_stale_earnings_preview_filter_drops_after_report_exists(self) -> None:
        rows = [
            _message_row(
                event_signature="nvda-preview",
                event_type="earnings",
                subject="nvda",
                action="guidance_update",
                title="Nvidia climbs ahead of earnings as investors await AI guidance",
            ),
            _message_row(
                event_signature="nvda-report",
                event_type="earnings",
                subject="nvda",
                action="earnings_report",
                title="Nvidia forecasts revenue above estimates, announces $80 billion share buyback",
            ),
        ]

        kept, dropped = _filter_stale_company_preview_rows(rows)

        self.assertEqual([row["event_signature"] for row in kept], ["nvda-report"])
        self.assertEqual(
            [row["event_signature"] for row in dropped],
            ["nvda-preview"],
        )

    def test_select_digest_rows_merges_same_company_earnings_cluster(self) -> None:
        rows = [
            _message_row(
                event_signature="ual-earnings",
                event_type="earnings",
                subject="ual",
                action="earnings_report",
                score=90,
            ),
            _message_row(
                event_signature="ual-guidance",
                event_type="earnings",
                subject="ual",
                action="guidance_cut",
                score=88,
            ),
            _message_row(
                event_signature="txn-earnings",
                event_type="earnings",
                subject="txn",
                action="earnings_report",
                score=70,
            ),
        ]

        selected = select_digest_rows(rows, limit=7)

        self.assertEqual(
            [row["event_signature"] for row in selected],
            ["ual-earnings", "txn-earnings"],
        )
        self.assertEqual(
            selected[0]["merged_event_signatures"],
            ["ual-earnings", "ual-guidance"],
        )
        self.assertEqual(
            selected[0]["merged_actions"],
            ["earnings_report", "guidance_cut"],
        )

    def test_select_digest_rows_merges_duplicate_company_mechanisms(self) -> None:
        rows = [
            _message_row(
                event_signature="nvda-earnings-a",
                event_type="earnings",
                subject="nvda",
                action="earnings_report",
                title="Nvidia reports earnings beat",
                score=90,
            ),
            _message_row(
                event_signature="nvda-earnings-b",
                event_type="earnings",
                subject="nvda",
                action="earnings_report",
                title="Nvidia posts record quarterly revenue",
                score=88,
            ),
        ]

        selected = select_digest_rows(rows, limit=7)

        self.assertEqual(len(selected), 1)
        self.assertEqual(
            selected[0]["merged_event_signatures"],
            ["nvda-earnings-a", "nvda-earnings-b"],
        )



    def test_select_digest_rows_keeps_atomic_rescue_beyond_limit(self) -> None:
        rows = [
            _message_row(
                event_signature=f"ordinary-{index}",
                event_type="earnings",
                subject=f"ticker{index}",
                action="earnings_report",
                score=90 - index,
            )
            for index in range(7)
        ]
        rows.append(
            _message_row(
                event_signature="iran-talks-halt",
                event_type="geo",
                subject="iran",
                action="diplomacy",
                object="ceasefire_talks",
                atomic_digest=True,
                rescue_type="geo_fresh_delta",
                title="Iran negotiating team halts indirect messages with U.S. mediators",
                score=74,
            )
        )

        selected = select_digest_rows(rows, limit=7)

        self.assertEqual(len(selected), 8)
        self.assertIn(
            "iran-talks-halt",
            [row["event_signature"] for row in selected],
        )

    def test_select_digest_rows_does_not_merge_atomic_rescue_rows(self) -> None:
        rows = [
            _message_row(
                event_signature="iran-talks-halt",
                event_type="geo",
                subject="iran",
                action="diplomacy",
                object="ceasefire_talks",
                atomic_digest=True,
                rescue_type="geo_fresh_delta",
                title="Iran negotiating team halts indirect messages with U.S. mediators",
                score=78,
            ),
            _message_row(
                event_signature="iran-kuwait-missile",
                event_type="geo",
                subject="iran",
                action="conflict",
                object="ceasefire_talks",
                atomic_digest=True,
                rescue_type="geo_fresh_delta",
                title="Kuwait says missile fired after Iran ceasefire talks stall",
                score=76,
            ),
        ]

        selected = select_digest_rows(rows, limit=7)

        self.assertEqual(
            [row["event_signature"] for row in selected],
            ["iran-talks-halt", "iran-kuwait-missile"],
        )
        self.assertNotIn("merged_event_signatures", selected[0])

    def test_select_digest_rows_keeps_geo_state_dates_separate(self) -> None:
        rows = [
            _message_row(
                event_signature="iran-oil-slide",
                event_type="geo",
                subject="iran",
                action="policy_geo",
                effective_date="2026-05-20",
                title="Oil prices slide after Trump says Iran talks are in final stages",
                score=76,
            ),
            _message_row(
                event_signature="iran-wait",
                event_type="geo",
                subject="iran",
                action="policy_geo",
                effective_date="2026-05-21",
                title="Iran reviews U.S. peace proposal as Trump says he will wait a few days",
                score=81,
            ),
        ]

        selected = select_digest_rows(rows, limit=7)

        self.assertEqual(
            [row["event_signature"] for row in selected],
            ["iran-oil-slide", "iran-wait"],
        )

    def test_select_digest_rows_merges_hormuz_toll_cluster_across_actions(self) -> None:
        rows = [
            _message_row(
                event_signature="iran-hormuz-toll-talks",
                event_type="geo",
                subject="iran",
                action="diplomacy",
                object="hormuz_toll_regime",
                effective_date="2026-05-21",
                title="US-Iran peace efforts face setbacks over Hormuz tolls",
                score=82,
            ),
            _message_row(
                event_signature="iran-hormuz-toll-warning",
                event_type="geo",
                subject="iran",
                action="policy_geo",
                object="hormuz_toll_regime",
                effective_date="2026-05-21",
                title="Rubio warns Iran against Strait of Hormuz toll system",
                score=79,
            ),
        ]

        selected = select_digest_rows(rows, limit=7)

        self.assertEqual(len(selected), 1)
        self.assertEqual(
            selected[0]["merged_event_signatures"],
            ["iran-hormuz-toll-talks", "iran-hormuz-toll-warning"],
        )
        self.assertEqual(
            selected[0]["merged_actions"],
            ["diplomacy", "policy_geo"],
        )

    def test_select_digest_rows_merges_iran_state_updates_across_generic_objects(self) -> None:
        rows = [
            _message_row(
                event_signature="iran-peace-wait",
                event_type="geo",
                subject="iran",
                action="diplomacy",
                object="ceasefire_talks",
                effective_date="2026-05-22",
                title="Iran reviews U.S. peace proposal as Trump waits for reply",
                score=83,
            ),
            _message_row(
                event_signature="iran-oil-pressure",
                event_type="geo",
                subject="iran",
                action="policy_geo",
                object="market_pressure",
                effective_date="2026-05-22",
                title="Oil markets remain pressured by Iran talks uncertainty",
                score=79,
            ),
            _message_row(
                event_signature="iran-sanctions",
                event_type="geo",
                subject="iran",
                action="sanctions",
                object="sanctions_enforcement",
                effective_date="2026-05-22",
                title="Treasury imposes new Iran sanctions on shadow fleet",
                score=78,
            ),
        ]

        selected = select_digest_rows(rows, limit=7)

        self.assertEqual(
            [row["event_signature"] for row in selected],
            ["iran-peace-wait", "iran-sanctions"],
        )
        self.assertEqual(
            selected[0]["merged_event_signatures"],
            ["iran-peace-wait", "iran-oil-pressure"],
        )

    def test_select_digest_rows_caps_same_geo_subject_at_three(self) -> None:
        rows = [
            _message_row(
                event_signature="iran-state",
                event_type="geo",
                subject="iran",
                action="diplomacy",
                object="ceasefire_talks",
                effective_date="2026-05-22",
                title="Iran reviews U.S. peace proposal",
                score=96,
            ),
            _message_row(
                event_signature="iran-sanctions",
                event_type="geo",
                subject="iran",
                action="sanctions",
                object="sanctions_enforcement",
                effective_date="2026-05-22",
                title="Treasury imposes new Iran sanctions",
                score=95,
            ),
            _message_row(
                event_signature="iran-hormuz",
                event_type="geo",
                subject="iran",
                action="policy_geo",
                object="hormuz_toll_regime",
                effective_date="2026-05-22",
                title="Rubio warns Iran against Hormuz tolls",
                score=94,
            ),
            _message_row(
                event_signature="iran-oil",
                event_type="geo",
                subject="iran",
                action="conflict",
                object="oil_market_pressure",
                effective_date="2026-05-22",
                title="Oil traders price renewed Iran conflict risk",
                score=93,
            ),
            _message_row(
                event_signature="dell-earnings",
                event_type="earnings",
                subject="dell",
                action="guidance_update",
                title="Dell Q1 results and AI server guidance top estimates",
                score=86,
            ),
        ]

        selected = select_digest_rows(rows, limit=5)

        self.assertEqual(
            [row["event_signature"] for row in selected],
            ["iran-state", "iran-sanctions", "iran-hormuz", "dell-earnings"],
        )

    def test_select_digest_rows_does_not_compound_merged_action_strings(self) -> None:
        rows = [
            _message_row(
                event_signature="iran-state-a",
                event_type="geo",
                subject="iran",
                action="conflict",
                object="iran_energy_supply",
                effective_date="2026-05-22",
                title="Iran war keeps energy markets under pressure",
            ),
            _message_row(
                event_signature="iran-state-b",
                event_type="geo",
                subject="iran",
                action="diplomacy",
                object="iran_ceasefire_talks",
                effective_date="2026-05-22",
                title="Iran talks show slight movement",
            ),
            _message_row(
                event_signature="iran-state-c",
                event_type="geo",
                subject="iran",
                action="conflict",
                object="market_pressure",
                effective_date="2026-05-22",
                title="Oil market still pressured by Iran war",
            ),
        ]

        selected = select_digest_rows(rows, limit=7)

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["action"], "conflict+diplomacy")
        self.assertEqual(selected[0]["merged_actions"], ["conflict", "diplomacy"])

    def test_select_digest_rows_preserves_llm_editorial_summary_when_merging(self) -> None:
        rows = [
            _message_row(
                event_signature="iran-attack-cancelled",
                grade="A",
                score=95,
                llm_annotation={
                    "summary_ko": "이란, 트럼프가 협상 진행을 이유로 예정된 군사공격 취소",
                    "market_marker": "green",
                    "confidence": "high",
                    "basis": "body",
                },
                llm_editorial={
                    "decision": "send",
                    "source_hint": "AP 5/18",
                },
            ),
            _message_row(
                event_signature="iran-talks",
                grade="B",
                score=85,
                llm_annotation={
                    "summary_ko": "이란, 중동 동맹 요청에 군사공격 보류",
                    "market_marker": "green",
                    "confidence": "high",
                    "basis": "body",
                },
                llm_editorial={
                    "decision": "send",
                    "source_hint": "The Hindu 5/19",
                },
            ),
        ]

        selected = select_digest_rows(rows, limit=7)
        deliveries = build_live_deliveries(
            selected,
            run={"id": "run-1", "as_of": "2026-05-19T16:00:00+09:00"},
        )

        text = deliveries[0].payload["message"]["text"]
        self.assertEqual(len(selected), 1)
        self.assertIn("이란, 트럼프가 협상 진행을 이유", text)
        self.assertIn("(AP 5/18)", text)
        self.assertNotIn("이란 리스크", text)

    def test_build_live_deliveries_marks_all_merged_event_signatures(self) -> None:
        selected = select_digest_rows(
            [
                _message_row(
                    event_signature="ual-earnings",
                    event_type="earnings",
                    subject="ual",
                    action="earnings_report",
                    score=90,
                ),
                _message_row(
                    event_signature="ual-earnings-update",
                    event_type="earnings",
                    subject="ual",
                    action="earnings_report",
                    score=88,
                ),
            ],
            limit=7,
        )

        deliveries = build_live_deliveries(selected)

        self.assertEqual(
            [delivery.event_signature for delivery in deliveries],
            ["ual-earnings", "ual-earnings-update"],
        )
        for delivery in deliveries:
            self.assertEqual(
                delivery.payload["merged_event_signatures"],
                ["ual-earnings", "ual-earnings-update"],
            )

    def test_create_live_deliveries_sends_once_per_event_signature(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config, summary = _build_report_fixture(Path(tmp))

            with patch(
                "news_scanner_v2.delivery.send_telegram_message",
                return_value="1001",
            ) as send:
                first = create_live_deliveries(
                    config.db_path,
                    bot_token="token",
                    chat_id="123456789",
                    run_id=summary["run_id"],
                    llm_enabled=True,
                    llm_client=FakeLLMClient(),
                )
                second = create_live_deliveries(
                    config.db_path,
                    bot_token="token",
                    chat_id="123456789",
                    run_id=summary["run_id"],
                    llm_enabled=True,
                    llm_client=FakeLLMClient(),
                )

            self.assertEqual(first["requested"], 1)
            self.assertEqual(first["selected"], 1)
            self.assertEqual(first["sent"], 1)
            self.assertEqual(first["inserted"], 1)
            self.assertEqual(first["message_ids"], ["1001"])
            self.assertEqual(second["requested"], 1)
            self.assertEqual(second["selected"], 0)
            self.assertEqual(second["sent"], 0)
            self.assertEqual(second["skipped_previously_sent"], 1)
            self.assertEqual(send.call_count, 1)

            rows = _delivery_rows(config.db_path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["status"], "sent")
            self.assertEqual(rows[0]["message_id"], "1001")

    def test_create_live_deliveries_blocks_company_fallback_without_llm_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config, summary = _build_report_fixture(Path(tmp))

            with patch(
                "news_scanner_v2.delivery.send_telegram_message",
                return_value="1001",
            ) as send:
                result = create_live_deliveries(
                    config.db_path,
                    bot_token="token",
                    chat_id="123456789",
                    run_id=summary["run_id"],
                    llm_enabled=False,
                )

            self.assertEqual(result["final_publish_dropped"], 1)
            self.assertEqual(result["selected"], 0)
            sent_message = send.call_args.kwargs["message"]["text"]
            self.assertIn("✅ 특이사항 없음", sent_message)
            self.assertNotIn("실적 관련 신규 이슈", sent_message)

    def test_create_live_deliveries_can_use_llm_annotation_for_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config, summary = _build_report_fixture(Path(tmp))

            with patch(
                "news_scanner_v2.delivery.send_telegram_message",
                return_value="1001",
            ) as send:
                result = create_live_deliveries(
                    config.db_path,
                    bot_token="token",
                    chat_id="123456789",
                    run_id=summary["run_id"],
                    llm_enabled=True,
                    llm_model="base-model",
                    llm_editorial_model="editor-model",
                    llm_theme_editor_model="theme-model",
                    llm_summary_model="summary-model",
                    llm_client=FakeLLMClient(),
                )

            self.assertEqual(
                result["llm_models"],
                {
                    "base": "base-model",
                    "editorial": "editor-model",
                    "summary": "summary-model",
                    "theme_editor": "theme-model",
                },
            )
            self.assertEqual(result["llm_annotation"]["accepted"], 1)
            sent_message = send.call_args.kwargs["message"]["text"]
            self.assertIn("🟢 NVDA, AI 데이터센터 수요", sent_message)

            rows = _delivery_rows(config.db_path)
            payload = json.loads(rows[0]["payload_json"])
            self.assertEqual(
                payload["message"]["summary_basis_counts"],
                {"llm_snippet": 1},
            )

    def test_create_live_deliveries_blocks_validation_rejected_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config, summary = _build_report_fixture(Path(tmp))

            with patch(
                "news_scanner_v2.delivery.send_telegram_message",
                return_value="1001",
            ) as send:
                result = create_live_deliveries(
                    config.db_path,
                    bot_token="token",
                    chat_id="123456789",
                    run_id=summary["run_id"],
                    llm_enabled=True,
                    llm_client=FakeInvalidSummaryClient(),
                )

            self.assertEqual(result["llm_annotation"]["rejected"], 1)
            self.assertEqual(result["llm_summary_rejected"], 1)
            self.assertEqual(result["selected"], 0)
            sent_message = send.call_args.kwargs["message"]["text"]
            self.assertIn("✅ 특이사항 없음", sent_message)
            self.assertNotIn("실적 관련 신규 이슈", sent_message)

            rows = _delivery_rows(config.db_path)
            self.assertEqual(len(rows), 1)
            self.assertIsNone(rows[0]["event_signature"])

    def test_create_live_deliveries_uses_editorial_drop_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config, summary = _build_report_fixture(Path(tmp))

            with patch(
                "news_scanner_v2.delivery.send_telegram_message",
                return_value="1001",
            ) as send:
                result = create_live_deliveries(
                    config.db_path,
                    bot_token="token",
                    chat_id="123456789",
                    run_id=summary["run_id"],
                    llm_enabled=True,
                    llm_client=FakeDropEditorClient(),
                )

            self.assertEqual(result["llm_editorial"]["decisions"]["drop"], 1)
            self.assertEqual(result["llm_annotation"]["requested"], 0)
            self.assertEqual(result["selected"], 0)
            self.assertEqual(result["sent"], 1)
            sent_message = send.call_args.kwargs["message"]["text"]
            self.assertIn("✅ 특이사항 없음", sent_message)

            rows = _delivery_rows(config.db_path)
            self.assertEqual(len(rows), 1)
            self.assertIsNone(rows[0]["event_signature"])
            con = sqlite3.connect(config.db_path)
            try:
                annotations = con.execute(
                    "select annotation_type, status from llm_annotations"
                ).fetchall()
            finally:
                con.close()
            self.assertEqual(annotations, [("editorial", "ok")])

    def test_create_live_deliveries_adds_llm_theme_when_no_event_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path, run_id = _build_theme_only_fixture(Path(tmp))
            client = FakeThemeEditorClient()

            with patch(
                "news_scanner_v2.delivery.send_telegram_message",
                return_value="1001",
            ) as send:
                result = create_live_deliveries(
                    db_path,
                    bot_token="token",
                    chat_id="123456789",
                    run_id=run_id,
                    llm_enabled=True,
                    llm_client=client,
                )

            self.assertEqual(result["requested"], 0)
            self.assertEqual(result["llm_theme_editor"]["candidates"], 1)
            self.assertEqual(result["llm_theme_editor"]["selected"], 1)
            self.assertEqual(result["selected"], 1)
            sent_message = send.call_args.kwargs["message"]["text"]
            self.assertIn("• [B] 🔴 반도체주 하락세 지속", sent_message)
            self.assertIn("EVENTS: 테마:1", sent_message)

            rows = _delivery_rows(db_path)
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0]["event_signature"].startswith("market_theme:"))
            payload = json.loads(rows[0]["payload_json"])
            self.assertEqual(
                payload["message"]["summary_basis_counts"],
                {"llm_snippet": 1},
            )

    def test_create_live_deliveries_suppresses_recent_theme_key_repeat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path, run_id = _build_theme_only_fixture(Path(tmp))
            client = FakeThemeEditorClient()

            with patch(
                "news_scanner_v2.delivery.send_telegram_message",
                return_value="1001",
            ):
                create_live_deliveries(
                    db_path,
                    bot_token="token",
                    chat_id="123456789",
                    run_id=run_id,
                    llm_enabled=True,
                    llm_client=client,
                )

            with connect(db_path) as conn:
                conn.execute(
                    "UPDATE deliveries SET created_at = ? WHERE status = 'sent'",
                    ("2026-05-22T13:54:00+09:00",),
                )
                second_run_id = "theme-repeat-run"
                second_as_of = "2026-05-20T01:00:00+09:00"
                insert_run(
                    conn,
                    run_id=second_run_id,
                    started_at=second_as_of,
                    as_of=second_as_of,
                    mode="live",
                    dispatch_enabled=True,
                    llm_enabled=True,
                    legacy_prompt_hash=None,
                    legacy_snapshot={},
                )
                source_c = CandidateItem(
                    source="brave-news-largecap-movers",
                    provider="brave",
                    category="MOVE",
                    title="Nvidia slips again as chipmakers face pressure",
                    url="https://finance.yahoo.com/markets/semis-repeat-a.html",
                    published_at="2026-05-19T15:10:00+00:00",
                    summary="Nvidia fell while semiconductor stocks stayed under pressure.",
                )
                source_d = CandidateItem(
                    source="brave-news-largecap-movers",
                    provider="brave",
                    category="MOVE",
                    title="Chipmakers drop as yields pressure valuations",
                    url="https://www.cnbc.com/semis-repeat-b.html",
                    published_at="2026-05-19T15:12:00+00:00",
                    summary="Semiconductors dropped as rising yields pressured valuations.",
                )
                insert_candidate_items(
                    conn,
                    run_id=second_run_id,
                    fetched_at=second_as_of,
                    items=[source_c.as_record(second_run_id), source_d.as_record(second_run_id)],
                )
                finish_run(
                    conn,
                    run_id=second_run_id,
                    status="ok",
                    finished_at=second_as_of,
                )

            with patch(
                "news_scanner_v2.delivery.send_telegram_message",
                return_value="1002",
            ) as send:
                result = create_live_deliveries(
                    db_path,
                    bot_token="token",
                    chat_id="123456789",
                    run_id=second_run_id,
                    llm_enabled=True,
                    llm_client=client,
                )

            self.assertEqual(result["llm_theme_editor"]["selected"], 0)
            self.assertEqual(
                result["llm_theme_editor"]["skipped_recent_theme_keys"],
                ["semiconductor_pressure"],
            )
            sent_message = send.call_args.kwargs["message"]["text"]
            self.assertIn("✅ 특이사항 없음", sent_message)
            self.assertNotIn("반도체주 하락세 지속", sent_message)

    def test_create_live_deliveries_dedupes_seed_theme_by_theme_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path, run_id = _build_theme_only_fixture(Path(tmp))
            semis_seed_items = [
                {
                    "id": "seed-semi-a",
                    "category": "MOVE",
                    "provider": "brave",
                    "source": "brave-discovery-1-strat",
                    "title": "Nvidia fell as rising yields pressured chipmakers",
                    "url": "https://finance.yahoo.com/semis-a",
                    "summary": "Nvidia fell and chipmakers were under pressure.",
                },
                {
                    "id": "seed-semi-b",
                    "category": "MOVE",
                    "provider": "brave",
                    "source": "brave-discovery-2-strat",
                    "title": "Investors take profits in semiconductors",
                    "url": "https://www.cnbc.com/semis-b",
                    "summary": "Semiconductors saw profit-taking after a surge.",
                },
            ]
            seeds = build_news_seeds(
                raw_items=semis_seed_items,
                as_of=datetime(2026, 5, 19, 22, 30, tzinfo=ZoneInfo("Asia/Seoul")),
            )
            with connect(db_path) as conn:
                insert_news_seeds(
                    conn,
                    run_id=run_id,
                    created_at="2026-05-19T22:30:00+09:00",
                    seeds=seeds,
                )
            client = FakeThemeEditorClient()

            with patch(
                "news_scanner_v2.delivery.send_telegram_message",
                return_value="1001",
            ):
                result = create_live_deliveries(
                    db_path,
                    bot_token="token",
                    chat_id="123456789",
                    run_id=run_id,
                    llm_enabled=True,
                    llm_client=client,
                )

            self.assertEqual(result["llm_theme_editor"]["seed_candidates"], 1)
            self.assertEqual(result["llm_theme_editor"]["candidates"], 1)
            self.assertEqual(
                result["llm_theme_editor"]["candidate_keys"],
                ["semiconductor_pressure"],
            )
            self.assertEqual(len(client.calls), 1)

    def test_create_live_deliveries_adds_llm_news_seed_theme(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path, run_id = _build_seed_only_fixture(Path(tmp))
            client = FakeSeedThemeEditorClient()

            with patch(
                "news_scanner_v2.delivery.send_telegram_message",
                return_value="1001",
            ) as send:
                result = create_live_deliveries(
                    db_path,
                    bot_token="token",
                    chat_id="123456789",
                    run_id=run_id,
                    llm_enabled=True,
                    llm_client=client,
                )

            self.assertEqual(result["requested"], 0)
            self.assertEqual(result["llm_theme_editor"]["seed_candidates"], 1)
            self.assertEqual(result["llm_theme_editor"]["selected"], 1)
            self.assertIn(
                "news_seed_theme_v1",
                result["llm_theme_editor"]["candidate_policies"],
            )
            self.assertEqual(result["selected"], 1)
            sent_message = send.call_args.kwargs["message"]["text"]
            self.assertIn("AI 인프라 투자 확대", sent_message)
            self.assertNotIn("AI_INFRA - ai infrastructure", sent_message)
            self.assertIn("EVENTS: 테마:1", sent_message)

            rows = _delivery_rows(db_path)
            self.assertEqual(len(rows), 1)
            payload = json.loads(rows[0]["payload_json"])
            self.assertEqual(
                payload["evidence_contract"]["theme_key"],
                "ai_infrastructure_jv",
            )
            self.assertEqual(
                payload["message"]["summary_basis_counts"],
                {"llm_snippet": 1},
            )

    def test_create_live_deliveries_blocks_validation_rejected_theme_send(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path, run_id = _build_seed_only_fixture(Path(tmp))

            with patch(
                "news_scanner_v2.delivery.send_telegram_message",
                return_value="1001",
            ) as send:
                result = create_live_deliveries(
                    db_path,
                    bot_token="token",
                    chat_id="123456789",
                    run_id=run_id,
                    llm_enabled=True,
                    llm_client=FakeInvalidThemeEditorClient(),
                )

            self.assertEqual(result["llm_theme_editor"]["rejected"], 1)
            self.assertEqual(
                result["llm_theme_editor"]["validation_errors"],
                {"summary_has_raw_english": 1},
            )
            self.assertEqual(result["llm_theme_editor"]["selected"], 0)
            self.assertEqual(result["selected"], 0)
            sent_message = send.call_args.kwargs["message"]["text"]
            self.assertIn("✅ 특이사항 없음", sent_message)

            rows = _delivery_rows(db_path)
            self.assertEqual(len(rows), 1)
            self.assertIsNone(rows[0]["event_signature"])

    def test_create_live_deliveries_sends_no_new_digest_once_for_later_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config, summary = _build_report_fixture(Path(tmp))
            later_run_id = "later-run"

            with patch(
                "news_scanner_v2.delivery.send_telegram_message",
                side_effect=["1001", "1002"],
            ) as send:
                first = create_live_deliveries(
                    config.db_path,
                    bot_token="token",
                    chat_id="123456789",
                    run_id=summary["run_id"],
                    llm_enabled=True,
                    llm_client=FakeLLMClient(),
                )

                con = sqlite3.connect(config.db_path)
                try:
                    con.execute(
                        """
                        INSERT INTO runs (
                          id, started_at, finished_at, as_of, mode, status,
                          dispatch_enabled, llm_enabled, legacy_prompt_hash,
                          legacy_snapshot_json, error
                        )
                        SELECT ?, '2026-05-15T15:00:00+09:00',
                               '2026-05-15T15:00:05+09:00',
                               '2026-05-15T15:00:00+09:00', mode, status,
                               dispatch_enabled, llm_enabled, legacy_prompt_hash,
                               legacy_snapshot_json, error
                        FROM runs
                        WHERE id = ?
                        """,
                        (later_run_id, summary["run_id"]),
                    )
                    con.execute(
                        """
                        INSERT INTO dispatch_decisions (
                          id, run_id, event_signature, decision, reason, policy,
                          score, payload_json, created_at
                        )
                        SELECT 'later-decision', ?, event_signature, decision,
                               reason, policy, score, payload_json,
                               '2026-05-15T15:00:01+09:00'
                        FROM dispatch_decisions
                        WHERE run_id = ? AND decision = 'send_candidate'
                        LIMIT 1
                        """,
                        (later_run_id, summary["run_id"]),
                    )
                    con.commit()
                finally:
                    con.close()

                second = create_live_deliveries(
                    config.db_path,
                    bot_token="token",
                    chat_id="123456789",
                    run_id=later_run_id,
                    llm_enabled=True,
                    llm_client=FakeLLMClient(),
                )
                third = create_live_deliveries(
                    config.db_path,
                    bot_token="token",
                    chat_id="123456789",
                    run_id=later_run_id,
                    llm_enabled=True,
                    llm_client=FakeLLMClient(),
                )

            self.assertEqual(first["selected"], 1)
            self.assertEqual(first["sent"], 1)
            self.assertEqual(second["requested"], 1)
            self.assertEqual(second["selected"], 0)
            self.assertEqual(second["sent"], 1)
            self.assertEqual(second["inserted"], 1)
            self.assertEqual(second["message_ids"], ["1002"])
            self.assertEqual(second["skipped_previously_sent"], 1)
            self.assertEqual(third["selected"], 0)
            self.assertEqual(third["sent"], 0)
            self.assertEqual(send.call_count, 2)

            rows = _delivery_rows(config.db_path)
            self.assertEqual(len(rows), 2)
            no_new = [row for row in rows if row["run_id"] == later_run_id][0]
            self.assertIsNone(no_new["event_signature"])
            self.assertEqual(no_new["message_id"], "1002")
            payload = json.loads(no_new["payload_json"])
            self.assertIn("✅ 특이사항 없음", payload["message"]["text"])

    def test_build_live_deliveries_uses_distinct_ids_for_distinct_events(self) -> None:
        rows = [
            _message_row(event_signature="event-1"),
            _message_row(decision_id="decision-2", event_signature="event-2"),
        ]
        deliveries = build_live_deliveries(rows)
        self.assertNotEqual(deliveries[0].id, deliveries[1].id)

    def test_sort_delivery_rows_prioritizes_hard_company_before_b_geo(self) -> None:
        rows = [
            _message_row(
                event_signature="geo-b",
                event_type="geo",
                subject="iran",
                action="conflict",
                grade="B",
                score=88.0,
            ),
            _message_row(
                event_signature="company-hard-b",
                event_type="corporate_action",
                subject="ctsh",
                action="buyback",
                grade="B",
                score=68.0,
                event_quality="hard_event",
            ),
            _message_row(
                event_signature="geo-a",
                event_type="geo",
                subject="iran",
                action="conflict",
                grade="A",
                score=95.0,
            ),
        ]

        sorted_rows = sort_delivery_rows(rows)

        self.assertEqual(
            [row["event_signature"] for row in sorted_rows],
            ["geo-a", "company-hard-b", "geo-b"],
        )

    def test_send_telegram_message_parses_message_id(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"ok": true, "result": {"message_id": 1234}}'

        with patch("news_scanner_v2.delivery.request.urlopen", return_value=Response()):
            message_id = send_telegram_message(
                bot_token="token",
                chat_id="123456789",
                message={"text": "hello", "parse_mode": None},
            )

        self.assertEqual(message_id, "1234")


if __name__ == "__main__":
    unittest.main()
