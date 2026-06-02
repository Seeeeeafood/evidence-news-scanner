from __future__ import annotations

import csv
import io
import json
from pathlib import Path
import sqlite3
from typing import Any

from .db import connect
from .earnings_facts import attach_earnings_fact_contract
from .evidence_contract import attach_evidence_contracts
from .llm import ANNOTATION_TYPE_EDITORIAL, ANNOTATION_TYPE_SUMMARY
from .llm import summary_annotation_from_editorial


DECISION_ORDER = {
    "send_candidate": 0,
    "review": 1,
    "reject": 2,
}

REPORT_COLUMNS = (
    "decision",
    "score",
    "event_type",
    "subject",
    "action",
    "effective_date",
    "evidence_count",
    "providers",
    "title",
    "url",
    "score_reasons",
    "extractor_reasons",
)

PRICE_REACTION_REPORT_COLUMNS = (
    "decision",
    "grade",
    "subject",
    "action",
    "price_status",
    "direction",
    "pct_change",
    "price_as_of",
    "contract_status",
    "contract_failures",
    "delivered",
    "title",
)

COMPANY_EVENT_TYPES = {
    "analyst",
    "corporate_action",
    "earnings",
    "mover",
    "strategic",
}


class ReportError(RuntimeError):
    pass


def _load_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _latest_run_id(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        """
        SELECT id
        FROM runs
        ORDER BY started_at DESC, id DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        raise ReportError("no runs found in DB")
    return str(row["id"])


def _resolve_run_id(conn: sqlite3.Connection, run_id: str) -> str:
    resolved = _latest_run_id(conn) if run_id == "latest" else run_id
    row = conn.execute("SELECT id FROM runs WHERE id = ?", (resolved,)).fetchone()
    if row is None:
        raise ReportError(f"run not found: {resolved}")
    return resolved


def _list_to_cell(value: object) -> str:
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    return str(value or "")


def _candidate_evidence_items(
    conn: sqlite3.Connection,
    candidate_ids: list[str],
) -> list[dict[str, Any]]:
    if not candidate_ids:
        return []
    placeholders = ",".join("?" for _ in candidate_ids)
    order = {candidate_id: index for index, candidate_id in enumerate(candidate_ids)}
    rows = conn.execute(
        f"""
        SELECT id, source, provider, category, title, url, published_at, raw_json
        FROM candidate_items
        WHERE id IN ({placeholders})
        """,
        candidate_ids,
    ).fetchall()
    items = []
    for row in sorted(rows, key=lambda item: order.get(str(item["id"]), 9999)):
        raw = _load_json(row["raw_json"])
        summary = str(raw.get("summary") or "").strip()
        items.append(
            {
                "candidate_id": row["id"],
                "source": row["source"],
                "provider": row["provider"],
                "category": row["category"],
                "title": row["title"],
                "url": row["url"] or "",
                "published_at": row["published_at"] or "",
                "summary": summary,
                "summary_chars": len(summary),
                "body_text": str(raw.get("body_text") or "").strip(),
                "body_fetch": raw.get("body_fetch", {})
                if isinstance(raw.get("body_fetch"), dict)
                else {},
            }
        )
    return items


def _verified_evidence_items(decision_payload: dict[str, Any]) -> list[dict[str, Any]]:
    verification = decision_payload.get("verification")
    if not isinstance(verification, dict) or verification.get("status") != "verified":
        return []
    match = verification.get("match")
    if not isinstance(match, dict):
        return []
    title = str(match.get("title") or "").strip()
    url = str(match.get("url") or "").strip()
    summary = str(match.get("summary") or "").strip()
    if not title and not url and not summary:
        return []
    return [
        {
            "candidate_id": "verification",
            "source": str(match.get("source") or "verification"),
            "provider": str(
                match.get("provider") or verification.get("provider") or ""
            ),
            "category": str(match.get("category") or "VERIFY"),
            "title": title,
            "url": url,
            "published_at": str(match.get("published_at") or ""),
            "summary": summary,
            "summary_chars": len(summary),
            "body_text": str(match.get("body_text") or "").strip(),
            "body_fetch": {},
        }
    ]


def _best_body_text(evidence_items: list[dict[str, Any]]) -> str:
    bodies = [
        str(item.get("body_text") or "").strip()
        for item in evidence_items
        if str(item.get("body_text") or "").strip()
    ]
    if not bodies:
        return ""
    return max(bodies, key=len)


def _llm_annotations_by_event(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    annotation_type: str,
) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT event_signature, model, prompt_version, evidence_hash, payload_json
        FROM llm_annotations
        WHERE run_id = ?
          AND annotation_type = ?
          AND status = 'ok'
        ORDER BY created_at DESC, id DESC
        """,
        (run_id, annotation_type),
    ).fetchall()
    annotations: dict[str, dict[str, Any]] = {}
    for row in rows:
        event_signature = str(row["event_signature"] or "")
        if not event_signature or event_signature in annotations:
            continue
        payload = _load_json(row["payload_json"])
        if not payload:
            continue
        payload["_meta"] = {
            "model": row["model"],
            "prompt_version": row["prompt_version"],
            "evidence_hash": row["evidence_hash"],
        }
        annotations[event_signature] = payload
    return annotations


def load_decision_rows(
    db_path: Path,
    *,
    run_id: str = "latest",
    decisions: set[str] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    if not db_path.exists():
        raise ReportError(f"DB does not exist: {db_path}")

    with connect(db_path) as conn:
        resolved_run_id = _resolve_run_id(conn, run_id)
        run = conn.execute(
            """
            SELECT id, started_at, finished_at, as_of, status
            FROM runs
            WHERE id = ?
            """,
            (resolved_run_id,),
        ).fetchone()
        query = """
            SELECT d.id, d.run_id, d.event_signature, d.decision, d.reason,
                   d.policy, d.score, d.payload_json, d.created_at,
                   e.event_type, e.subject, e.effective_date,
                   e.payload_json AS event_payload_json
            FROM dispatch_decisions d
            JOIN events e ON e.signature = d.event_signature
            WHERE d.run_id = ?
        """
        params: list[Any] = [resolved_run_id]
        if decisions:
            placeholders = ",".join("?" for _ in decisions)
            query += f" AND d.decision IN ({placeholders})"
            params.extend(sorted(decisions))
        query += """
            ORDER BY
              CASE d.decision
                WHEN 'send_candidate' THEN 0
                WHEN 'review' THEN 1
                WHEN 'reject' THEN 2
                ELSE 3
              END,
              d.score DESC,
              e.event_type ASC,
              e.subject ASC,
              d.event_signature ASC
        """
        if limit is not None and limit > 0:
            query += " LIMIT ?"
            params.append(limit)

        rows = []
        counts: dict[str, int] = {}
        llm_annotations = _llm_annotations_by_event(
            conn,
            run_id=resolved_run_id,
            annotation_type=ANNOTATION_TYPE_SUMMARY,
        )
        llm_editorials = _llm_annotations_by_event(
            conn,
            run_id=resolved_run_id,
            annotation_type=ANNOTATION_TYPE_EDITORIAL,
        )
        for row in conn.execute(query, params):
            decision_payload = _load_json(row["payload_json"])
            event_payload = _load_json(row["event_payload_json"])
            event = decision_payload.get("event", {})
            if not isinstance(event, dict):
                event = {}
            signature_payload = event_payload.get("payload", {})
            if not isinstance(signature_payload, dict):
                signature_payload = {}
            decision = str(row["decision"])
            counts[decision] = counts.get(decision, 0) + 1
            candidate_ids = decision_payload.get("candidate_ids", [])
            if not isinstance(candidate_ids, list):
                candidate_ids = []
            candidate_ids = [str(candidate_id) for candidate_id in candidate_ids]
            ranked_candidate_ids = decision_payload.get("ranked_candidate_ids", [])
            if not isinstance(ranked_candidate_ids, list):
                ranked_candidate_ids = []
            ranked_candidate_ids = [
                str(candidate_id) for candidate_id in ranked_candidate_ids
            ]
            evidence_items = _candidate_evidence_items(
                conn,
                ranked_candidate_ids or candidate_ids,
            )
            verified_evidence_items = _verified_evidence_items(decision_payload)
            if event.get("event_type") == "earnings" and verified_evidence_items:
                evidence_items = verified_evidence_items
            event_metadata = event.get("metadata") or event_payload.get("metadata", {})
            if not isinstance(event_metadata, dict):
                event_metadata = {}
            price_reaction = decision_payload.get("price_reaction")
            if not isinstance(price_reaction, dict):
                price_reaction = event_metadata.get("price_reaction")
            if not isinstance(price_reaction, dict):
                price_reaction = {}
            verification = decision_payload.get("verification")
            if not isinstance(verification, dict):
                verification = {}
            event_signature = str(row["event_signature"])
            llm_editorial = llm_editorials.get(event_signature)
            llm_annotation = llm_annotations.get(event_signature)
            if (
                llm_annotation is None
                and isinstance(llm_editorial, dict)
                and llm_editorial.get("decision") == "send"
            ):
                llm_annotation = summary_annotation_from_editorial(llm_editorial)
            grade = str(decision_payload.get("grade") or "")
            if (
                isinstance(llm_editorial, dict)
                and llm_editorial.get("decision") == "send"
                and llm_editorial.get("grade") in {"A", "B"}
            ):
                grade = str(llm_editorial["grade"])

            report_row = {
                "decision_id": row["id"],
                "run_id": row["run_id"],
                "event_signature": event_signature,
                "decision": decision,
                "score": float(row["score"]),
                "reason": row["reason"],
                "policy": row["policy"],
                "event_type": event.get("event_type") or row["event_type"],
                "subject": event.get("subject") or row["subject"],
                "action": event.get("action") or signature_payload.get("action", ""),
                "object": event.get("object") or signature_payload.get("object", ""),
                "effective_date": event.get("effective_date") or row["effective_date"],
                "title": event.get("title") or event_payload.get("title", ""),
                "url": event.get("url") or event_payload.get("url", ""),
                "evidence_count": int(decision_payload.get("evidence_count") or 0),
                "grade": grade,
                "risk_flags": decision_payload.get("risk_flags", []),
                "source_tier": str(decision_payload.get("source_tier") or ""),
                "event_quality": str(decision_payload.get("event_quality") or ""),
                "hard_event_reason": str(decision_payload.get("hard_event_reason") or ""),
                "soft_analysis_reason": str(
                    decision_payload.get("soft_analysis_reason") or ""
                ),
                "event_metadata": event_metadata,
                "price_reaction": price_reaction,
                "verification": verification,
                "verification_status": str(
                    decision_payload.get("verification_status")
                    or verification.get("status")
                    or ""
                ),
                "price_reaction_required": bool(
                    decision_payload.get("price_reaction_required")
                ),
                "send_worthy_reason": decision_payload.get("send_worthy_reason", ""),
                "rescue_type": str(decision_payload.get("rescue_type") or ""),
                "rescue_reason": str(decision_payload.get("rescue_reason") or ""),
                "atomic_digest": bool(decision_payload.get("atomic_digest")),
                "requires_numeric_fact": bool(
                    decision_payload.get("requires_numeric_fact")
                ),
                "providers": decision_payload.get("providers", []),
                "sources": decision_payload.get("sources", []),
                "candidate_ids": candidate_ids,
                "ranked_candidate_ids": ranked_candidate_ids,
                "evidence_items": evidence_items,
                "body_text": _best_body_text(evidence_items),
                "score_reasons": decision_payload.get("score_reasons", []),
                "extractor_reasons": decision_payload.get("extractor_reasons", []),
                "llm_annotation": llm_annotation,
                "llm_editorial": llm_editorial,
                "created_at": row["created_at"],
            }
            attach_earnings_fact_contract(report_row)
            rows.append(report_row)
        attach_evidence_contracts(rows)

    return {
        "run": dict(run) if run is not None else {},
        "row_count": len(rows),
        "decision_counts": dict(
            sorted(counts.items(), key=lambda item: DECISION_ORDER.get(item[0], 9))
        ),
        "rows": rows,
    }


def _markdown_escape(value: object) -> str:
    text = _list_to_cell(value)
    return text.replace("\n", " ").replace("|", "\\|")


def render_markdown(report: dict[str, Any]) -> str:
    run = report["run"]
    lines = [
        "# News Scanner V2 Decision Report",
        "",
        f"- run_id: `{run.get('id', '')}`",
        f"- as_of: `{run.get('as_of', '')}`",
        f"- status: `{run.get('status', '')}`",
        f"- rows: `{report['row_count']}`",
        f"- decision_counts: `{json.dumps(report['decision_counts'], sort_keys=True)}`",
        "",
        "| " + " | ".join(REPORT_COLUMNS) + " |",
        "| " + " | ".join("---" for _ in REPORT_COLUMNS) + " |",
    ]
    for row in report["rows"]:
        values = []
        for column in REPORT_COLUMNS:
            if column == "score":
                values.append(f"{float(row[column]):.1f}")
            else:
                values.append(_markdown_escape(row.get(column)))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines) + "\n"


def render_csv(report: dict[str, Any]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=REPORT_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in report["rows"]:
        record = {}
        for column in REPORT_COLUMNS:
            value = row.get(column)
            if column == "score":
                record[column] = f"{float(value):.1f}"
            else:
                record[column] = _list_to_cell(value)
        writer.writerow(record)
    return out.getvalue()


def render_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def render_decision_report(report: dict[str, Any], *, output_format: str) -> str:
    if output_format == "markdown":
        return render_markdown(report)
    if output_format == "csv":
        return render_csv(report)
    if output_format == "json":
        return render_json(report)
    raise ReportError(f"unsupported report format: {output_format}")


def _counter_dict(values: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _delivery_summary_for_run(
    db_path: Path,
    *,
    run_id: str,
) -> dict[str, Any]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT event_signature, channel, status, message_id
            FROM deliveries
            WHERE run_id = ?
            ORDER BY created_at, id
            """,
            (run_id,),
        ).fetchall()
    sent_event_signatures = {
        str(row["event_signature"])
        for row in rows
        if str(row["status"] or "") == "sent" and row["event_signature"]
    }
    return {
        "rows": len(rows),
        "status_counts": _counter_dict([str(row["status"] or "") for row in rows]),
        "channel_counts": _counter_dict([str(row["channel"] or "") for row in rows]),
        "sent_event_rows": len(sent_event_signatures),
        "run_digest_rows": sum(1 for row in rows if row["event_signature"] is None),
        "sent_event_signatures": sorted(sent_event_signatures),
    }


def _price_row(row: dict[str, Any], *, delivered: bool) -> dict[str, Any]:
    price_reaction = row.get("price_reaction")
    if not isinstance(price_reaction, dict):
        price_reaction = {}
    contract = row.get("evidence_contract")
    if not isinstance(contract, dict):
        contract = {}
    return {
        "decision_id": row.get("decision_id"),
        "event_signature": row.get("event_signature"),
        "decision": row.get("decision"),
        "grade": row.get("grade"),
        "score": row.get("score"),
        "event_type": row.get("event_type"),
        "subject": row.get("subject"),
        "action": row.get("action"),
        "title": row.get("title"),
        "source_tier": row.get("source_tier"),
        "price_status": price_reaction.get("status") or "missing",
        "direction": price_reaction.get("direction") or "",
        "pct_change": price_reaction.get("pct_change"),
        "price_as_of": price_reaction.get("price_as_of") or "",
        "price_as_of_at": price_reaction.get("price_as_of_at") or "",
        "session": price_reaction.get("session") or "",
        "stale": bool(price_reaction.get("stale")),
        "verification_status": row.get("verification_status") or "",
        "verification": row.get("verification") or {},
        "contract_status": row.get("contract_status"),
        "contract_failures": row.get("contract_failures") or [],
        "contract_warnings": row.get("contract_warnings") or [],
        "delivery_eligible": bool(contract.get("delivery_eligible")),
        "delivered": delivered,
    }


def load_price_reaction_report(
    db_path: Path,
    *,
    run_id: str = "latest",
    decisions: set[str] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    report = load_decision_rows(
        db_path,
        run_id=run_id,
        decisions=decisions or {"send_candidate"},
        limit=limit,
    )
    resolved_run_id = str(report["run"].get("id") or "")
    delivery_summary = _delivery_summary_for_run(db_path, run_id=resolved_run_id)
    sent_signatures = set(delivery_summary["sent_event_signatures"])
    company_rows = [
        row
        for row in report["rows"]
        if str(row.get("event_type") or "") in COMPANY_EVENT_TYPES
    ]
    rows = [
        _price_row(
            row,
            delivered=str(row.get("event_signature") or "") in sent_signatures,
        )
        for row in company_rows
    ]
    required_rows = [
        row for row in rows if str(row.get("decision") or "") == "send_candidate"
    ]
    blocked_rows = [
        row
        for row in required_rows
        if not row.get("delivery_eligible")
    ]
    delivered_rows = [row for row in rows if row.get("delivered")]
    delivered_ineligible_rows = [
        row
        for row in delivered_rows
        if not row.get("delivery_eligible")
    ]
    failure_values = [
        str(failure)
        for row in rows
        for failure in row.get("contract_failures", [])
        if str(failure)
    ]
    return {
        "run": report["run"],
        "decision_counts": report["decision_counts"],
        "source_decision_rows": report["row_count"],
        "company_rows": len(rows),
        "required_company_send_candidates": len(required_rows),
        "eligible_company_send_candidates": len(required_rows) - len(blocked_rows),
        "blocked_company_send_candidates": len(blocked_rows),
        "delivered_company_rows": len(delivered_rows),
        "delivered_ineligible_company_rows": len(delivered_ineligible_rows),
        "price_status_counts": _counter_dict(
            [str(row.get("price_status") or "") for row in rows]
        ),
        "price_direction_counts": _counter_dict(
            [str(row.get("direction") or "none") for row in rows]
        ),
        "contract_status_counts": _counter_dict(
            [str(row.get("contract_status") or "") for row in rows]
        ),
        "contract_failure_counts": _counter_dict(failure_values),
        "delivery": {
            key: value
            for key, value in delivery_summary.items()
            if key != "sent_event_signatures"
        },
        "rows": rows,
    }


def render_price_reaction_markdown(report: dict[str, Any]) -> str:
    run = report["run"]
    lines = [
        "# News Scanner V2 Price Reaction Report",
        "",
        f"- run_id: `{run.get('id', '')}`",
        f"- as_of: `{run.get('as_of', '')}`",
        f"- status: `{run.get('status', '')}`",
        f"- company_rows: `{report['company_rows']}`",
        f"- required_company_send_candidates: `{report['required_company_send_candidates']}`",
        f"- eligible_company_send_candidates: `{report['eligible_company_send_candidates']}`",
        f"- blocked_company_send_candidates: `{report['blocked_company_send_candidates']}`",
        f"- delivered_company_rows: `{report['delivered_company_rows']}`",
        f"- delivered_ineligible_company_rows: `{report['delivered_ineligible_company_rows']}`",
        f"- price_status_counts: `{json.dumps(report['price_status_counts'], sort_keys=True)}`",
        f"- price_direction_counts: `{json.dumps(report['price_direction_counts'], sort_keys=True)}`",
        f"- contract_failure_counts: `{json.dumps(report['contract_failure_counts'], sort_keys=True)}`",
        f"- delivery: `{json.dumps(report['delivery'], sort_keys=True)}`",
        "",
    ]
    if not report["rows"]:
        lines.append("No company rows in selected decisions.")
        return "\n".join(lines) + "\n"

    lines.extend(
        [
            "| " + " | ".join(PRICE_REACTION_REPORT_COLUMNS) + " |",
            "| "
            + " | ".join("---" for _ in PRICE_REACTION_REPORT_COLUMNS)
            + " |",
        ]
    )
    for row in report["rows"]:
        values = []
        for column in PRICE_REACTION_REPORT_COLUMNS:
            value = row.get(column)
            if column == "pct_change" and value is not None:
                values.append(f"{float(value):.2f}")
            elif column == "delivered":
                values.append("yes" if value else "no")
            else:
                values.append(_markdown_escape(value))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines) + "\n"


def render_price_reaction_report(
    report: dict[str, Any],
    *,
    output_format: str,
) -> str:
    if output_format == "markdown":
        return render_price_reaction_markdown(report)
    if output_format == "json":
        return json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    raise ReportError(f"unsupported price reaction report format: {output_format}")
