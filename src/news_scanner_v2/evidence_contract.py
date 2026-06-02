from __future__ import annotations

from typing import Any
from urllib.parse import urlparse


CONTRACT_VERSION = "evidence_contract_v1"
MIN_BODY_BASIS_CHARS = 300
MIN_SNIPPET_BASIS_CHARS = 120
COMPANY_EVENT_TYPES = {
    "analyst",
    "corporate_action",
    "earnings",
    "mover",
    "strategic",
}
VALID_SOURCE_TIERS = {"trusted", "untrusted", "low_quality"}
VALID_PRICE_REACTION_STATUS = "ok"
MIN_SINGLE_SOURCE_HARD_EVENT_PRICE_MOVE_PCT = 2.0
EARNINGS_CURRENT_VERIFICATION_DOMAIN_SUFFIXES = (
    "sec.gov",
    "businesswire.com",
    "cnbc.com",
    "globenewswire.com",
    "marketwatch.com",
    "prnewswire.com",
    "reuters.com",
    "apnews.com",
)
EARNINGS_CURRENT_VERIFICATION_SOURCE_TERMS = (
    "official",
    "issuer",
    "sec",
    "businesswire",
    "cnbc",
    "globenewswire",
    "marketwatch",
    "prnewswire",
    "reuters",
    "ap",
)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _domain_from_url(value: Any) -> str:
    try:
        parsed = urlparse(str(value or ""))
    except ValueError:
        return ""
    return parsed.netloc.lower().removeprefix("www.")


def _domain_matches(domain: str, suffixes: tuple[str, ...]) -> bool:
    normalized = domain.lower().removeprefix("www.")
    return any(
        normalized == suffix or normalized.endswith(f".{suffix}")
        for suffix in suffixes
    )


def _row_domains(row: dict[str, Any]) -> list[str]:
    domains: list[str] = []
    for key in ("domains", "trusted_domains"):
        for value in _as_list(row.get(key)):
            text = _text(value).lower().removeprefix("www.")
            if text:
                domains.append(text)
    url_domain = _domain_from_url(row.get("url"))
    if url_domain:
        domains.append(url_domain)
    for item in _as_list(row.get("evidence_items")):
        if not isinstance(item, dict):
            continue
        item_domain = _domain_from_url(item.get("url"))
        if item_domain:
            domains.append(item_domain)
    verification = _as_dict(row.get("verification"))
    match = _as_dict(verification.get("match"))
    match_domain = _text(match.get("domain")).lower().removeprefix("www.")
    if match_domain:
        domains.append(match_domain)
    return list(dict.fromkeys(domains))


def _earnings_current_verified(row: dict[str, Any]) -> bool:
    domains = _row_domains(row)
    if any(
        _domain_matches(domain, EARNINGS_CURRENT_VERIFICATION_DOMAIN_SUFFIXES)
        for domain in domains
    ):
        return True
    source_text = " ".join(
        _text(value).lower()
        for value in (
            _as_list(row.get("sources"))
            + _as_list(row.get("providers"))
            + [_as_dict(row.get("verification")).get("provider")]
        )
    )
    evidence_text = " ".join(
        _text(value).lower()
        for value in (
            [row.get("title"), row.get("url")]
            + [
                item.get("title")
                for item in _as_list(row.get("evidence_items"))
                if isinstance(item, dict)
            ]
            + [
                item.get("url")
                for item in _as_list(row.get("evidence_items"))
                if isinstance(item, dict)
            ]
        )
    )
    combined_text = f"{source_text} {evidence_text}"
    return any(
        term in combined_text
        for term in EARNINGS_CURRENT_VERIFICATION_SOURCE_TERMS
    )


def select_evidence_basis(row: dict[str, Any]) -> dict[str, Any]:
    body_text = _text(row.get("body_text"))
    if len(body_text) >= MIN_BODY_BASIS_CHARS:
        return {
            "basis_level": "body",
            "basis_chars": len(body_text),
            "source_count": 1,
        }

    evidence_items = [
        item for item in _as_list(row.get("evidence_items")) if isinstance(item, dict)
    ]
    body_sources = [
        _text(item.get("body_text"))
        for item in evidence_items
        if len(_text(item.get("body_text"))) >= MIN_BODY_BASIS_CHARS
    ]
    if body_sources:
        return {
            "basis_level": "body",
            "basis_chars": max(len(body) for body in body_sources),
            "source_count": len(body_sources),
        }

    snippet_sources = [
        _text(item.get("summary"))
        for item in evidence_items
        if len(_text(item.get("summary"))) >= 40
    ]
    snippet_chars = len(" ".join(snippet_sources))
    if snippet_chars >= MIN_SNIPPET_BASIS_CHARS:
        return {
            "basis_level": "snippet",
            "basis_chars": snippet_chars,
            "source_count": len(snippet_sources),
        }

    title = _text(row.get("title"))
    if title:
        return {
            "basis_level": "title",
            "basis_chars": len(title),
            "source_count": 1,
        }
    return {
        "basis_level": "none",
        "basis_chars": 0,
        "source_count": 0,
    }


def _freshness_contract(row: dict[str, Any]) -> dict[str, Any]:
    event_type = _text(row.get("event_type"))
    metadata = _as_dict(row.get("event_metadata"))
    freshness = _as_dict(metadata.get("freshness"))
    if event_type == "earnings":
        return {
            "status": _text(freshness.get("status")) or "unknown",
            "event_date": _text(freshness.get("event_date"))
            or _text(row.get("effective_date")),
            "event_date_source": _text(freshness.get("event_date_source"))
            or "unknown",
            "event_age_days": freshness.get("event_age_days"),
            "stale": bool(freshness.get("stale")),
            "max_age_days": freshness.get("max_age_days"),
        }
    return {
        "status": "current_by_event_date",
        "event_date": _text(row.get("effective_date"))
        or _text(metadata.get("published_date")),
        "event_date_source": _text(metadata.get("event_date_source"))
        or "effective_date",
        "event_age_days": None,
        "stale": False,
        "max_age_days": None,
    }


def _price_reaction_contract(row: dict[str, Any]) -> dict[str, Any]:
    event_type = _text(row.get("event_type"))
    if event_type not in COMPANY_EVENT_TYPES:
        return {
            "status": "not_applicable",
            "required": False,
        }
    decision = _text(row.get("decision"))
    if decision and decision != "send_candidate":
        return {
            "status": "not_delivery_candidate",
            "required": False,
        }
    source_tier = _text(row.get("source_tier"))
    evidence_count = int(row.get("evidence_count") or 0)
    required = source_tier != "trusted" and evidence_count == 1
    price_reaction = _as_dict(row.get("price_reaction"))
    if not price_reaction:
        price_reaction = _as_dict(
            _as_dict(row.get("event_metadata")).get("price_reaction")
        )
    status = _text(price_reaction.get("status"))
    if not price_reaction or status in {"", "missing", "unavailable"}:
        return {
            "status": "missing" if required else "missing_optional",
            "required": required,
        }
    return {
        "status": status,
        "required": required,
        "price_as_of": _text(price_reaction.get("price_as_of")),
        "price_as_of_at": _text(price_reaction.get("price_as_of_at")),
        "pct_change": price_reaction.get("pct_change"),
        "direction": _text(price_reaction.get("direction")),
        "session": _text(price_reaction.get("session")),
        "stale": bool(price_reaction.get("stale")),
    }


def build_evidence_contract(row: dict[str, Any]) -> dict[str, Any]:
    event_type = _text(row.get("event_type"))
    source_tier = _text(row.get("source_tier"))
    evidence_count = int(row.get("evidence_count") or 0)
    risk_flags = [str(flag) for flag in _as_list(row.get("risk_flags")) if str(flag)]
    basis = select_evidence_basis(row)
    freshness = _freshness_contract(row)
    price_reaction = _price_reaction_contract(row)
    event_date = _text(freshness.get("event_date")) or _text(row.get("effective_date"))
    price_pct_change = _float_or_none(price_reaction.get("pct_change"))
    hard_event_lane = (
        event_type in COMPANY_EVENT_TYPES
        and _text(row.get("event_quality")) == "hard_event"
        and source_tier == "untrusted"
        and price_reaction.get("required")
        and price_reaction.get("status") == VALID_PRICE_REACTION_STATUS
        and price_pct_change is not None
        and abs(price_pct_change) >= MIN_SINGLE_SOURCE_HARD_EVENT_PRICE_MOVE_PCT
    )

    failures: list[str] = []
    warnings: list[str] = []

    if not event_date:
        failures.append("missing_event_date")
    if basis["basis_level"] == "none":
        failures.append("missing_evidence_basis")
    if not source_tier:
        failures.append("missing_source_tier")
    elif source_tier not in VALID_SOURCE_TIERS:
        failures.append("invalid_source_tier")
    elif source_tier == "low_quality":
        failures.append("low_quality_source")

    if evidence_count < 1:
        failures.append("missing_evidence_count")

    if freshness.get("stale"):
        failures.append("stale_event")
    unknown_earnings_event_date = (
        event_type == "earnings"
        and freshness.get("status") == "unknown_event_date"
    )
    if unknown_earnings_event_date:
        warnings.append("unknown_earnings_event_date")
        if evidence_count <= 1 and not _earnings_current_verified(row):
            failures.append("unknown_earnings_event_date_single_source")
    if (
        event_type == "earnings"
        and evidence_count == 1
        and source_tier != "trusted"
    ):
        if hard_event_lane:
            warnings.append("single_source_untrusted_earnings")
        else:
            failures.append("single_source_untrusted_earnings")
    if (
        event_type in COMPANY_EVENT_TYPES
        and evidence_count == 1
        and source_tier != "trusted"
    ):
        if hard_event_lane:
            warnings.append("single_source_untrusted_company_event")
        else:
            failures.append("single_source_untrusted_company_event")

    if basis["basis_level"] == "title":
        warnings.append("title_only_basis")
        if event_type in COMPANY_EVENT_TYPES:
            failures.append("title_only_company_event")
    if price_reaction.get("required") and (
        price_reaction.get("status") != VALID_PRICE_REACTION_STATUS
    ):
        failures.append("missing_price_reaction")
    if (
        price_reaction.get("required")
        and price_reaction.get("status") == VALID_PRICE_REACTION_STATUS
        and price_reaction.get("pct_change") is None
    ):
        failures.append("invalid_price_reaction")

    status = "fail" if failures else "warn" if warnings else "pass"
    return {
        "version": CONTRACT_VERSION,
        "status": status,
        "delivery_eligible": not failures,
        "failures": failures,
        "warnings": warnings,
        "event_date": event_date,
        "event_date_source": _text(freshness.get("event_date_source")),
        "freshness": freshness,
        "basis_level": basis["basis_level"],
        "basis_chars": basis["basis_chars"],
        "basis_source_count": basis["source_count"],
        "source_tier": source_tier,
        "evidence_count": evidence_count,
        "risk_flags": risk_flags,
        "price_reaction": price_reaction,
        "hard_event_lane": hard_event_lane,
        "hard_event_price_move_min_pct": MIN_SINGLE_SOURCE_HARD_EVENT_PRICE_MOVE_PCT,
    }


def attach_evidence_contracts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for row in rows:
        contract = build_evidence_contract(row)
        row["evidence_contract"] = contract
        row["contract_status"] = contract["status"]
        row["contract_failures"] = contract["failures"]
        row["contract_warnings"] = contract["warnings"]
    return rows
