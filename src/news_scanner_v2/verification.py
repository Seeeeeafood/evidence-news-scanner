from __future__ import annotations

import copy
import re
from typing import Any

from .dispatch import (
    _domain_from_url,
    _domain_matches,
    TRUSTED_DOMAIN_SUFFIXES,
    TRUSTED_PUBLISHER_TITLE_RE,
)
from .fetcher import FetchResult, fetch_brave_news
from .sources import BRAVE_NEWS_ENDPOINT, NewsSource


DEFAULT_MAX_VERIFICATION_BRAVE_REQUESTS_PER_RUN = 2
DEFAULT_VERIFICATION_TIMEOUT_SECONDS = 8
MIN_VERIFICATION_PRICE_MOVE_PCT = 2.0
REVIEW_EARNINGS_RESCUE_MIN_SCORE = 55.0
VERIFIED_RESCUE_MIN_SCORE = 86.0
DYNAMIC_EARNINGS_RESCUE_EXTRA_REQUESTS = 3
DYNAMIC_EARNINGS_RESCUE_MAX_REQUESTS = 5

VERIFICATION_TRUSTED_DOMAIN_SUFFIXES = TRUSTED_DOMAIN_SUFFIXES + (
    "businesswire.com",
    "globenewswire.com",
    "investing.com",
    "marketscreener.com",
    "prnewswire.com",
    "stocktitan.net",
)
COMPANY_DOMAIN_STOPWORDS = {
    "class",
    "company",
    "corp",
    "corporation",
    "group",
    "holdings",
    "inc",
    "market",
    "markets",
    "limited",
    "ltd",
    "plc",
    "sales",
    "share",
    "shares",
    "solutions",
    "stock",
    "stocks",
    "technologies",
    "technology",
}
VERIFICATION_COMPANY_ALIASES = {
    "CAG": ("conagra", "conagra brands"),
    "CTSH": ("cognizant", "cognizant technology solutions"),
    "F": ("ford", "ford motor"),
    "GE": ("ge aerospace", "general electric"),
    "GEHC": ("ge healthcare", "gehealthcare"),
    "NIO": ("nio",),
    "SE": ("sea limited",),
    "TTWO": ("take-two", "take two", "take-two interactive"),
    "UPWK": ("upwork",),
    "WMT": ("walmart",),
    "WDAY": ("workday",),
}
AMOUNT_RE = re.compile(
    r"\$\s*\d+(?:\.\d+)?\s*(?:billion|million|bn|b|m)\b|"
    r"\b\d+(?:\.\d+)?\s*(?:billion|million|bn|b|m)\b",
    re.I,
)
ACTION_TERMS = {
    "buyback": ("buyback", "repurchase", "share repurchase", "stock repurchase"),
    "ma": ("acquisition", "acquire", "merger", "buyout", "takeover"),
    "guidance_raise": ("guidance", "outlook", "raise", "raises", "boost"),
    "guidance_cut": ("guidance", "outlook", "cut", "cuts", "lower"),
    "guidance_update": (
        "guidance",
        "outlook",
        "forecast",
        "reaffirm",
        "maintain",
        "results",
        "earnings",
        "revenue",
        "eps",
        "profit",
        "sales",
    ),
    "earnings_report": ("earnings", "revenue", "eps", "sales"),
    "strategic_investment": ("investment", "invest", "stake"),
    "investment": ("investment", "invest", "stake"),
    "partnership": ("partnership", "partner"),
    "supply_deal": ("supply", "supplier", "agreement"),
}
EARNINGS_RESCUE_ACTIONS = {
    "earnings_report",
    "guidance_update",
    "guidance_raise",
    "guidance_cut",
}
EARNINGS_RESCUE_SIGNAL_RE = re.compile(
    r"\b(?:earnings|results?|guidance|outlook|forecast|revenue|sales|eps)\b"
    r".{0,120}\b(?:beat|beats|beating|top|tops|topped|raise|raises|raised|"
    r"cut|cuts|lower|lowers|sees|above|below|estimates?)\b|"
    r"\b(?:beat|beats|beating|top|tops|topped|raise|raises|raised|cut|cuts|"
    r"lower|lowers|sees|above|below)\b.{0,120}"
    r"\b(?:earnings|results?|guidance|outlook|forecast|revenue|sales|eps)\b|"
    r"\$\s*\d+(?:\.\d+)?\s*(?:billion|million|bn|b|m)\b",
    re.I,
)
LOW_SIGNAL_EARNINGS_RESCUE_RE = re.compile(
    r"\b(?:ahead of|before|set to report|will report|scheduled to report)\b"
    r".{0,80}\bearnings\b|"
    r"\b(?:buy .* before earnings|earnings preview|price target|analyst|rating|"
    r"maintains?|valuation|top pick|large-cap pick|stands out as|"
    r"stock(?:'s)? earnings selloff raises stakes|partners? with|stake sale|"
    r"sell \d|gift \d|trusts tied)\b",
    re.I,
)
EARNINGS_DIRECT_MATCH_RE = re.compile(
    r"\b(?:earnings|results?|eps|revenue|sales|quarter|q[1-4])\b",
    re.I,
)
GUIDANCE_DIRECT_MATCH_RE = re.compile(
    r"\b(?:guidance|outlook|forecast|sees|raises?|raised|cuts?|cut|"
    r"lowers?|lowered|reaffirms?|maintains?)\b",
    re.I,
)
SEGMENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")



def _text(value: Any) -> str:
    return str(value or "").strip()


def _price_move_ok(price_reaction: dict[str, Any]) -> bool:
    if price_reaction.get("status") != "ok":
        return False
    try:
        pct_change = abs(float(price_reaction.get("pct_change")))
    except (TypeError, ValueError):
        return False
    return pct_change >= MIN_VERIFICATION_PRICE_MOVE_PCT


def _event_payload(record: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return {}, {}
    event = payload.get("event")
    if not isinstance(event, dict):
        event = {}
    return payload, event


def _record_score(record: dict[str, Any], payload: dict[str, Any]) -> float:
    for value in (record.get("score"), payload.get("score")):
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _review_earnings_rescue_candidate(record: dict[str, Any]) -> bool:
    if record.get("decision") != "review":
        return False
    payload, event = _event_payload(record)
    if not payload:
        return False
    if _text(event.get("event_type")) != "earnings":
        return False
    if _text(event.get("action")) not in EARNINGS_RESCUE_ACTIONS:
        return False
    if _record_score(record, payload) < REVIEW_EARNINGS_RESCUE_MIN_SCORE:
        return False
    if _text(event.get("subject")).lower() in {"", "market", "macro", "news"}:
        return False
    source_tier = _text(payload.get("source_tier"))
    if source_tier not in {"untrusted", "low_quality"}:
        return False
    if int(payload.get("evidence_count") or 0) < 1:
        return False
    title = _text(event.get("title"))
    subject = _text(event.get("subject"))
    if LOW_SIGNAL_EARNINGS_RESCUE_RE.search(title):
        return False
    if not _subject_seen_in_match(subject, title):
        return False
    return bool(EARNINGS_RESCUE_SIGNAL_RE.search(title))


def _effective_verification_max_requests(
    *,
    requested_max: int,
    earnings_rescue_candidates: int,
) -> tuple[int, int]:
    base_max = max(0, int(requested_max))
    if (
        base_max < DEFAULT_MAX_VERIFICATION_BRAVE_REQUESTS_PER_RUN
        or earnings_rescue_candidates <= 0
    ):
        return base_max, 0
    expanded = min(
        DYNAMIC_EARNINGS_RESCUE_MAX_REQUESTS,
        base_max
        + min(DYNAMIC_EARNINGS_RESCUE_EXTRA_REQUESTS, earnings_rescue_candidates),
    )
    return max(base_max, expanded), max(0, expanded - base_max)


def _verification_candidate(record: dict[str, Any]) -> bool:
    payload, _ = _event_payload(record)
    if not payload:
        return False
    if _review_earnings_rescue_candidate(record):
        return True
    if record.get("decision") != "send_candidate":
        return False
    if payload.get("event_quality") != "hard_event":
        return False
    if payload.get("source_tier") != "untrusted":
        return False
    if int(payload.get("evidence_count") or 0) != 1:
        return False
    return _price_move_ok(payload.get("price_reaction") or {})


def _verification_priority(record: dict[str, Any]) -> tuple[int, float, str]:
    payload, event = _event_payload(record)
    rescue_rank = 0 if _review_earnings_rescue_candidate(record) else 1
    return (
        rescue_rank,
        -_record_score(record, payload),
        _text(event.get("subject")).upper(),
    )


def _subject_aliases(subject: str) -> list[str]:
    subject_upper = subject.upper()
    aliases: list[str] = []
    from .extractor import COMPANY_ALIASES

    for alias, ticker in COMPANY_ALIASES.items():
        if ticker.upper() == subject_upper and alias not in aliases:
            aliases.append(alias)
    for alias in VERIFICATION_COMPANY_ALIASES.get(subject_upper, ()):
        if alias not in aliases:
            aliases.append(alias)
    return aliases


def _alias_seen(alias: str, text: str) -> bool:
    normalized_alias = re.sub(r"[^a-z0-9]+", " ", alias.lower()).strip()
    if not normalized_alias:
        return False
    normalized_text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return bool(re.search(rf"\b{re.escape(normalized_alias)}\b", normalized_text))


def _ticker_context_seen(subject: str, text: str) -> bool:
    subject_upper = subject.upper()
    if not subject_upper:
        return False
    if len(subject_upper) <= 2:
        patterns = (
            rf"\((?:NYSE|NASDAQ|AMEX|NAS)?[:\s]*{re.escape(subject_upper)}(?::[A-Z]+)?\)",
            rf"\b(?:NYSE|NASDAQ|AMEX|NAS):{re.escape(subject_upper)}\b",
            rf"\b{re.escape(subject_upper)}:(?:NYSE|NASDAQ|AMEX|NAS)\b",
        )
    else:
        patterns = (
            rf"(?<![A-Z0-9]){re.escape(subject_upper)}(?![A-Z0-9])",
        )
    upper_text = text.upper()
    return any(re.search(pattern, upper_text) for pattern in patterns)


def _subject_seen_in_match(subject: str, text: str) -> bool:
    if not subject:
        return True
    if _ticker_context_seen(subject, text):
        return True
    return any(_alias_seen(alias, text) for alias in _subject_aliases(subject))


def _company_tokens(title: str, subject: str) -> list[str]:
    prefix = title
    marker = f"({subject.upper()})"
    if marker in title:
        prefix = title.split(marker, 1)[0]
    else:
        from .extractor import COMPANY_ALIASES

        aliases = [
            alias
            for alias, ticker in COMPANY_ALIASES.items()
            if ticker.lower() == subject.lower()
        ]
        if aliases:
            prefix = " ".join(aliases)
        else:
            return []
    tokens = []
    for token in re.findall(r"[A-Za-z][A-Za-z&'-]{2,}", prefix.lower()):
        cleaned = token.strip("-'&")
        if len(cleaned) < 4 or cleaned in COMPANY_DOMAIN_STOPWORDS:
            continue
        if cleaned not in tokens:
            tokens.append(cleaned)
    return tokens[:4]


def _issuer_domain_match(domain: str, *, title: str, subject: str) -> bool:
    if not domain:
        return False
    return any(token in domain for token in _company_tokens(title, subject))


def _normalized_amounts(title: str) -> list[str]:
    amounts = []
    for match in AMOUNT_RE.findall(title):
        text = re.sub(r"\s+", " ", match.lower().replace("$", "")).strip()
        text = text.replace("bn", "billion").replace(" b", " billion")
        text = text.replace(" m", " million")
        if text and text not in amounts:
            amounts.append(text)
    return amounts


def _amount_seen(amount: str, text: str) -> bool:
    compact_amount = amount.replace(" billion", "b").replace(" million", "m")
    compact_text = text.replace(" ", "")
    return amount in text or compact_amount.replace(" ", "") in compact_text


def _direct_earnings_match(action: str, text: str) -> bool:
    if LOW_SIGNAL_EARNINGS_RESCUE_RE.search(text):
        return False
    if action == "guidance_update":
        return bool(
            GUIDANCE_DIRECT_MATCH_RE.search(text) or EARNINGS_DIRECT_MATCH_RE.search(text)
        )
    if action in {"guidance_raise", "guidance_cut"}:
        return bool(GUIDANCE_DIRECT_MATCH_RE.search(text))
    if action == "earnings_report":
        return bool(EARNINGS_DIRECT_MATCH_RE.search(text))
    return True


def _verification_text_segments(item_title: str, item_summary: str) -> list[str]:
    segments = [item_title]
    segments.extend(SEGMENT_SPLIT_RE.split(item_summary))
    return [segment.strip() for segment in segments if segment.strip()]


def _direct_earnings_context_match(
    *,
    action: str,
    subject: str,
    item_title: str,
    item_summary: str,
    amounts: list[str],
) -> bool:
    for segment in _verification_text_segments(item_title, item_summary):
        segment_text = segment.lower()
        if not _direct_earnings_match(action, segment_text):
            continue
        if _subject_seen_in_match(subject, segment):
            return True
        if amounts and any(_amount_seen(amount, segment_text) for amount in amounts):
            return True
    return False


def _action_match(action: str, text: str) -> bool:
    terms = ACTION_TERMS.get(action, ())
    if not terms:
        return True
    return any(term in text for term in terms)


def _verification_match(
    *,
    record: dict[str, Any],
    item: Any,
) -> dict[str, Any] | None:
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    title = _text(event.get("title"))
    subject = _text(event.get("subject")).lower()
    action = _text(event.get("action"))
    item_title = _text(getattr(item, "title", ""))
    item_summary = _text(getattr(item, "summary", ""))
    item_published_at = _text(getattr(item, "published_at", ""))
    url = _text(getattr(item, "url", ""))
    text = f"{item_title} {item_summary}".lower()
    domain = _domain_from_url(url)
    trusted_domain = _domain_matches(domain, VERIFICATION_TRUSTED_DOMAIN_SUFFIXES)
    issuer_domain = _issuer_domain_match(domain, title=title, subject=subject)
    trusted_byline = bool(TRUSTED_PUBLISHER_TITLE_RE.search(item_title))
    if not (trusted_domain or issuer_domain):
        return None
    if not _subject_seen_in_match(subject, f"{item_title} {item_summary}"):
        return None
    amounts = _normalized_amounts(title)
    is_earnings_rescue = _review_earnings_rescue_candidate(record)
    if is_earnings_rescue and LOW_SIGNAL_EARNINGS_RESCUE_RE.search(item_title):
        return None
    if is_earnings_rescue and not _direct_earnings_context_match(
        action=action,
        subject=subject,
        item_title=item_title,
        item_summary=item_summary,
        amounts=amounts,
    ):
        return None
    if not _action_match(action, text):
        return None
    if amounts and not any(_amount_seen(amount, text) for amount in amounts):
        return None
    return {
        "title": item_title,
        "url": url,
        "domain": domain,
        "summary": item_summary,
        "published_at": item_published_at,
        "source": _text(getattr(item, "source", "")),
        "provider": _text(getattr(item, "provider", "")),
        "category": _text(getattr(item, "category", "")),
        "trusted_domain": trusted_domain,
        "issuer_domain": issuer_domain,
        "trusted_byline": trusted_byline,
    }


def _verification_query(record: dict[str, Any]) -> str:
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    subject = _text(event.get("subject")).upper()
    action = _text(event.get("action")).replace("_", " ")
    title = _text(event.get("title"))
    query = f"{subject} {action} {title}".strip()
    return query[:260]


def _source_for_query(index: int, query: str) -> NewsSource:
    return NewsSource(
        name=f"brave-news-verification-{index}",
        category="VERIFY",
        url=BRAVE_NEWS_ENDPOINT,
        kind="brave_news",
        provider="brave",
        query=query,
        count=10,
        timeout_seconds=DEFAULT_VERIFICATION_TIMEOUT_SECONDS,
    )


def _mark_unverified(record: dict[str, Any], *, status: str, query: str) -> None:
    payload = record.setdefault("payload", {})
    if not isinstance(payload, dict):
        return
    payload["verification"] = {
        "status": status,
        "query": query,
        "provider": "brave",
    }
    payload["verification_status"] = status


def _promote_verified(record: dict[str, Any], *, match: dict[str, Any], query: str) -> None:
    payload = record.setdefault("payload", {})
    if not isinstance(payload, dict):
        return
    was_review_earnings_rescue = _review_earnings_rescue_candidate(record)
    trusted_domains = list(payload.get("trusted_domains") or [])
    domain = _text(match.get("domain"))
    if domain and domain not in trusted_domains:
        trusted_domains.append(domain)
    risk_flags = [
        str(flag)
        for flag in payload.get("risk_flags") or []
        if str(flag) not in {"single_source_untrusted", "low_quality_source"}
    ]
    if "verified_single_source" not in risk_flags:
        risk_flags.append("verified_single_source")
    payload["source_tier"] = "trusted"
    payload["trusted_domains"] = trusted_domains
    payload["trusted_source_count"] = max(int(payload.get("trusted_source_count") or 0), 1)
    payload["grade"] = "A"
    if was_review_earnings_rescue:
        record["decision"] = "send_candidate"
        score = max(_record_score(record, payload), VERIFIED_RESCUE_MIN_SCORE)
        record["score"] = score
        payload["score"] = score
        payload["event_quality"] = "hard_event"
        payload["hard_event_reason"] = "verified_earnings_rescue"
        payload["send_worthy_reason"] = "send_candidate:verified_earnings_rescue"
    payload["risk_flags"] = risk_flags
    payload["verification"] = {
        "status": "verified",
        "provider": "brave",
        "query": query,
        "match": match,
    }
    payload["verification_status"] = "verified"
    record["reason"] = f"{record.get('reason', '')};verification:trusted"
    if was_review_earnings_rescue:
        record["reason"] = f"{record['reason']};verified_earnings_rescue"


def verify_hard_event_records(
    records: list[dict[str, Any]],
    *,
    enabled: bool,
    api_key: str | None,
    max_requests: int = DEFAULT_MAX_VERIFICATION_BRAVE_REQUESTS_PER_RUN,
    timeout_seconds: float = DEFAULT_VERIFICATION_TIMEOUT_SECONDS,
    fetcher=fetch_brave_news,
) -> tuple[list[dict[str, Any]], dict[str, Any], tuple[FetchResult, ...]]:
    updated = [copy.deepcopy(record) for record in records]
    base_max_requests = max(0, int(max_requests))
    stats = {
        "verification_enabled": enabled,
        "verification_configured": bool(api_key),
        "verification_candidates": 0,
        "verification_earnings_rescue_candidates": 0,
        "verification_attempted": 0,
        "verification_verified": 0,
        "verification_unverified": 0,
        "verification_errors": 0,
        "verification_skipped_limit": 0,
        "verification_brave_base_max_requests": base_max_requests,
        "verification_brave_max_requests": base_max_requests,
        "verification_dynamic_budget_added": 0,
        "verification_dynamic_budget_cap": DYNAMIC_EARNINGS_RESCUE_MAX_REQUESTS,
        "verification_brave_requests_used": 0,
    }
    fetch_results: list[FetchResult] = []
    if not enabled or not api_key or base_max_requests <= 0:
        return updated, stats, tuple(fetch_results)

    candidates = sorted(
        [record for record in updated if _verification_candidate(record)],
        key=_verification_priority,
    )
    earnings_rescue_candidates = sum(
        1 for record in candidates if _review_earnings_rescue_candidate(record)
    )
    effective_max_requests, dynamic_added = _effective_verification_max_requests(
        requested_max=base_max_requests,
        earnings_rescue_candidates=earnings_rescue_candidates,
    )
    stats["verification_candidates"] = len(candidates)
    stats["verification_earnings_rescue_candidates"] = earnings_rescue_candidates
    stats["verification_brave_max_requests"] = effective_max_requests
    stats["verification_dynamic_budget_added"] = dynamic_added
    for index, record in enumerate(candidates, start=1):
        if stats["verification_attempted"] >= effective_max_requests:
            stats["verification_skipped_limit"] += 1
            continue
        query = _verification_query(record)
        source = _source_for_query(index, query)
        if timeout_seconds != DEFAULT_VERIFICATION_TIMEOUT_SECONDS:
            source = NewsSource(
                name=source.name,
                category=source.category,
                url=source.url,
                kind=source.kind,
                provider=source.provider,
                query=source.query,
                freshness=source.freshness,
                count=source.count,
                country=source.country,
                search_lang=source.search_lang,
                timeout_seconds=int(timeout_seconds),
            )
        result = fetcher(source, api_key=api_key)
        fetch_results.append(result)
        stats["verification_attempted"] += 1
        if not result.status.startswith("skipped"):
            stats["verification_brave_requests_used"] += 1
        if result.status != "ok":
            stats["verification_errors"] += 1
            _mark_unverified(record, status=result.status, query=query)
            continue
        match = None
        for item in result.items:
            match = _verification_match(record=record, item=item)
            if match is not None:
                break
        if match is None:
            stats["verification_unverified"] += 1
            _mark_unverified(record, status="unverified", query=query)
            continue
        stats["verification_verified"] += 1
        _promote_verified(record, match=match, query=query)
    return updated, stats, tuple(fetch_results)
