from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import csv
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from news_scanner_v2.cli import main
from news_scanner_v2.config import build_config
from news_scanner_v2.db import init_db
from news_scanner_v2.fetcher import FetchResult
from news_scanner_v2.models import CandidateItem
from news_scanner_v2.pipeline import run_shadow
from news_scanner_v2.reports import (
    ReportError,
    load_decision_rows,
    load_price_reaction_report,
    render_decision_report,
    render_price_reaction_report,
)
from news_scanner_v2.sources import NewsSource


AS_OF = datetime(2026, 5, 14, 22, 0, tzinfo=ZoneInfo("Asia/Seoul"))
PRICE_REACTION_OK = {
    "status": "ok",
    "provider": "polygon",
    "ticker": "NVDA",
    "price_as_of": "2026-05-14",
    "price_as_of_at": "2026-05-14T14:00:00+00:00",
    "session": "intraday_5min",
    "basis": "polygon_aggregate",
    "close": 105.0,
    "previous_close": 100.0,
    "pct_change": 5.0,
    "direction": "up",
    "stale": False,
}


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


def _source(name: str, category: str, provider: str = "fixture") -> NewsSource:
    return NewsSource(
        name=name,
        category=category,
        provider=provider,
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


def _build_report_fixture(root: Path):
    _write_legacy_jobs(root)
    config = build_config(
        legacy_root=root,
        db_path=root / "state" / "news_scanner_v2.sqlite",
        shadow_dir=root / "shadow",
        brave_enabled=False,
    )
    brave_earn = _source("brave-earnings", "EARN", provider="brave")
    rss_earn = _source("rss-earnings", "EARN", provider="google_rss")
    official_macro = _source("fed", "MACRO", provider="official_rss")
    analyst = _source("analyst", "ANAL", provider="brave")
    fetch_results = (
        _result(
            brave_earn,
            _item(
                brave_earn,
                "Nvidia (NVDA) raises guidance after Q1 earnings",
                url="https://example.com/nvda-a",
                summary=(
                    "Nvidia raised its full-year outlook after Q1 earnings, "
                    "citing stronger AI accelerator demand and data-center orders."
                ),
            ),
        ),
        _result(
            rss_earn,
            _item(
                rss_earn,
                "NVIDIA raises guidance after earnings beat",
                url="https://example.com/nvda-b",
                summary=(
                    "NVIDIA reported an earnings beat and lifted guidance as "
                    "cloud customers expanded AI infrastructure spending."
                ),
            ),
        ),
        _result(
            official_macro,
            _item(
                official_macro,
                "Federal Reserve Board issues rate policy statement",
                url="https://example.com/fed",
            ),
        ),
        _result(
            analyst,
            _item(
                analyst,
                "Veteran analyst resets Apple stock price target for 2026",
                url="https://example.com/aapl",
            ),
        ),
    )

    with patch(
        "news_scanner_v2.pipeline.DEFAULT_SOURCES",
        (brave_earn, rss_earn, official_macro, analyst),
    ), patch(
        "news_scanner_v2.pipeline.fetch_sources",
        return_value=fetch_results,
    ), patch(
        "news_scanner_v2.price_reaction.fetch_price_reaction",
        return_value=PRICE_REACTION_OK,
    ):
        summary = run_shadow(config, as_of=AS_OF)
    return config, summary


class DecisionReportTests(unittest.TestCase):
    def test_load_decision_rows_latest_run_includes_labeling_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config, summary = _build_report_fixture(Path(tmp))

            report = load_decision_rows(config.db_path)

            self.assertEqual(report["run"]["id"], summary["run_id"])
            self.assertEqual(report["row_count"], 3)
            self.assertEqual(
                report["decision_counts"],
                {"send_candidate": 1, "review": 1, "reject": 1},
            )
            first = report["rows"][0]
            self.assertEqual(first["decision"], "send_candidate")
            self.assertEqual(first["subject"], "nvda")
            self.assertEqual(first["evidence_count"], 2)
            self.assertEqual(first["providers"], ["brave", "google_rss"])
            self.assertEqual(len(first["evidence_items"]), 2)
            self.assertEqual(first["evidence_items"][0]["provider"], "brave")
            self.assertIn("AI accelerator demand", first["evidence_items"][0]["summary"])
            self.assertTrue(first["score_reasons"])
            self.assertTrue(first["extractor_reasons"])
            self.assertEqual(first["evidence_contract"]["version"], "evidence_contract_v1")
            self.assertTrue(first["evidence_contract"]["delivery_eligible"])
            self.assertEqual(first["contract_status"], "warn")
            self.assertEqual(first["contract_warnings"], ["unknown_earnings_event_date"])
            self.assertEqual(first["price_reaction"]["status"], "ok")
            self.assertEqual(first["price_reaction"]["ticker"], "NVDA")

    def test_load_decision_rows_exposes_best_body_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config, _summary = _build_report_fixture(Path(tmp))
            initial = load_decision_rows(config.db_path)
            candidate_id = initial["rows"][0]["candidate_ids"][0]
            con = sqlite3.connect(config.db_path)
            try:
                row = con.execute(
                    "select id, raw_json from candidate_items where id = ?",
                    (candidate_id,),
                ).fetchone()
                raw = json.loads(row[1])
                raw["body_text"] = "Full article body " * 80
                raw["body_fetch"] = {
                    "status": "full",
                    "text_chars": len(raw["body_text"]),
                    "http_status": 200,
                }
                con.execute(
                    "update candidate_items set raw_json = ? where id = ?",
                    (json.dumps(raw), row[0]),
                )
                con.commit()
            finally:
                con.close()

            report = load_decision_rows(config.db_path)

            first = report["rows"][0]
            self.assertIn("Full article body", first["body_text"])
            self.assertIn("body_text", first["evidence_items"][0])
            self.assertEqual(first["evidence_items"][0]["body_fetch"]["status"], "full")

    def test_verified_earnings_report_uses_verification_match_as_fact_basis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config, _summary = _build_report_fixture(Path(tmp))
            con = sqlite3.connect(config.db_path)
            try:
                row = con.execute(
                    """
                    select id, payload_json
                    from dispatch_decisions
                    where decision = 'send_candidate'
                    limit 1
                    """
                ).fetchone()
                payload = json.loads(row[1])
                payload["source_tier"] = "trusted"
                payload["verification_status"] = "verified"
                payload["verification"] = {
                    "status": "verified",
                    "provider": "brave",
                    "query": "NVDA earnings",
                    "match": {
                        "title": "Nvidia verified Q1 results",
                        "url": "https://www.cnbc.com/2026/05/14/nvidia-q1-results.html",
                        "summary": (
                            "Nvidia reported EPS of $3.88 per share and "
                            "revenue of $11.13 billion."
                        ),
                        "published_at": "2026-05-14T12:45:00+00:00",
                        "source": "brave-news-verification-1",
                        "provider": "brave",
                        "category": "VERIFY",
                    },
                }
                con.execute(
                    "update dispatch_decisions set payload_json = ? where id = ?",
                    (json.dumps(payload), row[0]),
                )
                con.commit()
            finally:
                con.close()

            report = load_decision_rows(config.db_path)

            first = report["rows"][0]
            self.assertEqual(len(first["evidence_items"]), 1)
            self.assertEqual(first["evidence_items"][0]["candidate_id"], "verification")
            self.assertEqual(first["evidence_items"][0]["category"], "VERIFY")
            self.assertIn("Nvidia reported EPS", first["evidence_items"][0]["summary"])
            facts = {
                (fact["kind"], fact["value"])
                for fact in first["earnings_fact_contract"]["facts"]
            }
            self.assertIn(("eps", "$3.88"), facts)
            self.assertIn(("revenue", "$11.13B"), facts)

    def test_markdown_csv_and_json_renderers_are_labeling_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config, _summary = _build_report_fixture(Path(tmp))
            report = load_decision_rows(config.db_path)

            markdown = render_decision_report(report, output_format="markdown")
            csv_text = render_decision_report(report, output_format="csv")
            json_text = render_decision_report(report, output_format="json")

            self.assertIn("# News Scanner V2 Decision Report", markdown)
            self.assertIn("| decision | score | event_type |", markdown)
            self.assertIn("| send_candidate |", markdown)
            csv_rows = list(csv.DictReader(io.StringIO(csv_text)))
            self.assertEqual(len(csv_rows), 3)
            self.assertEqual(csv_rows[0]["decision"], "send_candidate")
            parsed = json.loads(json_text)
            self.assertEqual(parsed["row_count"], 3)
            self.assertEqual(len(parsed["rows"]), 3)

    def test_price_reaction_report_summarizes_company_send_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config, summary = _build_report_fixture(Path(tmp))

            report = load_price_reaction_report(config.db_path)
            markdown = render_price_reaction_report(
                report,
                output_format="markdown",
            )
            json_text = render_price_reaction_report(report, output_format="json")

            self.assertEqual(report["run"]["id"], summary["run_id"])
            self.assertEqual(report["source_decision_rows"], 1)
            self.assertEqual(report["company_rows"], 1)
            self.assertEqual(report["required_company_send_candidates"], 1)
            self.assertEqual(report["eligible_company_send_candidates"], 1)
            self.assertEqual(report["blocked_company_send_candidates"], 0)
            self.assertEqual(report["delivered_company_rows"], 0)
            self.assertEqual(report["delivered_ineligible_company_rows"], 0)
            self.assertEqual(report["price_status_counts"], {"ok": 1})
            self.assertEqual(report["price_direction_counts"], {"up": 1})
            row = report["rows"][0]
            self.assertEqual(row["subject"], "nvda")
            self.assertEqual(row["price_status"], "ok")
            self.assertEqual(row["direction"], "up")
            self.assertTrue(row["delivery_eligible"])
            self.assertFalse(row["delivered"])
            self.assertIn("# News Scanner V2 Price Reaction Report", markdown)
            self.assertIn("| send_candidate |", markdown)
            parsed = json.loads(json_text)
            self.assertEqual(parsed["company_rows"], 1)

    def test_price_reaction_report_exposes_missing_price_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config, _summary = _build_report_fixture(Path(tmp))
            con = sqlite3.connect(config.db_path)
            try:
                row = con.execute(
                    """
                    select id, event_signature, payload_json
                    from dispatch_decisions
                    where decision = 'send_candidate'
                    limit 1
                    """
                ).fetchone()
                payload = json.loads(row[2])
                payload.pop("price_reaction", None)
                payload.pop("price_reaction_required", None)
                event = payload.get("event", {})
                if isinstance(event, dict):
                    metadata = event.get("metadata", {})
                    if isinstance(metadata, dict):
                        metadata.pop("price_reaction", None)
                con.execute(
                    "update dispatch_decisions set payload_json = ? where id = ?",
                    (json.dumps(payload), row[0]),
                )

                event_row = con.execute(
                    "select payload_json from events where signature = ?",
                    (row[1],),
                ).fetchone()
                event_payload = json.loads(event_row[0])
                metadata = event_payload.get("metadata", {})
                if isinstance(metadata, dict):
                    metadata.pop("price_reaction", None)
                con.execute(
                    "update events set payload_json = ? where signature = ?",
                    (json.dumps(event_payload), row[1]),
                )
                con.commit()
            finally:
                con.close()

            report = load_price_reaction_report(config.db_path)

            self.assertEqual(report["company_rows"], 1)
            self.assertEqual(report["required_company_send_candidates"], 1)
            self.assertEqual(report["eligible_company_send_candidates"], 1)
            self.assertEqual(report["blocked_company_send_candidates"], 0)
            self.assertEqual(report["price_status_counts"], {"missing": 1})
            self.assertEqual(report["contract_failure_counts"], {})
            row = report["rows"][0]
            self.assertEqual(row["price_status"], "missing")
            self.assertTrue(row["delivery_eligible"])

    def test_decision_filter_and_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config, _summary = _build_report_fixture(Path(tmp))

            report = load_decision_rows(
                config.db_path,
                decisions={"review", "reject"},
                limit=1,
            )

            self.assertEqual(report["row_count"], 1)
            self.assertEqual(report["rows"][0]["decision"], "review")

    def test_missing_and_empty_db_fail_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(ReportError, "DB does not exist"):
                load_decision_rows(root / "missing.sqlite")

            empty_db = root / "empty.sqlite"
            init_db(empty_db)
            with self.assertRaisesRegex(ReportError, "no runs found"):
                load_decision_rows(empty_db)

            with self.assertRaisesRegex(ReportError, "run not found"):
                load_decision_rows(empty_db, run_id="not-a-run")

    def test_cli_report_decisions_writes_output_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config, _summary = _build_report_fixture(root)
            output = root / "labels" / "decisions.csv"

            exit_code = main(
                [
                    "report",
                    "decisions",
                    "--db-path",
                    str(config.db_path),
                    "--format",
                    "csv",
                    "--decision",
                    "send_candidate",
                    "--output",
                    str(output),
                ]
            )

            self.assertEqual(exit_code, 0)
            rows = list(csv.DictReader(io.StringIO(output.read_text())))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["decision"], "send_candidate")

    def test_cli_report_price_reaction_writes_output_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config, _summary = _build_report_fixture(root)
            output = root / "labels" / "price-reaction.md"

            exit_code = main(
                [
                    "report",
                    "price-reaction",
                    "--db-path",
                    str(config.db_path),
                    "--output",
                    str(output),
                ]
            )

            self.assertEqual(exit_code, 0)
            text = output.read_text()
            self.assertIn("# News Scanner V2 Price Reaction Report", text)
            self.assertIn("| send_candidate |", text)

    def test_cli_report_decisions_missing_db_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stderr = io.StringIO()
            stdout = io.StringIO()

            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main(
                    [
                        "report",
                        "decisions",
                        "--db-path",
                        str(Path(tmp) / "missing.sqlite"),
                    ]
                )

            self.assertEqual(exit_code, 1)
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn("DB does not exist", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
