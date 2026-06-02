from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from news_scanner_v2.config import build_config
from news_scanner_v2.body_fetcher import BodyFetchResult
from news_scanner_v2.discovery import discovery_sources_from_queries
from news_scanner_v2.fetcher import FetchResult
from news_scanner_v2.models import CandidateItem
from news_scanner_v2.pipeline import run_shadow
from news_scanner_v2.sources import NewsSource


AS_OF = datetime(2026, 5, 14, 22, 0, tzinfo=ZoneInfo("Asia/Seoul"))


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


def _config(root: Path):
    _write_legacy_jobs(root)
    return build_config(
        legacy_root=root,
        db_path=root / "state" / "news_scanner_v2.sqlite",
        shadow_dir=root / "shadow",
        brave_enabled=False,
    )


def _source(name: str, category: str) -> NewsSource:
    return NewsSource(
        name=name,
        category=category,
        provider="fixture",
        url=f"fixture://{name}",
    )


def _item(
    source: NewsSource,
    title: str,
    *,
    url: str,
    published_at: str = "2026-05-14T12:45:00+00:00",
    summary: str = "",
) -> CandidateItem:
    return CandidateItem(
        source=source.name,
        provider=source.provider,
        category=source.category,
        title=title,
        url=url,
        published_at=published_at,
        summary=summary,
    )


def _result(source: NewsSource, *items: CandidateItem) -> FetchResult:
    return FetchResult(
        source=source,
        status="ok",
        started_at=datetime.now(timezone.utc).isoformat(),
        finished_at=datetime.now(timezone.utc).isoformat(),
        items=items,
    )


class PipelineEventTests(unittest.TestCase):
    def test_run_shadow_inserts_extracted_events_and_candidate_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _config(root)
            source = _source("fixture-earnings", "EARN")
            item = _item(
                source,
                "Nvidia (NVDA) raises guidance after Q1 earnings",
                url="https://reuters.com/nvda",
            )
            result = _result(source, item)

            with patch("news_scanner_v2.pipeline.DEFAULT_SOURCES", (source,)), patch(
                "news_scanner_v2.pipeline.fetch_sources",
                return_value=(result,),
            ):
                summary = run_shadow(config, as_of=AS_OF)

            self.assertEqual(summary["status"], "ok")
            self.assertEqual(summary["candidate_items_inserted"], 1)
            self.assertEqual(summary["events_extracted"], 1)
            self.assertEqual(summary["events_inserted"], 1)
            self.assertEqual(summary["event_links_inserted"], 1)
            self.assertEqual(summary["events_by_type"], {"earnings": 1})
            self.assertEqual(summary["dispatch_decisions_evaluated"], 1)
            self.assertEqual(summary["dispatch_decisions_inserted"], 1)
            self.assertEqual(
                summary["dispatch_decisions_by_decision"], {"send_candidate": 1}
            )

            con = sqlite3.connect(config.db_path)
            self.assertEqual(con.execute("select count(*) from events").fetchone()[0], 1)
            self.assertEqual(
                con.execute("select count(*) from candidate_events").fetchone()[0],
                1,
            )
            self.assertEqual(
                con.execute("select count(*) from dispatch_decisions").fetchone()[0],
                1,
            )
            row = con.execute(
                "select event_type, subject, effective_date from events"
            ).fetchone()
            self.assertEqual(row, ("earnings", "nvda", "2026-05-14"))

    def test_run_shadow_stores_market_snapshot_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_legacy_jobs(root)
            config = build_config(
                legacy_root=root,
                db_path=root / "state" / "news_scanner_v2.sqlite",
                shadow_dir=root / "shadow",
                brave_enabled=False,
                market_snapshot_enabled=True,
                fmp_api_key="fixture-fmp",
            )
            source = _source("fixture-earnings", "EARN")
            item = _item(
                source,
                "Nvidia (NVDA) raises guidance after Q1 earnings",
                url="https://reuters.com/nvda",
            )
            result = _result(source, item)
            snapshot = {
                "schema_version": 1,
                "as_of": AS_OF.isoformat(),
                "status": "ok",
                "values": {
                    "sp500": {"status": "ok", "provider": "fmp", "value": 7432.97},
                    "nasdaq": {"status": "ok", "provider": "fmp", "value": 26270.36},
                    "dow": {"status": "ok", "provider": "fmp", "value": 50009.35},
                    "wti": {"status": "ok", "provider": "stooq", "value": 97.75},
                    "brent": {"status": "ok", "provider": "fmp", "value": 104.17},
                    "gold": {"status": "ok", "provider": "fmp", "value": 4533.8},
                    "dxy": {"status": "ok", "provider": "stooq", "value": 99.075},
                    "ten_year": {"status": "ok", "provider": "fmp", "value": 4.57},
                    "vix": {"status": "ok", "provider": "fmp", "value": 17.19},
                    "usd_krw": {"status": "ok", "provider": "fmp", "value": 1503.68},
                },
                "providers": ["fmp", "stooq"],
                "usable_values": 10,
                "expected_values": 10,
                "value_status_counts": {"ok": 10},
            }

            with patch("news_scanner_v2.pipeline.DEFAULT_SOURCES", (source,)), patch(
                "news_scanner_v2.pipeline.fetch_sources",
                return_value=(result,),
            ), patch(
                "news_scanner_v2.pipeline.fetch_market_snapshot",
                return_value=snapshot,
            ):
                summary = run_shadow(config, as_of=AS_OF)

            self.assertEqual(summary["market_snapshot_status"], "ok")
            self.assertEqual(summary["market_snapshot_values_ok"], 10)
            self.assertEqual(summary["market_snapshot_inserted"], 1)
            con = sqlite3.connect(config.db_path)
            row = con.execute(
                "select status, provider, payload_json from market_snapshots"
            ).fetchone()
            self.assertEqual(row[0], "ok")
            self.assertEqual(row[1], "fmp,stooq")
            payload = json.loads(row[2])
            self.assertEqual(payload["values"]["usd_krw"]["value"], 1503.68)

    def test_run_shadow_dedupes_same_event_across_candidate_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _config(root)
            source = _source("fixture-earnings", "EARN")
            result = _result(
                source,
                _item(
                    source,
                    "Nvidia (NVDA) raises guidance after Q1 earnings",
                    url="https://reuters.com/nvda-a",
                ),
                _item(
                    source,
                    "NVIDIA raises guidance after earnings beat",
                    url="https://cnbc.com/nvda-b",
                ),
            )

            with patch("news_scanner_v2.pipeline.DEFAULT_SOURCES", (source,)), patch(
                "news_scanner_v2.pipeline.fetch_sources",
                return_value=(result,),
            ):
                summary = run_shadow(config, as_of=AS_OF)

            self.assertEqual(summary["candidate_items_inserted"], 2)
            self.assertEqual(summary["events_extracted"], 2)
            self.assertEqual(summary["events_inserted"], 1)
            self.assertEqual(summary["event_links_inserted"], 2)
            self.assertEqual(summary["dispatch_decisions_evaluated"], 1)
            self.assertEqual(summary["dispatch_decisions_inserted"], 1)
            self.assertEqual(
                summary["dispatch_decisions_by_decision"],
                {"send_candidate": 1},
            )

            con = sqlite3.connect(config.db_path)
            self.assertEqual(con.execute("select count(*) from events").fetchone()[0], 1)
            self.assertEqual(
                con.execute("select count(*) from candidate_events").fetchone()[0],
                2,
            )
            decision = con.execute(
                "select decision from dispatch_decisions"
            ).fetchone()[0]
            self.assertEqual(decision, "send_candidate")

    def test_run_shadow_enriches_send_candidates_with_body_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _config(root)
            source = _source("fixture-earnings", "EARN")
            result = _result(
                source,
                _item(
                    source,
                    "Nvidia (NVDA) raises guidance after Q1 earnings",
                    url="https://source.example/nvda-a",
                ),
                _item(
                    source,
                    "NVIDIA raises guidance after earnings beat",
                    url="https://source.example/nvda-b",
                ),
            )

            with patch("news_scanner_v2.pipeline.DEFAULT_SOURCES", (source,)), patch(
                "news_scanner_v2.pipeline.fetch_sources",
                return_value=(result,),
            ), patch(
                "news_scanner_v2.body_fetcher.fetch_article_body",
                return_value=BodyFetchResult(
                    url="https://source.example/nvda-a",
                    status="full",
                    fetched_at="2026-05-15T00:00:00+00:00",
                    body_text="Nvidia lifted guidance after AI demand. " * 40,
                    text_chars=1600,
                    http_status=200,
                ),
            ):
                summary = run_shadow(config, as_of=AS_OF)

            self.assertEqual(summary["dispatch_decisions_by_decision"], {"send_candidate": 1})
            self.assertEqual(summary["body_fetch_candidates"], 2)
            self.assertEqual(summary["body_fetch_attempts"], 2)
            self.assertEqual(summary["body_fetch_full"], 2)

            con = sqlite3.connect(config.db_path)
            raw_json = con.execute(
                "select raw_json from candidate_items order by id limit 1"
            ).fetchone()[0]
            raw = json.loads(raw_json)
            self.assertIn("body_text", raw)
            self.assertEqual(raw["body_fetch"]["status"], "full")

    def test_body_fetch_recomputes_dispatch_and_suppresses_stale_earnings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _config(root)
            source = _source("fixture-earnings", "EARN")
            result = _result(
                source,
                _item(
                    source,
                    "UNH Q1 EPS $7.23 beats estimates, guidance raised",
                    url="https://reuters.com/unh-q1-replay",
                    published_at="2026-05-18T10:35:20+00:00",
                ),
            )

            with patch("news_scanner_v2.pipeline.DEFAULT_SOURCES", (source,)), patch(
                "news_scanner_v2.pipeline.fetch_sources",
                return_value=(result,),
            ), patch(
                "news_scanner_v2.body_fetcher.fetch_article_body",
                return_value=BodyFetchResult(
                    url="https://reuters.com/unh-q1-replay",
                    status="full",
                    fetched_at="2026-05-18T13:30:00+00:00",
                    body_text=(
                        "UnitedHealth Group reported first-quarter 2026 results on "
                        "April 21. Revenue was $111.7 billion and adjusted EPS was "
                        "$7.23."
                    )
                    * 20,
                    text_chars=2200,
                    http_status=200,
                ),
            ):
                summary = run_shadow(
                    config,
                    as_of=datetime(
                        2026,
                        5,
                        18,
                        22,
                        30,
                        tzinfo=ZoneInfo("Asia/Seoul"),
                    ),
                )

            self.assertEqual(summary["pre_body_events_extracted"], 1)
            self.assertEqual(summary["pre_body_dispatch_decisions_evaluated"], 1)
            self.assertEqual(summary["body_fetch_attempts"], 1)
            self.assertEqual(summary["events_extracted"], 0)
            self.assertEqual(summary["dispatch_decisions_evaluated"], 0)

            con = sqlite3.connect(config.db_path)
            self.assertEqual(con.execute("select count(*) from events").fetchone()[0], 0)
            self.assertEqual(
                con.execute("select count(*) from dispatch_decisions").fetchone()[0],
                0,
            )

    def test_body_fetch_budget_prioritizes_unsent_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_legacy_jobs(root)
            db_path = root / "state" / "news_scanner_v2.sqlite"
            shadow_dir = root / "shadow"
            first_config = build_config(
                legacy_root=root,
                db_path=db_path,
                shadow_dir=shadow_dir,
                brave_enabled=False,
                body_fetch_enabled=False,
            )
            second_config = build_config(
                legacy_root=root,
                db_path=db_path,
                shadow_dir=shadow_dir,
                brave_enabled=False,
                max_body_fetches_per_run=1,
            )
            source = _source("fixture-earnings", "EARN")
            old_items = (
                _item(
                    source,
                    "Nvidia (NVDA) raises guidance after Q1 earnings",
                    url="https://reuters.com/nvda-a",
                ),
                _item(
                    source,
                    "NVIDIA raises guidance after earnings beat",
                    url="https://cnbc.com/nvda-b",
                ),
            )
            new_items = (
                _item(
                    source,
                    "AMD cuts guidance after Q1 earnings",
                    url="https://reuters.com/amd-a",
                ),
                _item(
                    source,
                    "Advanced Micro Devices (AMD) lowers outlook after earnings",
                    url="https://cnbc.com/amd-b",
                ),
            )

            with patch("news_scanner_v2.pipeline.DEFAULT_SOURCES", (source,)), patch(
                "news_scanner_v2.pipeline.fetch_sources",
                return_value=(_result(source, *old_items),),
            ):
                first = run_shadow(first_config, as_of=AS_OF)

            con = sqlite3.connect(db_path)
            old_signature = con.execute(
                "select event_signature from dispatch_decisions where run_id = ?",
                (first["run_id"],),
            ).fetchone()[0]
            con.execute(
                """
                insert into deliveries (
                  id, run_id, event_signature, channel, status, message_id,
                  payload_json, created_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "sent-old-event",
                    first["run_id"],
                    old_signature,
                    "telegram",
                    "sent",
                    "999",
                    "{}",
                    "2026-05-14T22:01:00+09:00",
                ),
            )
            con.commit()
            con.close()

            with patch("news_scanner_v2.pipeline.DEFAULT_SOURCES", (source,)), patch(
                "news_scanner_v2.pipeline.fetch_sources",
                return_value=(_result(source, *(old_items + new_items)),),
            ), patch(
                "news_scanner_v2.body_fetcher.fetch_article_body",
                return_value=BodyFetchResult(
                    url="https://reuters.com/amd-a",
                    status="full",
                    fetched_at="2026-05-15T00:00:00+00:00",
                    body_text="AMD cut guidance after softer demand. " * 40,
                    text_chars=1600,
                    http_status=200,
                ),
            ) as fetch:
                summary = run_shadow(second_config, as_of=AS_OF)

            self.assertEqual(summary["body_fetch_candidates"], 4)
            self.assertEqual(summary["body_fetch_unsent_events"], 1)
            self.assertEqual(summary["body_fetch_previously_sent_events"], 1)
            self.assertEqual(summary["body_fetch_attempts"], 1)
            self.assertEqual(fetch.call_count, 1)
            self.assertIn("/amd-a", fetch.call_args[0][0])

    def test_run_shadow_records_source_errors_without_dropping_ok_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _config(root)
            ok_source = _source("fixture-ok", "EARN")
            error_source = _source("fixture-error", "GEO")
            ok_result = _result(
                ok_source,
                _item(
                    ok_source,
                    "Nvidia (NVDA) raises guidance after Q1 earnings",
                    url="https://reuters.com/nvda",
                ),
            )
            error_result = FetchResult(
                source=error_source,
                status="error",
                started_at=datetime.now(timezone.utc).isoformat(),
                finished_at=datetime.now(timezone.utc).isoformat(),
                error="fixture failure",
            )

            with patch(
                "news_scanner_v2.pipeline.DEFAULT_SOURCES",
                (ok_source, error_source),
            ), patch(
                "news_scanner_v2.pipeline.fetch_sources",
                return_value=(ok_result, error_result),
            ):
                summary = run_shadow(config, as_of=AS_OF)

            self.assertEqual(summary["status"], "ok")
            self.assertEqual(summary["source_attempts"], 2)
            self.assertEqual(summary["source_errors"], 1)
            self.assertEqual(summary["candidate_items_inserted"], 1)
            self.assertEqual(summary["events_inserted"], 1)
            self.assertEqual(summary["dispatch_decisions_inserted"], 1)

            con = sqlite3.connect(config.db_path)
            rows = con.execute(
                "select source, status, error from source_attempts order by source"
            ).fetchall()
            self.assertEqual(rows[0], ("fixture-error", "error", "fixture failure"))
            self.assertEqual(rows[1][0:2], ("fixture-ok", "ok"))

    def test_run_shadow_records_verification_rescue_attempts_and_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_legacy_jobs(root)
            config = build_config(
                legacy_root=root,
                db_path=root / "state" / "news_scanner_v2.sqlite",
                shadow_dir=root / "shadow",
                brave_enabled=True,
                brave_api_key="brave-key",
                scout_lanes_enabled=False,
                body_fetch_enabled=False,
            )
            source = NewsSource(
                name="fixture-brave-corp",
                category="CORP",
                provider="brave",
                url="fixture://corp",
            )
            item = _item(
                source,
                "Cognizant Technology Solutions (CTSH) expands stock repurchase program by $2 billion",
                url="https://stockstory.example/ctsh-buyback",
            )
            result = _result(source, item)
            verification_source = NewsSource(
                name="brave-news-verification-1",
                category="VERIFY",
                provider="brave",
                url="https://api.search.brave.com/res/v1/news/search",
                query="CTSH buyback",
            )
            verification_result = FetchResult(
                source=verification_source,
                status="ok",
                started_at=datetime.now(timezone.utc).isoformat(),
                finished_at=datetime.now(timezone.utc).isoformat(),
            )

            def verify_records(records, **kwargs):
                self.assertTrue(kwargs["enabled"])
                self.assertEqual(kwargs["api_key"], "brave-key")
                self.assertEqual(kwargs["max_requests"], 2)
                records[0]["payload"]["grade"] = "A"
                records[0]["payload"]["source_tier"] = "trusted"
                records[0]["payload"]["verification_status"] = "verified"
                records[0]["payload"]["verification"] = {
                    "status": "verified",
                    "provider": "brave",
                }
                return (
                    records,
                    {
                        "verification_enabled": True,
                        "verification_configured": True,
                        "verification_candidates": 1,
                        "verification_attempted": 1,
                        "verification_verified": 1,
                        "verification_unverified": 0,
                        "verification_errors": 0,
                        "verification_skipped_limit": 0,
                        "verification_brave_max_requests": 2,
                        "verification_brave_requests_used": 1,
                    },
                    (verification_result,),
                )

            with patch("news_scanner_v2.pipeline.DEFAULT_SOURCES", (source,)), patch(
                "news_scanner_v2.pipeline.fetch_sources",
                return_value=(result,),
            ), patch(
                "news_scanner_v2.price_reaction.fetch_price_reaction",
                return_value={
                    "status": "ok",
                    "ticker": "CTSH",
                    "direction": "up",
                    "pct_change": 8.78,
                    "session": "intraday_5min",
                    "price_as_of": "2026-05-14",
                },
            ), patch(
                "news_scanner_v2.pipeline.verify_hard_event_records",
                side_effect=verify_records,
            ):
                summary = run_shadow(config, as_of=AS_OF)

            self.assertEqual(summary["status"], "ok")
            self.assertEqual(summary["source_attempts"], 2)
            self.assertEqual(summary["brave_requests_used"], 2)
            self.assertEqual(summary["verification_verified"], 1)
            self.assertEqual(summary["verification_brave_requests_used"], 1)

            con = sqlite3.connect(config.db_path)
            attempts = con.execute(
                "select source, category, kept_count from source_attempts order by source"
            ).fetchall()
            self.assertEqual(
                attempts,
                [
                    ("brave-news-verification-1", "VERIFY", 0),
                    ("fixture-brave-corp", "CORP", 1),
                ],
            )
            payload = json.loads(
                con.execute("select payload_json from dispatch_decisions").fetchone()[0]
            )
            self.assertEqual(payload["grade"], "A")
            self.assertEqual(payload["verification_status"], "verified")

    def test_run_shadow_builds_news_seeds_before_delivery_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_legacy_jobs(root)
            config = build_config(
                legacy_root=root,
                db_path=root / "state" / "news_scanner_v2.sqlite",
                shadow_dir=root / "shadow",
                brave_enabled=False,
                body_fetch_enabled=False,
            )
            source = _source("fixture-strategic", "STRAT")
            result = _result(
                source,
                _item(
                    source,
                    (
                        "Google and Blackstone launch $5B TPU cloud AI "
                        "infrastructure joint venture to rival CoreWeave"
                    ),
                    url="https://reuters.com/google-blackstone-tpu",
                ),
            )

            with patch("news_scanner_v2.pipeline.DEFAULT_SOURCES", (source,)), patch(
                "news_scanner_v2.pipeline.fetch_sources",
                return_value=(result,),
            ):
                summary = run_shadow(config, as_of=AS_OF)

            self.assertEqual(summary["status"], "ok")
            self.assertEqual(summary["news_seeds_built"], 1)
            self.assertEqual(summary["news_seeds_inserted"], 1)
            self.assertEqual(
                summary["news_seeds_by_theme"], {"ai_infrastructure_jv": 1}
            )

            con = sqlite3.connect(config.db_path)
            row = con.execute(
                """
                select seed_type, subject, theme, evidence_count, payload_json
                from news_seeds
                """
            ).fetchone()
            self.assertEqual(
                row[:4],
                ("strategic_theme", "AI_INFRA", "ai_infrastructure_jv", 1),
            )
            payload = json.loads(row[4])
            atom_texts = {atom["text"] for atom in payload["claim_atoms"]}
            self.assertIn("Blackstone investment or partnership", atom_texts)
            self.assertIn("accelerator or AI compute capacity", atom_texts)
            self.assertIn("data center or cloud capacity expansion", atom_texts)

    def test_run_shadow_fetches_llm_discovery_queries_before_filtering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_legacy_jobs(root)
            config = build_config(
                legacy_root=root,
                db_path=root / "state" / "news_scanner_v2.sqlite",
                shadow_dir=root / "shadow",
                brave_enabled=True,
                brave_api_key="brave-key",
                llm_enabled=True,
                discovery_llm_model="planner-model",
                scout_lanes_enabled=False,
                body_fetch_enabled=False,
                price_reaction_enabled=False,
                verification_enabled=False,
                max_brave_requests_per_run=4,
                max_discovery_queries_per_run=3,
                max_discovery_results_per_query=5,
            )
            base_source = NewsSource(
                name="fixture-brave-earnings",
                category="EARN",
                provider="brave",
                url="fixture://earnings",
            )
            base_result = _result(
                base_source,
                _item(
                    base_source,
                    "Nvidia (NVDA) raises guidance after Q1 earnings",
                    url="https://reuters.com/nvda",
                ),
            )
            discovery_sources = discovery_sources_from_queries(
                [
                    {
                        "query": "Google Blackstone TPU cloud AI infrastructure JV",
                        "category": "STRAT",
                        "reason": "AI infrastructure sample gap",
                        "max_results": 5,
                    }
                ]
            )

            def fetch_side_effect(sources, **kwargs):
                self.assertEqual(kwargs["brave_api_key"], "brave-key")
                if sources == (base_source,):
                    return (base_result,)
                self.assertEqual(sources, discovery_sources)
                discovery_source = discovery_sources[0]
                return (
                    _result(
                        discovery_source,
                        _item(
                            discovery_source,
                            (
                                "Google and Blackstone launch $5B TPU cloud AI "
                                "infrastructure joint venture to rival CoreWeave"
                            ),
                            url="https://reuters.com/google-blackstone-tpu",
                        ),
                    ),
                )

            with patch("news_scanner_v2.pipeline.DEFAULT_SOURCES", (base_source,)), patch(
                "news_scanner_v2.pipeline.load_openai_api_key",
                return_value="openai-key",
            ), patch(
                "news_scanner_v2.pipeline.create_discovery_plan",
                return_value={
                    "status": "ok",
                    "requested": 3,
                    "queries": [
                        {
                            "query": "Google Blackstone TPU cloud AI infrastructure JV",
                            "category": "STRAT",
                            "reason": "AI infrastructure sample gap",
                            "max_results": 5,
                        }
                    ],
                    "sources": discovery_sources,
                    "model": "gpt-5.5",
                    "prompt_version": "discovery_query_planner_v1",
                    "payload_chars": 1200,
                },
            ) as discovery_plan, patch(
                "news_scanner_v2.pipeline.fetch_sources",
                side_effect=fetch_side_effect,
            ):
                summary = run_shadow(config, as_of=AS_OF)

            self.assertEqual(summary["status"], "ok")
            self.assertEqual(summary["discovery_planner_active"], True)
            self.assertEqual(summary["discovery_planner_status"], "ok")
            self.assertEqual(summary["discovery_brave_request_budget"], 3)
            self.assertEqual(summary["discovery_query_count"], 1)
            self.assertEqual(summary["discovery_fetch_attempts"], 1)
            self.assertEqual(summary["discovery_fetch_items"], 1)
            self.assertEqual(summary["candidate_items_seen"], 2)
            self.assertEqual(summary["news_seeds_built"], 1)
            self.assertEqual(summary["news_seeds_by_theme"], {"ai_infrastructure_jv": 1})
            self.assertEqual(summary["source_attempts"], 2)
            self.assertEqual(summary["brave_source_slots"], 2)
            self.assertEqual(summary["brave_requests_planned_total_max"], 4)
            self.assertEqual(discovery_plan.call_args.kwargs["model"], "planner-model")
            self.assertEqual(summary["llm_models"]["discovery"], "planner-model")

            con = sqlite3.connect(config.db_path)
            attempts = con.execute(
                "select source, category, kept_count from source_attempts order by source"
            ).fetchall()
            self.assertEqual(
                attempts,
                [
                    ("brave-discovery-1-strat", "STRAT", 1),
                    ("fixture-brave-earnings", "EARN", 1),
                ],
            )

    def test_run_shadow_fetches_high_recall_scout_lanes_from_breaking_hints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_legacy_jobs(root)
            news_dir = root / "workspace" / "memory" / "news"
            news_dir.mkdir(parents=True)
            (news_dir / "breaking_2026-05-14.md").write_text(
                "\n".join(
                    [
                        "Rubio says Hormuz toll would break Iran talks; Pakistan delegation heads to Tehran",
                        "Kawasaki Heavy and NVIDIA physical AI partnership with Microsoft and Fujitsu robot center",
                    ]
                )
            )
            config = build_config(
                legacy_root=root,
                db_path=root / "state" / "news_scanner_v2.sqlite",
                shadow_dir=root / "shadow",
                brave_enabled=True,
                brave_api_key="brave-key",
                llm_enabled=False,
                body_fetch_enabled=False,
                price_reaction_enabled=False,
                verification_enabled=False,
                max_brave_requests_per_run=3,
                max_scout_queries_per_run=2,
                max_discovery_queries_per_run=0,
                max_discovery_results_per_query=5,
            )
            base_source = NewsSource(
                name="fixture-brave-base",
                category="GEO",
                provider="brave",
                url="fixture://base",
            )
            base_result = _result(base_source)

            def fetch_side_effect(sources, **kwargs):
                self.assertEqual(kwargs["brave_api_key"], "brave-key")
                if sources == (base_source,):
                    return (base_result,)
                self.assertEqual(len(sources), 2)
                source_names = [source.name for source in sources]
                self.assertIn(
                    "brave-scout-1-breaking-geo-policy-speaker",
                    source_names,
                )
                self.assertIn(
                    "brave-scout-2-breaking-strat-industrial-ai",
                    source_names,
                )
                results = []
                for source in sources:
                    if "geo-policy" in source.name:
                        results.append(
                            _result(
                                source,
                                _item(
                                    source,
                                    (
                                        "Rubio warns Hormuz toll would break Iran "
                                        "talks as Pakistan delegation heads to Tehran"
                                    ),
                                    url="https://reuters.example/rubio-hormuz",
                                ),
                            )
                        )
                    else:
                        results.append(
                            _result(
                                source,
                                _item(
                                    source,
                                    (
                                        "Kawasaki Heavy and NVIDIA launch physical "
                                        "AI robot center with Microsoft and Fujitsu"
                                    ),
                                    url="https://nikkei.example/kawasaki-nvidia",
                                ),
                            )
                        )
                return tuple(results)

            with patch("news_scanner_v2.pipeline.DEFAULT_SOURCES", (base_source,)), patch(
                "news_scanner_v2.pipeline.fetch_sources",
                side_effect=fetch_side_effect,
            ):
                summary = run_shadow(config, as_of=AS_OF)

            self.assertEqual(summary["status"], "ok")
            self.assertEqual(summary["scout_lanes_active"], True)
            self.assertEqual(summary["breaking_hint_line_count"], 2)
            self.assertEqual(summary["breaking_hint_items"], 2)
            self.assertEqual(summary["scout_query_count"], 2)
            self.assertEqual(summary["scout_fetch_items"], 2)
            self.assertEqual(summary["candidate_items_seen"], 4)
            self.assertEqual(summary["brave_requests_planned_total_max"], 3)

            con = sqlite3.connect(config.db_path)
            candidate_titles = [
                row[0]
                for row in con.execute(
                    "select title from candidate_items order by title"
                ).fetchall()
            ]
            self.assertTrue(any("Rubio warns Hormuz toll" in title for title in candidate_titles))
            self.assertTrue(any("Kawasaki Heavy and NVIDIA" in title for title in candidate_titles))
            self.assertTrue(any("Rubio says Hormuz toll" in title for title in candidate_titles))
            self.assertTrue(any("physical AI partnership" in title for title in candidate_titles))
            attempts = con.execute(
                "select source, category, kept_count from source_attempts order by source"
            ).fetchall()
            self.assertEqual(
                attempts,
                [
                    ("brave-scout-1-breaking-geo-policy-speaker", "GEO", 1),
                    ("brave-scout-2-breaking-strat-industrial-ai", "STRAT", 1),
                    ("breaking-hints-geo", "GEO", 1),
                    ("breaking-hints-strat", "STRAT", 1),
                    ("fixture-brave-base", "GEO", 0),
                ],
            )



    def test_run_shadow_uses_initial_fetch_ai_platform_item_for_linked_mover_scout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_legacy_jobs(root)
            config = build_config(
                legacy_root=root,
                db_path=root / "state" / "news_scanner_v2.sqlite",
                shadow_dir=root / "shadow",
                brave_enabled=True,
                brave_api_key="brave-key",
                llm_enabled=False,
                body_fetch_enabled=False,
                price_reaction_enabled=False,
                verification_enabled=False,
                max_brave_requests_per_run=2,
                max_scout_queries_per_run=1,
                max_discovery_queries_per_run=0,
                max_discovery_results_per_query=5,
            )
            base_source = NewsSource(
                name="fixture-brave-strat",
                category="STRAT",
                provider="brave",
                url="fixture://base",
            )
            base_result = _result(
                base_source,
                _item(
                    base_source,
                    "Nvidia GTC AI PC platform launch pressures Intel and Qualcomm while Dell and HP rally",
                    url="https://example.com/nvidia-ai-pc",
                ),
            )

            def fetch_side_effect(sources, **kwargs):
                self.assertEqual(kwargs["brave_api_key"], "brave-key")
                if sources == (base_source,):
                    return (base_result,)
                self.assertEqual(len(sources), 1)
                self.assertEqual(sources[0].name, "brave-scout-1-event-linked-ai-pc-movers")
                self.assertEqual(sources[0].category, "MOVE")
                self.assertIn("Dell", sources[0].query)
                self.assertIn("Qualcomm", sources[0].query)
                return (
                    _result(
                        sources[0],
                        _item(
                            sources[0],
                            "Dell shares jump 10% as Nvidia AI PC launch boosts OEM demand",
                            url="https://example.com/dell-ai-pc-mover",
                        ),
                    ),
                )

            with patch("news_scanner_v2.pipeline.DEFAULT_SOURCES", (base_source,)), patch(
                "news_scanner_v2.pipeline.fetch_sources",
                side_effect=fetch_side_effect,
            ):
                summary = run_shadow(config, as_of=AS_OF)

            self.assertEqual(summary["status"], "ok")
            self.assertEqual(summary["breaking_hint_line_count"], 0)
            self.assertEqual(summary["scout_query_count"], 1)
            self.assertEqual(summary["scout_fetch_items"], 1)
            self.assertGreaterEqual(summary["scout_hint_count"], 1)

            con = sqlite3.connect(config.db_path)
            attempts = con.execute(
                "select source, category, kept_count from source_attempts order by source"
            ).fetchall()
            self.assertEqual(
                attempts,
                [
                    ("brave-scout-1-event-linked-ai-pc-movers", "MOVE", 1),
                    ("fixture-brave-strat", "STRAT", 1),
                ],
            )

    def test_run_shadow_filters_old_items_before_event_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _config(root)
            source = _source("fixture-old", "EARN")
            result = _result(
                source,
                _item(
                    source,
                    "Nvidia (NVDA) raises guidance after Q1 earnings",
                    url="https://example.com/old-nvda",
                    published_at="2026-05-09T12:45:00+00:00",
                ),
            )

            with patch("news_scanner_v2.pipeline.DEFAULT_SOURCES", (source,)), patch(
                "news_scanner_v2.pipeline.fetch_sources",
                return_value=(result,),
            ):
                summary = run_shadow(config, as_of=AS_OF)

            self.assertEqual(summary["candidate_items_raw"], 1)
            self.assertEqual(summary["candidate_items_seen"], 0)
            self.assertEqual(summary["candidate_items_inserted"], 0)
            self.assertEqual(summary["events_extracted"], 0)
            self.assertEqual(summary["events_inserted"], 0)
            self.assertEqual(summary["dispatch_decisions_evaluated"], 0)
            self.assertEqual(summary["dispatch_decisions_inserted"], 0)

            con = sqlite3.connect(config.db_path)
            self.assertEqual(
                con.execute("select kept_count from source_attempts").fetchone()[0],
                0,
            )

    def test_run_shadow_reuses_event_across_runs_but_keeps_run_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _config(root)
            source = _source("fixture-earnings", "EARN")
            result = _result(
                source,
                _item(
                    source,
                    "Nvidia (NVDA) raises guidance after Q1 earnings",
                    url="https://reuters.com/nvda",
                ),
            )

            with patch("news_scanner_v2.pipeline.DEFAULT_SOURCES", (source,)), patch(
                "news_scanner_v2.pipeline.fetch_sources",
                return_value=(result,),
            ):
                first = run_shadow(config, as_of=AS_OF)
                second = run_shadow(config, as_of=AS_OF)

            self.assertNotEqual(first["run_id"], second["run_id"])
            self.assertEqual(first["events_inserted"], 1)
            self.assertEqual(second["events_inserted"], 0)
            self.assertEqual(first["dispatch_decisions_inserted"], 1)
            self.assertEqual(second["dispatch_decisions_inserted"], 1)

            con = sqlite3.connect(config.db_path)
            counts = {
                table: con.execute(f"select count(*) from {table}").fetchone()[0]
                for table in (
                    "runs",
                    "candidate_items",
                    "events",
                    "candidate_events",
                    "dispatch_decisions",
                    "deliveries",
                )
            }
            self.assertEqual(
                counts,
                {
                    "runs": 2,
                    "candidate_items": 2,
                    "events": 1,
                    "candidate_events": 2,
                    "dispatch_decisions": 2,
                    "deliveries": 0,
                },
            )
            self.assertEqual(
                con.execute(
                    "select count(distinct run_id) from dispatch_decisions"
                ).fetchone()[0],
                2,
            )
            self.assertEqual(con.execute("pragma foreign_key_check").fetchall(), [])

    def test_run_shadow_writes_summary_file_with_dispatch_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _config(root)
            source = _source("fixture-earnings", "EARN")
            result = _result(
                source,
                _item(
                    source,
                    "Nvidia (NVDA) raises guidance after Q1 earnings",
                    url="https://reuters.com/nvda",
                ),
            )

            with patch("news_scanner_v2.pipeline.DEFAULT_SOURCES", (source,)), patch(
                "news_scanner_v2.pipeline.fetch_sources",
                return_value=(result,),
            ):
                summary = run_shadow(config, as_of=AS_OF)

            out_path = Path(str(summary["shadow_output"]))
            self.assertTrue(out_path.exists())
            payload = json.loads(out_path.read_text())
            self.assertEqual(payload["run_id"], summary["run_id"])
            self.assertEqual(payload["dispatch_decisions_evaluated"], 1)
            self.assertEqual(payload["dispatch_decisions_inserted"], 1)
            self.assertEqual(
                payload["dispatch_decisions_by_decision"], {"send_candidate": 1}
            )


if __name__ == "__main__":
    unittest.main()
