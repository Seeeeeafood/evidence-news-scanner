from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import sqlite3
from typing import Any


def load_fixture(path: Path) -> dict[str, Any]:
    with path.open() as handle:
        fixture = json.load(handle)
    if not isinstance(fixture, dict):
        raise ValueError("fixture root must be an object")
    if fixture.get("version") != "news_quality_golden_v1":
        raise ValueError("unsupported golden fixture version")
    if not isinstance(fixture.get("slots"), list):
        raise ValueError("fixture.slots must be a list")
    return fixture


def _load_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _as_text(*parts: object) -> str:
    values: list[str] = []
    for part in parts:
        if part is None:
            continue
        if isinstance(part, (dict, list)):
            values.append(json.dumps(part, ensure_ascii=False, sort_keys=True))
        else:
            values.append(str(part))
    return "\n".join(values)


def _candidate_text(row: sqlite3.Row) -> str:
    raw = _load_json(row["raw_json"])
    return _as_text(
        row["source"],
        row["provider"],
        row["category"],
        row["title"],
        row["url"],
        row["published_at"],
        raw.get("summary"),
        raw.get("body_text"),
    )


def _event_text(event_payload: dict[str, Any], decision_payload: dict[str, Any]) -> str:
    return _as_text(event_payload, decision_payload)


def _message_text(payload_json: str | None) -> str:
    payload = _load_json(payload_json)
    message = payload.get("message")
    if isinstance(message, dict):
        return str(message.get("text") or "")
    return str(payload.get("text") or "")


def _term_list(match: dict[str, Any], key: str) -> list[str]:
    raw = match.get(key)
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if str(item or "").strip()]


def _text_matches(text: str, match: dict[str, Any]) -> bool:
    lowered = text.lower()
    all_terms = _term_list(match, "terms_all")
    if all_terms and not all(term.lower() in lowered for term in all_terms):
        return False
    any_terms = _term_list(match, "terms_any")
    if any_terms and not any(term.lower() in lowered for term in any_terms):
        return False
    none_terms = _term_list(match, "terms_none")
    if none_terms and any(term.lower() in lowered for term in none_terms):
        return False
    return bool(all_terms or any_terms or not none_terms)


def _category_matches(row: sqlite3.Row, match: dict[str, Any]) -> bool:
    categories = _term_list(match, "category_any")
    if not categories:
        return True
    category = str(row["category"] or "").upper()
    return category in {value.upper() for value in categories}


def _event_type_matches(row: sqlite3.Row, match: dict[str, Any]) -> bool:
    event_types = _term_list(match, "event_type_any")
    if not event_types:
        return True
    event_type = str(row["event_type"] or "")
    return event_type in set(event_types)


def _decision_matches(row: sqlite3.Row, match: dict[str, Any]) -> bool:
    decisions = _term_list(match, "decision_any")
    if not decisions:
        return True
    decision = str(row["decision"] or "")
    return decision in set(decisions)


def _candidate_matches(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    match: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = []
    for row in conn.execute(
        """
        SELECT id, source, provider, category, title, url, published_at, raw_json
        FROM candidate_items
        WHERE run_id = ?
        ORDER BY published_at, id
        """,
        (run_id,),
    ):
        text = _candidate_text(row)
        if not _category_matches(row, match):
            continue
        if not _text_matches(text, match):
            continue
        rows.append(
            {
                "id": row["id"],
                "source": row["source"],
                "provider": row["provider"],
                "category": row["category"],
                "title": row["title"],
                "published_at": row["published_at"],
                "url": row["url"],
            }
        )
    return rows


def _candidate_event_matches(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    match: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = []
    for row in conn.execute(
        """
        SELECT ce.event_signature, ce.reason, ce.confidence,
               ci.id AS candidate_id, ci.source, ci.provider, ci.category,
               ci.title, ci.url, ci.published_at, ci.raw_json,
               e.event_type, e.subject, e.payload_json AS event_payload_json
        FROM candidate_events ce
        JOIN candidate_items ci ON ci.id = ce.candidate_id
        JOIN events e ON e.signature = ce.event_signature
        WHERE ce.run_id = ?
        ORDER BY ci.published_at, ci.id, ce.event_signature
        """,
        (run_id,),
    ):
        text = _candidate_text(row)
        if not _category_matches(row, match):
            continue
        if not _event_type_matches(row, match):
            continue
        if not _text_matches(text, match):
            continue
        rows.append(
            {
                "event_signature": row["event_signature"],
                "candidate_id": row["candidate_id"],
                "event_type": row["event_type"],
                "subject": row["subject"],
                "reason": row["reason"],
                "confidence": row["confidence"],
                "title": row["title"],
                "source": row["source"],
            }
        )
    return rows


def _dispatch_matches(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    match: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = []
    for row in conn.execute(
        """
        SELECT d.event_signature, d.decision, d.reason, d.score,
               d.payload_json AS decision_payload_json,
               e.event_type, e.subject, e.payload_json AS event_payload_json
        FROM dispatch_decisions d
        JOIN events e ON e.signature = d.event_signature
        WHERE d.run_id = ?
        ORDER BY d.score DESC, d.event_signature
        """,
        (run_id,),
    ):
        event_payload = _load_json(row["event_payload_json"])
        decision_payload = _load_json(row["decision_payload_json"])
        text = _event_text(event_payload, decision_payload)
        if not _event_type_matches(row, match):
            continue
        if not _decision_matches(row, match):
            continue
        if not _text_matches(text, match):
            continue
        rows.append(
            {
                "event_signature": row["event_signature"],
                "decision": row["decision"],
                "event_type": row["event_type"],
                "subject": row["subject"],
                "score": row["score"],
                "reason": row["reason"],
                "title": event_payload.get("title") or decision_payload.get("title"),
            }
        )
    return rows


def _delivery_messages(conn: sqlite3.Connection, *, run_id: str) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in conn.execute(
        """
        SELECT message_id, event_signature, payload_json
        FROM deliveries
        WHERE run_id = ? AND status = 'sent'
        ORDER BY created_at, id
        """,
        (run_id,),
    ):
        text = _message_text(row["payload_json"])
        key = (str(row["message_id"] or ""), text)
        if key in seen:
            continue
        seen.add(key)
        messages.append(
            {
                "message_id": row["message_id"],
                "event_signature": row["event_signature"],
                "text": text,
            }
        )
    return messages


def _delivery_matches(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    match: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = []
    for message in _delivery_messages(conn, run_id=run_id):
        if not _text_matches(str(message.get("text") or ""), match):
            continue
        rows.append(
            {
                "message_id": message.get("message_id"),
                "event_signature": message.get("event_signature"),
                "excerpt": str(message.get("text") or "")[:500],
            }
        )
    return rows


def _status_for_stage(
    *,
    expected_stage: str,
    candidate_count: int,
    event_count: int,
    dispatch_count: int,
    send_count: int,
    delivery_count: int,
) -> str:
    if expected_stage == "candidate":
        return "pass" if candidate_count > 0 else "fail"
    if expected_stage == "event":
        return "pass" if event_count > 0 else "fail"
    if expected_stage == "dispatch":
        return "pass" if dispatch_count > 0 else "fail"
    if expected_stage == "send_candidate":
        return "pass" if send_count > 0 else "fail"
    if expected_stage == "delivery":
        return "pass" if delivery_count > 0 else "fail"
    if expected_stage == "not_delivery":
        return "pass" if delivery_count == 0 else "fail"
    raise ValueError(f"unsupported expected_stage: {expected_stage}")


def _audit_check(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    check: dict[str, Any],
) -> dict[str, Any]:
    match = check.get("match")
    if not isinstance(match, dict):
        raise ValueError(f"check {check.get('id')} missing match object")
    delivery_match = check.get("delivery_match")
    if not isinstance(delivery_match, dict):
        delivery_match = match

    candidate_matches = _candidate_matches(conn, run_id=run_id, match=match)
    event_matches = _candidate_event_matches(conn, run_id=run_id, match=match)
    dispatch_matches = _dispatch_matches(conn, run_id=run_id, match=match)
    send_matches = [
        row for row in dispatch_matches if row.get("decision") == "send_candidate"
    ]
    delivery_matches = _delivery_matches(
        conn,
        run_id=run_id,
        match=delivery_match,
    )
    expected_stage = str(check.get("expected_stage") or "delivery")
    status = _status_for_stage(
        expected_stage=expected_stage,
        candidate_count=len(candidate_matches),
        event_count=len(event_matches),
        dispatch_count=len(dispatch_matches),
        send_count=len(send_matches),
        delivery_count=len(delivery_matches),
    )
    return {
        "id": check.get("id"),
        "description": check.get("description", ""),
        "expected_stage": expected_stage,
        "severity": check.get("severity", "must"),
        "status": status,
        "counts": {
            "candidate": len(candidate_matches),
            "event": len(event_matches),
            "dispatch": len(dispatch_matches),
            "send_candidate": len(send_matches),
            "delivery": len(delivery_matches),
        },
        "v1_reference": check.get("v1_reference", ""),
        "examples": {
            "candidate": candidate_matches[:3],
            "event": event_matches[:3],
            "dispatch": dispatch_matches[:3],
            "delivery": delivery_matches[:3],
        },
    }


def audit_golden_fixture(db_path: Path, fixture_path: Path) -> dict[str, Any]:
    fixture = load_fixture(fixture_path)
    if not db_path.exists():
        raise FileNotFoundError(str(db_path))
    checks: list[dict[str, Any]] = []
    with _connect_readonly(db_path) as conn:
        for slot in fixture["slots"]:
            if not isinstance(slot, dict):
                raise ValueError("fixture slot must be an object")
            run_id = str(slot.get("run_id") or "")
            if not run_id:
                raise ValueError("fixture slot missing run_id")
            run_row = conn.execute(
                "SELECT id, as_of, status FROM runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            run_info = dict(run_row) if run_row is not None else None
            for check in slot.get("checks") or []:
                result = _audit_check(conn, run_id=run_id, check=check)
                result["slot"] = {
                    "label": slot.get("label", ""),
                    "run_id": run_id,
                    "fixture_as_of": slot.get("as_of", ""),
                    "db_run": run_info,
                }
                checks.append(result)

    status_counts = Counter(str(check["status"]) for check in checks)
    failures = [check for check in checks if check["status"] != "pass"]
    return {
        "version": fixture["version"],
        "description": fixture.get("description", ""),
        "db_path": str(db_path),
        "fixture_path": str(fixture_path),
        "summary": {
            "checks": len(checks),
            "passed": status_counts.get("pass", 0),
            "failed": len(failures),
            "status_counts": dict(sorted(status_counts.items())),
        },
        "checks": checks,
        "failures": failures,
    }
