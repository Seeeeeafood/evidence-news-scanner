from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
import re
from typing import Any, Protocol
from urllib import error, request
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .config import DEFAULT_LLM_MODEL, DEFAULT_LLM_TIMEOUT_SECONDS, KST_TZ
from .db import connect, insert_llm_annotations
from .extractor import COMPANY_ALIASES


PROMPT_VERSION = "news_summary_v4"
EDITORIAL_PROMPT_VERSION = "news_editor_v1"
THEME_EDITORIAL_PROMPT_VERSION = "market_theme_editor_v1"
DISCOVERY_QUERY_PROMPT_VERSION = "discovery_query_planner_v1"
ANNOTATION_TYPE_SUMMARY = "summary"
ANNOTATION_TYPE_EDITORIAL = "editorial"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
PROVIDER_OPENAI = "openai"
HANGUL_RE = re.compile(r"[가-힣]")
ENGLISH_TOKEN_RE = re.compile(r"\b[A-Za-z][A-Za-z.-]*\b")
NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?%?\b")
SOURCE_PREFIX_RE = re.compile(
    r"^\s*(?:Reuters|NYT|CNBC|Bloomberg|AP|Associated Press|"
    r"Wall Street Journal|WSJ|Financial Times|FT|MarketWatch|"
    r"Yahoo Finance|Investing\.com|Benzinga|The Guardian|Guardian)"
    r"\s*[:：-]\s*",
    re.IGNORECASE,
)
SOURCE_NAME_RE = re.compile(
    r"\b(?:Reuters|NYT|CNBC|Bloomberg|AP|Associated Press|"
    r"Wall Street Journal|WSJ|Financial Times|FT|MarketWatch|"
    r"Yahoo Finance|Investing\.com|Benzinga|Guardian)\b",
    re.IGNORECASE,
)
AMOUNT_WORD_RE = re.compile(
    r"(\$\s*\d+(?:\.\d+)?)\s*(trillion|billion|million|thousand)(?=$|[^A-Za-z])",
    re.IGNORECASE,
)
TICKER_PREFIX_RE = re.compile(r"^\s*([A-Z]{1,6}):\s+")
DUPLICATE_TICKER_SLASH_RE = re.compile(r"\b([A-Z]{1,6})/\1(?=[^A-Za-z0-9]|$)")
GENERIC_SUMMARY_RE = re.compile(
    r"관련 (?:정책·지정학 이벤트 진행|정책/지정학 리스크 부각|"
    r"관세/정책 리스크 부각|제재 이슈로 지정학 리스크 부각|"
    r"분쟁 리스크가 커지며 시장 부담 확대)"
)
GENERIC_THEME_SUMMARY_RE = re.compile(
    r"(?:부담(?:으로|이)?|리스크(?:가|는)?|압박(?:\s*신호)?|경계감|"
    r"투자심리|섹터 전반).{0,24}부각|"
    r"(?:관련 이슈|압박 신호|흐름입니다)"
)
AI_INFRA_THEME_KEY = "ai_infrastructure_jv"
TRUSTED_THEME_DOMAINS = {
    "apnews.com",
    "bloomberg.com",
    "cnbc.com",
    "finance.yahoo.com",
    "ft.com",
    "reuters.com",
    "sec.gov",
    "wsj.com",
}
WEAK_AI_INFRA_SOURCE_HINT_RE = re.compile(
    r"\b(?:AOL|Southern Maryland Chronicle|FourWeekMBA|MarketBeat|CoinCentral)\b",
    re.I,
)
THEME_MATERIAL_AMOUNT_RE = re.compile(
    r"\$\s*\d+(?:\.\d+)?\s*(?:B|M|K|billion|million)?|"
    r"\b\d+(?:\.\d+)?\s*(?:B|M|billion|million)\b",
    re.I,
)
SOURCE_HINT_DOMAIN_RULES: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    (re.compile(r"\bReuters\b", re.I), ("reuters.com",)),
    (re.compile(r"\bCNBC\b", re.I), ("cnbc.com",)),
    (re.compile(r"\bBloomberg\b", re.I), ("bloomberg.com",)),
    (re.compile(r"\bAP\b|Associated Press", re.I), ("apnews.com",)),
    (re.compile(r"\bWSJ\b|Wall Street Journal", re.I), ("wsj.com",)),
    (re.compile(r"\bFT\b|Financial Times", re.I), ("ft.com",)),
    (re.compile(r"\bYahoo\b|Yahoo Finance", re.I), ("finance.yahoo.com",)),
    (re.compile(r"\bMarketWatch\b", re.I), ("marketwatch.com",)),
    (re.compile(r"\bInvestopedia\b", re.I), ("investopedia.com",)),
    (re.compile(r"\bTradingView\b", re.I), ("tradingview.com",)),
    (re.compile(r"\bNikkei\b|Nikkei Asia", re.I), ("nikkei.com", "asia.nikkei.com")),
    (re.compile(r"\bFederal Reserve\b", re.I), ("federalreserve.gov",)),
    (re.compile(r"\bChartMill\b", re.I), ("chartmill.com",)),
    (re.compile(r"\bBenzinga\b", re.I), ("benzinga.com",)),
    (re.compile(r"\bBarron'?s\b", re.I), ("barrons.com",)),
    (
        re.compile(r"\bEconomic Times\b", re.I),
        ("economictimes.indiatimes.com",),
    ),
    (re.compile(r"\bAOL\b", re.I), ("aol.com",)),
)
GENERIC_PROVIDER_SOURCE_HINT_RE = re.compile(
    r"^\s*(?:brave|google(?:\s+rss)?|rss|web|source|provider|news|"
    r"breaking[_\s-]?hint)"
    r"(?:\s+(?:\d{1,2}/\d{1,2}|\d{4}-\d{2}-\d{2}))?\s*$",
    re.IGNORECASE,
)


def _geo_pattern(term: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![A-Za-z]){term}(?![A-Za-z])", re.IGNORECASE)


GEOPOLITICAL_TRANSLATIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (_geo_pattern(r"TRUMP[-\s]+XI"), "트럼프-시진핑"),
    (_geo_pattern(r"TRUMP"), "트럼프"),
    (_geo_pattern(r"XI"), "시진핑"),
    (_geo_pattern(r"IRAN"), "이란"),
    (_geo_pattern(r"CHINA"), "중국"),
    (_geo_pattern(r"TAIWAN"), "대만"),
    (_geo_pattern(r"RUSSIA"), "러시아"),
    (_geo_pattern(r"UKRAINE"), "우크라이나"),
    (_geo_pattern(r"HORMUZ"), "호르무즈"),
    (_geo_pattern(r"MIDDLE EAST"), "중동"),
    (re.compile(r"(?<![A-Za-z])U\.S\.(?![A-Za-z])", re.IGNORECASE), "미국"),
    (_geo_pattern(r"US"), "미국"),
)
KOREAN_PARTICLE_CORRECTIONS = (
    ("이란가", "이란이"),
    ("이란는", "이란은"),
    ("이란를", "이란을"),
    ("이란와", "이란과"),
    ("중국가", "중국이"),
    ("중국는", "중국은"),
    ("중국를", "중국을"),
    ("중국와", "중국과"),
    ("대만가", "대만이"),
    ("대만는", "대만은"),
    ("대만를", "대만을"),
    ("대만와", "대만과"),
    ("중동가", "중동이"),
    ("중동는", "중동은"),
    ("중동를", "중동을"),
    ("중동와", "중동과"),
    ("미국가", "미국이"),
    ("미국는", "미국은"),
    ("미국를", "미국을"),
    ("미국와", "미국과"),
    ("시진핑가", "시진핑이"),
    ("시진핑는", "시진핑은"),
    ("시진핑를", "시진핑을"),
    ("시진핑와", "시진핑과"),
    ("트럼프-시진핑가", "트럼프-시진핑이"),
    ("트럼프-시진핑는", "트럼프-시진핑은"),
    ("트럼프-시진핑를", "트럼프-시진핑을"),
    ("트럼프-시진핑와", "트럼프-시진핑과"),
)
ALLOWED_ENGLISH_TOKENS = {
    "ADR",
    "AI",
    "CBOE",
    "CEO",
    "CFO",
    "CPI",
    "DOW",
    "DXY",
    "EPS",
    "ETF",
    "FOMC",
    "FX",
    "GAAP",
    "GDP",
    "IPO",
    "ISM",
    "M&A",
    "NASDAQ",
    "NON-GAAP",
    "PCE",
    "PMI",
    "QOQ",
    "S&P",
    "SEC",
    "VIX",
    "WTI",
    "YOY",
}
SUMMARY_COMPANY_ALIASES = {
    "dell": "DELL",
    "micron": "MU",
    "micron technology": "MU",
    "nvidia": "NVDA",
}
TICKER_PARTICLE_CORRECTIONS = (
    ("MU이", "MU가"),
    ("MU은", "MU는"),
    ("MU을", "MU를"),
    ("MU과", "MU와"),
)


def _subject_ticker(row: dict[str, Any] | None) -> str:
    if not isinstance(row, dict):
        return ""
    subject = str(row.get("subject") or "").strip().upper()
    if subject.isalpha() and 1 <= len(subject) <= 6:
        return subject
    return ""


def _company_aliases_for_ticker(ticker: str) -> list[str]:
    if not ticker:
        return []
    aliases = [
        alias
        for alias, alias_ticker in COMPANY_ALIASES.items()
        if alias_ticker.upper() == ticker
    ]
    return sorted(set(aliases), key=lambda value: (-len(value), value))


def _company_alias_pattern(alias: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])",
        re.IGNORECASE,
    )


def _normalize_company_mentions(summary: str, row: dict[str, Any] | None) -> str:
    ticker = _subject_ticker(row)
    aliases = _company_aliases_for_ticker(ticker)
    if not aliases:
        aliases = sorted(SUMMARY_COMPANY_ALIASES, key=lambda value: (-len(value), value))
    for alias in aliases:
        alias_ticker = ticker or SUMMARY_COMPANY_ALIASES.get(alias, "")
        if not alias_ticker:
            continue
        summary = _company_alias_pattern(alias).sub(alias_ticker, summary)
    for wrong, correct in TICKER_PARTICLE_CORRECTIONS:
        summary = summary.replace(wrong, correct)
    summary = DUPLICATE_TICKER_SLASH_RE.sub(r"\1", summary)
    return summary


def _normalize_geopolitical_terms(summary: str) -> str:
    for pattern, replacement in GEOPOLITICAL_TRANSLATIONS:
        summary = pattern.sub(replacement, summary)
    for wrong, correct in KOREAN_PARTICLE_CORRECTIONS:
        summary = summary.replace(wrong, correct)
    return summary

SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary_ko": {"type": "string"},
        "market_marker": {"type": "string", "enum": ["red", "green", "none"]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "basis": {"type": "string", "enum": ["body", "snippet", "title"]},
        "reason_ko": {"type": "string"},
        "source_quote": {"type": "string"},
    },
    "required": [
        "summary_ko",
        "market_marker",
        "confidence",
        "basis",
        "reason_ko",
        "source_quote",
    ],
}

EDITORIAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "decision": {"type": "string", "enum": ["send", "drop", "hold"]},
        "grade": {"type": "string", "enum": ["A", "B", "C"]},
        "drop_reason": {
            "type": "string",
            "enum": [
                "",
                "soft_analysis",
                "stale",
                "weak_source",
                "duplicate",
                "not_market_moving",
                "unsupported_claim",
                "evidence_conflict",
                "other",
            ],
        },
        "summary_ko": {"type": "string"},
        "market_marker": {"type": "string", "enum": ["red", "green", "none"]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "basis": {"type": "string", "enum": ["body", "snippet", "title"]},
        "reason_ko": {"type": "string"},
        "source_hint": {"type": "string"},
        "risk_flags": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "decision",
        "grade",
        "drop_reason",
        "summary_ko",
        "market_marker",
        "confidence",
        "basis",
        "reason_ko",
        "source_hint",
        "risk_flags",
    ],
}

THEME_CLAIM_ATOM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "text": {"type": "string"},
        "evidence_id": {"type": "string"},
    },
    "required": ["text", "evidence_id"],
}

THEME_EDITORIAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "decision": {"type": "string", "enum": ["send", "drop", "hold"]},
        "grade": {"type": "string", "enum": ["A", "B", "C"]},
        "drop_reason": {
            "type": "string",
            "enum": [
                "",
                "soft_analysis",
                "stale",
                "weak_source",
                "duplicate",
                "not_market_moving",
                "unsupported_claim",
                "evidence_conflict",
                "trusted_rescue_required",
                "preview_only",
                "other",
            ],
        },
        "summary_ko": {"type": "string"},
        "market_marker": {"type": "string", "enum": ["red", "green", "none"]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "basis": {"type": "string", "enum": ["body", "snippet", "title"]},
        "reason_ko": {"type": "string"},
        "source_hint": {"type": "string"},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
        "claim_atoms": {"type": "array", "items": THEME_CLAIM_ATOM_SCHEMA},
        "risk_flags": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "decision",
        "grade",
        "drop_reason",
        "summary_ko",
        "market_marker",
        "confidence",
        "basis",
        "reason_ko",
        "source_hint",
        "evidence_ids",
        "claim_atoms",
        "risk_flags",
    ],
}

DISCOVERY_QUERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "extra_queries": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "query": {"type": "string"},
                    "category": {
                        "type": "string",
                        "enum": [
                            "GEO",
                            "EARN",
                            "MA",
                            "STRAT",
                            "MOVE",
                            "ANAL",
                            "MACRO",
                        ],
                    },
                    "reason": {"type": "string"},
                    "max_results": {"type": "integer"},
                },
                "required": ["query", "category", "reason", "max_results"],
            },
        }
    },
    "required": ["extra_queries"],
}


class LLMClient(Protocol):
    def create_annotation(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class OpenAIResponsesClient:
    api_key: str
    model: str = DEFAULT_LLM_MODEL
    timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS
    api_url: str = OPENAI_RESPONSES_URL

    def _create_json(
        self,
        *,
        payload: dict[str, Any],
        schema: dict[str, Any],
        schema_name: str,
        system_prompt: str,
    ) -> dict[str, Any]:
        body = {
            "model": self.model,
            "store": False,
            "input": [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                },
                "verbosity": "low",
            },
        }
        req = request.Request(
            self.api_url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"OpenAI Responses HTTP {exc.code}: {detail}") from exc
        except (error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"OpenAI Responses request failed: {exc}") from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("OpenAI Responses returned invalid JSON") from exc
        text = _extract_response_text(data)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError("OpenAI Responses output was not JSON") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("OpenAI Responses output was not an object")
        return parsed

    def create_annotation(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._create_json(
            payload=payload,
            schema=SUMMARY_SCHEMA,
            schema_name="news_summary_annotation",
            system_prompt=(
                "You summarize market-moving US equity news for a Korean "
                "investor. Use only the provided evidence. Do not add facts, "
                "numbers, dates, companies, or causal claims that are not in "
                "the evidence. summary_ko must be Korean-only except stock "
                "tickers and standard market acronyms such as AI, EPS, WTI, "
                "DXY, VIX, CPI, FOMC. Do not include source names or source "
                "prefixes such as Reuters:, NYT:, CNBC:, Bloomberg:. "
                "For company names, use the event.subject ticker or a "
                "Korean company name; do not spell raw English company "
                "names such as Intel, Microsoft, Nvidia, or Boeing in "
                "summary_ko. "
                "Translate people, countries, meetings, wars, and policies "
                "into Korean, for example Trump-Xi -> 트럼프-시진핑, Iran -> "
                "이란, Taiwan -> 대만, US -> 미국. When using numbers, "
                "copy numeric tokens exactly from the evidence, including "
                "$ signs, decimals, %, B/M/K suffixes, and ranges. Do not "
                "convert $44.500B into Korean units like 445억달러."
            ),
        )

    def create_editorial(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._create_json(
            payload=payload,
            schema=EDITORIAL_SCHEMA,
            schema_name="news_editorial_decision",
            system_prompt=(
                "You are the final editor for a Korean US-market news alert. "
                "Use only the supplied evidence and deterministic metadata. "
                "Your job is to decide whether this candidate should be sent. "
                "Send only concrete, fresh, market-relevant events. Drop soft "
                "analysis, old news repackaged as new, weak-source speculation, "
                "unsupported earnings/price-target claims, and items whose "
                "market impact is unclear. Do not add facts, numbers, dates, "
                "companies, or causal claims not present in evidence. If you "
                "send, produce one specific Korean bulletin line. Korean-only "
                "except stock tickers and standard market acronyms such as AI, "
                "EPS, WTI, DXY, VIX, CPI, FOMC. Use the event.subject ticker "
                "for company names. Copy numeric tokens exactly from evidence. "
                "source_hint may contain a compact source/date label such as "
                "Reuters 5/18; summary_ko must not contain source names."
            ),
        )

    def create_theme_editorial(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._create_json(
            payload=payload,
            schema=THEME_EDITORIAL_SCHEMA,
            schema_name="market_theme_editorial_decision",
            system_prompt=(
                "You are the theme editor for a Korean US-market Telegram digest. "
                "You may synthesize a market theme only from the supplied evidence "
                "items and claim atoms. Do not add facts, numbers, dates, causes, "
                "companies, sectors, or price moves that are absent from evidence. "
                "Every material claim in summary_ko must be represented by a "
                "claim_atoms entry tied to a supplied evidence_id. Send only when "
                "the theme is fresh, market-relevant, and supported by multiple "
                "items or a trusted hard-event source. If trusted rescue is "
                "required but absent, hold or drop. Korean-only except tickers "
                "and standard market acronyms such as AI, EPS, WTI, DXY, VIX, "
                "CPI, FOMC. Copy numeric tokens exactly from evidence. "
                "summary_ko must not contain source names or URLs; source_hint "
                "may contain compact source labels. Avoid generic theme endings "
                "such as '부각', '흐름', '관련 이슈', or '압박 신호'; write the "
                "specific evidence facts instead."
            ),
        )

    def create_discovery_queries(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._create_json(
            payload=payload,
            schema=DISCOVERY_QUERY_SCHEMA,
            schema_name="news_discovery_query_plan",
            system_prompt=(
                "You are a search-query planner for a Korean US-market news "
                "scanner. Your job is not to summarize news and not to invent "
                "facts. Suggest only extra Brave News search queries that may "
                "cover market-moving stories missed by the fixed source set. "
                "Use only the supplied titles, category counts, source samples, "
                "and recent delivery context as clues. Prefer concrete query terms for "
                "strategic AI infrastructure, megacap capex/JV, semiconductor "
                "supply chain, sector pressure, macro shock, policy, earnings, "
                "or M&A. Do not include URLs. Do not include claims that are not "
                "already implied by the clues. Return an empty list when no "
                "extra search axis is justified."
            ),
        )


def _extract_response_text(data: dict[str, Any]) -> str:
    direct = data.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    for item in data.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
    raise RuntimeError("OpenAI Responses output text missing")


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())


def _limited(value: object, max_chars: int) -> str:
    text = _clean(value)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _evidence_text(row: dict[str, Any]) -> str:
    parts = [
        _clean(row.get("title")),
        _clean(row.get("body_text")),
    ]
    evidence_items = row.get("evidence_items")
    if isinstance(evidence_items, list):
        for item in evidence_items:
            if isinstance(item, dict):
                parts.append(_clean(item.get("summary")))
                parts.append(_clean(item.get("title")))
                parts.append(_clean(item.get("body_text")))
    return "\n".join(part for part in parts if part)


def _domain_from_url(url: object) -> str:
    try:
        host = urlsplit(str(url or "")).netloc.lower()
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host.split("@")[-1].split(":")[0]


def _theme_evidence_domain(item: dict[str, Any]) -> str:
    domain = _clean(item.get("domain")).lower()
    if domain:
        return domain[4:] if domain.startswith("www.") else domain
    return _domain_from_url(item.get("url") or item.get("canonical_url"))


def _theme_evidence_text(item: dict[str, Any]) -> str:
    return " ".join(
        _clean(item.get(key))
        for key in ("title", "summary", "body_text")
        if _clean(item.get(key))
    )


def _domain_matches_any(domain: str, allowed_domains: tuple[str, ...]) -> bool:
    return any(
        domain == allowed or domain.endswith(f".{allowed}")
        for allowed in allowed_domains
    )


def _evidence_items_from_row(row: dict[str, Any]) -> list[dict[str, Any]]:
    items = [item for item in row.get("evidence_items", []) if isinstance(item, dict)]
    items.append(
        {
            "source": row.get("source"),
            "title": row.get("title"),
            "url": row.get("url"),
        }
    )
    return items


def _source_hint_supported_by_items(
    source_hint: str,
    items: list[dict[str, Any]],
) -> bool:
    hint = _clean(source_hint)
    if not hint:
        return True
    domains = {_theme_evidence_domain(item) for item in items}
    domains.discard("")
    evidence_text = " ".join(
        _clean(value)
        for item in items
        for value in (
            item.get("source"),
            item.get("provider"),
            item.get("domain"),
            item.get("url"),
            item.get("canonical_url"),
            item.get("title"),
        )
    ).lower()
    for pattern, allowed_domains in SOURCE_HINT_DOMAIN_RULES:
        if not pattern.search(hint):
            continue
        if any(_domain_matches_any(domain, allowed_domains) for domain in domains):
            continue
        if any(allowed in evidence_text for allowed in allowed_domains):
            continue
        return False
    return True


def _source_hint_is_generic_provider(source_hint: str) -> bool:
    return bool(GENERIC_PROVIDER_SOURCE_HINT_RE.fullmatch(_clean(source_hint)))


def _theme_amount_tokens(text: str) -> set[str]:
    return {
        "".join(match.group(0).lower().split())
        for match in THEME_MATERIAL_AMOUNT_RE.finditer(text)
    }


def _selected_theme_evidence(
    *,
    candidate: dict[str, Any],
    evidence_ids: list[str],
) -> list[dict[str, Any]]:
    wanted = set(evidence_ids)
    items = [
        item
        for item in candidate.get("evidence", [])
        if isinstance(item, dict)
        and str(item.get("candidate_id") or "") in wanted
    ]
    if items:
        return items
    return [item for item in candidate.get("evidence", []) if isinstance(item, dict)]


def _ai_infra_theme_gate_reason(
    *,
    editorial: dict[str, Any],
    candidate: dict[str, Any],
    evidence_ids: list[str],
) -> str:
    if candidate.get("theme_key") != AI_INFRA_THEME_KEY:
        return ""
    selected = _selected_theme_evidence(candidate=candidate, evidence_ids=evidence_ids)
    trusted_items = [
        item
        for item in selected
        if _theme_evidence_domain(item) in TRUSTED_THEME_DOMAINS
    ]
    if not trusted_items:
        return "ai_infra_needs_trusted_evidence"
    if WEAK_AI_INFRA_SOURCE_HINT_RE.search(_clean(editorial.get("source_hint"))):
        return "ai_infra_weak_source_hint"
    summary_amounts = _theme_amount_tokens(_clean(editorial.get("summary_ko")))
    if summary_amounts:
        trusted_text = "\n".join(_theme_evidence_text(item) for item in trusted_items)
        trusted_amounts = _theme_amount_tokens(trusted_text)
        if not summary_amounts <= trusted_amounts:
            return "ai_infra_amount_needs_trusted_evidence"
    return ""


def evidence_hash(row: dict[str, Any]) -> str:
    payload = {
        "event_signature": row.get("event_signature"),
        "title": row.get("title"),
        "price_reaction": row.get("price_reaction") or {},
        "body_text": _limited(row.get("body_text"), 2000),
        "evidence_items": [
            {
                "title": _limited(item.get("title"), 300),
                "summary": _limited(item.get("summary"), 700),
                "body_text": _limited(item.get("body_text"), 1200),
                "source": item.get("source"),
                "provider": item.get("provider"),
            }
            for item in row.get("evidence_items", [])
            if isinstance(item, dict)
        ],
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return sha256(raw.encode("utf-8")).hexdigest()


def build_annotation_payload(row: dict[str, Any]) -> dict[str, Any]:
    evidence_items = []
    for item in row.get("evidence_items", []):
        if not isinstance(item, dict):
            continue
        evidence_items.append(
            {
                "source": _limited(item.get("source"), 80),
                "provider": _limited(item.get("provider"), 40),
                "title": _limited(item.get("title"), 300),
                "summary": _limited(item.get("summary"), 700),
                "body_text": _limited(item.get("body_text"), 1200),
                "body_fetch_status": (
                    item.get("body_fetch", {}).get("status")
                    if isinstance(item.get("body_fetch"), dict)
                    else ""
                ),
            }
        )
    subject_ticker = _subject_ticker(row)
    subject_aliases = _company_aliases_for_ticker(subject_ticker)
    earnings_contract = row.get("earnings_fact_contract")
    return {
        "task": (
            "Return one Korean bulletin line for Telegram. Keep it specific. "
            "Avoid generic phrases like '관련 이벤트 진행'."
        ),
        "event": {
            "event_signature": row.get("event_signature"),
            "merged_event_signatures": row.get("merged_event_signatures") or [],
            "event_type": row.get("event_type"),
            "subject": row.get("subject"),
            "action": row.get("action"),
            "merged_actions": row.get("merged_actions") or [],
            "score": row.get("score"),
            "evidence_count": row.get("evidence_count"),
            "providers": row.get("providers") or [],
            "source_tier": row.get("source_tier") or "",
            "metadata": row.get("event_metadata") or {},
            "price_reaction": row.get("price_reaction") or {},
            "earnings_fact_contract": earnings_contract
            if isinstance(earnings_contract, dict)
            else {},
        },
        "title": _limited(row.get("title"), 300),
        "body_text": _limited(row.get("body_text"), 1800),
        "evidence_items": evidence_items[:6],
        "output_rules": {
            "summary_ko": (
                "Korean, 45-150 chars, no URL, no source prefix, no source name, "
                "no raw English except tickers/market acronyms. Use event.subject "
                "ticker or Korean company names instead of raw English company "
                "names. Copy numeric tokens exactly from evidence; do not convert "
                "B/M/K or percentages into Korean numeric units. For earnings "
                "events, preserve the most material actual revenue, EPS, "
                "guidance, or buyback numbers when present. If "
                "event.earnings_fact_contract.facts is supplied, treat those "
                "facts as required numeric anchors."
            ),
            "market_marker": (
                "red if clearly negative, green if clearly positive, else none. "
                "For company events, use the economic news polarity first; treat "
                "price_reaction as decisive only when the move is material "
                "(roughly 2%+ or explicitly cited as the market reaction). If "
                "positive company news conflicts with a small price move, keep "
                "green; if the conflict is material, use none."
            ),
            "confidence": "high only when evidence is concrete and non-conflicting.",
            "source_quote": "short evidence phrase copied or translated from supplied evidence.",
        },
        "company_name_rule": {
            "event_subject_ticker": subject_ticker,
            "raw_english_aliases_to_avoid": subject_aliases[:8],
            "preferred_company_prefix": subject_ticker,
        },
    }


def build_editorial_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = build_annotation_payload(row)
    contract = row.get("evidence_contract")
    payload["task"] = (
        "Decide if this candidate should be sent in the live Telegram digest. "
        "Return decision=send only for a concrete fresh market-moving event."
    )
    payload["deterministic_decision"] = {
        "decision": row.get("decision"),
        "grade": row.get("grade"),
        "reason": row.get("reason"),
        "send_worthy_reason": row.get("send_worthy_reason"),
        "event_quality": row.get("event_quality"),
        "hard_event_reason": row.get("hard_event_reason"),
        "soft_analysis_reason": row.get("soft_analysis_reason"),
        "risk_flags": row.get("risk_flags") or [],
        "score_reasons": row.get("score_reasons") or [],
        "extractor_reasons": row.get("extractor_reasons") or [],
    }
    payload["verification"] = row.get("verification") or {}
    payload["evidence_contract"] = contract if isinstance(contract, dict) else {}
    payload["editor_rules"] = {
        "send": (
            "fresh concrete event with enough evidence and clear market relevance"
        ),
        "drop": (
            "soft analysis, stale/repackaged item, weak source, duplicate, "
            "unsupported claim, or not market-moving"
        ),
        "hold": (
            "potentially important but evidence is too ambiguous for automated send"
        ),
        "grade": "A only for immediate market-moving event; B for useful watch item; C for drop/hold.",
        "source_hint": (
            "Use a named outlet or official source only, e.g. Reuters, CNBC, "
            "Bloomberg, Federal Reserve. Never use provider names such as "
            "Brave, Google RSS, RSS, source, web, or provider."
        ),
    }
    return payload


def normalize_annotation(
    annotation: dict[str, Any],
    *,
    row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = dict(annotation)
    summary = _clean(normalized.get("summary_ko"))
    previous = None
    while summary and summary != previous:
        previous = summary
        summary = SOURCE_PREFIX_RE.sub("", summary).strip()
    summary = AMOUNT_WORD_RE.sub(
        lambda match: f"{match.group(1).replace(' ', '')}"
        + {"trillion": "T", "billion": "B", "million": "M", "thousand": "K"}[
            match.group(2).lower()
        ],
        summary,
    )
    summary = TICKER_PREFIX_RE.sub(r"\1, ", summary)
    summary = _normalize_geopolitical_terms(summary)
    summary = _normalize_company_mentions(summary, row)
    normalized["summary_ko"] = summary
    return normalized


def normalize_editorial(
    editorial: dict[str, Any],
    *,
    row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = normalize_annotation(editorial, row=row)
    source_hint = _clean(normalized.get("source_hint"))
    source_hint = source_hint.replace("http://", "").replace("https://", "")
    normalized["source_hint"] = source_hint[:80]
    risk_flags = normalized.get("risk_flags")
    if not isinstance(risk_flags, list):
        risk_flags = []
    normalized["risk_flags"] = [
        _clean(flag)[:60] for flag in risk_flags[:8] if _clean(flag)
    ]
    return normalized


def _raw_english_token(summary: str) -> str | None:
    for token in ENGLISH_TOKEN_RE.findall(summary):
        normalized = token.strip(".-")
        if not normalized:
            continue
        upper = normalized.upper()
        if upper in ALLOWED_ENGLISH_TOKENS:
            continue
        if normalized.isupper() and 1 <= len(normalized) <= 6:
            continue
        return token
    return None


def validate_annotation(
    annotation: dict[str, Any],
    *,
    row: dict[str, Any],
) -> tuple[bool, str]:
    summary = _clean(annotation.get("summary_ko"))
    if len(summary) < 16:
        return False, "summary_too_short"
    if len(summary) > 180:
        return False, "summary_too_long"
    if not HANGUL_RE.search(summary):
        return False, "summary_not_korean"
    if "http://" in summary or "https://" in summary:
        return False, "summary_has_url"
    if SOURCE_PREFIX_RE.search(summary) or SOURCE_NAME_RE.search(summary):
        return False, "summary_has_source_name"
    if _raw_english_token(summary):
        return False, "summary_has_raw_english"
    if GENERIC_SUMMARY_RE.search(summary):
        return False, "summary_too_generic"

    if annotation.get("market_marker") not in {"red", "green", "none"}:
        return False, "invalid_market_marker"
    if annotation.get("confidence") not in {"high", "medium", "low"}:
        return False, "invalid_confidence"
    if annotation.get("basis") not in {"body", "snippet", "title"}:
        return False, "invalid_basis"

    evidence = _evidence_text(row)
    for number in NUMBER_RE.findall(summary):
        if number not in evidence:
            return False, "summary_number_not_in_evidence"
    return True, ""


def validate_editorial(
    editorial: dict[str, Any],
    *,
    row: dict[str, Any],
) -> tuple[bool, str]:
    decision = editorial.get("decision")
    if decision not in {"send", "drop", "hold"}:
        return False, "invalid_decision"
    grade = editorial.get("grade")
    if grade not in {"A", "B", "C"}:
        return False, "invalid_grade"
    drop_reason = editorial.get("drop_reason")
    allowed_drop_reasons = set(EDITORIAL_SCHEMA["properties"]["drop_reason"]["enum"])
    if drop_reason not in allowed_drop_reasons:
        return False, "invalid_drop_reason"
    if decision == "drop" and not drop_reason:
        return False, "drop_reason_required"
    if decision == "send" and drop_reason:
        return False, "send_has_drop_reason"
    if decision == "send" and grade not in {"A", "B"}:
        return False, "send_grade_not_deliverable"
    if decision in {"drop", "hold"} and grade == "A":
        return False, "non_send_grade_a"

    reason = _clean(editorial.get("reason_ko"))
    if len(reason) < 6 or not HANGUL_RE.search(reason):
        return False, "reason_ko_invalid"
    source_hint = _clean(editorial.get("source_hint"))
    if "http://" in source_hint or "https://" in source_hint:
        return False, "source_hint_has_url"
    if len(source_hint) > 80:
        return False, "source_hint_too_long"
    if _source_hint_is_generic_provider(source_hint):
        return False, "source_hint_generic_provider"
    if not _source_hint_supported_by_items(
        source_hint,
        _evidence_items_from_row(row),
    ):
        return False, "source_hint_not_in_evidence"
    risk_flags = editorial.get("risk_flags")
    if not isinstance(risk_flags, list):
        return False, "invalid_risk_flags"

    if decision != "send":
        return True, ""

    return validate_annotation(editorial, row=row)


def build_theme_editorial_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    evidence_items = []
    for item in candidate.get("evidence", []):
        if not isinstance(item, dict):
            continue
        evidence_items.append(
            {
                "evidence_id": _limited(item.get("candidate_id"), 80),
                "source": _limited(item.get("source"), 80),
                "provider": _limited(item.get("provider"), 40),
                "domain": _limited(item.get("domain"), 80),
                "title": _limited(item.get("title"), 300),
                "summary": _limited(item.get("summary"), 700),
                "body_text": _limited(item.get("body_text"), 1200),
                "published_at": _limited(item.get("published_at"), 80),
            }
        )
    return {
        "task": (
            "Decide if this synthesized theme should be sent in the live digest. "
            "Return decision=send only when the summary is directly supported."
        ),
        "theme": {
            "theme_id": candidate.get("id"),
            "theme_type": candidate.get("theme_type"),
            "theme_key": candidate.get("theme_key"),
            "subject": candidate.get("subject"),
            "action": candidate.get("action"),
            "suggested_grade": candidate.get("grade"),
            "suggested_market_marker": candidate.get("market_marker"),
            "source_tier": candidate.get("source_tier"),
            "requires_verification": bool(candidate.get("requires_verification")),
            "requires_trusted_rescue": bool(
                candidate.get("requires_trusted_rescue")
            ),
            "preview_only": bool(candidate.get("preview_only")),
            "summary_seed": candidate.get("summary_seed"),
        },
        "evidence_items": evidence_items[:8],
        "candidate_claim_atoms": candidate.get("claim_atoms") or [],
        "output_rules": {
            "decision": (
                "send only for a supported, fresh, market-relevant theme. "
                "hold/drop if source quality or evidence linkage is weak."
            ),
            "evidence_ids": "Use only evidence_id values present in evidence_items.",
            "claim_atoms": (
                "Each material claim in summary_ko must map to one evidence_id. "
                "Do not invent claim atoms."
            ),
            "summary_ko": (
                "Korean, 45-170 chars, no URL, no source name, no raw English "
                "except tickers/market acronyms. Copy numeric tokens exactly. "
                "Use concrete evidence facts; do not end with generic "
                "'부각/흐름/관련 이슈/압박 신호' wording."
            ),
        },
    }


def _theme_validation_row(candidate: dict[str, Any]) -> dict[str, Any]:
    evidence_items = []
    for item in candidate.get("evidence", []):
        if isinstance(item, dict):
            evidence_items.append(
                {
                    "title": item.get("title"),
                    "summary": item.get("summary"),
                    "body_text": item.get("body_text"),
                    "source": item.get("source"),
                    "provider": item.get("provider"),
                }
            )
    return {
        "title": candidate.get("summary_seed") or "",
        "subject": candidate.get("subject") or "",
        "evidence_items": evidence_items,
        "body_text": "",
    }


def normalize_theme_editorial(editorial: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_editorial(editorial)
    evidence_ids = normalized.get("evidence_ids")
    if not isinstance(evidence_ids, list):
        evidence_ids = []
    normalized["evidence_ids"] = [
        _clean(value)[:100] for value in evidence_ids[:8] if _clean(value)
    ]
    claim_atoms = normalized.get("claim_atoms")
    if not isinstance(claim_atoms, list):
        claim_atoms = []
    cleaned_atoms = []
    for atom in claim_atoms[:12]:
        if not isinstance(atom, dict):
            continue
        text = _clean(atom.get("text"))[:160]
        evidence_id = _clean(atom.get("evidence_id"))[:100]
        if text and evidence_id:
            cleaned_atoms.append({"text": text, "evidence_id": evidence_id})
    normalized["claim_atoms"] = cleaned_atoms
    return normalized


def validate_theme_editorial(
    editorial: dict[str, Any],
    *,
    candidate: dict[str, Any],
) -> tuple[bool, str]:
    decision = editorial.get("decision")
    if decision not in {"send", "drop", "hold"}:
        return False, "invalid_decision"
    grade = editorial.get("grade")
    if grade not in {"A", "B", "C"}:
        return False, "invalid_grade"
    allowed_drop_reasons = set(
        THEME_EDITORIAL_SCHEMA["properties"]["drop_reason"]["enum"]
    )
    drop_reason = editorial.get("drop_reason")
    if drop_reason not in allowed_drop_reasons:
        return False, "invalid_drop_reason"
    if decision == "drop" and not drop_reason:
        return False, "drop_reason_required"
    if decision == "send" and drop_reason:
        return False, "send_has_drop_reason"
    if decision == "send" and grade not in {"A", "B"}:
        return False, "send_grade_not_deliverable"
    if decision in {"drop", "hold"} and grade == "A":
        return False, "non_send_grade_a"

    reason = _clean(editorial.get("reason_ko"))
    if len(reason) < 6 or not HANGUL_RE.search(reason):
        return False, "reason_ko_invalid"
    source_hint = _clean(editorial.get("source_hint"))
    if "http://" in source_hint or "https://" in source_hint:
        return False, "source_hint_has_url"
    if len(source_hint) > 80:
        return False, "source_hint_too_long"
    if _source_hint_is_generic_provider(source_hint):
        return False, "source_hint_generic_provider"
    if not _source_hint_supported_by_items(
        source_hint,
        [item for item in candidate.get("evidence", []) if isinstance(item, dict)],
    ):
        return False, "source_hint_not_in_evidence"
    risk_flags = editorial.get("risk_flags")
    if not isinstance(risk_flags, list):
        return False, "invalid_risk_flags"

    candidate_evidence_ids = {
        str(item.get("candidate_id") or "")
        for item in candidate.get("evidence", [])
        if isinstance(item, dict) and str(item.get("candidate_id") or "")
    }
    evidence_ids = [
        str(value or "") for value in editorial.get("evidence_ids", []) if str(value or "")
    ]
    if any(evidence_id not in candidate_evidence_ids for evidence_id in evidence_ids):
        return False, "unknown_evidence_id"
    claim_atoms = editorial.get("claim_atoms")
    if not isinstance(claim_atoms, list):
        return False, "invalid_claim_atoms"
    for atom in claim_atoms:
        if not isinstance(atom, dict):
            return False, "invalid_claim_atom"
        if str(atom.get("evidence_id") or "") not in candidate_evidence_ids:
            return False, "claim_atom_unknown_evidence_id"
        if len(_clean(atom.get("text"))) < 4:
            return False, "claim_atom_too_short"

    if decision != "send":
        return True, ""
    if bool(candidate.get("requires_trusted_rescue")):
        return False, "trusted_rescue_required"
    if bool(candidate.get("preview_only")):
        return False, "preview_only"
    if not evidence_ids:
        return False, "send_missing_evidence_ids"
    if not claim_atoms:
        return False, "send_missing_claim_atoms"
    if (
        candidate.get("theme_type") in {"sector_pressure", "sector_rally"}
        and len(set(evidence_ids)) < 2
    ):
        return False, "sector_theme_needs_multiple_evidence"
    ai_infra_reason = _ai_infra_theme_gate_reason(
        editorial=editorial,
        candidate=candidate,
        evidence_ids=evidence_ids,
    )
    if ai_infra_reason:
        return False, ai_infra_reason
    if GENERIC_THEME_SUMMARY_RE.search(_clean(editorial.get("summary_ko"))):
        return False, "theme_summary_too_generic"

    return validate_annotation(editorial, row=_theme_validation_row(candidate))


def summary_annotation_from_editorial(editorial: dict[str, Any]) -> dict[str, Any]:
    return {
        "summary_ko": editorial.get("summary_ko"),
        "market_marker": editorial.get("market_marker"),
        "confidence": editorial.get("confidence"),
        "basis": editorial.get("basis"),
        "reason_ko": editorial.get("reason_ko"),
        "source_quote": editorial.get("source_hint") or "",
        "source_hint": editorial.get("source_hint") or "",
        "_from_editorial": True,
    }


def _annotation_id(
    *,
    run_id: str,
    event_signature: str,
    annotation_type: str,
    model: str,
    prompt_version: str,
    evidence_hash_value: str,
) -> str:
    raw = "|".join(
        [
            run_id,
            event_signature,
            annotation_type,
            model,
            prompt_version,
            evidence_hash_value,
        ]
    )
    return sha256(raw.encode("utf-8")).hexdigest()


def _record(
    *,
    row: dict[str, Any],
    annotation_type: str = ANNOTATION_TYPE_SUMMARY,
    model: str,
    prompt_version: str,
    evidence_hash_value: str,
    status: str,
    payload: dict[str, Any],
    error_text: str | None = None,
) -> dict[str, Any]:
    run_id = str(row.get("run_id") or "")
    event_signature = str(row.get("event_signature") or "")
    return {
        "id": _annotation_id(
            run_id=run_id,
            event_signature=event_signature,
            annotation_type=annotation_type,
            model=model,
            prompt_version=prompt_version,
            evidence_hash_value=evidence_hash_value,
        ),
        "run_id": run_id,
        "event_signature": event_signature,
        "annotation_type": annotation_type,
        "provider": PROVIDER_OPENAI,
        "model": model,
        "prompt_version": prompt_version,
        "evidence_hash": evidence_hash_value,
        "status": status,
        "payload": payload,
        "error": error_text,
        "created_at": datetime.now(ZoneInfo(KST_TZ)).isoformat(),
    }


def _create_editorial_decision(
    client: LLMClient,
    payload: dict[str, Any],
) -> dict[str, Any]:
    create_editorial = getattr(client, "create_editorial", None)
    if callable(create_editorial):
        result = create_editorial(payload)
    else:
        result = client.create_annotation(payload)
    if not isinstance(result, dict):
        raise RuntimeError("LLM editorial output was not an object")
    return result


def _create_theme_editorial_decision(
    client: LLMClient,
    payload: dict[str, Any],
) -> dict[str, Any]:
    create_theme_editorial = getattr(client, "create_theme_editorial", None)
    if callable(create_theme_editorial):
        result = create_theme_editorial(payload)
    else:
        create_editorial = getattr(client, "create_editorial", None)
        if callable(create_editorial):
            result = create_editorial(payload)
        else:
            result = client.create_annotation(payload)
    if not isinstance(result, dict):
        raise RuntimeError("LLM theme editorial output was not an object")
    return result


def edit_theme_candidates(
    candidates: list[dict[str, Any]],
    *,
    enabled: bool,
    api_key: str | None,
    model: str = DEFAULT_LLM_MODEL,
    timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS,
    client: LLMClient | None = None,
) -> dict[str, Any]:
    if not enabled:
        return {"status": "disabled", "requested": len(candidates), "attempted": 0}
    if not candidates:
        return {"status": "ok", "requested": 0, "attempted": 0}
    if not api_key and client is None:
        return {
            "status": "skipped_no_api_key",
            "requested": len(candidates),
            "attempted": 0,
        }

    llm_client = client or OpenAIResponsesClient(
        api_key=str(api_key),
        model=model,
        timeout_seconds=timeout_seconds,
    )
    attempted = 0
    accepted = 0
    rejected = 0
    errors = 0
    decision_counts = {"send": 0, "drop": 0, "hold": 0}
    validation_errors: dict[str, int] = {}
    error_candidates: list[dict[str, str]] = []
    for candidate in candidates:
        attempted += 1
        try:
            payload = normalize_theme_editorial(
                _create_theme_editorial_decision(
                    llm_client,
                    build_theme_editorial_payload(candidate),
                )
            )
            valid, reason = validate_theme_editorial(payload, candidate=candidate)
            if valid:
                accepted += 1
                decision = str(payload["decision"])
                decision_counts[decision] += 1
                candidate["llm_editorial"] = payload
                if decision == "send":
                    candidate["grade"] = payload["grade"]
                    candidate["llm_annotation"] = summary_annotation_from_editorial(
                        payload
                    )
            else:
                rejected += 1
                validation_errors[reason] = validation_errors.get(reason, 0) + 1
                candidate["llm_editorial"] = dict(payload, validation_error=reason)
        except Exception as exc:
            errors += 1
            error_candidates.append(
                {
                    "theme_key": str(candidate.get("theme_key") or ""),
                    "theme_type": str(candidate.get("theme_type") or ""),
                    "subject": str(candidate.get("subject") or ""),
                    "error": str(exc)[:300],
                }
            )
            candidate["llm_editorial"] = {
                "decision": "hold",
                "grade": "C",
                "drop_reason": "other",
                "summary_ko": "",
                "market_marker": "none",
                "confidence": "low",
                "basis": "title",
                "reason_ko": "테마 편집장 오류",
                "source_hint": "",
                "evidence_ids": [],
                "claim_atoms": [],
                "risk_flags": ["llm_error"],
                "error": str(exc)[:500],
            }
    return {
        "status": "ok" if errors == 0 else "partial_error",
        "requested": len(candidates),
        "attempted": attempted,
        "accepted": accepted,
        "rejected": rejected,
        "errors": errors,
        "decisions": decision_counts,
        "validation_errors": dict(sorted(validation_errors.items())),
        "error_candidates": error_candidates,
        "model": model,
        "prompt_version": THEME_EDITORIAL_PROMPT_VERSION,
    }


def edit_rows(
    rows: list[dict[str, Any]],
    *,
    db_path,
    enabled: bool,
    api_key: str | None,
    model: str = DEFAULT_LLM_MODEL,
    timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS,
    client: LLMClient | None = None,
) -> dict[str, Any]:
    if not enabled:
        return {"status": "disabled", "requested": len(rows), "attempted": 0}
    if not rows:
        return {"status": "ok", "requested": 0, "attempted": 0, "inserted": 0}
    if not api_key and client is None:
        return {
            "status": "skipped_no_api_key",
            "requested": len(rows),
            "attempted": 0,
            "inserted": 0,
        }

    llm_client = client or OpenAIResponsesClient(
        api_key=str(api_key),
        model=model,
        timeout_seconds=timeout_seconds,
    )
    records = []
    attempted = 0
    accepted = 0
    rejected = 0
    errors = 0
    decision_counts = {"send": 0, "drop": 0, "hold": 0}
    for row in rows:
        evidence_hash_value = evidence_hash(row)
        attempted += 1
        try:
            payload = normalize_editorial(
                _create_editorial_decision(
                    llm_client,
                    build_editorial_payload(row),
                ),
                row=row,
            )
            valid, reason = validate_editorial(payload, row=row)
            status = "ok" if valid else "rejected_validation"
            if valid:
                accepted += 1
                decision = str(payload["decision"])
                decision_counts[decision] += 1
                row["llm_editorial"] = payload
                if decision == "send":
                    row["grade"] = payload["grade"]
                    row["llm_annotation"] = summary_annotation_from_editorial(payload)
            else:
                rejected += 1
                payload = dict(payload)
                payload["validation_error"] = reason
            records.append(
                _record(
                    row=row,
                    annotation_type=ANNOTATION_TYPE_EDITORIAL,
                    model=model,
                    prompt_version=EDITORIAL_PROMPT_VERSION,
                    evidence_hash_value=evidence_hash_value,
                    status=status,
                    payload=payload,
                    error_text=None if valid else reason,
                )
            )
        except Exception as exc:
            errors += 1
            records.append(
                _record(
                    row=row,
                    annotation_type=ANNOTATION_TYPE_EDITORIAL,
                    model=model,
                    prompt_version=EDITORIAL_PROMPT_VERSION,
                    evidence_hash_value=evidence_hash_value,
                    status="error",
                    payload={},
                    error_text=str(exc)[:500],
                )
            )

    with connect(db_path) as conn:
        inserted = insert_llm_annotations(conn, annotations=records)
    return {
        "status": "ok" if errors == 0 else "partial_error",
        "requested": len(rows),
        "attempted": attempted,
        "accepted": accepted,
        "rejected": rejected,
        "errors": errors,
        "inserted": inserted,
        "decisions": decision_counts,
        "model": model,
        "prompt_version": EDITORIAL_PROMPT_VERSION,
    }


def annotate_rows(
    rows: list[dict[str, Any]],
    *,
    db_path,
    enabled: bool,
    api_key: str | None,
    model: str = DEFAULT_LLM_MODEL,
    timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS,
    client: LLMClient | None = None,
) -> dict[str, Any]:
    if not enabled:
        return {"status": "disabled", "requested": len(rows), "attempted": 0}
    if not rows:
        return {"status": "ok", "requested": 0, "attempted": 0, "inserted": 0}
    if not api_key and client is None:
        return {
            "status": "skipped_no_api_key",
            "requested": len(rows),
            "attempted": 0,
            "inserted": 0,
        }

    llm_client = client or OpenAIResponsesClient(
        api_key=str(api_key),
        model=model,
        timeout_seconds=timeout_seconds,
    )
    records = []
    attempted = 0
    accepted = 0
    rejected = 0
    errors = 0
    for row in rows:
        evidence_hash_value = evidence_hash(row)
        attempted += 1
        try:
            payload = normalize_annotation(
                llm_client.create_annotation(build_annotation_payload(row)),
                row=row,
            )
            valid, reason = validate_annotation(payload, row=row)
            status = "ok" if valid else "rejected_validation"
            if valid:
                accepted += 1
                row["llm_annotation"] = payload
            else:
                rejected += 1
                payload = dict(payload)
                payload["validation_error"] = reason
                row["llm_annotation_rejected"] = {
                    "reason": reason,
                    "payload": payload,
                }
            records.append(
                _record(
                    row=row,
                    model=model,
                    prompt_version=PROMPT_VERSION,
                    evidence_hash_value=evidence_hash_value,
                    status=status,
                    payload=payload,
                    error_text=None if valid else reason,
                )
            )
        except Exception as exc:
            errors += 1
            records.append(
                _record(
                    row=row,
                    model=model,
                    prompt_version=PROMPT_VERSION,
                    evidence_hash_value=evidence_hash_value,
                    status="error",
                    payload={},
                    error_text=str(exc)[:500],
                )
            )

    with connect(db_path) as conn:
        inserted = insert_llm_annotations(conn, annotations=records)
    return {
        "status": "ok" if errors == 0 else "partial_error",
        "requested": len(rows),
        "attempted": attempted,
        "accepted": accepted,
        "rejected": rejected,
        "errors": errors,
        "inserted": inserted,
        "model": model,
        "prompt_version": PROMPT_VERSION,
    }
