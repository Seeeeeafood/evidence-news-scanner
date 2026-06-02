from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any
from urllib import error, request
from zoneinfo import ZoneInfo

from .composer import DEFAULT_MESSAGE_DECISIONS, compose_digest_message, compose_message
from .config import DEFAULT_LLM_MODEL, DEFAULT_LLM_TIMEOUT_SECONDS, KST_TZ
from .db import connect, insert_delivery_records, load_market_snapshot_for_run
from .earnings_facts import augment_earnings_summary_with_contract
from .llm import LLMClient, annotate_rows, edit_rows, edit_theme_candidates
from .market_theme import (
    build_market_theme_candidates,
    load_news_seed_candidates_for_run,
    load_raw_items_for_run,
    theme_candidate_to_delivery_row,
)
from .reports import ReportError, load_decision_rows


DRY_RUN_STATUS = "dry_run"
LIVE_SENT_STATUS = "sent"
DEFAULT_CHANNEL = "telegram"
DEFAULT_TELEGRAM_CHAT_ID = ""
ALLOWED_CHANNELS = (DEFAULT_CHANNEL,)
TELEGRAM_TEXT_MIN_CHARS = 1
TELEGRAM_TEXT_MAX_CHARS = 4096
PLAIN_TEXT_PARSE_MODE = None
TELEGRAM_API_BASE = "https://api.telegram.org"
DEFAULT_TELEGRAM_TIMEOUT_SECONDS = 15.0
THEME_REPEAT_COOLDOWN_HOURS = 12
COMPANY_EVENT_TYPES = {
    "analyst",
    "corporate_action",
    "earnings",
    "mover",
    "strategic",
}
MAX_DIGEST_GEO_ROWS_PER_SUBJECT = 3
GRADE_RANK = {"C": 0, "B": 1, "A": 2}
MARKET_SNAPSHOT_SOURCE = "market_snapshot"
MARKET_SNAPSHOT_HARD_EVENT_REASON = "market_snapshot_threshold"
OIL_MOVE_THRESHOLD_PCT = 3.0
GOLD_MOVE_THRESHOLD_PCT = 2.0
FX_MOVE_THRESHOLD_PCT = 1.0
VIX_MOVE_THRESHOLD_PCT = 8.0
INDEX_MOVE_THRESHOLDS_PCT = {
    "sp500": 1.0,
    "nasdaq": 1.5,
    "dow": 1.2,
}
TEN_YEAR_MOVE_THRESHOLD_BP = 8.0
UP_MOVE_PATTERN = re.compile(
    r"\b(rise|rises|rising|rose|jump|jumps|jumped|surge|surges|surged|"
    r"rebound|rebounds|rebounded|gain|gains|gained|climb|climbs|climbed|"
    r"up|higher)\b|상승|급등|반등|오름",
    re.IGNORECASE,
)
DOWN_MOVE_PATTERN = re.compile(
    r"\b(fall|falls|fell|fallen|drop|drops|dropped|slide|slides|slid|"
    r"slip|slips|slipped|ease|eases|eased|plunge|plunges|plunged|"
    r"down|lower)\b|하락|급락|내림|약세",
    re.IGNORECASE,
)
OIL_TEXT_PATTERN = re.compile(r"\b(oil|crude|brent|wti)\b|유가|원유", re.IGNORECASE)
EARNINGS_PREVIEW_PATTERN = re.compile(
    r"\b(ahead of earnings|await(?:s|ing)? .*earnings|earnings loom|"
    r"before earnings|set to report|will report|scheduled to report)\b",
    re.IGNORECASE,
)
GENERIC_FINAL_SUMMARY_PATTERN = re.compile(
    r"(실적 관련 신규 이슈|관련 신규 이슈|관련 중요 뉴스|"
    r"애널리스트 의견 또는 목표가 변경|전략 투자 또는 파트너십 이슈|"
    r"관련 외교 이벤트 진행|관련 정책·지정학 이벤트 진행|"
    r"관련 관세/정책 리스크 부각|관련 제재 이슈로 지정학 리스크 부각|"
    r"관련 분쟁 리스크가 커지며 시장 부담 확대|매크로 지표 변화 감지)"
)
MATERIAL_NUMBER_PATTERN = r"(?:\$\s*\d|\d+(?:\.\d+)?(?:%|B|M|K|bp|bps)|\d+\.\d+)"
EARNINGS_ACTIONS = {
    "earnings_report",
    "earnings_result",
    "earnings_related",
    "guidance_raise",
    "guidance_cut",
    "guidance_update",
}
ANALYST_TARGET_ACTIONS = {"price_target", "analyst_action", "upgrade", "downgrade"}
EARNINGS_RESULT_EVIDENCE_NUMERIC_RE = re.compile(
    r"\b(?:eps|earnings per share|revenue|sales|매출)\b.{0,100}"
    + MATERIAL_NUMBER_PATTERN,
    re.I,
)
EARNINGS_RESULT_SUMMARY_NUMERIC_RE = re.compile(
    r"(?:EPS|매출|revenue|sales)[^.;…]{0,80}" + MATERIAL_NUMBER_PATTERN,
    re.I,
)
GUIDANCE_EVIDENCE_NUMERIC_RE = re.compile(
    r"\b(?:guidance|outlook|가이던스)\b.{0,100}" + MATERIAL_NUMBER_PATTERN,
    re.I,
)
GUIDANCE_SUMMARY_NUMERIC_RE = re.compile(
    r"(?:가이던스|guidance|outlook)[^.;…]{0,80}" + MATERIAL_NUMBER_PATTERN,
    re.I,
)
BUYBACK_EVIDENCE_NUMERIC_RE = re.compile(
    r"\b(?:buyback|repurchase|자사주)\b.{0,100}" + MATERIAL_NUMBER_PATTERN,
    re.I,
)
BUYBACK_SUMMARY_NUMERIC_RE = re.compile(
    r"(?:자사주|buyback|repurchase)[^.;…]{0,80}" + MATERIAL_NUMBER_PATTERN,
    re.I,
)
ANALYST_EVIDENCE_TARGET_RE = re.compile(
    r"(?:price target|PT|target price|목표가).{0,80}(?:\$\s*)?\d|"
    r"(?:\$\s*)?\d.{0,80}(?:price target|PT|target price|목표가)",
    re.I,
)
ANALYST_SUMMARY_TARGET_RE = re.compile(
    r"(?:목표가|price target|PT).{0,80}(?:\$\s*)?\d|"
    r"(?:\$\s*)?\d.{0,80}(?:목표가|price target|PT)",
    re.I,
)
VALID_LLM_MARKERS = {"red", "green", "none"}
VALID_LLM_CONFIDENCE = {"high", "medium", "low"}
VALID_LLM_BASIS = {"body", "snippet", "title"}


class DeliverySafetyError(ReportError):
    pass


@dataclass(frozen=True)
class DeliveryRecord:
    id: str
    run_id: str
    event_signature: str | None
    channel: str
    status: str
    message_id: str | None
    payload: dict[str, Any]

    def as_record(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "event_signature": self.event_signature,
            "channel": self.channel,
            "status": self.status,
            "message_id": self.message_id,
            "payload": self.payload,
        }


def _delivery_id(
    *,
    run_id: str,
    event_signature: str,
    channel: str,
    status: str,
) -> str:
    raw = f"{run_id}|{event_signature}|{channel}|{status}"
    return sha256(raw.encode("utf-8")).hexdigest()


def _live_delivery_id(*, event_signature: str, channel: str) -> str:
    raw = f"live|{event_signature}|{channel}"
    return sha256(raw.encode("utf-8")).hexdigest()


def _live_run_digest_delivery_id(*, run_id: str, channel: str) -> str:
    raw = f"live-digest|{run_id}|{channel}"
    return sha256(raw.encode("utf-8")).hexdigest()


def _disallowed_control_chars(text: str) -> list[str]:
    allowed = {"\n", "\r", "\t"}
    return sorted(
        {
            f"U+{ord(char):04X}"
            for char in text
            if ord(char) < 32 and char not in allowed
        }
    )


def validate_message_for_delivery(
    message: dict[str, Any],
    *,
    channel: str,
) -> dict[str, Any]:
    if channel not in ALLOWED_CHANNELS:
        raise DeliverySafetyError(f"unsupported delivery channel: {channel}")

    parse_mode = message.get("parse_mode")
    if parse_mode is not PLAIN_TEXT_PARSE_MODE:
        raise DeliverySafetyError("only plain-text Telegram messages are allowed")

    text = str(message.get("text") or "")
    text_length = len(text)
    if text_length < TELEGRAM_TEXT_MIN_CHARS:
        raise DeliverySafetyError("message text is empty")
    if text_length > TELEGRAM_TEXT_MAX_CHARS:
        raise DeliverySafetyError(
            f"message text too long: {text_length}>{TELEGRAM_TEXT_MAX_CHARS}"
        )

    control_chars = _disallowed_control_chars(text)
    if control_chars:
        raise DeliverySafetyError(
            "message text has disallowed control characters: "
            + ",".join(control_chars)
        )

    return {
        "status": "ok",
        "channel": channel,
        "parse_mode": parse_mode,
        "text_length": text_length,
        "max_text_length": TELEGRAM_TEXT_MAX_CHARS,
    }


def _contract_is_delivery_eligible(row: dict[str, Any]) -> bool:
    contract = row.get("evidence_contract")
    if not isinstance(contract, dict):
        return False
    return bool(contract.get("delivery_eligible"))


def _filter_contract_eligible_rows(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    eligible: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for row in rows:
        if _contract_is_delivery_eligible(row):
            eligible.append(row)
        else:
            blocked.append(row)
    return eligible, blocked


def _editorial_allows_delivery(row: dict[str, Any]) -> bool:
    editorial = row.get("llm_editorial")
    if not isinstance(editorial, dict):
        return True
    return editorial.get("decision") == "send"


def _theme_editorial_allows_delivery(candidate: dict[str, Any]) -> bool:
    editorial = candidate.get("llm_editorial")
    if not isinstance(editorial, dict):
        return False
    if editorial.get("validation_error"):
        return False
    return editorial.get("decision") == "send"


def _rows_missing_llm_annotation(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if not isinstance(row.get("llm_annotation"), dict)]


def _filter_llm_summary_validated_rows(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    eligible: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row.get("llm_annotation_rejected"), dict):
            rejected.append(row)
        else:
            eligible.append(row)
    return eligible, rejected


def _valid_final_llm_annotation(row: dict[str, Any]) -> bool:
    annotation = row.get("llm_annotation")
    if not isinstance(annotation, dict):
        return False
    summary = str(annotation.get("summary_ko") or "").strip()
    if not summary:
        return False
    if GENERIC_FINAL_SUMMARY_PATTERN.search(summary):
        return False
    if annotation.get("market_marker") not in VALID_LLM_MARKERS:
        return False
    if annotation.get("confidence") not in VALID_LLM_CONFIDENCE:
        return False
    if annotation.get("basis") not in VALID_LLM_BASIS:
        return False
    return True


def _row_action_set(row: dict[str, Any]) -> set[str]:
    actions: set[str] = set()
    raw = row.get("merged_actions")
    if isinstance(raw, list):
        actions.update(str(value or "").strip() for value in raw)
    action = str(row.get("action") or "")
    actions.update(part.strip() for part in re.split(r"[+/,]", action))
    actions.discard("")
    return actions


def _row_combined_text(row: dict[str, Any]) -> str:
    chunks = [
        str(row.get("title") or ""),
        str(row.get("body_text") or ""),
    ]
    annotation = row.get("llm_annotation")
    if isinstance(annotation, dict):
        chunks.append(str(annotation.get("summary_ko") or ""))
    editorial = row.get("llm_editorial")
    if isinstance(editorial, dict):
        chunks.append(str(editorial.get("summary_ko") or ""))
    for item in row.get("evidence_items") or []:
        if not isinstance(item, dict):
            continue
        chunks.extend(
            [
                str(item.get("title") or ""),
                str(item.get("summary") or ""),
                str(item.get("body_text") or ""),
            ]
        )
    return " ".join(chunk for chunk in chunks if chunk)


def _final_summary_text(row: dict[str, Any]) -> str:
    annotation = row.get("llm_annotation")
    if isinstance(annotation, dict):
        return augment_earnings_summary_with_contract(
            row,
            str(annotation.get("summary_ko") or "").strip(),
        )
    editorial = row.get("llm_editorial")
    if isinstance(editorial, dict):
        return augment_earnings_summary_with_contract(
            row,
            str(editorial.get("summary_ko") or "").strip(),
        )
    return ""


def _numeric_quality_gate_reason(row: dict[str, Any], summary: str) -> str:
    event_type = str(row.get("event_type") or "").strip()
    actions = _row_action_set(row)
    text = _row_combined_text(row)
    if event_type == "earnings" and actions & EARNINGS_ACTIONS:
        if (
            EARNINGS_RESULT_EVIDENCE_NUMERIC_RE.search(text)
            and not EARNINGS_RESULT_SUMMARY_NUMERIC_RE.search(summary)
        ):
            return "summary_missing_earnings_result_numbers"
        if (
            GUIDANCE_EVIDENCE_NUMERIC_RE.search(text)
            and not GUIDANCE_SUMMARY_NUMERIC_RE.search(summary)
        ):
            return "summary_missing_guidance_numbers"
        if (
            BUYBACK_EVIDENCE_NUMERIC_RE.search(text)
            and not BUYBACK_SUMMARY_NUMERIC_RE.search(summary)
        ):
            return "summary_missing_buyback_numbers"
    if event_type == "analyst" and actions & ANALYST_TARGET_ACTIONS:
        if (
            ANALYST_EVIDENCE_TARGET_RE.search(text)
            and not ANALYST_SUMMARY_TARGET_RE.search(summary)
        ):
            return "summary_missing_analyst_target"
    return ""


def _final_publish_gate_reason(row: dict[str, Any]) -> str:
    summary = _final_summary_text(row)
    if summary and GENERIC_FINAL_SUMMARY_PATTERN.search(summary):
        return "generic_final_summary"

    event_type = str(row.get("event_type") or "").strip()
    if event_type not in COMPANY_EVENT_TYPES:
        return ""
    if _valid_final_llm_annotation(row):
        numeric_reason = _numeric_quality_gate_reason(row, summary)
        if numeric_reason:
            return numeric_reason
        return ""
    if isinstance(row.get("llm_annotation_rejected"), dict):
        reason = str(row["llm_annotation_rejected"].get("reason") or "").strip()
        return f"llm_summary_rejected:{reason or 'unknown'}"
    editorial = row.get("llm_editorial")
    if isinstance(editorial, dict) and editorial.get("validation_error"):
        return f"editorial_rejected:{editorial.get('validation_error')}"
    return "missing_valid_llm_summary"


def _filter_final_publish_ready_rows(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ready: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for row in rows:
        reason = _final_publish_gate_reason(row)
        if reason:
            row["final_publish_blocked"] = {
                "reason": reason,
                "event_type": row.get("event_type"),
                "subject": row.get("subject"),
                "action": row.get("action"),
                "event_signature": row.get("event_signature"),
            }
            dropped.append(row)
        else:
            ready.append(row)
    return ready, dropped


def _contract_summary(
    *,
    rows: list[dict[str, Any]],
    blocked: list[dict[str, Any]],
) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    failure_counts: dict[str, int] = {}
    warning_counts: dict[str, int] = {}
    for row in rows:
        contract = row.get("evidence_contract")
        if not isinstance(contract, dict):
            status_counts["missing"] = status_counts.get("missing", 0) + 1
            failure_counts["missing_contract"] = (
                failure_counts.get("missing_contract", 0) + 1
            )
            continue
        status = str(contract.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        for failure in contract.get("failures") or []:
            key = str(failure or "")
            if key:
                failure_counts[key] = failure_counts.get(key, 0) + 1
        for warning in contract.get("warnings") or []:
            key = str(warning or "")
            if key:
                warning_counts[key] = warning_counts.get(key, 0) + 1
    return {
        "status": "ok" if not blocked else "blocked",
        "evaluated": len(rows),
        "eligible": len(rows) - len(blocked),
        "blocked": len(blocked),
        "status_counts": dict(sorted(status_counts.items())),
        "failure_counts": dict(sorted(failure_counts.items())),
        "warning_counts": dict(sorted(warning_counts.items())),
        "blocked_events": [
            {
                "event_signature": row.get("event_signature"),
                "event_type": row.get("event_type"),
                "subject": row.get("subject"),
                "failures": (row.get("evidence_contract") or {}).get("failures", [])
                if isinstance(row.get("evidence_contract"), dict)
                else ["missing_contract"],
            }
            for row in blocked
        ],
    }


def _validate_unique_delivery_ids(deliveries: list[DeliveryRecord]) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for delivery in deliveries:
        if delivery.id in seen:
            duplicates.add(delivery.id)
        seen.add(delivery.id)
    if duplicates:
        raise DeliverySafetyError(
            "duplicate delivery ids in batch: " + ",".join(sorted(duplicates))
        )


def _row_atomic_digest(row: dict[str, Any]) -> bool:
    if bool(row.get("atomic_digest")):
        return True
    metadata = row.get("event_metadata")
    return isinstance(metadata, dict) and bool(metadata.get("atomic_digest"))


def _digest_subject_quota_key(row: dict[str, Any]) -> str:
    if _row_atomic_digest(row):
        return ""
    event_type = str(row.get("event_type") or "").strip()
    subject = str(row.get("subject") or "").strip()
    if event_type == "geo" and subject:
        return f"geo:{subject}"
    return ""


def _digest_subject_quota_limit(row: dict[str, Any]) -> int:
    if _digest_subject_quota_key(row):
        return MAX_DIGEST_GEO_ROWS_PER_SUBJECT
    return 0


def _digest_diversity_key(row: dict[str, Any]) -> str:
    if _row_atomic_digest(row):
        return ""
    event_type = str(row.get("event_type") or "").strip()
    subject = str(row.get("subject") or "").strip()
    action = str(row.get("action") or "").strip()
    object_key = str(row.get("object") or "").strip()
    if not subject:
        return ""
    if event_type in COMPANY_EVENT_TYPES:
        return f"company:{subject}:{_company_mechanism_key(row)}"
    if event_type == "geo":
        movement = _row_movement_direction(row)
        effective_date = _row_effective_date(row)
        if object_key == "hormuz_toll_regime":
            return f"geo:{subject}:{object_key}:{effective_date}:{movement}"
        story_key = _geo_digest_story_key(object_key, action)
        if story_key == "state_update":
            return f"geo:{subject}:{story_key}:{effective_date}"
        return f"geo:{subject}:{story_key}:{effective_date}:{movement}"
    if event_type == "macro":
        movement = _row_movement_direction(row)
        return f"macro:{subject}:{action}:{movement}"
    return f"{event_type}:{subject}:{action}"


def _geo_digest_story_key(object_key: str, action: str) -> str:
    key = object_key.strip()
    if not key:
        return action
    if key == "hormuz_toll_regime":
        return key
    if key == "hormuz_shipping" or key.endswith("_maritime_blockade"):
        return "hormuz_shipping"
    if key == "taiwan_warning" or key.endswith("_warning"):
        return "taiwan_warning"
    if key == "sanctions_enforcement" or key.endswith("_sanctions"):
        return "sanctions_enforcement"
    if key == "military_escalation" or key.endswith("_military_escalation"):
        return "military_escalation"
    if key == "iran_nuclear" or key.endswith("_nuclear"):
        return "iran_nuclear"
    if key.endswith("_market_pressure"):
        return "market_pressure"
    if key in {"ceasefire_talks", "market_pressure", "summit_diplomacy"}:
        return "state_update"
    if key.endswith("_ceasefire_talks") or key.endswith("_energy_supply"):
        return "state_update"
    if key.endswith("_summit_diplomacy"):
        return "state_update"
    return key


def _effective_date_ordinal(row: dict[str, Any]) -> int:
    text = _row_effective_date(row)
    if not text:
        return 0
    try:
        return datetime.fromisoformat(text).date().toordinal()
    except ValueError:
        return 0


def _llm_priority(row: dict[str, Any]) -> int:
    annotation = row.get("llm_annotation")
    editorial = row.get("llm_editorial")
    if isinstance(annotation, dict) or isinstance(editorial, dict):
        return 1
    return 0


def _delivery_priority_key(row: dict[str, Any]) -> tuple[int, int, int, float, str, str]:
    event_type = str(row.get("event_type") or "")
    grade = str(row.get("grade") or "").upper()
    event_quality = str(row.get("event_quality") or "")
    subject = str(row.get("subject") or "")
    action = str(row.get("action") or "")
    score = float(row.get("score") or 0.0)
    is_company_hard = event_type in COMPANY_EVENT_TYPES and event_quality == "hard_event"

    if is_company_hard and grade == "A":
        rank = 0
    elif event_type in {"geo", "macro"} and grade == "A":
        rank = 1
    elif is_company_hard and grade == "B":
        rank = 2
    elif event_type == "geo" and grade == "B":
        rank = 3
    elif event_type == "macro" and grade == "B":
        rank = 4
    elif event_type in COMPANY_EVENT_TYPES:
        rank = 5
    else:
        rank = 6
    return (
        rank,
        -_llm_priority(row),
        -_effective_date_ordinal(row),
        -score,
        event_type,
        f"{subject}:{action}",
    )


def sort_delivery_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=_delivery_priority_key)


def _unique_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    results: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        results.append(text)
    return results


def _safe_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _snapshot_values(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        return {}
    values = snapshot.get("values")
    return values if isinstance(values, dict) else {}


def _usable_snapshot_quote(snapshot: dict[str, Any] | None, key: str) -> dict[str, Any]:
    quote = _snapshot_values(snapshot).get(key)
    if not isinstance(quote, dict):
        return {}
    if quote.get("status") not in {"ok", "stale"}:
        return {}
    if _safe_float(quote.get("value")) is None:
        return {}
    return quote


def _snapshot_date(snapshot: dict[str, Any] | None, run: dict[str, Any] | None = None) -> str:
    raw = ""
    if isinstance(snapshot, dict):
        raw = str(snapshot.get("as_of") or "").strip()
    if not raw and isinstance(run, dict):
        raw = str(run.get("as_of") or "").strip()
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return raw[:10]
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(KST_TZ))
    return parsed.astimezone(ZoneInfo(KST_TZ)).date().isoformat()


def _row_effective_date(row: dict[str, Any]) -> str:
    text = str(row.get("effective_date") or "").strip()
    return text[:10] if text else ""


def _movement_direction_from_text(text: str) -> str:
    has_up = bool(UP_MOVE_PATTERN.search(text))
    has_down = bool(DOWN_MOVE_PATTERN.search(text))
    if has_up and not has_down:
        return "up"
    if has_down and not has_up:
        return "down"
    return ""


def _row_movement_direction(row: dict[str, Any]) -> str:
    chunks = [str(row.get("title") or "")]
    annotation = row.get("llm_annotation")
    if isinstance(annotation, dict):
        chunks.append(str(annotation.get("summary_ko") or ""))
    editorial = row.get("llm_editorial")
    if isinstance(editorial, dict):
        chunks.append(str(editorial.get("summary_ko") or ""))
    for item in row.get("evidence_items") or []:
        if not isinstance(item, dict):
            continue
        chunks.append(str(item.get("title") or ""))
        chunks.append(str(item.get("summary") or ""))
    return _movement_direction_from_text(" ".join(chunks))


def _company_mechanism_key(row: dict[str, Any]) -> str:
    event_type = str(row.get("event_type") or "").strip()
    action = str(row.get("action") or "").strip()
    actions = set(part for part in re.split(r"[+/,]", action) if part)
    if event_type == "earnings":
        if actions & EARNINGS_ACTIONS:
            return "earnings"
    if event_type == "corporate_action":
        if actions & {"buyback"}:
            return "buyback"
        if actions & {"ma", "corporate_transaction"}:
            return "transaction"
    if event_type == "analyst":
        if actions & {"upgrade", "downgrade"}:
            return "rating"
        if actions & {"price_target", "analyst_action"}:
            return "target"
    if event_type == "mover":
        return "price_move"
    if event_type == "strategic":
        if actions & {"supply_deal"}:
            return "supply_deal"
        if actions & {"partnership"}:
            return "partnership"
        if actions & {"policy_risk"}:
            return "policy_risk"
        if actions & {"investment", "strategic_investment"}:
            return "investment"
        if actions & {"sector_pressure", "semiconductor_pressure"}:
            return "sector_pressure"
    return action or event_type


def _row_event_signatures(row: dict[str, Any]) -> list[str]:
    signatures = [row.get("event_signature")]
    merged = row.get("merged_event_signatures")
    if isinstance(merged, list):
        signatures.extend(merged)
    return _unique_strings(signatures)


def _row_actions(row: dict[str, Any]) -> list[str]:
    actions = []
    merged = row.get("merged_actions")
    if isinstance(merged, list):
        actions.extend(merged)
    actions.append(row.get("action"))
    split_actions: list[str] = []
    for action in actions:
        split_actions.extend(
            part.strip()
            for part in re.split(r"[+/,]", str(action or ""))
            if part.strip()
        )
    return _unique_strings(split_actions)


def _unique_evidence_items(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for row in rows:
        for item in row.get("evidence_items") or []:
            if not isinstance(item, dict):
                continue
            fingerprint = "|".join(
                [
                    str(item.get("candidate_id") or ""),
                    str(item.get("title") or "")[:240],
                    str(item.get("summary") or "")[:240],
                ]
            )
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            items.append(item)
    return items


def _better_grade(left: object, right: object) -> str:
    left_grade = str(left or "").strip().upper()
    right_grade = str(right or "").strip().upper()
    if GRADE_RANK.get(right_grade, -1) > GRADE_RANK.get(left_grade, -1):
        return right_grade
    return left_grade


def _merge_digest_rows(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    signatures = _unique_strings(_row_event_signatures(base) + _row_event_signatures(extra))
    decision_ids = _unique_strings(
        [base.get("decision_id"), extra.get("decision_id")]
        + list(base.get("merged_decision_ids") or [])
        + list(extra.get("merged_decision_ids") or [])
    )
    actions = _unique_strings(_row_actions(base) + _row_actions(extra))
    titles = _unique_strings(
        list(base.get("merged_titles") or [])
        + [base.get("title"), extra.get("title")]
        + list(extra.get("merged_titles") or [])
    )
    bodies = [
        str(value or "").strip()
        for value in [base.get("body_text"), extra.get("body_text")]
        if str(value or "").strip()
    ]

    merged["merged_event_signatures"] = signatures
    merged["merged_decision_ids"] = decision_ids
    merged["merged_actions"] = actions
    merged["merged_titles"] = titles
    merged["title"] = " | ".join(titles[:3])
    merged["score"] = max(float(base.get("score") or 0), float(extra.get("score") or 0))
    merged["grade"] = _better_grade(base.get("grade"), extra.get("grade"))
    merged["evidence_count"] = int(base.get("evidence_count") or 0) + int(
        extra.get("evidence_count") or 0
    )
    merged["providers"] = _unique_strings(
        list(base.get("providers") or []) + list(extra.get("providers") or [])
    )
    merged["sources"] = _unique_strings(
        list(base.get("sources") or []) + list(extra.get("sources") or [])
    )
    merged["candidate_ids"] = _unique_strings(
        list(base.get("candidate_ids") or []) + list(extra.get("candidate_ids") or [])
    )
    merged["risk_flags"] = _unique_strings(
        list(base.get("risk_flags") or []) + list(extra.get("risk_flags") or [])
    )
    merged["score_reasons"] = _unique_strings(
        list(base.get("score_reasons") or []) + list(extra.get("score_reasons") or [])
    )
    merged["extractor_reasons"] = _unique_strings(
        list(base.get("extractor_reasons") or [])
        + list(extra.get("extractor_reasons") or [])
    )
    merged["evidence_items"] = _unique_evidence_items([base, extra])
    merged["body_text"] = max(bodies, key=len) if bodies else ""
    if not isinstance(merged.get("llm_annotation"), dict) and isinstance(
        extra.get("llm_annotation"),
        dict,
    ):
        merged["llm_annotation"] = extra["llm_annotation"]
    if not isinstance(merged.get("llm_editorial"), dict) and isinstance(
        extra.get("llm_editorial"),
        dict,
    ):
        merged["llm_editorial"] = extra["llm_editorial"]
    if actions:
        merged["action"] = actions[0] if len(actions) == 1 else "+".join(actions[:4])
    return merged


def select_digest_rows(
    rows: list[dict[str, Any]],
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    if limit is None or limit <= 0:
        return rows

    selected: list[dict[str, Any]] = []
    selected_by_key: dict[str, int] = {}
    selected_by_quota_key: dict[str, int] = {}
    for row in rows:
        key = _digest_diversity_key(row)
        if key and key in selected_by_key:
            index = selected_by_key[key]
            selected[index] = _merge_digest_rows(selected[index], row)
            continue
        quota_key = _digest_subject_quota_key(row)
        quota_limit = _digest_subject_quota_limit(row)
        if (
            quota_key
            and quota_limit > 0
            and selected_by_quota_key.get(quota_key, 0) >= quota_limit
        ):
            continue
        if len(selected) >= limit and not _row_atomic_digest(row):
            continue
        selected.append(row)
        if key:
            selected_by_key[key] = len(selected) - 1
        if quota_key:
            selected_by_quota_key[quota_key] = (
                selected_by_quota_key.get(quota_key, 0) + 1
            )
    return selected


def _format_decimal(value: float, precision: int = 1) -> str:
    return f"{value:,.{precision}f}"


def _format_signed_pct(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


def _snapshot_provider_hint(quote: dict[str, Any]) -> str:
    provider = str(quote.get("provider") or MARKET_SNAPSHOT_SOURCE).strip()
    if not provider:
        return "snapshot"
    return f"{provider.upper()} snapshot"


def _snapshot_data_only_summary(change_text: str) -> str:
    return f"{change_text} — 시장 데이터 단독 변동, 뉴스 촉매 미확인"


def _snapshot_event_row(
    *,
    run: dict[str, Any] | None,
    snapshot: dict[str, Any],
    key: str,
    subject: str,
    action: str,
    title: str,
    summary: str,
    value: float,
    direction: str,
    grade: str,
    score: float,
    market_marker: str,
    quote: dict[str, Any],
) -> dict[str, Any]:
    as_of_date = _snapshot_date(snapshot, run)
    signature_seed = f"market_snapshot_hard_event_v1|{as_of_date}|{key}|{direction}"
    event_signature = sha256(signature_seed.encode("utf-8")).hexdigest()
    source_hint = _snapshot_provider_hint(quote)
    return {
        "decision_id": f"market-snapshot:{event_signature[:12]}",
        "run_id": str((run or {}).get("id") or ""),
        "event_signature": event_signature,
        "decision": "send_candidate",
        "score": score,
        "reason": MARKET_SNAPSHOT_HARD_EVENT_REASON,
        "policy": "market_snapshot_hard_event_v1",
        "event_type": "macro",
        "subject": subject,
        "action": action,
        "effective_date": as_of_date,
        "title": title,
        "url": "",
        "evidence_count": 1,
        "grade": grade,
        "risk_flags": [MARKET_SNAPSHOT_HARD_EVENT_REASON],
        "source_tier": "market_data",
        "event_quality": "hard_event",
        "hard_event_reason": MARKET_SNAPSHOT_HARD_EVENT_REASON,
        "soft_analysis_reason": "",
        "event_metadata": {
            "market_snapshot_key": key,
            "market_snapshot_value": value,
            "market_snapshot_direction": direction,
        },
        "price_reaction": {},
        "verification": {},
        "verification_status": "market_snapshot",
        "price_reaction_required": False,
        "send_worthy_reason": MARKET_SNAPSHOT_HARD_EVENT_REASON,
        "providers": [str(quote.get("provider") or MARKET_SNAPSHOT_SOURCE)],
        "sources": [MARKET_SNAPSHOT_SOURCE],
        "candidate_ids": [],
        "evidence_items": [
            {
                "candidate_id": event_signature,
                "source": MARKET_SNAPSHOT_SOURCE,
                "provider": str(quote.get("provider") or ""),
                "category": "MACRO",
                "title": title,
                "url": "",
                "published_at": str(snapshot.get("as_of") or ""),
                "summary": summary,
                "summary_chars": len(summary),
                "body_text": "",
                "body_fetch": {},
            }
        ],
        "body_text": "",
        "score_reasons": [MARKET_SNAPSHOT_HARD_EVENT_REASON],
        "extractor_reasons": [MARKET_SNAPSHOT_SOURCE],
        "llm_annotation": {
            "summary_ko": summary,
            "market_marker": market_marker,
            "confidence": "high",
            "basis": "snippet",
        },
        "llm_editorial": {
            "decision": "send",
            "grade": grade,
            "source_hint": source_hint,
            "summary_ko": summary,
            "market_marker": market_marker,
            "confidence": "high",
            "basis": "snippet",
        },
        "created_at": str(snapshot.get("as_of") or ""),
    }


def _quote_pct_change(
    quote: dict[str, Any],
    previous_quote: dict[str, Any] | None = None,
) -> float | None:
    pct = _safe_float(quote.get("change_pct"))
    if pct is not None:
        return pct
    current_value = _safe_float(quote.get("value"))
    previous_value = (
        _safe_float(previous_quote.get("value"))
        if isinstance(previous_quote, dict)
        else None
    )
    if current_value is None or previous_value in {None, 0.0}:
        return None
    assert previous_value is not None
    return ((current_value - previous_value) / previous_value) * 100.0


def _best_oil_move(
    snapshot: dict[str, Any] | None,
    previous_snapshot: dict[str, Any] | None,
) -> tuple[str, dict[str, Any], float] | None:
    candidates: list[tuple[str, dict[str, Any], float]] = []
    for key in ("brent", "wti"):
        quote = _usable_snapshot_quote(snapshot, key)
        if not quote:
            continue
        previous_quote = _usable_snapshot_quote(previous_snapshot, key)
        pct = _quote_pct_change(quote, previous_quote)
        if pct is None:
            continue
        candidates.append((key, quote, pct))
    if not candidates:
        return None
    return max(candidates, key=lambda item: abs(item[2]))


def _snapshot_hard_oil_row(
    snapshot: dict[str, Any],
    previous_snapshot: dict[str, Any] | None,
    run: dict[str, Any] | None,
) -> dict[str, Any] | None:
    move = _best_oil_move(snapshot, previous_snapshot)
    if move is None:
        return None
    key, quote, pct = move
    if abs(pct) < OIL_MOVE_THRESHOLD_PCT:
        return None
    value = float(quote["value"])
    label = "Brent" if key == "brent" else "WTI"
    direction = "up" if pct > 0 else "down"
    change_text = f"{label} 원유 ${_format_decimal(value)} ({_format_signed_pct(pct)})"
    summary = _snapshot_data_only_summary(change_text)
    marker = "red" if direction == "up" else "green"
    return _snapshot_event_row(
        run=run,
        snapshot=snapshot,
        key="oil",
        subject="OIL",
        action="oil_update",
        title=summary,
        summary=summary,
        value=value,
        direction=direction,
        grade="A",
        score=93.0,
        market_marker=marker,
        quote=quote,
    )


def _snapshot_pct_row(
    snapshot: dict[str, Any],
    previous_snapshot: dict[str, Any] | None,
    run: dict[str, Any] | None,
    *,
    key: str,
    subject: str,
    action: str,
    label: str,
    threshold_pct: float,
    precision: int,
    grade: str,
    score: float,
    up_summary: str,
    down_summary: str,
    up_marker: str,
    down_marker: str,
) -> dict[str, Any] | None:
    quote = _usable_snapshot_quote(snapshot, key)
    if not quote:
        return None
    pct = _quote_pct_change(quote, _usable_snapshot_quote(previous_snapshot, key))
    if pct is None or abs(pct) < threshold_pct:
        return None
    value = float(quote["value"])
    direction = "up" if pct > 0 else "down"
    change_text = f"{label} {_format_decimal(value, precision)} ({_format_signed_pct(pct)})"
    summary = _snapshot_data_only_summary(change_text)
    return _snapshot_event_row(
        run=run,
        snapshot=snapshot,
        key=key,
        subject=subject,
        action=action,
        title=summary,
        summary=summary,
        value=value,
        direction=direction,
        grade=grade,
        score=score,
        market_marker=up_marker if direction == "up" else down_marker,
        quote=quote,
    )


def _snapshot_index_row(
    snapshot: dict[str, Any],
    previous_snapshot: dict[str, Any] | None,
    run: dict[str, Any] | None,
) -> dict[str, Any] | None:
    moves: list[tuple[str, dict[str, Any], float]] = []
    for key, threshold in INDEX_MOVE_THRESHOLDS_PCT.items():
        quote = _usable_snapshot_quote(snapshot, key)
        if not quote:
            continue
        pct = _quote_pct_change(quote, _usable_snapshot_quote(previous_snapshot, key))
        if pct is None or abs(pct) < threshold:
            continue
        moves.append((key, quote, pct))
    if not moves:
        return None
    direction_scores = {"up": 0, "down": 0}
    for _key, _quote, pct in moves:
        direction_scores["up" if pct > 0 else "down"] += 1
    direction = "up" if direction_scores["up"] >= direction_scores["down"] else "down"
    labels = {"sp500": "S&P", "nasdaq": "NASDAQ", "dow": "DOW"}
    parts = [
        f"{labels[key]} {_format_decimal(float(quote['value']), 0)} ({_format_signed_pct(pct)})"
        for key, quote, pct in moves[:3]
    ]
    summary = _snapshot_data_only_summary(" / ".join(parts))
    best_key, best_quote, _best_pct = max(moves, key=lambda item: abs(item[2]))
    return _snapshot_event_row(
        run=run,
        snapshot=snapshot,
        key="indices",
        subject="MACRO",
        action="macro_update",
        title=summary,
        summary=summary,
        value=float(best_quote["value"]),
        direction=direction,
        grade="A",
        score=91.0,
        market_marker="green" if direction == "up" else "red",
        quote=best_quote,
    )


def _snapshot_ten_year_row(
    snapshot: dict[str, Any],
    previous_snapshot: dict[str, Any] | None,
    run: dict[str, Any] | None,
) -> dict[str, Any] | None:
    quote = _usable_snapshot_quote(snapshot, "ten_year")
    previous_quote = _usable_snapshot_quote(previous_snapshot, "ten_year")
    if not quote or not previous_quote:
        return None
    current = _safe_float(quote.get("value"))
    previous = _safe_float(previous_quote.get("value"))
    if current is None or previous is None:
        return None
    change_bp = (current - previous) * 100.0
    if abs(change_bp) < TEN_YEAR_MOVE_THRESHOLD_BP:
        return None
    direction = "up" if change_bp > 0 else "down"
    sign = "+" if change_bp > 0 else ""
    summary = _snapshot_data_only_summary(
        f"미 10Y 금리 {current:.2f}% ({sign}{change_bp:.0f}bp)"
    )
    return _snapshot_event_row(
        run=run,
        snapshot=snapshot,
        key="ten_year",
        subject="RATES",
        action="rates_update",
        title=summary,
        summary=summary,
        value=current,
        direction=direction,
        grade="B" if abs(change_bp) < 15.0 else "A",
        score=88.0 if abs(change_bp) < 15.0 else 92.0,
        market_marker="red" if direction == "up" else "green",
        quote=quote,
    )


def _market_snapshot_hard_event_rows(
    snapshot: dict[str, Any] | None,
    previous_snapshot: dict[str, Any] | None,
    *,
    run: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    # Market snapshots are already shown in the digest footer. Do not promote
    # price-only moves into news bullets without a source-backed catalyst.
    return []


def _oil_snapshot_direction(snapshot: dict[str, Any] | None) -> str:
    move = _best_oil_move(snapshot, None)
    if move is None:
        return ""
    _key, _quote, pct = move
    if abs(pct) < OIL_MOVE_THRESHOLD_PCT:
        return ""
    return "up" if pct > 0 else "down"


def _row_text_for_snapshot_guard(row: dict[str, Any]) -> str:
    chunks = [str(row.get("title") or "")]
    annotation = row.get("llm_annotation")
    if isinstance(annotation, dict):
        chunks.append(str(annotation.get("summary_ko") or ""))
    editorial = row.get("llm_editorial")
    if isinstance(editorial, dict):
        chunks.append(str(editorial.get("summary_ko") or ""))
    for item in row.get("evidence_items") or []:
        if not isinstance(item, dict):
            continue
        chunks.append(str(item.get("title") or ""))
        chunks.append(str(item.get("summary") or ""))
    return " ".join(chunks)


def _row_conflicts_with_market_snapshot(
    row: dict[str, Any],
    market_snapshot: dict[str, Any] | None,
) -> bool:
    if str(row.get("event_type") or "") not in {"geo", "macro"}:
        return False
    snapshot_direction = _oil_snapshot_direction(market_snapshot)
    if not snapshot_direction:
        return False
    text = _row_text_for_snapshot_guard(row)
    if not OIL_TEXT_PATTERN.search(text):
        return False
    row_direction = _movement_direction_from_text(text)
    return bool(row_direction and row_direction != snapshot_direction)


def _filter_snapshot_conflicting_rows(
    rows: list[dict[str, Any]],
    market_snapshot: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for row in rows:
        if _row_conflicts_with_market_snapshot(row, market_snapshot):
            dropped.append(row)
        else:
            kept.append(row)
    return kept, dropped


def _row_is_earnings_preview(row: dict[str, Any]) -> bool:
    if str(row.get("event_type") or "") != "earnings":
        return False
    chunks = [str(row.get("title") or "")]
    annotation = row.get("llm_annotation")
    if isinstance(annotation, dict):
        chunks.append(str(annotation.get("summary_ko") or ""))
    editorial = row.get("llm_editorial")
    if isinstance(editorial, dict):
        chunks.append(str(editorial.get("summary_ko") or ""))
    text = " ".join(chunks)
    return bool(EARNINGS_PREVIEW_PATTERN.search(text))


def _filter_stale_company_preview_rows(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    reported_subjects = {
        str(row.get("subject") or "").strip().lower()
        for row in rows
        if str(row.get("event_type") or "") == "earnings"
        and "earnings_report" in str(row.get("action") or "")
        and not _row_is_earnings_preview(row)
    }
    reported_subjects.discard("")
    if not reported_subjects:
        return rows, []
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for row in rows:
        subject = str(row.get("subject") or "").strip().lower()
        if subject in reported_subjects and _row_is_earnings_preview(row):
            dropped.append(row)
        else:
            kept.append(row)
    return kept, dropped


def _load_previous_market_snapshot_for_run(
    conn: Any,
    *,
    run_id: str,
) -> dict[str, Any] | None:
    current = conn.execute(
        """
        SELECT as_of, created_at
        FROM market_snapshots
        WHERE run_id = ?
        LIMIT 1
        """,
        (run_id,),
    ).fetchone()
    if current is None:
        return None
    row = conn.execute(
        """
        SELECT payload_json
        FROM market_snapshots
        WHERE run_id != ?
          AND (created_at < ? OR as_of < ?)
        ORDER BY created_at DESC, as_of DESC
        LIMIT 1
        """,
        (run_id, current["created_at"], current["as_of"]),
    ).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(row["payload_json"])
    except (TypeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def build_dry_run_deliveries(
    rows: list[dict[str, Any]],
    *,
    channel: str = DEFAULT_CHANNEL,
) -> list[DeliveryRecord]:
    deliveries = []
    for index, row in enumerate(rows, start=1):
        message = compose_message(row, index=index)
        safety = validate_message_for_delivery(message, channel=channel)
        run_id = str(row.get("run_id") or "")
        event_signature = str(row.get("event_signature") or "")
        payload = {
            "mode": "dry-run",
            "channel": channel,
            "decision_id": row.get("decision_id"),
            "decision": row.get("decision"),
            "score": row.get("score"),
            "evidence_contract": row.get("evidence_contract"),
            "safety": safety,
            "message": message,
        }
        deliveries.append(
            DeliveryRecord(
                id=_delivery_id(
                    run_id=run_id,
                    event_signature=event_signature,
                    channel=channel,
                    status=DRY_RUN_STATUS,
                ),
                run_id=run_id,
                event_signature=event_signature,
                channel=channel,
                status=DRY_RUN_STATUS,
                message_id=None,
                payload=payload,
            )
        )
    _validate_unique_delivery_ids(deliveries)
    return deliveries


def build_live_deliveries(
    rows: list[dict[str, Any]],
    *,
    channel: str = DEFAULT_CHANNEL,
    run: dict[str, Any] | None = None,
    skipped_previously_sent: int = 0,
    market_snapshot: dict[str, Any] | None = None,
    exclusion_counts: dict[str, int] | None = None,
) -> list[DeliveryRecord]:
    message = compose_digest_message(
        rows,
        run=run,
        skipped_previously_sent=skipped_previously_sent,
        market_snapshot=market_snapshot,
        exclusion_counts=exclusion_counts,
    )
    safety = validate_message_for_delivery(message, channel=channel)
    if not rows:
        run_id = str((run or {}).get("id") or "")
        payload = {
            "mode": "live",
            "channel": channel,
            "digest_index": 0,
            "digest_size": 0,
            "decision_id": None,
            "decision": "digest",
            "score": None,
            "evidence_contract": None,
            "safety": safety,
            "message": message,
        }
        return [
            DeliveryRecord(
                id=_live_run_digest_delivery_id(run_id=run_id, channel=channel),
                run_id=run_id,
                event_signature=None,
                channel=channel,
                status=LIVE_SENT_STATUS,
                message_id=None,
                payload=payload,
            )
        ]

    deliveries = []
    for index, row in enumerate(rows, start=1):
        run_id = str(row.get("run_id") or "")
        row_signatures = _row_event_signatures(row)
        for event_signature in row_signatures:
            payload = {
                "mode": "live",
                "channel": channel,
                "digest_index": index,
                "digest_size": len(rows),
                "decision_id": row.get("decision_id"),
                "decision": row.get("decision"),
                "score": row.get("score"),
                "primary_event_signature": row.get("event_signature"),
                "merged_event_signatures": row_signatures,
                "evidence_contract": row.get("evidence_contract"),
                "safety": safety,
                "message": message,
            }
            deliveries.append(
                DeliveryRecord(
                    id=_live_delivery_id(
                        event_signature=event_signature,
                        channel=channel,
                    ),
                    run_id=run_id,
                    event_signature=event_signature,
                    channel=channel,
                    status=LIVE_SENT_STATUS,
                    message_id=None,
                    payload=payload,
                )
            )
    _validate_unique_delivery_ids(deliveries)
    return deliveries


def _existing_sent_event_signatures(
    db_path: Path,
    *,
    channel: str,
    event_signatures: set[str],
) -> set[str]:
    if not event_signatures:
        return set()
    placeholders = ",".join("?" for _ in event_signatures)
    params: list[Any] = [channel, LIVE_SENT_STATUS]
    params.extend(sorted(event_signatures))
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT DISTINCT event_signature
            FROM deliveries
            WHERE channel = ?
              AND status = ?
              AND event_signature IN ({placeholders})
            """,
            params,
        ).fetchall()
    return {str(row["event_signature"]) for row in rows}


def _run_has_sent_delivery(
    db_path: Path,
    *,
    run_id: str,
    channel: str,
) -> bool:
    if not run_id:
        return False
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT 1
            FROM deliveries
            WHERE run_id = ?
              AND channel = ?
              AND status = ?
            LIMIT 1
            """,
            (run_id, channel, LIVE_SENT_STATUS),
        ).fetchone()
    return row is not None


def _theme_event_signature(candidate: dict[str, Any]) -> str:
    return f"market_theme:{candidate.get('id') or ''}"


def _parse_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(KST_TZ))
    return parsed


def _recent_sent_theme_keys(
    db_path: Path,
    *,
    channel: str,
    theme_keys: set[str],
    run: dict[str, Any],
    cooldown_hours: int = THEME_REPEAT_COOLDOWN_HOURS,
) -> set[str]:
    if not theme_keys:
        return set()
    run_at = _parse_datetime(run.get("as_of")) or _parse_datetime(run.get("started_at"))
    if run_at is None:
        return set()
    cutoff = run_at - timedelta(hours=cooldown_hours)
    seen: set[str] = set()
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT d.payload_json, d.created_at, r.as_of AS run_as_of
            FROM deliveries d
            LEFT JOIN runs r ON r.id = d.run_id
            WHERE d.channel = ?
              AND d.status = ?
              AND d.event_signature LIKE 'market_theme:%'
            ORDER BY created_at DESC
            LIMIT 200
            """,
            (channel, LIVE_SENT_STATUS),
        ).fetchall()
    for row in rows:
        sent_at = _parse_datetime(row["run_as_of"]) or _parse_datetime(row["created_at"])
        if sent_at is None or sent_at < cutoff or sent_at >= run_at:
            continue
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except json.JSONDecodeError:
            continue
        contract = payload.get("evidence_contract")
        if not isinstance(contract, dict):
            continue
        theme_key = str(contract.get("theme_key") or "")
        if theme_key in theme_keys:
            seen.add(theme_key)
    return seen


def _theme_rows_for_run(
    db_path: Path,
    *,
    run: dict[str, Any],
    decision_rows: list[dict[str, Any]],
    channel: str,
    llm_enabled: bool,
    llm_api_key: str | None,
    llm_model: str,
    llm_theme_editor_model: str,
    llm_timeout_seconds: float,
    llm_client: LLMClient | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    run_id = str(run.get("id") or "")
    if not run_id:
        return [], {
            "status": "skipped_no_run_id",
            "candidates": 0,
            "attempted": 0,
            "selected": 0,
            "skipped_previously_sent": 0,
        }
    if not llm_enabled:
        return [], {
            "status": "disabled",
            "candidates": 0,
            "attempted": 0,
            "selected": 0,
            "skipped_previously_sent": 0,
        }

    raw_items = load_raw_items_for_run(db_path, run_id=run_id)
    candidates = build_market_theme_candidates(
        raw_items=raw_items,
        decision_rows=decision_rows,
    )
    seed_candidates = load_news_seed_candidates_for_run(db_path, run_id=run_id)
    existing_theme_keys = {
        str(candidate.get("theme_key") or "")
        for candidate in candidates
        if str(candidate.get("theme_key") or "")
    }
    for candidate in seed_candidates:
        theme_key = str(candidate.get("theme_key") or "")
        if not theme_key or theme_key in existing_theme_keys:
            continue
        existing_theme_keys.add(theme_key)
        candidates.append(candidate)
    candidate_signatures = {
        _theme_event_signature(candidate)
        for candidate in candidates
        if candidate.get("id")
    }
    sent_signatures = _existing_sent_event_signatures(
        db_path,
        channel=channel,
        event_signatures=candidate_signatures,
    )
    recently_sent_theme_keys = _recent_sent_theme_keys(
        db_path,
        channel=channel,
        theme_keys={
            str(candidate.get("theme_key") or "")
            for candidate in candidates
            if str(candidate.get("theme_key") or "")
        },
        run=run,
    )
    unsent_candidates = [
        candidate
        for candidate in candidates
        if _theme_event_signature(candidate) not in sent_signatures
        and str(candidate.get("theme_key") or "") not in recently_sent_theme_keys
    ]
    llm_summary = edit_theme_candidates(
        unsent_candidates,
        enabled=llm_enabled,
        api_key=llm_api_key,
        model=llm_theme_editor_model,
        timeout_seconds=llm_timeout_seconds,
        client=llm_client,
    )
    theme_rows = [
        theme_candidate_to_delivery_row(candidate, run=run)
        for candidate in unsent_candidates
        if _theme_editorial_allows_delivery(candidate)
    ]
    summary = {
        **llm_summary,
        "status": llm_summary.get("status", "ok"),
        "candidates": len(candidates),
        "seed_candidates": len(seed_candidates),
        "raw_items": len(raw_items),
        "selected": len(theme_rows),
        "skipped_previously_sent": len(candidates) - len(unsent_candidates),
        "skipped_recent_theme_keys": sorted(recently_sent_theme_keys),
        "candidate_keys": [
            str(candidate.get("theme_key") or "") for candidate in candidates
        ],
        "candidate_policies": sorted(
            {
                str(candidate.get("policy") or "")
                for candidate in candidates
                if str(candidate.get("policy") or "")
            }
        ),
    }
    return theme_rows, summary


def _delivery_llm_models(
    *,
    llm_model: str,
    llm_editorial_model: str | None,
    llm_theme_editor_model: str | None,
    llm_summary_model: str | None,
) -> dict[str, str]:
    return {
        "base": llm_model,
        "editorial": llm_editorial_model or llm_model,
        "theme_editor": llm_theme_editor_model or llm_model,
        "summary": llm_summary_model or llm_model,
    }


def send_telegram_message(
    *,
    bot_token: str,
    chat_id: str,
    message: dict[str, Any],
    timeout_seconds: float = DEFAULT_TELEGRAM_TIMEOUT_SECONDS,
    api_base: str = TELEGRAM_API_BASE,
) -> str:
    if not bot_token.strip():
        raise DeliverySafetyError("Telegram bot token is missing")
    if not chat_id.strip():
        raise DeliverySafetyError("Telegram chat id is missing")

    validate_message_for_delivery(message, channel=DEFAULT_CHANNEL)
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": str(message["text"]),
        "disable_web_page_preview": False,
    }
    parse_mode = message.get("parse_mode")
    if parse_mode is not None:
        payload["parse_mode"] = parse_mode

    url = f"{api_base.rstrip('/')}/bot{bot_token}/sendMessage"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            response_body = response.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise DeliverySafetyError(
            f"Telegram sendMessage HTTP {exc.code}: {detail}"
        ) from exc
    except (error.URLError, TimeoutError, OSError) as exc:
        raise DeliverySafetyError(f"Telegram sendMessage failed: {exc}") from exc

    try:
        data = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise DeliverySafetyError("Telegram sendMessage returned invalid JSON") from exc
    if not data.get("ok"):
        raise DeliverySafetyError(
            "Telegram sendMessage returned ok=false: "
            + str(data.get("description") or "no description")
        )
    result = data.get("result")
    if not isinstance(result, dict) or "message_id" not in result:
        raise DeliverySafetyError("Telegram sendMessage response missing message_id")
    return str(result["message_id"])


def create_dry_run_deliveries(
    db_path: Path,
    *,
    run_id: str = "latest",
    channel: str = DEFAULT_CHANNEL,
    decisions: set[str] | None = None,
    limit: int | None = None,
    llm_enabled: bool = False,
    llm_api_key: str | None = None,
    llm_model: str = DEFAULT_LLM_MODEL,
    llm_editorial_model: str | None = None,
    llm_theme_editor_model: str | None = None,
    llm_summary_model: str | None = None,
    llm_timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS,
    llm_client: LLMClient | None = None,
) -> dict[str, Any]:
    report = load_decision_rows(
        db_path,
        run_id=run_id,
        decisions=decisions or DEFAULT_MESSAGE_DECISIONS,
    )
    eligible_rows, blocked_rows = _filter_contract_eligible_rows(report["rows"])
    llm_models = _delivery_llm_models(
        llm_model=llm_model,
        llm_editorial_model=llm_editorial_model,
        llm_theme_editor_model=llm_theme_editor_model,
        llm_summary_model=llm_summary_model,
    )
    theme_rows, llm_theme_editor = _theme_rows_for_run(
        db_path,
        run=report["run"],
        decision_rows=report["rows"],
        channel=channel,
        llm_enabled=llm_enabled,
        llm_api_key=llm_api_key,
        llm_model=llm_model,
        llm_theme_editor_model=llm_models["theme_editor"],
        llm_timeout_seconds=llm_timeout_seconds,
        llm_client=llm_client,
    )
    llm_editorial = edit_rows(
        eligible_rows,
        db_path=db_path,
        enabled=llm_enabled,
        api_key=llm_api_key,
        model=llm_models["editorial"],
        timeout_seconds=llm_timeout_seconds,
        client=llm_client,
    )
    eligible_rows = [
        row for row in eligible_rows if _editorial_allows_delivery(row)
    ]
    eligible_rows.extend(theme_rows)
    eligible_rows = select_digest_rows(sort_delivery_rows(eligible_rows), limit=limit)
    llm_summary = annotate_rows(
        _rows_missing_llm_annotation(eligible_rows),
        db_path=db_path,
        enabled=llm_enabled,
        api_key=llm_api_key,
        model=llm_models["summary"],
        timeout_seconds=llm_timeout_seconds,
        client=llm_client,
    )
    eligible_rows, llm_summary_rejected_rows = _filter_llm_summary_validated_rows(
        eligible_rows
    )
    if llm_enabled:
        eligible_rows, final_publish_dropped_rows = _filter_final_publish_ready_rows(
            eligible_rows
        )
    else:
        final_publish_dropped_rows = []
    deliveries = build_dry_run_deliveries(eligible_rows, channel=channel)
    created_at = datetime.now(ZoneInfo(KST_TZ)).isoformat()
    with connect(db_path) as conn:
        inserted = insert_delivery_records(
            conn,
            created_at=created_at,
            deliveries=[delivery.as_record() for delivery in deliveries],
        )

    return {
        "run": report["run"],
        "mode": "dry-run",
        "channel": channel,
        "llm_models": llm_models,
        "requested": len(deliveries),
        "inserted": inserted,
        "skipped_existing": len(deliveries) - inserted,
        "delivery_ids": [delivery.id for delivery in deliveries],
        "contract": _contract_summary(rows=report["rows"], blocked=blocked_rows),
        "safety": {
            "status": "ok",
            "allowed_channels": list(ALLOWED_CHANNELS),
            "parse_mode": PLAIN_TEXT_PARSE_MODE,
            "max_text_length": TELEGRAM_TEXT_MAX_CHARS,
        },
        "llm_theme_editor": llm_theme_editor,
        "llm_editorial": llm_editorial,
        "llm_annotation": llm_summary,
        "llm_summary_rejected": len(llm_summary_rejected_rows),
        "final_publish_dropped": len(final_publish_dropped_rows),
        "status": "ok",
    }


def create_live_deliveries(
    db_path: Path,
    *,
    bot_token: str,
    chat_id: str = DEFAULT_TELEGRAM_CHAT_ID,
    run_id: str = "latest",
    channel: str = DEFAULT_CHANNEL,
    decisions: set[str] | None = None,
    limit: int | None = None,
    llm_enabled: bool = False,
    llm_api_key: str | None = None,
    llm_model: str = DEFAULT_LLM_MODEL,
    llm_editorial_model: str | None = None,
    llm_theme_editor_model: str | None = None,
    llm_summary_model: str | None = None,
    llm_timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS,
    llm_client: LLMClient | None = None,
) -> dict[str, Any]:
    report = load_decision_rows(
        db_path,
        run_id=run_id,
        decisions=decisions or DEFAULT_MESSAGE_DECISIONS,
    )
    run_id_value = str(report["run"].get("id") or "")
    with connect(db_path) as conn:
        market_snapshot = load_market_snapshot_for_run(conn, run_id=run_id_value)
        previous_market_snapshot = _load_previous_market_snapshot_for_run(
            conn,
            run_id=run_id_value,
        )
    rows = report["rows"]
    eligible_rows, blocked_rows = _filter_contract_eligible_rows(rows)
    llm_models = _delivery_llm_models(
        llm_model=llm_model,
        llm_editorial_model=llm_editorial_model,
        llm_theme_editor_model=llm_theme_editor_model,
        llm_summary_model=llm_summary_model,
    )
    event_signatures = {str(row.get("event_signature") or "") for row in rows}
    event_signatures.discard("")
    sent_signatures = _existing_sent_event_signatures(
        db_path,
        channel=channel,
        event_signatures=event_signatures,
    )
    unsent_rows = [
        row
        for row in eligible_rows
        if str(row.get("event_signature") or "") not in sent_signatures
    ]
    skipped_previously_sent = len(eligible_rows) - len(unsent_rows)
    editorial_input_rows = list(unsent_rows)
    llm_editorial = edit_rows(
        unsent_rows,
        db_path=db_path,
        enabled=llm_enabled,
        api_key=llm_api_key,
        model=llm_models["editorial"],
        timeout_seconds=llm_timeout_seconds,
        client=llm_client,
    )
    editorial_dropped_rows = [
        row for row in editorial_input_rows if not _editorial_allows_delivery(row)
    ]
    unsent_rows = [row for row in unsent_rows if _editorial_allows_delivery(row)]
    theme_rows, llm_theme_editor = _theme_rows_for_run(
        db_path,
        run=report["run"],
        decision_rows=rows,
        channel=channel,
        llm_enabled=llm_enabled,
        llm_api_key=llm_api_key,
        llm_model=llm_model,
        llm_theme_editor_model=llm_models["theme_editor"],
        llm_timeout_seconds=llm_timeout_seconds,
        llm_client=llm_client,
    )
    skipped_previously_sent += int(
        llm_theme_editor.get("skipped_previously_sent") or 0
    )
    unsent_rows.extend(theme_rows)
    unsent_rows, stale_preview_dropped_rows = _filter_stale_company_preview_rows(
        unsent_rows
    )
    unsent_rows, snapshot_conflict_dropped_rows = _filter_snapshot_conflicting_rows(
        unsent_rows,
        market_snapshot,
    )
    snapshot_rows = _market_snapshot_hard_event_rows(
        market_snapshot,
        previous_market_snapshot,
        run=report["run"],
    )
    snapshot_signatures = {
        str(row.get("event_signature") or "")
        for row in snapshot_rows
        if row.get("event_signature")
    }
    sent_snapshot_signatures = _existing_sent_event_signatures(
        db_path,
        channel=channel,
        event_signatures=snapshot_signatures,
    )
    skipped_previously_sent += len(sent_snapshot_signatures)
    unsent_rows.extend(
        row
        for row in snapshot_rows
        if str(row.get("event_signature") or "") not in sent_snapshot_signatures
    )
    unsent_rows = select_digest_rows(sort_delivery_rows(unsent_rows), limit=limit)
    selected_count = len(unsent_rows)
    llm_summary = annotate_rows(
        _rows_missing_llm_annotation(unsent_rows),
        db_path=db_path,
        enabled=llm_enabled,
        api_key=llm_api_key,
        model=llm_models["summary"],
        timeout_seconds=llm_timeout_seconds,
        client=llm_client,
    )
    unsent_rows, llm_summary_rejected_rows = _filter_llm_summary_validated_rows(
        unsent_rows
    )
    unsent_rows, final_publish_dropped_rows = _filter_final_publish_ready_rows(
        unsent_rows
    )
    selected_count = len(unsent_rows)
    theme_decisions = llm_theme_editor.get("decisions")
    if not isinstance(theme_decisions, dict):
        theme_decisions = {}
    exclusion_counts = {
        "duplicate": skipped_previously_sent,
        "contract_blocked": len(blocked_rows),
        "editorial_dropped": len(editorial_dropped_rows),
        "theme_dropped": int(theme_decisions.get("drop") or 0),
        "stale_preview_dropped": len(stale_preview_dropped_rows),
        "snapshot_conflict_dropped": len(snapshot_conflict_dropped_rows),
        "summary_rejected": len(llm_summary_rejected_rows),
        "final_publish_dropped": len(final_publish_dropped_rows),
    }
    if not unsent_rows and _run_has_sent_delivery(
        db_path,
        run_id=run_id_value,
        channel=channel,
    ):
        deliveries = []
    else:
        deliveries = build_live_deliveries(
            unsent_rows,
            channel=channel,
            run=report["run"],
            skipped_previously_sent=skipped_previously_sent,
            market_snapshot=market_snapshot,
            exclusion_counts=exclusion_counts,
        )

    created_at = datetime.now(ZoneInfo(KST_TZ)).isoformat()
    sent = 0
    inserted = 0
    message_ids: list[str] = []
    sent_records: list[dict[str, Any]] = []
    if deliveries:
        digest_message = deliveries[0].payload["message"]
        message_id = send_telegram_message(
            bot_token=bot_token,
            chat_id=chat_id,
            message=digest_message,
        )
        for delivery in deliveries:
            payload = dict(delivery.payload)
            payload["telegram"] = {
                "chat_id": chat_id,
                "message_id": message_id,
            }
            record = DeliveryRecord(
                id=delivery.id,
                run_id=delivery.run_id,
                event_signature=delivery.event_signature,
                channel=delivery.channel,
                status=LIVE_SENT_STATUS,
                message_id=message_id,
                payload=payload,
            ).as_record()
            sent_records.append(record)
        with connect(db_path) as conn:
            inserted += insert_delivery_records(
                conn,
                created_at=created_at,
                deliveries=sent_records,
            )
        sent += 1
        message_ids.append(message_id)

    return {
        "run": report["run"],
        "mode": "live",
        "channel": channel,
        "chat_id": chat_id,
        "llm_models": llm_models,
        "requested": len(rows),
        "selected": selected_count,
        "sent": sent,
        "inserted": inserted,
        "skipped_previously_sent": skipped_previously_sent,
        "exclusion_counts": exclusion_counts,
        "contract": _contract_summary(rows=rows, blocked=blocked_rows),
        "message_ids": message_ids,
        "delivery_ids": [record["id"] for record in sent_records],
        "safety": {
            "status": "ok",
            "allowed_channels": list(ALLOWED_CHANNELS),
            "parse_mode": PLAIN_TEXT_PARSE_MODE,
            "max_text_length": TELEGRAM_TEXT_MAX_CHARS,
            "max_selected": limit,
        },
        "llm_theme_editor": llm_theme_editor,
        "llm_editorial": llm_editorial,
        "llm_annotation": llm_summary,
        "llm_summary_rejected": len(llm_summary_rejected_rows),
        "final_publish_dropped": len(final_publish_dropped_rows),
        "market_snapshot_hard_events": len(snapshot_rows),
        "market_snapshot_conflict_dropped": len(snapshot_conflict_dropped_rows),
        "stale_preview_dropped": len(stale_preview_dropped_rows),
        "market_snapshot_status": (
            market_snapshot.get("status") if isinstance(market_snapshot, dict) else "missing"
        ),
        "status": "ok",
    }


def render_delivery_summary(summary: dict[str, Any]) -> str:
    return json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
