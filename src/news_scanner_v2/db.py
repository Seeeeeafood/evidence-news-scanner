from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any


SCHEMA = """
PRAGMA journal_mode=DELETE;

CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  as_of TEXT NOT NULL,
  mode TEXT NOT NULL,
  status TEXT NOT NULL,
  dispatch_enabled INTEGER NOT NULL DEFAULT 0,
  llm_enabled INTEGER NOT NULL DEFAULT 0,
  legacy_prompt_hash TEXT,
  legacy_snapshot_json TEXT NOT NULL,
  error TEXT
);

CREATE TABLE IF NOT EXISTS candidate_items (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  source TEXT NOT NULL,
  provider TEXT NOT NULL DEFAULT '',
  category TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL,
  normalized_title TEXT NOT NULL DEFAULT '',
  url TEXT,
  canonical_url TEXT NOT NULL DEFAULT '',
  published_at TEXT,
  fetched_at TEXT NOT NULL DEFAULT '',
  item_hash TEXT NOT NULL DEFAULT '',
  raw_json TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES runs(id)
);

CREATE INDEX IF NOT EXISTS idx_candidate_items_run_id
ON candidate_items(run_id);

CREATE INDEX IF NOT EXISTS idx_candidate_items_item_hash
ON candidate_items(item_hash);

CREATE TABLE IF NOT EXISTS news_seeds (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  seed_key TEXT NOT NULL,
  seed_type TEXT NOT NULL,
  subject TEXT NOT NULL,
  theme TEXT NOT NULL,
  freshness TEXT NOT NULL DEFAULT '',
  market_relevance TEXT NOT NULL DEFAULT '',
  source_count INTEGER NOT NULL DEFAULT 0,
  evidence_count INTEGER NOT NULL DEFAULT 0,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES runs(id)
);

CREATE INDEX IF NOT EXISTS idx_news_seeds_run_id
ON news_seeds(run_id);

CREATE INDEX IF NOT EXISTS idx_news_seeds_key
ON news_seeds(seed_key);

CREATE TABLE IF NOT EXISTS source_attempts (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  source TEXT NOT NULL,
  provider TEXT NOT NULL DEFAULT '',
  category TEXT NOT NULL,
  url TEXT NOT NULL,
  query TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL,
  item_count INTEGER NOT NULL DEFAULT 0,
  kept_count INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  started_at TEXT NOT NULL,
  finished_at TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES runs(id)
);

CREATE INDEX IF NOT EXISTS idx_source_attempts_run_id
ON source_attempts(run_id);

CREATE TABLE IF NOT EXISTS market_snapshots (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  as_of TEXT NOT NULL,
  status TEXT NOT NULL,
  provider TEXT NOT NULL DEFAULT '',
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES runs(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_market_snapshots_run_id
ON market_snapshots(run_id);

CREATE INDEX IF NOT EXISTS idx_market_snapshots_created_at
ON market_snapshots(created_at);

CREATE TABLE IF NOT EXISTS events (
  signature TEXT PRIMARY KEY,
  first_seen_run_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  subject TEXT NOT NULL,
  effective_date TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(first_seen_run_id) REFERENCES runs(id)
);

CREATE INDEX IF NOT EXISTS idx_events_type_subject_date
ON events(event_type, subject, effective_date);

CREATE TABLE IF NOT EXISTS candidate_events (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  candidate_id TEXT NOT NULL,
  event_signature TEXT NOT NULL,
  extractor TEXT NOT NULL,
  confidence REAL NOT NULL,
  reason TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES runs(id),
  FOREIGN KEY(candidate_id) REFERENCES candidate_items(id),
  FOREIGN KEY(event_signature) REFERENCES events(signature)
);

CREATE INDEX IF NOT EXISTS idx_candidate_events_run_id
ON candidate_events(run_id);

CREATE INDEX IF NOT EXISTS idx_candidate_events_signature
ON candidate_events(event_signature);

CREATE TABLE IF NOT EXISTS dispatch_decisions (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  event_signature TEXT NOT NULL,
  decision TEXT NOT NULL,
  reason TEXT NOT NULL,
  policy TEXT NOT NULL DEFAULT '',
  score REAL NOT NULL DEFAULT 0,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES runs(id),
  FOREIGN KEY(event_signature) REFERENCES events(signature)
);

CREATE INDEX IF NOT EXISTS idx_dispatch_decisions_run_id
ON dispatch_decisions(run_id);

CREATE INDEX IF NOT EXISTS idx_dispatch_decisions_event_signature
ON dispatch_decisions(event_signature);

CREATE TABLE IF NOT EXISTS deliveries (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  event_signature TEXT,
  channel TEXT NOT NULL,
  status TEXT NOT NULL,
  message_id TEXT,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS llm_annotations (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  event_signature TEXT NOT NULL,
  annotation_type TEXT NOT NULL DEFAULT 'summary',
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  evidence_hash TEXT NOT NULL,
  status TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  error TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES runs(id),
  FOREIGN KEY(event_signature) REFERENCES events(signature)
);

CREATE INDEX IF NOT EXISTS idx_llm_annotations_run_event
ON llm_annotations(run_id, event_signature, annotation_type);

CREATE INDEX IF NOT EXISTS idx_llm_annotations_cache
ON llm_annotations(event_signature, annotation_type, model, prompt_version, evidence_hash, status);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Path) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        _ensure_candidate_columns(conn)
        _ensure_news_seed_columns(conn)
        _ensure_dispatch_columns(conn)
        _ensure_llm_annotation_columns(conn)
        _ensure_market_snapshot_columns(conn)


def _ensure_candidate_columns(conn: sqlite3.Connection) -> None:
    existing = {
        row["name"] for row in conn.execute("PRAGMA table_info(candidate_items)")
    }
    desired = {
        "provider": "TEXT NOT NULL DEFAULT ''",
        "category": "TEXT NOT NULL DEFAULT ''",
        "normalized_title": "TEXT NOT NULL DEFAULT ''",
        "canonical_url": "TEXT NOT NULL DEFAULT ''",
        "fetched_at": "TEXT NOT NULL DEFAULT ''",
        "item_hash": "TEXT NOT NULL DEFAULT ''",
    }
    for column, definition in desired.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE candidate_items ADD COLUMN {column} {definition}")

    source_existing = {
        row["name"] for row in conn.execute("PRAGMA table_info(source_attempts)")
    }
    if "kept_count" not in source_existing:
        conn.execute(
            "ALTER TABLE source_attempts ADD COLUMN kept_count INTEGER NOT NULL DEFAULT 0"
        )
    if "provider" not in source_existing:
        conn.execute(
            "ALTER TABLE source_attempts ADD COLUMN provider TEXT NOT NULL DEFAULT ''"
        )
    if "query" not in source_existing:
        conn.execute(
            "ALTER TABLE source_attempts ADD COLUMN query TEXT NOT NULL DEFAULT ''"
        )


def _ensure_news_seed_columns(conn: sqlite3.Connection) -> None:
    seed_existing = {
        row["name"] for row in conn.execute("PRAGMA table_info(news_seeds)")
    }
    if not seed_existing:
        return
    desired = {
        "freshness": "TEXT NOT NULL DEFAULT ''",
        "market_relevance": "TEXT NOT NULL DEFAULT ''",
        "source_count": "INTEGER NOT NULL DEFAULT 0",
        "evidence_count": "INTEGER NOT NULL DEFAULT 0",
        "payload_json": "TEXT NOT NULL DEFAULT '{}'",
        "created_at": "TEXT NOT NULL DEFAULT ''",
    }
    for column, definition in desired.items():
        if column not in seed_existing:
            conn.execute(f"ALTER TABLE news_seeds ADD COLUMN {column} {definition}")


def _ensure_dispatch_columns(conn: sqlite3.Connection) -> None:
    dispatch_existing = {
        row["name"] for row in conn.execute("PRAGMA table_info(dispatch_decisions)")
    }
    desired = {
        "policy": "TEXT NOT NULL DEFAULT ''",
        "score": "REAL NOT NULL DEFAULT 0",
        "payload_json": "TEXT NOT NULL DEFAULT '{}'",
    }
    for column, definition in desired.items():
        if column not in dispatch_existing:
            conn.execute(
                f"ALTER TABLE dispatch_decisions ADD COLUMN {column} {definition}"
            )


def _ensure_llm_annotation_columns(conn: sqlite3.Connection) -> None:
    annotation_existing = {
        row["name"] for row in conn.execute("PRAGMA table_info(llm_annotations)")
    }
    if not annotation_existing:
        return
    desired = {
        "annotation_type": "TEXT NOT NULL DEFAULT 'summary'",
        "provider": "TEXT NOT NULL DEFAULT ''",
        "model": "TEXT NOT NULL DEFAULT ''",
        "prompt_version": "TEXT NOT NULL DEFAULT ''",
        "evidence_hash": "TEXT NOT NULL DEFAULT ''",
        "status": "TEXT NOT NULL DEFAULT ''",
        "payload_json": "TEXT NOT NULL DEFAULT '{}'",
        "error": "TEXT",
        "created_at": "TEXT NOT NULL DEFAULT ''",
    }
    for column, definition in desired.items():
        if column not in annotation_existing:
            conn.execute(
                f"ALTER TABLE llm_annotations ADD COLUMN {column} {definition}"
            )


def _ensure_market_snapshot_columns(conn: sqlite3.Connection) -> None:
    snapshot_existing = {
        row["name"] for row in conn.execute("PRAGMA table_info(market_snapshots)")
    }
    if not snapshot_existing:
        return
    desired = {
        "as_of": "TEXT NOT NULL DEFAULT ''",
        "status": "TEXT NOT NULL DEFAULT ''",
        "provider": "TEXT NOT NULL DEFAULT ''",
        "payload_json": "TEXT NOT NULL DEFAULT '{}'",
        "created_at": "TEXT NOT NULL DEFAULT ''",
    }
    for column, definition in desired.items():
        if column not in snapshot_existing:
            conn.execute(
                f"ALTER TABLE market_snapshots ADD COLUMN {column} {definition}"
            )


def insert_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    started_at: str,
    as_of: str,
    mode: str,
    dispatch_enabled: bool,
    llm_enabled: bool,
    legacy_prompt_hash: str | None,
    legacy_snapshot: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO runs (
          id, started_at, as_of, mode, status, dispatch_enabled, llm_enabled,
          legacy_prompt_hash, legacy_snapshot_json
        )
        VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?)
        """,
        (
            run_id,
            started_at,
            as_of,
            mode,
            int(dispatch_enabled),
            int(llm_enabled),
            legacy_prompt_hash,
            json.dumps(legacy_snapshot, sort_keys=True, ensure_ascii=False),
        ),
    )


def finish_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    status: str,
    finished_at: str | None = None,
    error: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE runs
        SET status = ?, finished_at = ?, error = ?
        WHERE id = ?
        """,
        (status, finished_at or datetime.utcnow().isoformat(), error, run_id),
    )


def insert_source_attempt(
    conn: sqlite3.Connection,
    *,
    attempt_id: str,
    run_id: str,
    source: str,
    provider: str,
    category: str,
    url: str,
    query: str,
    status: str,
    item_count: int,
    kept_count: int,
    error: str | None,
    started_at: str,
    finished_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO source_attempts (
          id, run_id, source, provider, category, url, query, status, item_count,
          kept_count, error, started_at, finished_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            attempt_id,
            run_id,
            source,
            provider,
            category,
            url,
            query,
            status,
            item_count,
            kept_count,
            error,
            started_at,
            finished_at,
        ),
    )


def insert_market_snapshot(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    as_of: str,
    created_at: str,
    snapshot: dict[str, Any],
) -> int:
    snapshot_id = sha256(f"{run_id}|market_snapshot".encode("utf-8")).hexdigest()
    providers = ",".join(str(provider) for provider in snapshot.get("providers") or [])
    cursor = conn.execute(
        """
        INSERT OR REPLACE INTO market_snapshots (
          id, run_id, as_of, status, provider, payload_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_id,
            run_id,
            as_of,
            str(snapshot.get("status") or ""),
            providers,
            json.dumps(snapshot, sort_keys=True, ensure_ascii=False),
            created_at,
        ),
    )
    return cursor.rowcount


def load_market_snapshot_for_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT payload_json
        FROM market_snapshots
        WHERE run_id = ?
        LIMIT 1
        """,
        (run_id,),
    ).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(row["payload_json"])
    except (TypeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def load_latest_market_snapshot(
    conn: sqlite3.Connection,
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT payload_json
        FROM market_snapshots
        ORDER BY created_at DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(row["payload_json"])
    except (TypeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def insert_candidate_items(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    fetched_at: str,
    items: list[dict[str, Any]],
) -> int:
    inserted = 0
    for item in items:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO candidate_items (
              id, run_id, source, provider, category, title, normalized_title, url,
              canonical_url, published_at, fetched_at, item_hash, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item["id"],
                run_id,
                item["source"],
                item.get("provider", ""),
                item["category"],
                item["title"],
                item["normalized_title"],
                item.get("url"),
                item["canonical_url"],
                item.get("published_at"),
                fetched_at,
                item["item_hash"],
                json.dumps(item, sort_keys=True, ensure_ascii=False),
            ),
        )
        inserted += cursor.rowcount
    return inserted


def insert_news_seeds(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    created_at: str,
    seeds: list[dict[str, Any]],
) -> int:
    inserted = 0
    for seed in seeds:
        seed_key = str(seed["seed_key"])
        seed_id = sha256(f"{run_id}|{seed_key}".encode("utf-8")).hexdigest()
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO news_seeds (
              id, run_id, seed_key, seed_type, subject, theme, freshness,
              market_relevance, source_count, evidence_count, payload_json,
              created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                seed_id,
                run_id,
                seed_key,
                seed["seed_type"],
                seed["subject"],
                seed["theme"],
                seed.get("freshness", ""),
                seed.get("market_relevance", ""),
                int(seed.get("source_count") or 0),
                int(seed.get("evidence_count") or 0),
                json.dumps(seed, sort_keys=True, ensure_ascii=False),
                created_at,
            ),
        )
        inserted += cursor.rowcount
    return inserted


def insert_events_and_links(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    created_at: str,
    extracted: list[dict[str, Any]],
) -> tuple[int, int]:
    events_inserted = 0
    links_inserted = 0
    for item in extracted:
        event = item["event"]
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO events (
              signature, first_seen_run_id, event_type, subject, effective_date,
              payload_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["signature"],
                run_id,
                event["event_type"],
                event["subject"],
                event["effective_date"],
                json.dumps(event, sort_keys=True, ensure_ascii=False),
                created_at,
            ),
        )
        events_inserted += cursor.rowcount

        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO candidate_events (
              id, run_id, candidate_id, event_signature, extractor, confidence,
              reason, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item["id"],
                run_id,
                item["candidate_id"],
                event["signature"],
                item["extractor"],
                item["confidence"],
                item["reason"],
                created_at,
            ),
        )
        links_inserted += cursor.rowcount
    return events_inserted, links_inserted


def insert_dispatch_decisions(
    conn: sqlite3.Connection,
    *,
    created_at: str,
    decisions: list[dict[str, Any]],
) -> int:
    inserted = 0
    for decision in decisions:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO dispatch_decisions (
              id, run_id, event_signature, decision, reason, policy, score,
              payload_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision["id"],
                decision["run_id"],
                decision["event_signature"],
                decision["decision"],
                decision["reason"],
                decision["policy"],
                decision["score"],
                json.dumps(decision["payload"], sort_keys=True, ensure_ascii=False),
                created_at,
            ),
        )
        inserted += cursor.rowcount
    return inserted


def insert_delivery_records(
    conn: sqlite3.Connection,
    *,
    created_at: str,
    deliveries: list[dict[str, Any]],
) -> int:
    inserted = 0
    for delivery in deliveries:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO deliveries (
              id, run_id, event_signature, channel, status, message_id,
              payload_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                delivery["id"],
                delivery["run_id"],
                delivery.get("event_signature"),
                delivery["channel"],
                delivery["status"],
                delivery.get("message_id"),
                json.dumps(delivery["payload"], sort_keys=True, ensure_ascii=False),
                created_at,
            ),
        )
        inserted += cursor.rowcount
    return inserted


def insert_llm_annotations(
    conn: sqlite3.Connection,
    *,
    annotations: list[dict[str, Any]],
) -> int:
    inserted = 0
    for annotation in annotations:
        cursor = conn.execute(
            """
            INSERT OR REPLACE INTO llm_annotations (
              id, run_id, event_signature, annotation_type, provider, model,
              prompt_version, evidence_hash, status, payload_json, error,
              created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                annotation["id"],
                annotation["run_id"],
                annotation["event_signature"],
                annotation["annotation_type"],
                annotation["provider"],
                annotation["model"],
                annotation["prompt_version"],
                annotation["evidence_hash"],
                annotation["status"],
                json.dumps(
                    annotation.get("payload", {}),
                    sort_keys=True,
                    ensure_ascii=False,
                ),
                annotation.get("error"),
                annotation["created_at"],
            ),
        )
        inserted += cursor.rowcount
    return inserted
