from pathlib import Path
import sqlite3
import tempfile
import unittest

from news_scanner_v2.db import init_db


class DbSchemaTests(unittest.TestCase):
    def test_init_db_creates_event_link_table_and_delete_journal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "state" / "news_scanner_v2.sqlite"
            init_db(db_path)

            con = sqlite3.connect(db_path)
            tables = {
                row[0]
                for row in con.execute(
                    "select name from sqlite_master where type = 'table'"
                )
            }
            indexes = {
                row[0]
                for row in con.execute(
                    "select name from sqlite_master where type = 'index'"
                )
            }
            candidate_event_columns = {
                row[1] for row in con.execute("pragma table_info(candidate_events)")
            }
            news_seed_columns = {
                row[1] for row in con.execute("pragma table_info(news_seeds)")
            }
            dispatch_columns = {
                row[1] for row in con.execute("pragma table_info(dispatch_decisions)")
            }
            llm_columns = {
                row[1] for row in con.execute("pragma table_info(llm_annotations)")
            }
            market_snapshot_columns = {
                row[1] for row in con.execute("pragma table_info(market_snapshots)")
            }

            self.assertEqual(con.execute("pragma journal_mode").fetchone()[0], "delete")
            self.assertIn("events", tables)
            self.assertIn("candidate_events", tables)
            self.assertIn("news_seeds", tables)
            self.assertIn("dispatch_decisions", tables)
            self.assertIn("llm_annotations", tables)
            self.assertIn("market_snapshots", tables)
            self.assertIn("idx_events_type_subject_date", indexes)
            self.assertIn("idx_candidate_events_signature", indexes)
            self.assertIn("idx_news_seeds_run_id", indexes)
            self.assertIn("idx_news_seeds_key", indexes)
            self.assertIn("idx_dispatch_decisions_event_signature", indexes)
            self.assertIn("idx_llm_annotations_run_event", indexes)
            self.assertIn("idx_llm_annotations_cache", indexes)
            self.assertIn("idx_market_snapshots_run_id", indexes)
            self.assertIn("idx_market_snapshots_created_at", indexes)
            self.assertEqual(
                candidate_event_columns,
                {
                    "id",
                    "run_id",
                    "candidate_id",
                    "event_signature",
                    "extractor",
                    "confidence",
                    "reason",
                    "created_at",
                },
            )
            self.assertEqual(
                news_seed_columns,
                {
                    "id",
                    "run_id",
                    "seed_key",
                    "seed_type",
                    "subject",
                    "theme",
                    "freshness",
                    "market_relevance",
                    "source_count",
                    "evidence_count",
                    "payload_json",
                    "created_at",
                },
            )
            self.assertEqual(
                dispatch_columns,
                {
                    "id",
                    "run_id",
                    "event_signature",
                    "decision",
                    "reason",
                    "policy",
                    "score",
                    "payload_json",
                    "created_at",
                },
            )
            self.assertEqual(
                llm_columns,
                {
                    "id",
                    "run_id",
                    "event_signature",
                    "annotation_type",
                    "provider",
                    "model",
                    "prompt_version",
                    "evidence_hash",
                    "status",
                    "payload_json",
                    "error",
                    "created_at",
                },
            )
            self.assertEqual(
                market_snapshot_columns,
                {
                    "id",
                    "run_id",
                    "as_of",
                    "status",
                    "provider",
                    "payload_json",
                    "created_at",
                },
            )

    def test_init_db_migrates_legacy_dispatch_decision_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "state" / "news_scanner_v2.sqlite"
            db_path.parent.mkdir(parents=True)
            con = sqlite3.connect(db_path)
            con.execute(
                """
                CREATE TABLE dispatch_decisions (
                  id TEXT PRIMARY KEY,
                  run_id TEXT NOT NULL,
                  event_signature TEXT NOT NULL,
                  decision TEXT NOT NULL,
                  reason TEXT NOT NULL,
                  created_at TEXT NOT NULL
                )
                """
            )
            con.commit()
            con.close()

            init_db(db_path)

            con = sqlite3.connect(db_path)
            dispatch_columns = {
                row[1] for row in con.execute("pragma table_info(dispatch_decisions)")
            }
            self.assertIn("policy", dispatch_columns)
            self.assertIn("score", dispatch_columns)
            self.assertIn("payload_json", dispatch_columns)

    def test_init_db_migrates_legacy_news_seed_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "state" / "news_scanner_v2.sqlite"
            db_path.parent.mkdir(parents=True)
            con = sqlite3.connect(db_path)
            con.execute(
                """
                CREATE TABLE news_seeds (
                  id TEXT PRIMARY KEY,
                  run_id TEXT NOT NULL,
                  seed_key TEXT NOT NULL,
                  seed_type TEXT NOT NULL,
                  subject TEXT NOT NULL,
                  theme TEXT NOT NULL
                )
                """
            )
            con.commit()
            con.close()

            init_db(db_path)

            con = sqlite3.connect(db_path)
            seed_columns = {
                row[1] for row in con.execute("pragma table_info(news_seeds)")
            }
            self.assertIn("freshness", seed_columns)
            self.assertIn("market_relevance", seed_columns)
            self.assertIn("source_count", seed_columns)
            self.assertIn("evidence_count", seed_columns)
            self.assertIn("payload_json", seed_columns)
            self.assertIn("created_at", seed_columns)


if __name__ == "__main__":
    unittest.main()
