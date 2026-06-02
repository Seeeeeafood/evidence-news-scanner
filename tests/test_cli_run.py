from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from news_scanner_v2.cli import main
from news_scanner_v2.fetcher import FetchResult
from news_scanner_v2.models import CandidateItem
from news_scanner_v2.sources import NewsSource


def _write_legacy_jobs(root: Path) -> None:
    cron = root / "cron"
    cron.mkdir(parents=True)
    payload = {
        "kind": "agentTurn",
        "message": "legacy prompt",
        "model": "anthropic/claude-sonnet-4-6",
        "timeoutSeconds": 900,
    }
    jobs = {
        "jobs": [
            {
                "id": "f06099f3-6825-47f6-9410-81e9aebb6b04",
                "name": "US Market News Scanner (Hourly)",
                "enabled": True,
                "schedule": {"expr": "0 1,5,11,15,19 * * *", "tz": "Asia/Seoul"},
                "delivery": {"mode": "none"},
                "sessionTarget": "isolated",
                "wakeMode": "now",
                "payload": payload,
                "state": {},
            }
        ]
    }
    (cron / "jobs.json").write_text(json.dumps(jobs))


class CliRunTests(unittest.TestCase):
    def test_run_command_outputs_dispatch_summary_and_persists_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_legacy_jobs(root)
            db_path = root / "state" / "news_scanner_v2.sqlite"
            shadow_dir = root / "shadow"
            source = NewsSource(
                name="fixture-earnings",
                category="EARN",
                provider="fixture",
                url="fixture://earnings",
            )
            item = CandidateItem(
                source=source.name,
                provider=source.provider,
                category=source.category,
                title="Nvidia (NVDA) raises guidance after Q1 earnings",
                url="https://reuters.com/nvda",
                published_at="2026-05-14T12:45:00+00:00",
            )
            result = FetchResult(
                source=source,
                status="ok",
                started_at=datetime.now(timezone.utc).isoformat(),
                finished_at=datetime.now(timezone.utc).isoformat(),
                items=(item,),
            )
            out = io.StringIO()

            with patch("news_scanner_v2.pipeline.DEFAULT_SOURCES", (source,)), patch(
                "news_scanner_v2.pipeline.fetch_sources",
                return_value=(result,),
            ), redirect_stdout(out):
                exit_code = main(
                    [
                        "run",
                        "--mode",
                        "shadow",
                        "--legacy-root",
                        str(root),
                        "--db-path",
                        str(db_path),
                        "--shadow-dir",
                        str(shadow_dir),
                        "--disable-brave",
                        "--disable-market-snapshot",
                        "--as-of",
                        "2026-05-14T22:00:00+09:00",
                    ]
                )

            summary = json.loads(out.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(summary["status"], "ok")
            self.assertEqual(summary["brave_requests_used"], 0)
            self.assertEqual(summary["dispatch_decisions_evaluated"], 1)
            self.assertEqual(summary["dispatch_decisions_inserted"], 1)
            self.assertEqual(
                summary["dispatch_decisions_by_decision"], {"send_candidate": 1}
            )
            self.assertTrue(Path(summary["shadow_output"]).exists())

            con = sqlite3.connect(db_path)
            self.assertEqual(
                con.execute("select count(*) from dispatch_decisions").fetchone()[0],
                1,
            )
            self.assertEqual(
                con.execute("select count(*) from deliveries").fetchone()[0],
                0,
            )

    def test_run_dry_run_records_delivery_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_legacy_jobs(root)
            db_path = root / "state" / "news_scanner_v2.sqlite"
            shadow_dir = root / "shadow"
            brave_source = NewsSource(
                name="fixture-brave-earnings",
                category="EARN",
                provider="brave",
                url="fixture://brave-earnings",
            )
            rss_source = NewsSource(
                name="fixture-rss-earnings",
                category="EARN",
                provider="google_rss",
                url="fixture://rss-earnings",
            )
            fetch_results = (
                FetchResult(
                    source=brave_source,
                    status="ok",
                    started_at=datetime.now(timezone.utc).isoformat(),
                    finished_at=datetime.now(timezone.utc).isoformat(),
                    items=(
                        CandidateItem(
                            source=brave_source.name,
                            provider=brave_source.provider,
                            category=brave_source.category,
                            title="Nvidia (NVDA) raises guidance after Q1 earnings",
                            url="https://reuters.com/nvda-a",
                            published_at="2026-05-14T12:45:00+00:00",
                        ),
                    ),
                ),
                FetchResult(
                    source=rss_source,
                    status="ok",
                    started_at=datetime.now(timezone.utc).isoformat(),
                    finished_at=datetime.now(timezone.utc).isoformat(),
                    items=(
                        CandidateItem(
                            source=rss_source.name,
                            provider=rss_source.provider,
                            category=rss_source.category,
                            title="NVIDIA raises guidance after earnings beat",
                            url="https://cnbc.com/nvda-b",
                            published_at="2026-05-14T12:50:00+00:00",
                        ),
                    ),
                ),
            )
            out = io.StringIO()

            with patch(
                "news_scanner_v2.pipeline.DEFAULT_SOURCES",
                (brave_source, rss_source),
            ), patch(
                "news_scanner_v2.pipeline.fetch_sources",
                return_value=fetch_results,
            ), patch(
                "news_scanner_v2.price_reaction.fetch_price_reaction",
                return_value={
                    "status": "ok",
                    "ticker": "NVDA",
                    "direction": "up",
                    "pct_change": 3.2,
                    "session": "intraday_5min",
                    "price_as_of": "2026-05-14",
                },
            ), patch(
                "news_scanner_v2.cli.load_openai_api_key",
                return_value=None,
            ), redirect_stdout(out):
                exit_code = main(
                    [
                        "run",
                        "--mode",
                        "dry-run",
                        "--legacy-root",
                        str(root),
                        "--db-path",
                        str(db_path),
                        "--shadow-dir",
                        str(shadow_dir),
                        "--disable-brave",
                        "--disable-market-snapshot",
                        "--enable-llm",
                        "--as-of",
                        "2026-05-14T22:00:00+09:00",
                    ]
                )

            summary = json.loads(out.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(summary["mode"], "dry-run")
            self.assertEqual(summary["status"], "ok")
            self.assertEqual(
                summary["llm_models"],
                {
                    "base": "gpt-5.5",
                    "critic": "gpt-5.5",
                    "discovery": "gpt-5.5",
                    "editorial": "gpt-5.5",
                    "summary": "gpt-5.5",
                    "theme_editor": "gpt-5.5",
                },
            )
            self.assertEqual(summary["dispatch_decisions_by_decision"], {"send_candidate": 1})
            self.assertEqual(summary["delivery_dry_run"]["status"], "ok")
            self.assertEqual(
                summary["delivery_dry_run"]["llm_models"],
                {
                    "base": "gpt-5.5",
                    "editorial": "gpt-5.5",
                    "summary": "gpt-5.5",
                    "theme_editor": "gpt-5.5",
                },
            )
            self.assertEqual(summary["delivery_dry_run"]["requested"], 0)
            self.assertEqual(summary["delivery_dry_run"]["inserted"], 0)
            self.assertEqual(summary["delivery_dry_run"]["final_publish_dropped"], 0)
            self.assertEqual(summary["delivery_dry_run"]["contract"]["blocked"], 1)
            self.assertIn(
                "title_only_company_event",
                summary["delivery_dry_run"]["contract"]["blocked_events"][0]["failures"],
            )
            self.assertEqual(
                summary["delivery_dry_run"]["llm_editorial"]["status"],
                "ok",
            )
            self.assertEqual(
                summary["delivery_dry_run"]["llm_annotation"]["status"],
                "ok",
            )

            con = sqlite3.connect(db_path)
            self.assertEqual(
                con.execute("select mode from runs").fetchone()[0],
                "dry-run",
            )
            self.assertEqual(
                con.execute("select count(*) from deliveries").fetchone()[0],
                0,
            )

    def test_run_live_records_sent_delivery_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_legacy_jobs(root)
            db_path = root / "state" / "news_scanner_v2.sqlite"
            shadow_dir = root / "shadow"
            brave_source = NewsSource(
                name="fixture-brave-earnings",
                category="EARN",
                provider="brave",
                url="fixture://brave-earnings",
            )
            rss_source = NewsSource(
                name="fixture-rss-earnings",
                category="EARN",
                provider="google_rss",
                url="fixture://rss-earnings",
            )
            fetch_results = (
                FetchResult(
                    source=brave_source,
                    status="ok",
                    started_at=datetime.now(timezone.utc).isoformat(),
                    finished_at=datetime.now(timezone.utc).isoformat(),
                    items=(
                        CandidateItem(
                            source=brave_source.name,
                            provider=brave_source.provider,
                            category=brave_source.category,
                            title="Nvidia (NVDA) raises guidance after Q1 earnings",
                            url="https://reuters.com/nvda-a",
                            published_at="2026-05-14T12:45:00+00:00",
                        ),
                    ),
                ),
                FetchResult(
                    source=rss_source,
                    status="ok",
                    started_at=datetime.now(timezone.utc).isoformat(),
                    finished_at=datetime.now(timezone.utc).isoformat(),
                    items=(
                        CandidateItem(
                            source=rss_source.name,
                            provider=rss_source.provider,
                            category=rss_source.category,
                            title="NVIDIA raises guidance after earnings beat",
                            url="https://cnbc.com/nvda-b",
                            published_at="2026-05-14T12:50:00+00:00",
                        ),
                    ),
                ),
            )
            out = io.StringIO()

            with patch(
                "news_scanner_v2.pipeline.DEFAULT_SOURCES",
                (brave_source, rss_source),
            ), patch(
                "news_scanner_v2.pipeline.fetch_sources",
                return_value=fetch_results,
            ), patch(
                "news_scanner_v2.price_reaction.fetch_price_reaction",
                return_value={
                    "status": "ok",
                    "ticker": "NVDA",
                    "direction": "up",
                    "pct_change": 3.2,
                    "session": "intraday_5min",
                    "price_as_of": "2026-05-14",
                },
            ), patch(
                "news_scanner_v2.cli.load_telegram_bot_token",
                return_value="token",
            ), patch(
                "news_scanner_v2.delivery.send_telegram_message",
                return_value="2001",
            ), redirect_stdout(out):
                exit_code = main(
                    [
                        "run",
                        "--mode",
                        "live",
                        "--legacy-root",
                        str(root),
                        "--db-path",
                        str(db_path),
                        "--shadow-dir",
                        str(shadow_dir),
                        "--disable-brave",
                        "--disable-market-snapshot",
                        "--as-of",
                        "2026-05-14T22:00:00+09:00",
                        "--telegram-chat-id",
                        "123456789",
                    ]
                )

            summary = json.loads(out.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(summary["mode"], "live")
            self.assertEqual(summary["status"], "ok")
            self.assertEqual(summary["delivery_live"]["status"], "ok")
            self.assertEqual(summary["delivery_live"]["requested"], 1)
            self.assertEqual(summary["delivery_live"]["selected"], 0)
            self.assertEqual(summary["delivery_live"]["sent"], 1)
            self.assertEqual(summary["delivery_live"]["final_publish_dropped"], 0)
            self.assertEqual(summary["delivery_live"]["contract"]["blocked"], 1)
            self.assertIn(
                "title_only_company_event",
                summary["delivery_live"]["contract"]["blocked_events"][0]["failures"],
            )
            self.assertEqual(summary["delivery_live"]["message_ids"], ["2001"])
            self.assertEqual(
                summary["delivery_live"]["llm_editorial"]["status"],
                "disabled",
            )

            con = sqlite3.connect(db_path)
            delivery = con.execute(
                "select status, channel, message_id, event_signature from deliveries"
            ).fetchone()
            self.assertEqual(delivery, ("sent", "telegram", "2001", None))


if __name__ == "__main__":
    unittest.main()
