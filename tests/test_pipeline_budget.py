from datetime import datetime
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from news_scanner_v2.config import build_config
from news_scanner_v2.pipeline import run_shadow
from news_scanner_v2.sources import BRAVE_NEWS_SOURCES


def _write_legacy_jobs(root: Path) -> None:
    cron = root / "cron"
    cron.mkdir(parents=True)
    message = "legacy prompt"
    payload = {
        "kind": "agentTurn",
        "message": message,
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
            },
            {
                "id": "52ed935b-f85e-4203-bbac-ce4e87bc8913",
                "name": "US Market News Scanner (Half-hour)",
                "enabled": True,
                "schedule": {"expr": "30 22 * * *", "tz": "Asia/Seoul"},
                "delivery": {"mode": "none"},
                "sessionTarget": "isolated",
                "wakeMode": "now",
                "payload": payload,
                "state": {},
            },
        ]
    }
    (cron / "jobs.json").write_text(json.dumps(jobs))


class PipelineBudgetTests(unittest.TestCase):
    def test_run_shadow_blocks_before_fetch_when_brave_budget_exceeded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_legacy_jobs(root)
            config = build_config(
                legacy_root=root,
                db_path=root / "state" / "news_scanner_v2.sqlite",
                shadow_dir=root / "shadow",
                max_brave_requests_per_run=7,
            )
            with patch(
                "news_scanner_v2.pipeline.DEFAULT_SOURCES",
                BRAVE_NEWS_SOURCES + BRAVE_NEWS_SOURCES[:1],
            ), patch("news_scanner_v2.pipeline.fetch_sources") as fetch_mock:
                summary = run_shadow(
                    config,
                    as_of=datetime(2026, 5, 14, 17, 0, tzinfo=ZoneInfo("Asia/Seoul")),
                )

            self.assertEqual(summary["status"], "blocked_budget")
            self.assertEqual(summary["brave_requests_planned"], 8)
            self.assertEqual(summary["brave_requests_used"], 0)
            fetch_mock.assert_not_called()

            con = sqlite3.connect(config.db_path)
            self.assertEqual(
                con.execute("select status from runs").fetchone()[0],
                "blocked_budget",
            )


if __name__ == "__main__":
    unittest.main()
