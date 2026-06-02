from __future__ import annotations

import re
from typing import Any

from .extractor import COMPANY_ALIASES, KNOWN_TICKERS


CONTRACT_VERSION = "earnings_fact_contract_v1"
MAX_BASIS_CHARS = 260
MAX_SUMMARY_CHARS = 180

EARNINGS_ACTIONS = {
    "earnings_report",
    "earnings_result",
    "earnings_related",
    "guidance_raise",
    "guidance_cut",
    "guidance_update",
}

FACT_ORDER = {
    "eps": 0,
    "revenue": 1,
    "ai_revenue": 2,
    "guidance_revenue": 3,
    "ai_guidance_revenue": 4,
    "guidance_eps": 5,
    "guidance": 6,
    "buyback": 7,
    "stock_reaction": 8,
}

UNIT_PATTERN = r"(?:trillion|billion|million|thousand|T|B|M|K|bn|mn)(?![A-Za-z])"
AMOUNT_PATTERN = (
    r"(?:\$\s*\d+(?:,\d{3})*(?:\.\d+)?"
    rf"\s*(?:{UNIT_PATTERN})?|"
    r"\d+(?:,\d{3})*(?:\.\d+)?"
    rf"\s*{UNIT_PATTERN})"
)
RANGE_PATTERN = (
    AMOUNT_PATTERN
    + r"\s*(?:-|–|—|~|\bto\b|\band\b)\s*"
    + AMOUNT_PATTERN
)
AMOUNT_RE = re.compile(RANGE_PATTERN + "|" + AMOUNT_PATTERN, re.I)
PCT_RE = re.compile(r"[+-]?\s*\d+(?:\.\d+)?\s*%", re.I)

EPS_TERM_RE = re.compile(
    r"\b(?:adjusted\s+|non-gaap\s+)?(?:eps|earnings per share|per share)\b",
    re.I,
)
REVENUE_TERM_RE = re.compile(r"\b(?:revenue|sales)\b", re.I)
GUIDANCE_TERM_RE = re.compile(
    r"\b(?:guidance|outlook|guide|forecast|sees|expect|expects|"
    r"project|projects|projecting|projected|anticipate|anticipates|"
    r"anticipated)\b",
    re.I,
)
BUYBACK_TERM_RE = re.compile(
    r"\b(?:buyback|repurchase|authorization|share repurchase)\b",
    re.I,
)
GUIDANCE_CONTEXT_RE = re.compile(
    r"\b(?:guidance|outlook|guide|forecast|sees|expect|expects|"
    r"project|projects|projecting|projected|anticipate|anticipates|"
    r"anticipated)\b",
    re.I,
)
GUIDANCE_TO_ACTUALS_BRIDGE_RE = re.compile(
    r"\b(?:delivered|reported|posted|announced|released|actual|"
    r"beat|beats|exceed(?:ed|ing)?|earnings per share|eps|"
    r"revenue reached|top-line results?)\b",
    re.I,
)
UNRELATED_AMOUNT_BETWEEN_RE = re.compile(
    r"\b(?:arr|capex|cash flow|ebitda|income|margin|rpo|billings|"
    r"tax rate|shares?)\b",
    re.I,
)
EPS_SCALE_UNIT_RE = re.compile(
    r"(?:\d\s*(?:T|B|M|K|bn|mn)\b|\b(?:trillion|billion|million|thousand)\b)",
    re.I,
)
DEAL_AMOUNT_CONTEXT_RE = re.compile(
    r"\b(?:deal|contract|commitment|spending|partnership|infrastructure|"
    r"capacity|hyperscaler)\b",
    re.I,
)
SALES_NON_REVENUE_CONTEXT_RE = re.compile(
    r"\bsales\s+force\b|\bcross-sell\b|\bmarketplace\b|\blifetime\s+sales\b",
    re.I,
)
GROWTH_PCT_CONTEXT_RE = re.compile(
    r"\b(?:revenue|sales|product revenue)\b.{0,90}[+-]?\s*\d+(?:\.\d+)?\s*%|"
    r"[+-]?\s*\d+(?:\.\d+)?\s*%.{0,90}\b(?:year-over-year|y/y|yoy|growth)\b",
    re.I,
)
PER_SHARE_CONTEXT_RE = re.compile(r"\b(?:per share|earnings per share)\b", re.I)
PRIOR_PERIOD_CONTEXT_RE = re.compile(
    r"\b(?:year earlier|a year earlier|year ago|prior year|same quarter last year)\b",
    re.I,
)
AI_SEGMENT_REVENUE_CONTEXT_RE = re.compile(
    r"\b(?:ai|ai server|server|networking)\s+revenue\b",
    re.I,
)
AI_REVENUE_TERM_RE = re.compile(r"\b(?:(ai\s+server|ai)\s+revenue)\b", re.I)
STRICT_FACT_SOURCE_TITLE_RE = re.compile(
    r"\blive updates?\b|\bstock market today\b|\bmarket open\b",
    re.I,
)
CLOUD_COUNTERPARTY_CONTEXT_RE = re.compile(
    r"\b(?:aws|amazon|amazon\s+web\s+services|amazon\s+cloud|"
    r"azure|microsoft\s+azure|google\s+cloud|gcp|oracle\s+cloud|oci)\b",
    re.I,
)
CLOUD_PROVIDER_SUBJECTS = {"AMZN", "MSFT", "GOOGL", "GOOG", "ORCL"}
SUBJECT_CARRYOVER_RE = re.compile(
    r"\b(?:the company|its|it|shares?|stock|outlook|results?|"
    r"product revenue|revenue|sales|eps|guidance|forecast)\b",
    re.I,
)
SEGMENT_BRIDGE_RE = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b")
SENTENCE_BOUNDARY_RE = re.compile(r"\.(?!\d)|;")
NEW_RANGE_RE = re.compile(
    r"\b(?:new|revised|updated)\s+(?:range|outlook|guidance)\s+(?:of|to)\b|"
    r"\bnow\s+(?:expects|sees|projects)\b",
    re.I,
)
UP_REACTION_RE = re.compile(
    r"\b(?:shares?|stock)\b.{0,80}\b(?:up|rise|rises|rose|jump|jumps|"
    r"jumped|surge|surges|surged|gain|gains|gained|climb|climbs|climbed)\b"
    r".{0,80}",
    re.I,
)
DOWN_REACTION_RE = re.compile(
    r"\b(?:shares?|stock)\b.{0,80}\b(?:down|fall|falls|fell|drop|drops|"
    r"dropped|slide|slides|slid|plunge|plunges|plunged)\b.{0,80}",
    re.I,
)


def _clean(value: object) -> str:
    text = " ".join(str(value or "").split())
    text = re.sub(r"(?i)\b(billion|million|trillion|thousand)illion\b", r"\1", text)
    return text


def _actions(row: dict[str, Any]) -> set[str]:
    raw = row.get("merged_actions")
    actions: set[str] = set()
    if isinstance(raw, list):
        actions.update(_clean(value) for value in raw)
    actions.update(part.strip() for part in re.split(r"[+/,]", _clean(row.get("action"))))
    actions.discard("")
    return actions


def _is_earnings_row(row: dict[str, Any]) -> bool:
    if _clean(row.get("event_type")) == "earnings":
        return True
    return bool(_actions(row) & EARNINGS_ACTIONS)


def _subject_aliases(row: dict[str, Any]) -> list[str]:
    subject = _clean(row.get("subject")).upper()
    if not subject or not subject.isalpha() or len(subject) > 8:
        return []
    aliases = [
        alias
        for alias, ticker in COMPANY_ALIASES.items()
        if ticker.upper() == subject
    ]
    if len(subject) >= 2:
        aliases.append(subject)
    return sorted(set(aliases), key=lambda value: (-len(value), value))


def _subject(row: dict[str, Any]) -> str:
    return _clean(row.get("subject")).upper()


def _ownership_related_entities(row: dict[str, Any]) -> set[str]:
    metadata = row.get("event_metadata")
    if not isinstance(metadata, dict):
        return set()
    ownership = metadata.get("ownership")
    if not isinstance(ownership, dict):
        return set()
    return {
        _clean(value).upper()
        for value in ownership.get("related_entities") or []
        if _clean(value)
    }


def _record_requires_subject_alignment(
    row: dict[str, Any],
    record: dict[str, Any],
    aliases: list[str],
) -> bool:
    if not aliases:
        return False
    source_title = _clean(record.get("source_title"))
    text = _clean(record.get("text"))
    subject = _subject(row)
    if STRICT_FACT_SOURCE_TITLE_RE.search(source_title):
        return True
    if _ownership_related_entities(row):
        return True
    if subject not in CLOUD_PROVIDER_SUBJECTS and CLOUD_COUNTERPARTY_CONTEXT_RE.search(
        f"{source_title} {text}"
    ):
        return True
    return False


def _record_mentions_subject(record: dict[str, Any], aliases: list[str]) -> bool:
    if not aliases:
        return True
    text = " ".join(
        _clean(record.get(key))
        for key in ("source_title", "source", "text")
        if _clean(record.get(key))
    )
    return _text_mentions_alias(text, aliases)


def _text_mentions_alias(text: str, aliases: list[str]) -> bool:
    for alias in aliases:
        flags = 0 if alias.isupper() else re.I
        pattern = re.compile(
            rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])",
            flags,
        )
        if pattern.search(text):
            return True
    return False


def _text_mentions_other_subject(text: str, aliases: list[str], subject: str) -> bool:
    subject_aliases = {alias.lower() for alias in aliases}
    normalized = _clean(text)
    for alias, ticker in COMPANY_ALIASES.items():
        if ticker.upper() == subject or alias.lower() in subject_aliases:
            continue
        if _text_mentions_alias(normalized, [alias]):
            return True
    for match in re.finditer(r"\b[A-Z][A-Z0-9.-]{1,5}\b", text):
        ticker = match.group(0).upper().replace(".", "-")
        if ticker in KNOWN_TICKERS and ticker != subject:
            return True
    if subject not in CLOUD_PROVIDER_SUBJECTS and CLOUD_COUNTERPARTY_CONTEXT_RE.search(text):
        return True
    return False


def _previous_sentence(text: str, sentence_start: int) -> str:
    if sentence_start <= 0:
        return ""
    before = text[: max(0, sentence_start - 1)]
    boundaries = [match.start() for match in SENTENCE_BOUNDARY_RE.finditer(before)]
    left = boundaries[-1] + 1 if boundaries else 0
    return before[left:]


def _fact_context_mentions_subject(
    text: str,
    aliases: list[str],
    subject: str,
    source_title: str,
    start: int,
    end: int,
) -> bool:
    if not aliases:
        return True
    sentence_start, sentence_end = _sentence_bounds(text, start, end)
    current = text[sentence_start:sentence_end]
    if _text_mentions_alias(current, aliases):
        return True
    if _text_mentions_other_subject(current, aliases, subject):
        return False
    previous = _previous_sentence(text, sentence_start)
    if _clean(previous) == _clean(source_title):
        return False
    return _text_mentions_alias(previous, aliases) and bool(
        SUBJECT_CARRYOVER_RE.search(current)
    )


def _fact_mentions_subject(fact: dict[str, Any], aliases: list[str]) -> bool:
    if not aliases:
        return True
    text = " ".join(
        _clean(fact.get(key))
        for key in ("source_title", "basis_text")
        if _clean(fact.get(key))
    )
    return _text_mentions_alias(text, aliases)


def _normalize_single_amount(value: str) -> str:
    text = _clean(value).replace(",", "")
    text = re.sub(r"\$\s+", "$", text)
    text = re.sub(r"\s+", "", text)
    replacements = {
        "trillion": "T",
        "billion": "B",
        "million": "M",
        "thousand": "K",
        "bn": "B",
        "mn": "M",
    }
    for word, suffix in replacements.items():
        text = re.sub(rf"(?i){word}$", suffix, text)
    return text


def _normalize_amount(value: str) -> str:
    text = _clean(value).replace("–", "-").replace("—", "-").replace("~", "-")
    parts = re.split(r"\s*(?:-|\bto\b|\band\b)\s*", text, maxsplit=1, flags=re.I)
    if len(parts) == 2:
        left = _normalize_single_amount(parts[0])
        right = _normalize_single_amount(parts[1])
        unit_match = re.search(r"([TBMK])$", right)
        if unit_match and not re.search(r"[TBMK]$", left):
            left += unit_match.group(1)
        return f"{left}-{right}"
    return _normalize_single_amount(text)


def _looks_like_range(value: str) -> bool:
    return bool(re.search(r"(?:-|–|—|~|\bto\b|\band\b)", _clean(value), re.I))


def _has_segment_bridge(text: str) -> bool:
    return bool(SEGMENT_BRIDGE_RE.search(_clean(text)))


def _basis_window(text: str, start: int, end: int) -> str:
    window_start = max(0, start - 100)
    window_end = min(len(text), end + 160)
    return _clean(text[window_start:window_end])[:MAX_BASIS_CHARS]


def _sentence_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    boundaries = [
        match.start()
        for match in SENTENCE_BOUNDARY_RE.finditer(text)
    ]
    left_candidates = [index for index in boundaries if index < start]
    right_candidates = [index for index in boundaries if index >= end]
    left = max(left_candidates) if left_candidates else -1
    right = min(right_candidates) if right_candidates else len(text)
    return left + 1, right


def _source_records(row: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    row_text = ". ".join(
        part
        for part in (_clean(row.get("title")), _clean(row.get("body_text")))
        if part
    )
    if row_text:
        records.append(
            {
                "evidence_id": "event",
                "source": "",
                "provider": "",
                "source_url": _clean(row.get("url")),
                "source_title": _clean(row.get("title")),
                "text": row_text,
            }
        )
    evidence_items = row.get("evidence_items")
    if isinstance(evidence_items, list):
        for index, item in enumerate(evidence_items):
            if not isinstance(item, dict):
                continue
            text = ". ".join(
                part
                for part in (
                    _clean(item.get("title")),
                    _clean(item.get("summary")),
                    _clean(item.get("body_text")),
                )
                if part
            )
            if not text:
                continue
            records.append(
                {
                    "evidence_id": _clean(item.get("candidate_id")) or f"evidence-{index}",
                    "source": _clean(item.get("source")),
                    "provider": _clean(item.get("provider")),
                    "source_url": _clean(item.get("url")),
                    "source_title": _clean(item.get("title")),
                    "text": text,
                }
            )
    return records


def _fact(
    *,
    kind: str,
    label: str,
    value: str,
    record: dict[str, Any],
    basis_text: str,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "label": label,
        "value": value,
        "basis_text": basis_text,
        "source_url": record.get("source_url") or "",
        "source_title": record.get("source_title") or "",
        "source": record.get("source") or "",
        "provider": record.get("provider") or "",
        "evidence_id": record.get("evidence_id") or "",
        "confidence": "verified",
    }


def _first_amount_after(text: str, start: int, *, max_chars: int = 170) -> re.Match[str] | None:
    window = text[start : min(len(text), start + max_chars)]
    return AMOUNT_RE.search(window)


def _new_range_amount_after(
    text: str,
    start: int,
    *,
    max_chars: int = 180,
) -> tuple[re.Match[str], int, int] | None:
    window = text[start : min(len(text), start + max_chars)]
    marker = NEW_RANGE_RE.search(window)
    if marker is None:
        return None
    amount = AMOUNT_RE.search(window[marker.end() :])
    if amount is None:
        return None
    amount_start = start + marker.end() + amount.start()
    amount_end = start + marker.end() + amount.end()
    return amount, amount_start, amount_end


def _add_term_amount_facts(
    facts: list[dict[str, Any]],
    *,
    record: dict[str, Any],
    aliases: list[str],
    subject: str,
    require_subject_alignment: bool,
    kind: str,
    label: str,
    term_re: re.Pattern[str],
    skip_guidance_context: bool = False,
) -> None:
    text = str(record.get("text") or "")
    for term in term_re.finditer(text):
        sentence_start, sentence_end = _sentence_bounds(text, term.start(), term.end())
        candidates: list[tuple[int, int, re.Match[str], int, int]] = []
        per_share_term = kind == "eps" and "per share" in term.group(0).lower()
        after_priority = 1 if per_share_term else 0
        before_priority = 0 if per_share_term else 1
        after_window = text[term.end() : min(sentence_end, term.end() + 170)]
        for amount in AMOUNT_RE.finditer(after_window):
            amount_start = term.end() + amount.start()
            amount_end = term.end() + amount.end()
            candidates.append(
                (
                    after_priority,
                    amount_start - term.end(),
                    amount,
                    amount_start,
                    amount_end,
                )
            )
        if kind in {"eps", "revenue"}:
            before_start = max(sentence_start, term.start() - 90)
            before_amounts = list(AMOUNT_RE.finditer(text[before_start : term.start()]))
            if before_amounts:
                amount = before_amounts[-1]
                amount_start = before_start + amount.start()
                amount_end = before_start + amount.end()
                candidates.append(
                    (
                        before_priority,
                        term.start() - amount_end,
                        amount,
                        amount_start,
                        amount_end,
                    )
                )
        if not candidates:
            continue
        selected: tuple[re.Match[str], int, int] | None = None
        for _, _, amount, amount_start, amount_end in sorted(
            candidates,
            key=lambda item: (item[0], item[1]),
        ):
            if kind in {"eps", "revenue"} and _looks_like_range(amount.group(0)):
                continue
            if kind == "eps" and EPS_SCALE_UNIT_RE.search(amount.group(0)):
                continue
            between_start = min(term.end(), amount_end)
            between_end = max(term.start(), amount_start)
            between = text[between_start:between_end]
            if kind == "revenue" and UNRELATED_AMOUNT_BETWEEN_RE.search(between):
                continue
            amount_context = text[amount_start : min(sentence_end, amount_end + 40)]
            if kind == "eps" and PRIOR_PERIOD_CONTEXT_RE.search(amount_context):
                continue
            if kind == "revenue" and PER_SHARE_CONTEXT_RE.search(amount_context):
                continue
            revenue_context = text[
                max(sentence_start, term.start() - 80) : min(sentence_end, amount_end + 80)
            ]
            if kind == "revenue" and AI_SEGMENT_REVENUE_CONTEXT_RE.search(
                revenue_context
            ):
                continue
            if kind == "revenue" and DEAL_AMOUNT_CONTEXT_RE.search(revenue_context):
                continue
            if (
                kind == "revenue"
                and term.group(0).lower() == "sales"
                and SALES_NON_REVENUE_CONTEXT_RE.search(revenue_context)
            ):
                continue
            if require_subject_alignment and not _fact_context_mentions_subject(
                text,
                aliases,
                subject,
                _clean(record.get("source_title")),
                min(term.start(), amount_start),
                max(term.end(), amount_end),
            ):
                continue
            context_start = max(sentence_start, min(term.start(), amount_start) - 100)
            context_end = min(len(text), max(term.end(), amount_end))
            context = text[context_start:context_end]
            if skip_guidance_context and GUIDANCE_CONTEXT_RE.search(context):
                continue
            selected = (amount, amount_start, amount_end)
            break
        if selected is None:
            continue
        amount, amount_start, amount_end = selected
        facts.append(
            _fact(
                kind=kind,
                label=label,
                value=_normalize_amount(amount.group(0)),
                record=record,
                basis_text=_basis_window(
                    text,
                    term.start(),
                    amount_end,
                ),
            )
        )
        return


def _add_guidance_facts(
    facts: list[dict[str, Any]],
    *,
    record: dict[str, Any],
    aliases: list[str],
    subject: str,
    require_subject_alignment: bool,
) -> None:
    text = str(record.get("text") or "")
    for guidance in GUIDANCE_TERM_RE.finditer(text):
        sentence_start, sentence_end = _sentence_bounds(
            text,
            guidance.start(),
            guidance.end(),
        )
        window_start = max(sentence_start, guidance.start() - 100)
        window_end = min(sentence_end, guidance.end() + 260)
        window = text[window_start:window_end]
        found = False
        for kind, label, term_re in (
            ("guidance_revenue", "매출 가이던스", REVENUE_TERM_RE),
            ("guidance_eps", "EPS 가이던스", EPS_TERM_RE),
        ):
            guidance_offset = guidance.start() - window_start
            guidance_end_offset = guidance.end() - window_start
            terms = sorted(
                term_re.finditer(window),
                key=lambda match: abs(
                    match.start() - guidance_offset
                ),
            )
            amount_match = None
            amount_start = 0
            amount_end = 0
            term = None
            for candidate_term in terms:
                if kind == "guidance_revenue":
                    if candidate_term.start() >= guidance_offset:
                        bridge = window[guidance_end_offset : candidate_term.start()]
                    else:
                        bridge = window[candidate_term.end() : guidance_offset]
                    if _has_segment_bridge(bridge):
                        continue
                candidates = []
                after_amount = _first_amount_after(
                    window,
                    candidate_term.end(),
                    max_chars=120,
                )
                if after_amount is not None:
                    start = candidate_term.end() + after_amount.start()
                    end = candidate_term.end() + after_amount.end()
                    override = _new_range_amount_after(window, end)
                    if kind in {"guidance_revenue", "guidance_eps"} and override:
                        after_amount, start, end = override
                    candidates.append((start - candidate_term.end(), after_amount, start, end))
                before_start = max(0, candidate_term.start() - 120)
                before_matches = list(
                    AMOUNT_RE.finditer(window[before_start : candidate_term.start()])
                )
                if before_matches:
                    before_amount = before_matches[-1]
                    start = before_start + before_amount.start()
                    end = before_start + before_amount.end()
                    candidates.append((candidate_term.start() - end, before_amount, start, end))
                for _, candidate_amount, start, end in sorted(
                    candidates,
                    key=lambda item: item[0],
                ):
                    if candidate_term.start() < guidance_offset and start < guidance_offset:
                        continue
                    if start >= candidate_term.end():
                        between = window[candidate_term.end() : start]
                    else:
                        between = window[end : candidate_term.start()]
                    if kind == "guidance_eps" and REVENUE_TERM_RE.search(between):
                        continue
                    if kind == "guidance_revenue" and EPS_TERM_RE.search(between):
                        continue
                    if candidate_term.start() >= guidance_offset:
                        bridge = window[guidance_end_offset : candidate_term.start()]
                        if GUIDANCE_TO_ACTUALS_BRIDGE_RE.search(bridge):
                            continue
                    if start < candidate_term.start():
                        prior_metric_context = window[
                            max(0, start - 100) : candidate_term.start()
                        ]
                        if (
                            kind == "guidance_revenue"
                            and EPS_TERM_RE.search(prior_metric_context)
                        ):
                            continue
                        if (
                            kind == "guidance_eps"
                            and REVENUE_TERM_RE.search(prior_metric_context)
                        ):
                            continue
                    context_start = max(0, min(candidate_term.start(), start) - 60)
                    context_end = min(
                        len(window),
                        max(candidate_term.end(), end) + 60,
                    )
                    guidance_fact_context = window[context_start:context_end]
                    if (
                        kind == "guidance_revenue"
                        and AI_SEGMENT_REVENUE_CONTEXT_RE.search(guidance_fact_context)
                    ):
                        continue
                    amount_match = candidate_amount
                    amount_start = start
                    amount_end = end
                    break
                if amount_match is None:
                    continue
                term = candidate_term
                break
            if term is None or amount_match is None:
                continue
            absolute_start = window_start + min(term.start(), amount_start)
            absolute_end = window_start + max(term.end(), amount_end)
            if require_subject_alignment and not _fact_context_mentions_subject(
                text,
                aliases,
                subject,
                _clean(record.get("source_title")),
                absolute_start,
                absolute_end,
            ):
                continue
            facts.append(
                _fact(
                    kind=kind,
                    label=label,
                    value=_normalize_amount(amount_match.group(0)),
                    record=record,
                    basis_text=_basis_window(
                        text,
                        window_start + min(term.start(), amount_start),
                        window_start + max(term.end(), amount_end),
                    ),
                )
            )
            found = True
        if found:
            return


def _add_ai_revenue_facts(
    facts: list[dict[str, Any]],
    *,
    record: dict[str, Any],
    aliases: list[str],
    subject: str,
    require_subject_alignment: bool,
) -> None:
    text = str(record.get("text") or "")
    for term in AI_REVENUE_TERM_RE.finditer(text):
        sentence_start, sentence_end = _sentence_bounds(text, term.start(), term.end())
        amount = _first_amount_after(text, term.end(), max_chars=140)
        if amount is None or _looks_like_range(amount.group(0)):
            continue
        amount_start = term.end() + amount.start()
        amount_end = term.end() + amount.end()
        if require_subject_alignment and not _fact_context_mentions_subject(
            text,
            aliases,
            subject,
            _clean(record.get("source_title")),
            term.start(),
            amount_end,
        ):
            continue
        context = text[
            max(sentence_start, term.start() - 80) : min(sentence_end, amount_end + 80)
        ]
        is_guidance = bool(GUIDANCE_CONTEXT_RE.search(context))
        is_server = "server" in term.group(0).lower()
        facts.append(
            _fact(
                kind="ai_guidance_revenue" if is_guidance else "ai_revenue",
                label="AI 매출 가이던스"
                if is_guidance
                else "AI 서버 매출"
                if is_server
                else "AI 매출",
                value=_normalize_amount(amount.group(0)),
                record=record,
                basis_text=_basis_window(text, term.start(), amount_end),
            )
        )


def _add_buyback_fact(
    facts: list[dict[str, Any]],
    *,
    record: dict[str, Any],
    aliases: list[str],
    subject: str,
    require_subject_alignment: bool,
) -> None:
    text = str(record.get("text") or "")
    for term in BUYBACK_TERM_RE.finditer(text):
        amount = _first_amount_after(text, term.end(), max_chars=120)
        if amount is not None:
            amount_end = term.end() + amount.end()
            if require_subject_alignment and not _fact_context_mentions_subject(
                text,
                aliases,
                subject,
                _clean(record.get("source_title")),
                term.start(),
                amount_end,
            ):
                continue
            facts.append(
                _fact(
                    kind="buyback",
                    label="자사주",
                    value=_normalize_amount(amount.group(0)),
                    record=record,
                    basis_text=_basis_window(
                        text,
                        term.start(),
                        amount_end,
                    ),
                )
            )
            return
        before = text[max(0, term.start() - 80) : term.start()]
        before_amounts = list(AMOUNT_RE.finditer(before))
        if before_amounts:
            amount = before_amounts[-1]
            amount_start = max(0, term.start() - 80) + amount.start()
            if require_subject_alignment and not _fact_context_mentions_subject(
                text,
                aliases,
                subject,
                _clean(record.get("source_title")),
                amount_start,
                term.end(),
            ):
                continue
            facts.append(
                _fact(
                    kind="buyback",
                    label="자사주",
                    value=_normalize_amount(amount.group(0)),
                    record=record,
                    basis_text=_basis_window(
                        text,
                        max(0, term.start() - 80) + amount.start(),
                        term.end(),
                    ),
                )
            )
            return


def _add_stock_reaction_fact(
    facts: list[dict[str, Any]],
    *,
    record: dict[str, Any],
    aliases: list[str],
    subject: str,
    require_subject_alignment: bool,
) -> None:
    text = str(record.get("text") or "")
    for pattern, sign in ((UP_REACTION_RE, "+"), (DOWN_REACTION_RE, "-")):
        position = 0
        while True:
            reaction = pattern.search(text, position)
            if reaction is None:
                break
            position = reaction.start() + 1
            pct = PCT_RE.search(reaction.group(0))
            if pct is None:
                continue
            if GROWTH_PCT_CONTEXT_RE.search(reaction.group(0)):
                continue
            if require_subject_alignment and not _fact_context_mentions_subject(
                text,
                aliases,
                subject,
                _clean(record.get("source_title")),
                reaction.start(),
                reaction.end(),
            ):
                continue
            value = _clean(pct.group(0)).replace(" ", "")
            if sign == "+" and not value.startswith(("+", "-")):
                value = "+" + value
            if sign == "-" and not value.startswith(("+", "-")):
                value = "-" + value
            facts.append(
                _fact(
                    kind="stock_reaction",
                    label="주가반응",
                    value=value,
                    record=record,
                    basis_text=_basis_window(text, reaction.start(), reaction.end()),
                )
            )
            return


def _dedupe_facts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for fact in sorted(
        facts,
        key=lambda item: (
            FACT_ORDER.get(str(item.get("kind") or ""), 99),
            0 if item.get("source_url") else 1,
            str(item.get("value") or ""),
        ),
    ):
        key = (
            str(fact.get("kind") or ""),
            str(fact.get("value") or "").lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(fact)
    return deduped


def build_earnings_fact_contract(row: dict[str, Any]) -> dict[str, Any]:
    if not _is_earnings_row(row):
        return {}
    facts: list[dict[str, Any]] = []
    aliases = _subject_aliases(row)
    records = [
        record
        for record in _source_records(row)
        if _record_mentions_subject(record, aliases)
    ]
    for record in records:
        require_subject_alignment = _record_requires_subject_alignment(
            row,
            record,
            aliases,
        )
        _add_term_amount_facts(
            facts,
            record=record,
            aliases=aliases,
            subject=_subject(row),
            require_subject_alignment=require_subject_alignment,
            kind="eps",
            label="EPS",
            term_re=EPS_TERM_RE,
            skip_guidance_context=True,
        )
        _add_term_amount_facts(
            facts,
            record=record,
            aliases=aliases,
            subject=_subject(row),
            require_subject_alignment=require_subject_alignment,
            kind="revenue",
            label="매출",
            term_re=REVENUE_TERM_RE,
            skip_guidance_context=True,
        )
        _add_guidance_facts(
            facts,
            record=record,
            aliases=aliases,
            subject=_subject(row),
            require_subject_alignment=require_subject_alignment,
        )
        _add_ai_revenue_facts(
            facts,
            record=record,
            aliases=aliases,
            subject=_subject(row),
            require_subject_alignment=require_subject_alignment,
        )
        _add_buyback_fact(
            facts,
            record=record,
            aliases=aliases,
            subject=_subject(row),
            require_subject_alignment=require_subject_alignment,
        )
        _add_stock_reaction_fact(
            facts,
            record=record,
            aliases=aliases,
            subject=_subject(row),
            require_subject_alignment=require_subject_alignment,
        )
    facts = [fact for fact in facts if _fact_mentions_subject(fact, aliases)]
    facts = _dedupe_facts(facts)
    if not facts:
        return {}
    return {
        "version": CONTRACT_VERSION,
        "status": "ok",
        "facts": facts,
        "fact_count": len(facts),
        "source_count": len(records),
    }


def attach_earnings_fact_contract(row: dict[str, Any]) -> dict[str, Any]:
    contract = row.get("earnings_fact_contract")
    if isinstance(contract, dict) and contract.get("facts"):
        return contract
    contract = build_earnings_fact_contract(row)
    if contract:
        row["earnings_fact_contract"] = contract
    return contract


def attach_earnings_fact_contracts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for row in rows:
        attach_earnings_fact_contract(row)
    return rows


def earnings_fact_fragments(row: dict[str, Any], *, limit: int = 3) -> list[str]:
    contract = row.get("earnings_fact_contract")
    facts = contract.get("facts") if isinstance(contract, dict) else None
    if not isinstance(facts, list):
        return []
    fragments: list[str] = []
    seen_kinds: set[str] = set()
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        kind = _clean(fact.get("kind"))
        if kind in seen_kinds:
            continue
        label = _clean(fact.get("label"))
        value = _clean(fact.get("value"))
        if not label or not value:
            continue
        seen_kinds.add(kind)
        fragment = f"{label} {value}"
        if fragment not in fragments:
            fragments.append(fragment)
        if len(fragments) >= limit:
            break
    return fragments


def augment_earnings_summary_with_contract(
    row: dict[str, Any],
    summary: str,
    *,
    max_chars: int = MAX_SUMMARY_CHARS,
) -> str:
    text = _clean(summary)
    if not text:
        return text
    fragments = [
        fragment
        for fragment in earnings_fact_fragments(row)
        if fragment.split(" ", 1)[-1] not in text
    ]
    if not fragments:
        return text
    candidate = f"{text}; {' / '.join(fragments)}"
    if len(candidate) <= max_chars:
        return candidate
    for count in range(len(fragments) - 1, 0, -1):
        candidate = f"{text}; {' / '.join(fragments[:count])}"
        if len(candidate) <= max_chars:
            return candidate
    return text
