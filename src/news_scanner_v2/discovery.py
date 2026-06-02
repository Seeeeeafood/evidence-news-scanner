from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
import json
import re
from typing import Any, Protocol

from .fetcher import FetchResult
from .llm import (
    DEFAULT_LLM_MODEL,
    DEFAULT_LLM_TIMEOUT_SECONDS,
    DISCOVERY_QUERY_PROMPT_VERSION,
    OpenAIResponsesClient,
)
from .sources import BRAVE_NEWS_ENDPOINT, NewsSource, REQUIRED_CATEGORIES


DEFAULT_MAX_DISCOVERY_QUERIES_PER_RUN = 3
DEFAULT_MAX_DISCOVERY_RESULTS_PER_QUERY = 10
DEFAULT_MAX_SCOUT_QUERIES_PER_RUN = 4
DISCOVERY_SOURCE_PREFIX = "brave-discovery"
SCOUT_SOURCE_PREFIX = "brave-scout"
_QUERY_ALLOWED_RE = re.compile(r"[^A-Za-z0-9가-힣\s\"'()+./&,:-]+")
_SPACE_RE = re.compile(r"\s+")


class DiscoveryQueryClient(Protocol):
    def create_discovery_queries(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...


def _clean(value: object) -> str:
    return _SPACE_RE.sub(" ", str(value or "").strip())


def _limited(value: object, max_chars: int) -> str:
    text = _clean(value)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _sanitize_query(value: object) -> str:
    text = _clean(value)
    text = text.replace("http://", "").replace("https://", "")
    text = _QUERY_ALLOWED_RE.sub(" ", text)
    return _SPACE_RE.sub(" ", text).strip()[:180]


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms)


def _query_fingerprint(query: str) -> str:
    return _sanitize_query(query).lower()


def _scout_query(
    *,
    lane: str,
    category: str,
    query: str,
    reason: str,
    max_results: int,
) -> dict[str, Any]:
    return {
        "lane": lane,
        "category": category,
        "query": _sanitize_query(query),
        "reason": _limited(reason, 180),
        "max_results": max(1, int(max_results)),
    }


def build_high_recall_scout_queries(
    *,
    as_of: datetime,
    hint_texts: list[str] | None = None,
    max_queries: int = DEFAULT_MAX_SCOUT_QUERIES_PER_RUN,
    max_results_per_query: int = DEFAULT_MAX_DISCOVERY_RESULTS_PER_QUERY,
) -> list[dict[str, Any]]:
    """Build deterministic high-recall lane queries before LLM planning.

    These are deliberately recall-oriented. Precision is handled downstream by
    extraction, dispatch, LLM editor, and final publish gates.
    """
    if max_queries <= 0:
        return []
    year = as_of.year
    candidates: list[dict[str, Any]] = []
    hints = " ".join(_limited(text, 500) for text in (hint_texts or [])[:12])

    if (
        hints
        and _contains_any(hints, ("nvidia", "jensen huang", "gtc", "computex"))
        and _contains_any(
            hints,
            (
                "ai pc",
                "gtc",
                "computex",
                "rtx",
                "spark",
                "chip launch",
                "platform launch",
            ),
        )
    ):
        candidates.append(
            _scout_query(
                lane="event_linked_ai_pc_movers",
                category="MOVE",
                query=(
                    'Nvidia "AI PC" Dell HP Intel Qualcomm shares stock '
                    f'premarket movers {year}'
                ),
                reason="breaking hint mentions market-leader AI PC/platform launch; check linked OEM/competitor movers",
                max_results=max_results_per_query,
            )
        )

    if hints and _contains_any(
        hints,
        ("abraham accords", "iran deal", "deal condition", "required sign", "prerequisite"),
    ):
        candidates.append(
            _scout_query(
                lane="breaking_geo_iran_deal_conditions",
                category="GEO",
                query=(
                    '"Abraham Accords" "Iran deal" Trump Saudi Qatar '
                    f'Egypt Jordan Turkey Pakistan May {year}'
                ),
                reason="breaking hint mentions Iran deal conditions or Abraham Accords",
                max_results=max_results_per_query,
            )
        )
    if hints and _contains_any(
        hints,
        ("rubio", "hormuz", "strait of hormuz", "pakistan", "tehran", "toll"),
    ):
        candidates.append(
            _scout_query(
                lane="breaking_geo_policy_speaker",
                category="GEO",
                query=(
                    '"Rubio" "Hormuz" Iran talks Pakistan Tehran toll '
                    f"May {year}"
                ),
                reason="breaking hint mentions Rubio/Hormuz/Pakistan Iran diplomacy",
                max_results=max_results_per_query,
            )
        )
    if hints and _contains_any(
        hints,
        ("kawasaki", "physical ai", "fujitsu", "robot center", "robotics"),
    ):
        candidates.append(
            _scout_query(
                lane="breaking_strat_industrial_ai",
                category="STRAT",
                query=(
                    '"Kawasaki" NVIDIA "physical AI" Microsoft Fujitsu '
                    f'"robot center" May {year}'
                ),
                reason="breaking hint mentions industrial AI partnership",
                max_results=max_results_per_query,
            )
        )

    candidates.extend(
        [
            _scout_query(
                lane="geo_iran_deal_conditions",
                category="GEO",
                query=(
                    '"Abraham Accords" "Iran deal" Trump Saudi Qatar '
                    f'Egypt Jordan Turkey Pakistan May {year}'
                ),
                reason="catch fresh Iran deal prerequisite/condition deltas",
                max_results=max_results_per_query,
            ),
            _scout_query(
                lane="geo_policy_speaker",
                category="GEO",
                query=(
                    '"Rubio" OR "State Department" Iran Hormuz Pakistan Tehran '
                    f'sanctions "peace talks" May {year}'
                ),
                reason="catch policy-speaker quotes that generic geo queries miss",
                max_results=max_results_per_query,
            ),
            _scout_query(
                lane="geo_hormuz_detail",
                category="GEO",
                query=(
                    '"Hormuz toll" OR "Strait of Hormuz" Pakistan Tehran Rubio '
                    f'Iran talks May {year}'
                ),
                reason="catch detailed Hormuz/Iran negotiation updates",
                max_results=max_results_per_query,
            ),
            _scout_query(
                lane="strat_industrial_ai",
                category="STRAT",
                query=(
                    '"physical AI" OR robotics NVIDIA Microsoft Fujitsu '
                    f'Kawasaki "robot center" May {year}'
                ),
                reason="catch non-megacap industrial AI partnerships tied to NVDA/MSFT",
                max_results=max_results_per_query,
            ),
            _scout_query(
                lane="strat_source_specific",
                category="STRAT",
                query=(
                    'Nikkei OR Reuters OR CNBC NVIDIA Microsoft partnership '
                    f'robotics "physical AI" May {year}'
                ),
                reason="catch source-specific strategic deal hits",
                max_results=max_results_per_query,
            ),
        ]
    )

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        query = str(candidate.get("query") or "")
        if len(query) < 8:
            continue
        key = _query_fingerprint(query)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(candidate)
        if len(normalized) >= max_queries:
            break
    return normalized


def _items_by_category(
    fetch_results: tuple[FetchResult, ...],
) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for result in fetch_results:
        category = result.source.category
        for item in result.items[:12]:
            grouped[category].append(
                {
                    "provider": item.provider,
                    "source": item.source,
                    "title": _limited(item.title, 180),
                    "summary": _limited(item.summary, 260),
                    "published_at": _limited(item.published_at, 60),
                }
            )
    return {key: value[:8] for key, value in sorted(grouped.items())}


def build_discovery_payload(
    *,
    fetch_results: tuple[FetchResult, ...],
    as_of: datetime,
    recent_delivery_texts: list[str] | None = None,
    max_queries: int = DEFAULT_MAX_DISCOVERY_QUERIES_PER_RUN,
    max_results_per_query: int = DEFAULT_MAX_DISCOVERY_RESULTS_PER_QUERY,
) -> dict[str, Any]:
    category_counts = Counter()
    provider_counts = Counter()
    for result in fetch_results:
        category_counts[result.source.category] += len(result.items)
        provider_counts[result.source.provider] += 1

    return {
        "task": (
            "Suggest bounded extra Brave News queries for missed market-moving "
            "US-market stories."
        ),
        "as_of": as_of.isoformat(),
        "max_queries": max(0, int(max_queries)),
        "max_results_per_query": max(1, int(max_results_per_query)),
        "required_categories": list(REQUIRED_CATEGORIES),
        "category_item_counts": dict(sorted(category_counts.items())),
        "provider_source_counts": dict(sorted(provider_counts.items())),
        "sample_items_by_category": _items_by_category(fetch_results),
        "recent_delivery_texts": [
            _limited(text, 700) for text in (recent_delivery_texts or [])[:3]
        ],
        "query_guidance": {
            "prefer": [
                "AI infrastructure JV/capex/cloud compute",
                "megacap strategic partnerships",
                "semiconductor supply-chain or sector pressure",
                "macro shock in oil, gold, rates, FX, indices",
                "fresh policy/geopolitical material updates",
                "fresh earnings result, guidance, or M&A",
            ],
            "avoid": [
                "generic stock picks",
                "price prediction articles",
                "queries already covered by category samples",
                "old stories already reflected in recent_delivery_texts",
            ],
        },
    }


def normalize_discovery_plan(
    plan: dict[str, Any],
    *,
    max_queries: int,
    max_results_per_query: int,
) -> list[dict[str, Any]]:
    raw_queries = plan.get("extra_queries")
    if not isinstance(raw_queries, list):
        return []
    allowed_categories = set(REQUIRED_CATEGORIES)
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in raw_queries:
        if not isinstance(raw, dict):
            continue
        category = str(raw.get("category") or "").upper()
        if category not in allowed_categories:
            continue
        query = _sanitize_query(raw.get("query"))
        if len(query) < 8:
            continue
        key = (category, query.lower())
        if key in seen:
            continue
        seen.add(key)
        try:
            max_results = int(raw.get("max_results") or max_results_per_query)
        except (TypeError, ValueError):
            max_results = max_results_per_query
        max_results = max(1, min(max_results, max_results_per_query))
        normalized.append(
            {
                "query": query,
                "category": category,
                "reason": _limited(raw.get("reason"), 180),
                "max_results": max_results,
            }
        )
        if len(normalized) >= max(0, int(max_queries)):
            break
    return normalized


def discovery_sources_from_queries(
    queries: list[dict[str, Any]],
) -> tuple[NewsSource, ...]:
    return _brave_sources_from_queries(queries, source_prefix=DISCOVERY_SOURCE_PREFIX)


def scout_sources_from_queries(
    queries: list[dict[str, Any]],
) -> tuple[NewsSource, ...]:
    return _brave_sources_from_queries(queries, source_prefix=SCOUT_SOURCE_PREFIX)


def _brave_sources_from_queries(
    queries: list[dict[str, Any]],
    *,
    source_prefix: str,
) -> tuple[NewsSource, ...]:
    sources: list[NewsSource] = []
    for index, query in enumerate(queries, start=1):
        category = str(query["category"])
        lane = _sanitize_query(query.get("lane") or category.lower()).lower()
        lane = lane.replace(" ", "-")[:40] or category.lower()
        sources.append(
            NewsSource(
                name=f"{source_prefix}-{index}-{lane}",
                category=category,
                url=BRAVE_NEWS_ENDPOINT,
                kind="brave_news",
                provider="brave",
                query=str(query["query"]),
                count=int(
                    query.get("max_results")
                    or DEFAULT_MAX_DISCOVERY_RESULTS_PER_QUERY
                ),
            )
        )
    return tuple(sources)


def create_discovery_plan(
    *,
    fetch_results: tuple[FetchResult, ...],
    as_of: datetime,
    enabled: bool,
    api_key: str | None,
    model: str = DEFAULT_LLM_MODEL,
    timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS,
    max_queries: int = DEFAULT_MAX_DISCOVERY_QUERIES_PER_RUN,
    max_results_per_query: int = DEFAULT_MAX_DISCOVERY_RESULTS_PER_QUERY,
    recent_delivery_texts: list[str] | None = None,
    client: DiscoveryQueryClient | None = None,
) -> dict[str, Any]:
    if not enabled:
        return {"status": "disabled", "requested": 0, "queries": [], "sources": ()}
    if max_queries <= 0:
        return {
            "status": "disabled_limit_zero",
            "requested": 0,
            "queries": [],
            "sources": (),
        }
    if not api_key and client is None:
        return {
            "status": "skipped_no_api_key",
            "requested": 0,
            "queries": [],
            "sources": (),
        }

    payload = build_discovery_payload(
        fetch_results=fetch_results,
        as_of=as_of,
        recent_delivery_texts=recent_delivery_texts,
        max_queries=max_queries,
        max_results_per_query=max_results_per_query,
    )
    llm_client = client or OpenAIResponsesClient(
        api_key=str(api_key),
        model=model,
        timeout_seconds=timeout_seconds,
    )
    try:
        raw_plan = llm_client.create_discovery_queries(payload)
        queries = normalize_discovery_plan(
            raw_plan,
            max_queries=max_queries,
            max_results_per_query=max_results_per_query,
        )
    except Exception as exc:
        return {
            "status": "error",
            "requested": max_queries,
            "queries": [],
            "sources": (),
            "error": str(exc)[:500],
            "model": model,
            "prompt_version": DISCOVERY_QUERY_PROMPT_VERSION,
        }

    return {
        "status": "ok",
        "requested": max_queries,
        "queries": queries,
        "sources": discovery_sources_from_queries(queries),
        "model": model,
        "prompt_version": DISCOVERY_QUERY_PROMPT_VERSION,
        "payload_chars": len(json.dumps(payload, ensure_ascii=False, sort_keys=True)),
    }
