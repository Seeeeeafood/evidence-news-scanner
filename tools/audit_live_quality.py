from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from typing import Any
from urllib.parse import urlparse

from news_scanner_v2.dispatch import LOW_QUALITY_DOMAIN_SUFFIXES, _domain_matches
from news_scanner_v2.extractor import _detected_earnings_report_date


DEFAULT_DB_PATH = Path(".evidence-news-scanner/state/news_scanner_v2.sqlite")
STALE_EARNINGS_DAYS = 7


def _load_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _domain(url: str) -> str:
    host = urlparse(url or "").netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _candidate_rows(
    conn: sqlite3.Connection,
    *,
    candidate_ids: list[str],
) -> list[sqlite3.Row]:
    if not candidate_ids:
        return []
    placeholders = ",".join("?" for _ in candidate_ids)
    return list(
        conn.execute(
            f"SELECT * FROM candidate_items WHERE id IN ({placeholders})",
            candidate_ids,
        )
    )


def audit_live_quality(db_path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    sent_rows = conn.execute(
        """
        SELECT DISTINCT d.run_id, d.event_signature, r.as_of, e.event_type,
               e.subject, e.effective_date, dd.payload_json AS decision_payload
        FROM deliveries d
        JOIN runs r ON r.id = d.run_id
        JOIN events e ON e.signature = d.event_signature
        LEFT JOIN dispatch_decisions dd
          ON dd.run_id = d.run_id AND dd.event_signature = d.event_signature
        WHERE d.status = 'sent'
          AND d.event_signature IS NOT NULL
        ORDER BY r.as_of, d.event_signature
        """
    ).fetchall()

    by_type: Counter[str] = Counter()
    earnings_domains: Counter[str] = Counter()
    earnings_single_untrusted = 0
    earnings_low_quality = 0
    earnings_with_report_date = 0
    stale_earnings: list[dict[str, Any]] = []

    for row in sent_rows:
        by_type[str(row["event_type"] or "")] += 1
        if row["event_type"] != "earnings":
            continue
        decision_payload = _load_json(row["decision_payload"])
        if decision_payload.get("evidence_count") == 1 and not decision_payload.get(
            "trusted_domains"
        ):
            earnings_single_untrusted += 1
        row_has_low_quality = bool(decision_payload.get("low_quality_domains"))
        candidate_ids = [
            str(candidate_id)
            for candidate_id in decision_payload.get("candidate_ids", [])
            if str(candidate_id or "")
        ]
        for candidate in _candidate_rows(conn, candidate_ids=candidate_ids):
            raw = _load_json(candidate["raw_json"])
            record = {
                key: candidate[key]
                for key in candidate.keys()
                if key != "raw_json"
            }
            record.update(raw)
            domain = _domain(str(candidate["url"] or ""))
            if domain:
                earnings_domains[domain] += 1
                if _domain_matches(domain, LOW_QUALITY_DOMAIN_SUFFIXES):
                    row_has_low_quality = True
            text = " ".join(
                str(record.get(key) or "")
                for key in ("title", "summary", "body_text")
            )
            as_of = datetime.fromisoformat(str(row["as_of"]))
            report_date = _detected_earnings_report_date(
                candidate=record,
                text=text,
                as_of=as_of,
            )
            if report_date is None:
                continue
            earnings_with_report_date += 1
            age_days = (as_of.date() - report_date).days
            if age_days > STALE_EARNINGS_DAYS:
                stale_earnings.append(
                    {
                        "run_id": row["run_id"],
                        "as_of": row["as_of"],
                        "subject": row["subject"],
                        "report_date": report_date.isoformat(),
                        "age_days": age_days,
                        "domain": domain,
                        "title": candidate["title"],
                    }
                )
        if row_has_low_quality:
            earnings_low_quality += 1

    result = {
        "db_path": str(db_path),
        "sent_event_rows": len(sent_rows),
        "sent_by_type": dict(sorted(by_type.items())),
        "sent_earnings_events": by_type.get("earnings", 0),
        "sent_earnings_single_untrusted": earnings_single_untrusted,
        "sent_earnings_low_quality": earnings_low_quality,
        "sent_earnings_with_report_date": earnings_with_report_date,
        "sent_earnings_stale_gt_7d": len(stale_earnings),
        "sent_earnings_domains": dict(earnings_domains.most_common()),
        "stale_earnings": stale_earnings,
    }
    conn.close()
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()
    print(json.dumps(audit_live_quality(args.db_path), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
