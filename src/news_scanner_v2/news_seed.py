from __future__ import annotations

from collections import Counter
from datetime import datetime
from hashlib import sha256
import json
import re
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .config import KST_TZ


SEED_POLICY = "news_seed_v1"

AI_INFRA_RE = re.compile(
    r"\b(?:ai infrastructure|artificial intelligence infrastructure|"
    r"cloud infrastructure|data centers?|datacenters?|tpu|gpu|accelerators?|"
    r"coreweave|blackstone|hyperscale|compute capacity)\b",
    re.I,
)
AI_INFRA_ACTION_RE = re.compile(
    r"\b(?:joint venture|\bjv\b|partnership|partners?|acquisition|acquires?|"
    r"merger|deal|stake|build(?:s|ing)?|launch(?:es|ed)?|open(?:s|ed|ing)?|"
    r"go live|goes live|expand(?:s|ed|ing)?|advance(?:s|d)?|spend(?:s|ing)?|"
    r"capex|commit(?:s|ted|ment)?|invests?|invested)\b",
    re.I,
)
AI_INFRA_COUNTERPARTY_RE = re.compile(
    r"\b(?:google|alphabet|microsoft|msft|amazon|aws|meta|oracle|openai|xai|"
    r"blackstone|coreweave|nvidia|nvda|broadcom|avgo|amd|tsmc|softbank|"
    r"nextera|dominion|analog devices)\b",
    re.I,
)
AI_INFRA_PROJECT_RE = re.compile(
    r"\b(?:ai infrastructure|cloud infrastructure|data centers?|datacenters?|"
    r"ai data centers?|ai-data-centre|ai-data-center|compute capacity|"
    r"gpu|tpu|accelerators?|ai servers?|ai power|electricity demand|"
    r"power chips?|azure cloud)\b",
    re.I,
)
AI_INFRA_STOCK_NOISE_RE = re.compile(
    r"\b(?:which .*better buy|better buy now|should you buy|stock|shares?|"
    r"fair value|investment story|analyst upgrades?|earnings report|"
    r"price target)\b",
    re.I,
)
SEMICONDUCTOR_RE = re.compile(
    r"\b(?:semiconductors?|chips?|chipmakers?|nvidia|nvda|micron|\bmu\b|"
    r"smh|sox|broadcom|avgo|amd|intel|intc)\b",
    re.I,
)
PRESSURE_RE = re.compile(
    r"\b(?:falls?|fell|drops?|dropped|down|lower|losses|selloff|selling|"
    r"pressure|profit[- ]taking|weakness|slides?|slips?|sank|negative|"
    r"valuation|yields?|rates?|treasury)\b",
    re.I,
)
AMOUNT_RE = re.compile(r"\$\s?\d+(?:\.\d+)?\s?(?:B|M|K|billion|million)?", re.I)

TRUSTED_DOMAINS = {
    "apnews.com",
    "bloomberg.com",
    "cnbc.com",
    "finance.yahoo.com",
    "ft.com",
    "reuters.com",
    "sec.gov",
    "wsj.com",
}


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())


def _limited(value: object, max_chars: int) -> str:
    text = _clean(value)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _domain(url: object) -> str:
    try:
        host = urlsplit(str(url or "")).netloc.lower()
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host.split("@")[-1].split(":")[0]


def _candidate_id(item: dict[str, Any]) -> str:
    return str(item.get("id") or item.get("candidate_id") or "")


def _text(item: dict[str, Any]) -> str:
    return " ".join(
        _clean(item.get(key)) for key in ("title", "summary", "body_text")
    ).strip()


def _evidence_key(item: dict[str, Any]) -> str:
    return str(
        item.get("item_hash")
        or item.get("canonical_url")
        or item.get("url")
        or item.get("normalized_title")
        or item.get("title")
        or _candidate_id(item)
        or ""
    )


def _unique_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for item in items:
        key = _evidence_key(item)
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _source_tier(items: list[dict[str, Any]]) -> str:
    domains = {_domain(item.get("url")) for item in items}
    domains.discard("")
    if any(domain in TRUSTED_DOMAINS for domain in domains):
        return "trusted"
    if any(str(item.get("provider") or "") == "official_rss" for item in items):
        return "official"
    return "untrusted"


def _source_count(items: list[dict[str, Any]]) -> int:
    sources = {
        _domain(item.get("url")) or str(item.get("source") or "")
        for item in items
    }
    sources.discard("")
    return len(sources)


def _is_trusted_ai_infra_source(item: dict[str, Any]) -> bool:
    if _domain(item.get("url")) in TRUSTED_DOMAINS:
        return True
    return str(item.get("provider") or "") == "official_rss"


def _trusted_ai_infra_item_has_concrete_claim(item: dict[str, Any]) -> bool:
    if not _is_trusted_ai_infra_source(item):
        return False
    text = _text(item)
    if not AI_INFRA_PROJECT_RE.search(text):
        return False
    has_action = AI_INFRA_ACTION_RE.search(text) is not None
    has_amount = AMOUNT_RE.search(text) is not None
    has_counterparty = AI_INFRA_COUNTERPARTY_RE.search(text) is not None
    return has_action and (has_counterparty or has_amount)


def _published_dates(items: list[dict[str, Any]], *, as_of: datetime) -> list[str]:
    tz = ZoneInfo(KST_TZ)
    dates = []
    for item in items:
        value = str(item.get("published_at") or "")
        if not value:
            continue
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo("UTC"))
        dates.append(parsed.astimezone(tz).date().isoformat())
    if dates:
        return sorted(set(dates))
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=tz)
    return [as_of.astimezone(tz).date().isoformat()]


def _seed_key(
    seed_type: str,
    subject: str,
    theme: str,
    items: list[dict[str, Any]],
) -> str:
    evidence_keys = [_evidence_key(item) for item in items if _evidence_key(item)]
    raw = json.dumps(
        {
            "policy": SEED_POLICY,
            "seed_type": seed_type,
            "subject": subject,
            "theme": theme,
            "evidence": sorted(evidence_keys),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(raw.encode("utf-8")).hexdigest()


def _evidence_payload(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "evidence_id": _candidate_id(item),
        "source": str(item.get("source") or ""),
        "provider": str(item.get("provider") or ""),
        "category": str(item.get("category") or ""),
        "domain": _domain(item.get("url")),
        "title": _limited(item.get("title"), 300),
        "summary": _limited(item.get("summary"), 700),
        "body_text": _limited(item.get("body_text"), 1200),
        "url": str(item.get("url") or ""),
        "canonical_url": str(item.get("canonical_url") or ""),
        "published_at": str(item.get("published_at") or ""),
        "item_hash": str(item.get("item_hash") or ""),
    }


def _claim_atoms_for_ai_infra(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    atoms: list[dict[str, str]] = []
    for item in items:
        evidence_id = _candidate_id(item)
        text = _text(item)
        lower = text.lower()
        if "google" in lower or "alphabet" in lower:
            atoms.append(
                {
                    "text": "Google/Alphabet AI infrastructure involvement",
                    "evidence_id": evidence_id,
                }
            )
        if re.search(
            r"\b(?:microsoft|msft|amazon|aws|meta|oracle|openai|xai)\b",
            text,
            re.I,
        ):
            atoms.append(
                {
                    "text": "Big Tech AI infrastructure involvement",
                    "evidence_id": evidence_id,
                }
            )
        if "blackstone" in lower:
            atoms.append(
                {
                    "text": "Blackstone investment or partnership",
                    "evidence_id": evidence_id,
                }
            )
        if re.search(r"\b(?:gpu|tpu|accelerators?|ai servers?)\b", text, re.I):
            atoms.append(
                {
                    "text": "accelerator or AI compute capacity",
                    "evidence_id": evidence_id,
                }
            )
        if re.search(
            r"\b(?:ai infrastructure|cloud infrastructure|data centers?|"
            r"datacenters?|azure cloud|compute capacity)\b",
            text,
            re.I,
        ):
            atoms.append(
                {
                    "text": "data center or cloud capacity expansion",
                    "evidence_id": evidence_id,
                }
            )
        if re.search(
            r"\b(?:ai power|electricity demand|power chips?)\b",
            text,
            re.I,
        ):
            atoms.append(
                {
                    "text": "power or electricity demand tied to AI infrastructure",
                    "evidence_id": evidence_id,
                }
            )
        if "coreweave" in lower:
            atoms.append(
                {
                    "text": "CoreWeave comparison or competition",
                    "evidence_id": evidence_id,
                }
            )
        if re.search(r"joint venture|\bjv\b|partnership", text, re.I):
            atoms.append(
                {
                    "text": "joint venture or partnership structure",
                    "evidence_id": evidence_id,
                }
            )
        if re.search(
            r"\b(?:acquisition|acquires?|merger|deal|stake)\b",
            text,
            re.I,
        ):
            atoms.append(
                {
                    "text": "acquisition or deal structure",
                    "evidence_id": evidence_id,
                }
            )
        if re.search(
            r"\b(?:spend(?:s|ing)?|capex|commit(?:s|ted|ment)?|invests?|"
            r"invested)\b",
            text,
            re.I,
        ):
            atoms.append(
                {
                    "text": "AI infrastructure capex or spending commitment",
                    "evidence_id": evidence_id,
                }
            )
        amount = AMOUNT_RE.search(text)
        if amount:
            atoms.append(
                {
                    "text": f"amount mentioned: {amount.group(0)}",
                    "evidence_id": evidence_id,
                }
            )
    return _dedupe_atoms(atoms)


def _is_ai_infra_item_worthy(item: dict[str, Any]) -> bool:
    category = str(item.get("category") or "")
    if category not in {"MA", "STRAT"}:
        return False
    text = _text(item)
    title = _clean(item.get("title"))
    if not AI_INFRA_RE.search(text):
        return False
    if not AI_INFRA_PROJECT_RE.search(text):
        return False
    has_action = AI_INFRA_ACTION_RE.search(text) is not None
    has_amount = AMOUNT_RE.search(text) is not None
    has_counterparty = AI_INFRA_COUNTERPARTY_RE.search(text) is not None
    if category == "MA":
        return has_amount and (
            has_action
            or re.search(
                r"\b(?:ai power|electricity demand|data centers?)\b",
                text,
                re.I,
            )
            is not None
        )
    if AI_INFRA_STOCK_NOISE_RE.search(title) and not (
        re.search(
            r"\b(?:data centers?|datacenters?|joint venture|\bjv\b|"
            r"acquisition|merger|launch(?:es|ed)?|open(?:s|ed|ing)?|go live|"
            r"goes live|build(?:s|ing)?|expand(?:s|ed|ing)?|capex|spend(?:s|ing)?)\b",
            title,
            re.I,
        )
    ):
        return False
    return has_counterparty and has_action


def _ai_infra_seed_has_sufficient_sources(items: list[dict[str, Any]]) -> bool:
    return any(_trusted_ai_infra_item_has_concrete_claim(item) for item in items)


def _claim_atoms_for_semis(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    atoms: list[dict[str, str]] = []
    for item in items:
        evidence_id = _candidate_id(item)
        text = _text(item)
        if re.search(r"\b(?:nvidia|nvda)\b", text, re.I):
            atoms.append(
                {
                    "text": "NVDA mentioned in semiconductor pressure",
                    "evidence_id": evidence_id,
                }
            )
        if re.search(
            r"\b(?:semiconductors?|chips?|chipmakers?|smh|sox)\b",
            text,
            re.I,
        ):
            atoms.append(
                {
                    "text": "semiconductor or chip pressure",
                    "evidence_id": evidence_id,
                }
            )
        if re.search(r"\b(?:yields?|rates?|treasury|valuation)\b", text, re.I):
            atoms.append(
                {"text": "rates or valuation pressure", "evidence_id": evidence_id}
            )
        if PRESSURE_RE.search(text):
            atoms.append(
                {
                    "text": "negative price or sentiment pressure",
                    "evidence_id": evidence_id,
                }
            )
    return _dedupe_atoms(atoms)


def _dedupe_atoms(atoms: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, str]] = []
    for atom in atoms:
        text = _clean(atom.get("text"))
        evidence_id = _clean(atom.get("evidence_id"))
        key = (text, evidence_id)
        if not text or not evidence_id or key in seen:
            continue
        seen.add(key)
        deduped.append({"text": text, "evidence_id": evidence_id})
    return deduped


def _base_seed(
    *,
    seed_type: str,
    subject: str,
    theme: str,
    summary_seed: str,
    items: list[dict[str, Any]],
    claim_atoms: list[dict[str, str]],
    as_of: datetime,
    market_relevance: str,
) -> dict[str, Any]:
    items = _unique_items(items)
    return {
        "policy": SEED_POLICY,
        "seed_key": _seed_key(seed_type, subject, theme, items),
        "seed_type": seed_type,
        "subject": subject,
        "theme": theme,
        "summary_seed": summary_seed,
        "freshness": "fresh",
        "published_dates": _published_dates(items, as_of=as_of),
        "market_relevance": market_relevance,
        "source_tier": _source_tier(items),
        "source_count": _source_count(items),
        "evidence_count": len(items),
        "evidence_ids": [_candidate_id(item) for item in items if _candidate_id(item)],
        "claim_atoms": claim_atoms,
        "evidence_items": [_evidence_payload(item) for item in items],
    }


def _ai_infra_items(raw_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matches = []
    for item in raw_items:
        if _is_ai_infra_item_worthy(item):
            matches.append(item)
    return _unique_items(matches)


def _semiconductor_pressure_items(
    raw_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    matches = []
    for item in raw_items:
        category = str(item.get("category") or "")
        if category not in {"ANAL", "EARN", "MA", "MOVE", "STRAT"}:
            continue
        text = _text(item)
        if SEMICONDUCTOR_RE.search(text) and PRESSURE_RE.search(text):
            matches.append(item)
    return _unique_items(matches)


def build_news_seeds(
    *,
    raw_items: list[dict[str, Any]],
    as_of: datetime,
) -> list[dict[str, Any]]:
    seeds: list[dict[str, Any]] = []

    ai_items = _ai_infra_items(raw_items)
    if ai_items and _ai_infra_seed_has_sufficient_sources(ai_items):
        seeds.append(
            _base_seed(
                seed_type="strategic_theme",
                subject="AI_INFRA",
                theme="ai_infrastructure_jv",
                summary_seed="AI infrastructure, cloud compute, or JV/capex story",
                items=ai_items,
                claim_atoms=_claim_atoms_for_ai_infra(ai_items),
                as_of=as_of,
                market_relevance="medium_high",
            )
        )

    semi_items = _semiconductor_pressure_items(raw_items)
    if len(semi_items) >= 2:
        seeds.append(
            _base_seed(
                seed_type="sector_pressure",
                subject="SEMIS",
                theme="semiconductor_pressure",
                summary_seed=(
                    "Semiconductor pressure from chip weakness, NVDA caution, "
                    "rates, or valuation"
                ),
                items=semi_items,
                claim_atoms=_claim_atoms_for_semis(semi_items),
                as_of=as_of,
                market_relevance="medium",
            )
        )

    return seeds


def summarize_news_seeds(seeds: list[dict[str, Any]]) -> dict[str, Any]:
    by_type = Counter(str(seed.get("seed_type") or "unknown") for seed in seeds)
    by_theme = Counter(str(seed.get("theme") or "unknown") for seed in seeds)
    return {
        "news_seeds_built": len(seeds),
        "news_seeds_by_type": dict(sorted(by_type.items())),
        "news_seeds_by_theme": dict(sorted(by_theme.items())),
    }
