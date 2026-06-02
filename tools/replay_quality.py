from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from typing import Any

from news_scanner_v2.dispatch import decide_dispatch
from news_scanner_v2.earnings_facts import attach_earnings_fact_contract
from news_scanner_v2.evidence_contract import attach_evidence_contracts
from news_scanner_v2.extractor import extract_events
from news_scanner_v2.reports import load_decision_rows


DEFAULT_DB_PATH = Path(".evidence-news-scanner/state/news_scanner_v2.sqlite")


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


def _run_rows(
    conn: sqlite3.Connection,
    run_id: str,
    limit_runs: int | None,
) -> list[sqlite3.Row]:
    if run_id != "all":
        row = conn.execute(
            "SELECT id, as_of FROM runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise SystemExit(f"run not found: {run_id}")
        return [row]
    query = "SELECT id, as_of FROM runs ORDER BY as_of, id"
    params: list[Any] = []
    if limit_runs is not None and limit_runs > 0:
        query += " LIMIT ?"
        params.append(limit_runs)
    return list(conn.execute(query, params))


def _candidate_records(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in conn.execute(
        "SELECT * FROM candidate_items WHERE run_id = ? ORDER BY id",
        (run_id,),
    ):
        raw = _load_json(row["raw_json"])
        record = {key: row[key] for key in row.keys() if key != "raw_json"}
        record.update(raw)
        record.update({key: row[key] for key in row.keys() if key != "raw_json"})
        records.append(record)
    return records


def _evidence_items(
    candidates_by_id: dict[str, dict[str, Any]],
    candidate_ids: list[str],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for candidate_id in candidate_ids:
        candidate = candidates_by_id.get(candidate_id)
        if not candidate:
            continue
        raw = _load_json(str(candidate.get("raw_json") or ""))
        body_fetch = candidate.get("body_fetch")
        if not isinstance(body_fetch, dict):
            body_fetch = raw.get("body_fetch", {})
        items.append(
            {
                "candidate_id": candidate_id,
                "source": candidate.get("source") or "",
                "provider": candidate.get("provider") or "",
                "category": candidate.get("category") or "",
                "title": candidate.get("title") or "",
                "url": candidate.get("url") or "",
                "published_at": candidate.get("published_at") or "",
                "summary": candidate.get("summary") or raw.get("summary", ""),
                "body_text": candidate.get("body_text") or raw.get("body_text", ""),
                "body_fetch": body_fetch if isinstance(body_fetch, dict) else {},
            }
        )
    return items


def _row_from_decision(
    *,
    run_id: str,
    decision: Any,
    candidates_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    payload = decision.payload
    event = payload.get("event", {})
    event_metadata = event.get("metadata", {})
    if not isinstance(event_metadata, dict):
        event_metadata = {}
    price_reaction = payload.get("price_reaction")
    if not isinstance(price_reaction, dict):
        price_reaction = event_metadata.get("price_reaction")
    if not isinstance(price_reaction, dict):
        price_reaction = {}
    candidate_ids = [str(value) for value in payload.get("candidate_ids", [])]
    ranked_candidate_ids = [
        str(value) for value in payload.get("ranked_candidate_ids", [])
    ]
    evidence_items = _evidence_items(
        candidates_by_id,
        ranked_candidate_ids or candidate_ids,
    )
    body_texts = [
        str(item.get("body_text") or "").strip()
        for item in evidence_items
        if str(item.get("body_text") or "").strip()
    ]
    verification = payload.get("verification")
    if not isinstance(verification, dict):
        verification = {}
    row = {
        "decision_id": decision.decision_id(run_id),
        "run_id": run_id,
        "event_signature": decision.event_signature,
        "decision": decision.decision,
        "score": decision.score,
        "reason": decision.reason,
        "policy": decision.policy,
        "event_type": event.get("event_type", ""),
        "subject": event.get("subject", ""),
        "action": event.get("action", ""),
        "object": event.get("object", ""),
        "effective_date": event.get("effective_date", ""),
        "title": event.get("title", ""),
        "url": event.get("url", ""),
        "evidence_count": int(payload.get("evidence_count") or 0),
        "grade": str(payload.get("grade") or ""),
        "risk_flags": payload.get("risk_flags", []),
        "source_tier": str(payload.get("source_tier") or ""),
        "event_quality": str(payload.get("event_quality") or ""),
        "hard_event_reason": str(payload.get("hard_event_reason") or ""),
        "soft_analysis_reason": str(payload.get("soft_analysis_reason") or ""),
        "event_metadata": event_metadata,
        "price_reaction": price_reaction,
        "verification": verification,
        "verification_status": str(
            payload.get("verification_status")
            or verification.get("status")
            or ""
        ),
        "price_reaction_required": bool(payload.get("price_reaction_required")),
        "send_worthy_reason": payload.get("send_worthy_reason", ""),
        "rescue_type": str(payload.get("rescue_type") or ""),
        "rescue_reason": str(payload.get("rescue_reason") or ""),
        "atomic_digest": bool(payload.get("atomic_digest")),
        "requires_numeric_fact": bool(payload.get("requires_numeric_fact")),
        "providers": payload.get("providers", []),
        "sources": payload.get("sources", []),
        "candidate_ids": candidate_ids,
        "ranked_candidate_ids": ranked_candidate_ids,
        "evidence_items": evidence_items,
        "body_text": max(body_texts, key=len) if body_texts else "",
        "score_reasons": payload.get("score_reasons", []),
        "extractor_reasons": payload.get("extractor_reasons", []),
    }
    attach_earnings_fact_contract(row)
    return row


def _recompute_rows_for_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    as_of: str,
) -> list[dict[str, Any]]:
    candidates = _candidate_records(conn, run_id)
    candidates_by_id = {str(candidate["id"]): candidate for candidate in candidates}
    extracted = extract_events(candidates, as_of=datetime.fromisoformat(as_of))
    decisions = decide_dispatch(extracted, candidates=candidates)
    rows = [
        _row_from_decision(
            run_id=run_id,
            decision=decision,
            candidates_by_id=candidates_by_id,
        )
        for decision in decisions
    ]
    return attach_evidence_contracts(rows)


def _stored_rows_for_run(db_path: Path, run_id: str) -> list[dict[str, Any]]:
    return load_decision_rows(db_path, run_id=run_id)["rows"]


def _counter_dict(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items()))


def _summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    decision_counts: Counter[str] = Counter()
    send_by_event_type: Counter[str] = Counter()
    send_by_source_tier: Counter[str] = Counter()
    eligible_send_by_event_type: Counter[str] = Counter()
    eligible_send_by_source_tier: Counter[str] = Counter()
    contract_status: Counter[str] = Counter()
    contract_failures: Counter[str] = Counter()
    contract_warnings: Counter[str] = Counter()
    basis_levels: Counter[str] = Counter()
    send_rows = []
    blocked_send_rows = []

    for row in rows:
        decision = str(row.get("decision") or "")
        decision_counts[decision] += 1
        contract = row.get("evidence_contract")
        if isinstance(contract, dict):
            contract_status[str(contract.get("status") or "unknown")] += 1
            basis_levels[str(contract.get("basis_level") or "unknown")] += 1
            for failure in contract.get("failures") or []:
                contract_failures[str(failure)] += 1
            for warning in contract.get("warnings") or []:
                contract_warnings[str(warning)] += 1
        else:
            contract_status["missing"] += 1
            contract_failures["missing_contract"] += 1
        if decision != "send_candidate":
            continue
        send_rows.append(row)
        send_by_event_type[str(row.get("event_type") or "")] += 1
        send_by_source_tier[str(row.get("source_tier") or "")] += 1
        if not isinstance(contract, dict) or not contract.get("delivery_eligible"):
            blocked_send_rows.append(row)
        else:
            eligible_send_by_event_type[str(row.get("event_type") or "")] += 1
            eligible_send_by_source_tier[str(row.get("source_tier") or "")] += 1

    return {
        "rows": len(rows),
        "decision_counts": _counter_dict(decision_counts),
        "send_candidate_rows": len(send_rows),
        "eligible_send_candidate_rows": len(send_rows) - len(blocked_send_rows),
        "send_by_event_type": _counter_dict(send_by_event_type),
        "send_by_source_tier": _counter_dict(send_by_source_tier),
        "eligible_send_by_event_type": _counter_dict(eligible_send_by_event_type),
        "eligible_send_by_source_tier": _counter_dict(eligible_send_by_source_tier),
        "contract_status": _counter_dict(contract_status),
        "contract_failures": _counter_dict(contract_failures),
        "contract_warnings": _counter_dict(contract_warnings),
        "basis_levels": _counter_dict(basis_levels),
        "blocked_send_rows": len(blocked_send_rows),
        "blocked_send_examples": [
            {
                "run_id": row.get("run_id"),
                "event_signature": row.get("event_signature"),
                "event_type": row.get("event_type"),
                "subject": row.get("subject"),
                "title": row.get("title"),
                "failures": (row.get("evidence_contract") or {}).get("failures", [])
                if isinstance(row.get("evidence_contract"), dict)
                else ["missing_contract"],
            }
            for row in blocked_send_rows[:20]
        ],
    }


def build_quality_report(
    db_path: Path,
    *,
    mode: str,
    run_id: str,
    limit_runs: int | None,
) -> dict[str, Any]:
    if not db_path.exists():
        raise SystemExit(f"DB does not exist: {db_path}")
    with _connect_readonly(db_path) as conn:
        runs = _run_rows(conn, run_id, limit_runs)
        candidate_count = sum(
            int(
                conn.execute(
                    "SELECT count(*) FROM candidate_items WHERE run_id = ?",
                    (row["id"],),
                ).fetchone()[0]
            )
            for row in runs
        )
        body_rows = sum(
            int(
                conn.execute(
                    """
                    SELECT count(*)
                    FROM candidate_items
                    WHERE run_id = ? AND raw_json LIKE '%body_text%'
                    """,
                    (row["id"],),
                ).fetchone()[0]
            )
            for row in runs
        )
        result: dict[str, Any] = {
            "db_path": str(db_path),
            "mode": mode,
            "run_id": run_id,
            "runs": len(runs),
            "as_of_min": runs[0]["as_of"] if runs else None,
            "as_of_max": runs[-1]["as_of"] if runs else None,
            "candidate_rows": candidate_count,
            "candidate_rows_with_body_text_marker": body_rows,
        }
        if mode in {"stored", "both"}:
            rows: list[dict[str, Any]] = []
            for run in runs:
                rows.extend(_stored_rows_for_run(db_path, str(run["id"])))
            result["stored"] = _summarize_rows(rows)
        if mode in {"recompute", "both"}:
            rows = []
            for run in runs:
                rows.extend(
                    _recompute_rows_for_run(
                        conn,
                        run_id=str(run["id"]),
                        as_of=str(run["as_of"]),
                    )
                )
            result["recompute"] = _summarize_rows(rows)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument(
        "--mode",
        choices=("stored", "recompute", "both"),
        default="recompute",
    )
    parser.add_argument("--run-id", default="all")
    parser.add_argument("--limit-runs", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = build_quality_report(
        args.db_path,
        mode=args.mode,
        run_id=args.run_id,
        limit_runs=args.limit_runs,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
