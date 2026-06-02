from __future__ import annotations

from hashlib import sha256
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .db import connect
from .llm import summary_annotation_from_editorial


THEME_POLICY = "market_theme_v1"
NEWS_SEED_THEME_POLICY = "news_seed_theme_v1"

SECTOR_PRESSURE = "sector_pressure"
SECTOR_RALLY = "sector_rally"
EARNINGS_RESULT = "earnings_result"

SEMICONDUCTOR_THEME_KEY = "semiconductor_pressure"
MEMORY_RALLY_THEME_KEY = "memory_sector_rally"
HD_EARNINGS_THEME_KEY = "hd_earnings_result"

SEMICONDUCTOR_RE = re.compile(
    r"\b(?:semiconductors?|chips?|chipmakers?|nvidia|nvda|micron|\bmu\b|smh|sox)\b",
    re.I,
)
SECTOR_PRESSURE_RE = re.compile(
    r"\b(?:falls?|fell|drops?|dropped|down|lower|losses|selloff|selling|"
    r"pressure|profit[- ]taking|taking profits?|weakness|slides?|slips?|sank|negative)\b",
    re.I,
)
RATES_PRESSURE_RE = re.compile(r"\b(?:yield|yields|rates?|treasury|valuation)\b", re.I)
MEMORY_SECTOR_RE = re.compile(
    r"\b(?:micron|micron technology|\bmu\b|hbm|hbm4|dram|nand|memory chips?|"
    r"memory stocks?|western digital|\bwdc\b|seagate|\bstx\b|sandisk|"
    r"sk hynix|samsung electronics)\b",
    re.I,
)
MEMORY_RALLY_RE = re.compile(
    r"\b(?:rall(?:y|ies|ied)|surge[sd]?|soar(?:s|ed)?|jump(?:s|ed)?|"
    r"gain(?:s|ed)?|climb(?:s|ed)?|record highs?|all[- ]time highs?|"
    r"trillion[- ]dollar|trillion club|market value|ai[- ]driven rally|"
    r"ai race powers|ai demand)\b",
    re.I,
)
MEMORY_RALLY_EXCLUDE_RE = re.compile(
    r"\b(?:beware|boom and bust|selloff|selling pressure|falls?|fell|drops?|"
    r"dropped|plunge[sd]?|warning signs?)\b",
    re.I,
)

HD_RE = re.compile(r"\b(?:home depot|nyse:hd|nasdaq:hd|\bhd\b)\b", re.I)
EARNINGS_RE = re.compile(
    r"\b(?:q[1-4]|earnings|results?|eps|revenue|sales|guidance|outlook)\b",
    re.I,
)
PREVIEW_RE = re.compile(
    r"\b(?:expects?|expected|what wall street expects|due to report|preview|"
    r"before the market opens|will report|set to report)\b",
    re.I,
)

LOW_QUALITY_DOMAINS = {
    "coincentral.com",
    "eciks.org",
    "marketbeat.com",
}
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


def _text(item: dict[str, Any]) -> str:
    return " ".join(
        str(item.get(key) or "")
        for key in ("title", "summary", "body_text")
    ).strip()


def _candidate_id(item: dict[str, Any]) -> str:
    return str(
        item.get("candidate_id")
        or item.get("id")
        or item.get("item_id")
        or ""
    )


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


def _domain(url: object) -> str:
    try:
        host = urlsplit(str(url or "")).netloc.lower()
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host.split("@")[-1].split(":")[0]


def _source_tier(items: list[dict[str, Any]]) -> str:
    domains = {_domain(item.get("url")) for item in items}
    domains.discard("")
    if any(domain in TRUSTED_DOMAINS for domain in domains):
        return "trusted"
    if domains and all(domain in LOW_QUALITY_DOMAINS for domain in domains):
        return "low_quality"
    return "untrusted"


def _theme_id(theme_type: str, theme_key: str, items: list[dict[str, Any]]) -> str:
    evidence_keys = [_evidence_key(item) for item in items if _evidence_key(item)]
    raw = "|".join([THEME_POLICY, theme_type, theme_key, *sorted(evidence_keys)])
    return sha256(raw.encode("utf-8")).hexdigest()


def _evidence_payload(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": _candidate_id(item),
        "category": str(item.get("category") or ""),
        "provider": str(item.get("provider") or ""),
        "source": str(item.get("source") or ""),
        "title": str(item.get("title") or ""),
        "url": str(item.get("url") or ""),
        "canonical_url": str(item.get("canonical_url") or ""),
        "item_hash": str(item.get("item_hash") or ""),
        "published_at": str(item.get("published_at") or ""),
        "summary": str(item.get("summary") or "")[:700],
        "body_text": str(item.get("body_text") or "")[:1200],
        "domain": _domain(item.get("url")),
    }


def _unique_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for item in items:
        key = _candidate_id(item) or str(item.get("url") or item.get("title") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _theme_item_rank(item: dict[str, Any]) -> tuple[int, str, str]:
    domain = _domain(item.get("url"))
    if domain in TRUSTED_DOMAINS:
        tier = 0
    elif domain in LOW_QUALITY_DOMAINS:
        tier = 2
    else:
        tier = 1
    return (tier, str(item.get("published_at") or ""), str(item.get("title") or ""))


def _ranked_unique_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(_unique_items(items), key=_theme_item_rank)


def _semiconductor_items(raw_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matches = []
    for item in raw_items:
        category = str(item.get("category") or "")
        if category not in {"MOVE", "EARN", "STRAT", "ANAL"}:
            continue
        text = _text(item)
        if not SEMICONDUCTOR_RE.search(text):
            continue
        if not (SECTOR_PRESSURE_RE.search(text) or RATES_PRESSURE_RE.search(text)):
            continue
        matches.append(item)
    return _ranked_unique_items(matches)


def _semiconductor_claim_atoms(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    atoms: list[dict[str, str]] = []
    for item in items:
        evidence_id = _candidate_id(item)
        text = _text(item)
        if re.search(r"\b(?:nvidia|nvda)\b", text, re.I) and SECTOR_PRESSURE_RE.search(text):
            atoms.append(
                {
                    "text": "NVDA weakness or earnings caution",
                    "evidence_id": evidence_id,
                }
            )
        if re.search(r"\b(?:semiconductors?|chips?|chipmakers?|smh|sox)\b", text, re.I):
            atoms.append(
                {
                    "text": "semiconductor or chip pressure",
                    "evidence_id": evidence_id,
                }
            )
        if RATES_PRESSURE_RE.search(text):
            atoms.append(
                {
                    "text": "rising yields or valuation pressure",
                    "evidence_id": evidence_id,
                }
            )
    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for atom in atoms:
        key = (atom["text"], atom["evidence_id"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(atom)
    return deduped


def _memory_rally_items(raw_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matches = []
    for item in raw_items:
        category = str(item.get("category") or "")
        if category not in {"MOVE", "EARN", "STRAT", "ANAL"}:
            continue
        text = _text(item)
        if not MEMORY_SECTOR_RE.search(text):
            continue
        if MEMORY_RALLY_EXCLUDE_RE.search(text):
            continue
        if not MEMORY_RALLY_RE.search(text):
            continue
        matches.append(item)
    return _ranked_unique_items(matches)


def _memory_rally_claim_atoms(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    atoms: list[dict[str, str]] = []
    for item in items:
        evidence_id = _candidate_id(item)
        text = _text(item)
        if re.search(r"\b(?:micron|micron technology|\bmu\b)\b", text, re.I):
            atoms.append(
                {
                    "text": "Micron/MU is the lead memory-stock evidence",
                    "evidence_id": evidence_id,
                }
            )
        if re.search(
            r"\b(?:hbm|hbm4|dram|nand|memory chips?|memory stocks?)\b",
            text,
            re.I,
        ):
            atoms.append(
                {
                    "text": "HBM/DRAM/NAND or memory stocks are mentioned",
                    "evidence_id": evidence_id,
                }
            )
        if re.search(
            r"\b(?:surge[sd]?|soar(?:s|ed)?|jump(?:s|ed)?|"
            r"rall(?:y|ies|ied)|gain(?:s|ed)?)\b",
            text,
            re.I,
        ):
            atoms.append(
                {
                    "text": "share-price rally language is present",
                    "evidence_id": evidence_id,
                }
            )
        if re.search(
            r"\b(?:record highs?|all[- ]time highs?|trillion[- ]dollar|"
            r"trillion club|market value)\b",
            text,
            re.I,
        ):
            atoms.append(
                {
                    "text": "record-high or trillion-dollar milestone is present",
                    "evidence_id": evidence_id,
                }
            )
        if re.search(r"\b(?:ai demand|ai race powers|ai[- ]driven rally)\b", text, re.I):
            atoms.append(
                {
                    "text": "AI demand is cited as a driver",
                    "evidence_id": evidence_id,
                }
            )
    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for atom in atoms:
        key = (atom["text"], atom["evidence_id"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(atom)
    return deduped


def _hd_items(raw_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matches = []
    for item in raw_items:
        if str(item.get("category") or "") != "EARN":
            continue
        text = _text(item)
        if not HD_RE.search(text) or not EARNINGS_RE.search(text):
            continue
        matches.append(item)
    return _unique_items(matches)


def _hd_claim_atoms(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    atoms: list[dict[str, str]] = []
    for item in items:
        evidence_id = _candidate_id(item)
        text = _text(item)
        if re.search(r"\beps\b", text, re.I):
            atoms.append({"text": "EPS data mentioned", "evidence_id": evidence_id})
        if re.search(r"\brevenue|sales\b", text, re.I):
            atoms.append({"text": "revenue data mentioned", "evidence_id": evidence_id})
        if re.search(r"\bguidance|outlook\b", text, re.I):
            atoms.append({"text": "guidance data mentioned", "evidence_id": evidence_id})
    return atoms


def build_market_theme_candidates(
    *,
    raw_items: list[dict[str, Any]],
    decision_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    themes: list[dict[str, Any]] = []
    decision_rows = decision_rows or []
    existing_event_keys = {
        (
            str(row.get("event_type") or ""),
            str(row.get("subject") or "").upper(),
            str(row.get("action") or ""),
        )
        for row in decision_rows
    }

    semi_items = _semiconductor_items(raw_items)
    if len(semi_items) >= 2:
        evidence_ids = [_candidate_id(item) for item in semi_items if _candidate_id(item)]
        themes.append(
            {
                "id": _theme_id(SECTOR_PRESSURE, SEMICONDUCTOR_THEME_KEY, semi_items),
                "policy": THEME_POLICY,
                "theme_type": SECTOR_PRESSURE,
                "theme_key": SEMICONDUCTOR_THEME_KEY,
                "subject": "semiconductors",
                "action": "sector_pressure",
                "market_marker": "red",
                "grade": "B",
                "requires_verification": False,
                "source_tier": _source_tier(semi_items),
                "evidence_ids": evidence_ids,
                "evidence": [_evidence_payload(item) for item in semi_items],
                "claim_atoms": _semiconductor_claim_atoms(semi_items),
                "summary_seed": (
                    "Semiconductor pressure from chip weakness, NVDA caution, "
                    "and/or rising yields"
                ),
            }
        )

    memory_items = _memory_rally_items(raw_items)
    if len(memory_items) >= 2:
        evidence_ids = [_candidate_id(item) for item in memory_items if _candidate_id(item)]
        themes.append(
            {
                "id": _theme_id(SECTOR_RALLY, MEMORY_RALLY_THEME_KEY, memory_items),
                "policy": THEME_POLICY,
                "theme_type": SECTOR_RALLY,
                "theme_key": MEMORY_RALLY_THEME_KEY,
                "subject": "memory_semiconductors",
                "action": "sector_rally",
                "market_marker": "green",
                "grade": "B",
                "requires_verification": False,
                "source_tier": _source_tier(memory_items),
                "evidence_ids": evidence_ids,
                "evidence": [_evidence_payload(item) for item in memory_items],
                "claim_atoms": _memory_rally_claim_atoms(memory_items),
                "summary_seed": (
                    "Memory semiconductor rally from Micron/MU, HBM/DRAM/NAND, "
                    "and AI demand evidence"
                ),
            }
        )

    hd_items = _hd_items(raw_items)
    if hd_items and ("earnings", "HD", "earnings_report") not in existing_event_keys:
        evidence_ids = [_candidate_id(item) for item in hd_items if _candidate_id(item)]
        trusted = _source_tier(hd_items) == "trusted"
        preview_only = all(PREVIEW_RE.search(_text(item)) for item in hd_items)
        themes.append(
            {
                "id": _theme_id(EARNINGS_RESULT, HD_EARNINGS_THEME_KEY, hd_items),
                "policy": THEME_POLICY,
                "theme_type": EARNINGS_RESULT,
                "theme_key": HD_EARNINGS_THEME_KEY,
                "subject": "HD",
                "action": "earnings_result",
                "market_marker": "none",
                "grade": "B",
                "requires_verification": not trusted,
                "requires_trusted_rescue": not trusted,
                "preview_only": preview_only,
                "source_tier": _source_tier(hd_items),
                "evidence_ids": evidence_ids,
                "evidence": [_evidence_payload(item) for item in hd_items],
                "claim_atoms": _hd_claim_atoms(hd_items),
                "summary_seed": "HD earnings or guidance update requires trusted rescue",
            }
        )

    return themes


def _load_raw_json(value: object) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def load_raw_items_for_run(db_path: Path, *, run_id: str) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, source, provider, category, title, normalized_title, url,
                   canonical_url, published_at, fetched_at, item_hash, raw_json
            FROM candidate_items
            WHERE run_id = ?
            ORDER BY fetched_at, id
            """,
            (run_id,),
        ).fetchall()

    items: list[dict[str, Any]] = []
    for row in rows:
        raw = _load_raw_json(row["raw_json"])
        item = {
            "id": str(row["id"] or ""),
            "candidate_id": str(row["id"] or ""),
            "source": str(row["source"] or ""),
            "provider": str(row["provider"] or ""),
            "category": str(row["category"] or ""),
            "title": str(row["title"] or raw.get("title") or ""),
            "normalized_title": str(row["normalized_title"] or raw.get("normalized_title") or ""),
            "url": str(row["url"] or raw.get("url") or ""),
            "canonical_url": str(row["canonical_url"] or raw.get("canonical_url") or ""),
            "published_at": str(row["published_at"] or raw.get("published_at") or ""),
            "fetched_at": str(row["fetched_at"] or ""),
            "item_hash": str(row["item_hash"] or raw.get("item_hash") or ""),
            "summary": str(raw.get("summary") or ""),
            "body_text": str(raw.get("body_text") or ""),
            "body_fetch": raw.get("body_fetch", {})
            if isinstance(raw.get("body_fetch"), dict)
            else {},
        }
        items.append(item)
    return items


def _seed_evidence_payload(item: dict[str, Any]) -> dict[str, Any]:
    evidence_id = str(item.get("evidence_id") or item.get("candidate_id") or "")
    return {
        "candidate_id": evidence_id,
        "category": str(item.get("category") or ""),
        "provider": str(item.get("provider") or ""),
        "source": str(item.get("source") or ""),
        "title": str(item.get("title") or ""),
        "url": str(item.get("url") or ""),
        "canonical_url": str(item.get("canonical_url") or ""),
        "item_hash": str(item.get("item_hash") or ""),
        "published_at": str(item.get("published_at") or ""),
        "summary": str(item.get("summary") or "")[:700],
        "body_text": str(item.get("body_text") or "")[:1200],
        "domain": str(item.get("domain") or _domain(item.get("url"))),
    }


def _seed_market_marker(theme: str) -> str:
    if theme == SEMICONDUCTOR_THEME_KEY:
        return "red"
    if theme == MEMORY_RALLY_THEME_KEY:
        return "green"
    if theme == "ai_infrastructure_jv":
        return "green"
    return "none"


def _news_seed_candidate(row: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    seed_key = str(row.get("seed_key") or payload.get("seed_key") or "")
    seed_type = str(row.get("seed_type") or payload.get("seed_type") or "")
    subject = str(row.get("subject") or payload.get("subject") or "")
    theme = str(row.get("theme") or payload.get("theme") or "")
    evidence = [
        _seed_evidence_payload(item)
        for item in payload.get("evidence_items", [])
        if isinstance(item, dict)
    ]
    source_tier = str(payload.get("source_tier") or "")
    trusted_enough = source_tier in {"official", "trusted"}
    return {
        "id": seed_key,
        "policy": NEWS_SEED_THEME_POLICY,
        "seed_policy": payload.get("policy"),
        "theme_type": seed_type,
        "theme_key": theme,
        "subject": subject,
        "action": theme,
        "market_marker": _seed_market_marker(theme),
        "grade": "B",
        "requires_verification": not trusted_enough,
        "requires_trusted_rescue": not trusted_enough,
        "preview_only": False,
        "source_tier": source_tier,
        "evidence_ids": [
            str(value or "")
            for value in payload.get("evidence_ids", [])
            if str(value or "")
        ],
        "evidence": evidence,
        "claim_atoms": [
            atom for atom in payload.get("claim_atoms", []) if isinstance(atom, dict)
        ],
        "summary_seed": str(payload.get("summary_seed") or theme or seed_type),
        "seed_freshness": row.get("freshness") or payload.get("freshness"),
        "market_relevance": row.get("market_relevance")
        or payload.get("market_relevance"),
        "source_count": int(row.get("source_count") or payload.get("source_count") or 0),
        "evidence_count": int(
            row.get("evidence_count") or payload.get("evidence_count") or 0
        ),
    }


def load_news_seed_candidates_for_run(
    db_path: Path,
    *,
    run_id: str,
) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT seed_key, seed_type, subject, theme, freshness,
                   market_relevance, source_count, evidence_count, payload_json
            FROM news_seeds
            WHERE run_id = ?
            ORDER BY created_at, id
            """,
            (run_id,),
        ).fetchall()

    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        payload = _load_raw_json(row["payload_json"])
        candidate = _news_seed_candidate(dict(row), payload)
        if not candidate["id"] or candidate["id"] in seen:
            continue
        if not candidate["evidence"] or not candidate["claim_atoms"]:
            continue
        seen.add(candidate["id"])
        candidates.append(candidate)
    return candidates


def _unique_texts(values: list[object]) -> list[str]:
    seen: set[str] = set()
    results: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        results.append(text)
    return results


def _run_effective_date(run: dict[str, Any] | None) -> str:
    as_of = str((run or {}).get("as_of") or "")
    if len(as_of) >= 10:
        return as_of[:10]
    return ""


def theme_candidate_to_delivery_row(
    candidate: dict[str, Any],
    *,
    run: dict[str, Any],
) -> dict[str, Any]:
    editorial = candidate.get("llm_editorial")
    if not isinstance(editorial, dict) or editorial.get("decision") != "send":
        raise ValueError("theme candidate has no send editorial")
    annotation = summary_annotation_from_editorial(editorial)
    selected_ids = set(
        str(value or "")
        for value in editorial.get("evidence_ids", [])
        if str(value or "")
    )
    evidence_items = [
        item
        for item in candidate.get("evidence", [])
        if isinstance(item, dict)
        and (not selected_ids or str(item.get("candidate_id") or "") in selected_ids)
    ]
    if not evidence_items:
        evidence_items = [
            item for item in candidate.get("evidence", []) if isinstance(item, dict)
        ]

    event_signature = f"market_theme:{candidate['id']}"
    grade = str(editorial.get("grade") or candidate.get("grade") or "B").upper()
    score = 92.0 if grade == "A" else 82.0
    return {
        "decision_id": f"theme:{candidate['id']}",
        "run_id": str(run.get("id") or ""),
        "event_signature": event_signature,
        "decision": "send_candidate",
        "score": score,
        "reason": "market_theme_editor:send",
        "policy": candidate.get("policy") or THEME_POLICY,
        "event_type": "theme",
        "subject": str(candidate.get("subject") or ""),
        "action": str(candidate.get("action") or candidate.get("theme_type") or ""),
        "effective_date": _run_effective_date(run),
        "title": str(candidate.get("summary_seed") or editorial.get("summary_ko") or ""),
        "url": "",
        "evidence_count": len(evidence_items),
        "grade": grade,
        "risk_flags": list(editorial.get("risk_flags") or []),
        "source_tier": str(candidate.get("source_tier") or ""),
        "event_quality": "theme_synthesis",
        "hard_event_reason": "",
        "soft_analysis_reason": "",
        "event_metadata": {
            "theme_key": candidate.get("theme_key"),
            "theme_type": candidate.get("theme_type"),
            "policy": candidate.get("policy"),
        },
        "price_reaction": {},
        "verification": {
            "status": "theme_editor",
            "policy": candidate.get("policy") or THEME_POLICY,
        },
        "verification_status": "theme_editor",
        "price_reaction_required": False,
        "send_worthy_reason": "llm_theme_editor",
        "providers": _unique_texts([item.get("provider") for item in evidence_items]),
        "sources": _unique_texts(
            [item.get("source") for item in evidence_items]
            + [item.get("domain") for item in evidence_items]
        ),
        "candidate_ids": _unique_texts(
            [item.get("candidate_id") for item in evidence_items]
        ),
        "evidence_items": evidence_items,
        "body_text": "",
        "score_reasons": ["market_theme_editor"],
        "extractor_reasons": list(candidate.get("claim_atoms") or []),
        "llm_annotation": annotation,
        "llm_editorial": editorial,
        "evidence_contract": {
            "version": "market_theme_contract_v1",
            "status": "pass",
            "delivery_eligible": True,
            "failures": [],
            "warnings": [],
            "source_tier": candidate.get("source_tier") or "",
            "theme_key": candidate.get("theme_key"),
            "evidence_count": len(evidence_items),
        },
        "created_at": "",
    }
