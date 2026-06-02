import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from news_scanner_v2.db import init_db
from news_scanner_v2.quality_golden import audit_golden_fixture, load_fixture


FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "news_quality_golden_20260521.json"
)


def _write_fixture(path: Path) -> None:
    fixture = {
        "version": "news_quality_golden_v1",
        "slots": [
            {
                "label": "synthetic",
                "run_id": "run-1",
                "as_of": "2026-05-21T05:00:00+09:00",
                "checks": [
                    {
                        "id": "fomc_event_expected",
                        "expected_stage": "event",
                        "match": {
                            "category_any": ["MACRO"],
                            "terms_all": ["Minutes", "Federal Open Market Committee"],
                        },
                    },
                    {
                        "id": "nvda_delivery_expected",
                        "expected_stage": "delivery",
                        "match": {
                            "category_any": ["EARN"],
                            "terms_all": ["Nvidia", "earnings"],
                        },
                        "delivery_match": {"terms_all": ["NVDA", "EPS"]},
                    },
                    {
                        "id": "semis_not_delivery_expected",
                        "expected_stage": "not_delivery",
                        "match": {"terms_any": ["semiconductor_pressure"]},
                        "delivery_match": {"terms_all": ["반도체"]},
                    },
                ],
            }
        ],
    }
    path.write_text(json.dumps(fixture))


def _build_db(path: Path) -> None:
    init_db(path)
    conn = sqlite3.connect(path)
    try:
        now = "2026-05-21T05:00:00+09:00"
        conn.execute(
            """
            INSERT INTO runs (
              id, started_at, finished_at, as_of, mode, status,
              dispatch_enabled, llm_enabled, legacy_snapshot_json
            )
            VALUES (?, ?, ?, ?, ?, ?, 1, 1, ?)
            """,
            ("run-1", now, now, now, "live", "ok", "{}"),
        )
        conn.execute(
            """
            INSERT INTO candidate_items (
              id, run_id, source, provider, category, title, normalized_title,
              url, canonical_url, published_at, fetched_at, item_hash, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "c-fomc",
                "run-1",
                "federal-reserve-press",
                "official_rss",
                "MACRO",
                "Minutes of the Federal Open Market Committee",
                "minutes of the federal open market committee",
                "https://federalreserve.gov/minutes",
                "https://federalreserve.gov/minutes",
                "2026-05-20T18:00:00+00:00",
                now,
                "hash-fomc",
                json.dumps(
                    {
                        "summary": (
                            "Minutes of the Federal Open Market Committee, "
                            "April 28-29, 2026"
                        )
                    }
                ),
            ),
        )
        conn.execute(
            """
            INSERT INTO candidate_items (
              id, run_id, source, provider, category, title, normalized_title,
              url, canonical_url, published_at, fetched_at, item_hash, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "c-nvda",
                "run-1",
                "brave-news-earnings-guidance",
                "brave",
                "EARN",
                "Nvidia earnings beat expectations",
                "nvidia earnings beat expectations",
                "https://example.com/nvda",
                "https://example.com/nvda",
                "2026-05-20T20:00:00+00:00",
                now,
                "hash-nvda",
                json.dumps({"summary": "NVDA EPS beat"}),
            ),
        )
        event_payload = {
            "signature": "event-nvda",
            "event_type": "earnings",
            "subject": "nvda",
            "action": "earnings_report",
            "effective_date": "2026-05-21",
            "title": "Nvidia earnings beat expectations",
        }
        conn.execute(
            """
            INSERT INTO events (
              signature, first_seen_run_id, event_type, subject, effective_date,
              payload_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "event-nvda",
                "run-1",
                "earnings",
                "nvda",
                "2026-05-21",
                json.dumps(event_payload),
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO candidate_events (
              id, run_id, candidate_id, event_signature, extractor, confidence,
              reason, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "link-nvda",
                "run-1",
                "c-nvda",
                "event-nvda",
                "rules_v1",
                0.9,
                "EARN:fixture",
                now,
            ),
        )
        decision_payload = {
            "event": event_payload,
            "candidate_ids": ["c-nvda"],
            "evidence_count": 1,
        }
        conn.execute(
            """
            INSERT INTO dispatch_decisions (
              id, run_id, event_signature, decision, reason, policy, score,
              payload_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "decision-nvda",
                "run-1",
                "event-nvda",
                "send_candidate",
                "fixture",
                "fixture",
                90,
                json.dumps(decision_payload),
                now,
            ),
        )
        delivery_payload = {
            "message": {"text": "📰 미국증시 뉴스\n\n• [A] NVDA EPS beat"}
        }
        conn.execute(
            """
            INSERT INTO deliveries (
              id, run_id, event_signature, channel, status, message_id,
              payload_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "delivery-nvda",
                "run-1",
                "event-nvda",
                "telegram",
                "sent",
                "100",
                json.dumps(delivery_payload),
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


class QualityGoldenTests(unittest.TestCase):
    def test_repository_fixture_loads(self) -> None:
        fixture = load_fixture(FIXTURE_PATH)

        self.assertEqual(fixture["version"], "news_quality_golden_v1")
        self.assertEqual(len(fixture["slots"]), 4)

    def test_audit_reports_passes_and_known_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "news.sqlite"
            fixture_path = root / "fixture.json"
            _build_db(db_path)
            _write_fixture(fixture_path)

            report = audit_golden_fixture(db_path, fixture_path)

        self.assertEqual(report["summary"]["checks"], 3)
        self.assertEqual(report["summary"]["passed"], 2)
        self.assertEqual(report["summary"]["failed"], 1)
        self.assertEqual(report["failures"][0]["id"], "fomc_event_expected")
        by_id = {check["id"]: check for check in report["checks"]}
        self.assertEqual(by_id["fomc_event_expected"]["counts"]["candidate"], 1)
        self.assertEqual(by_id["fomc_event_expected"]["counts"]["event"], 0)
        self.assertEqual(by_id["nvda_delivery_expected"]["status"], "pass")


if __name__ == "__main__":
    unittest.main()
