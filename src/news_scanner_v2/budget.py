from __future__ import annotations

from dataclasses import dataclass

from .fetcher import FetchResult
from .sources import NewsSource


BRAVE_SEARCH_COST_USD_PER_1000 = 5.0
DEFAULT_MAX_BRAVE_REQUESTS_PER_RUN = 14


@dataclass(frozen=True)
class BraveBudget:
    enabled: bool
    planned_requests: int
    max_requests: int
    estimated_cost_usd: float
    status: str
    message: str = ""


def estimate_brave_cost_usd(
    requests: int,
    *,
    cost_per_1000: float = BRAVE_SEARCH_COST_USD_PER_1000,
) -> float:
    return round((requests * cost_per_1000) / 1000, 6)


def count_brave_sources(sources: tuple[NewsSource, ...]) -> int:
    return sum(1 for source in sources if source.provider == "brave")


def evaluate_brave_budget(
    sources: tuple[NewsSource, ...],
    *,
    brave_enabled: bool,
    max_requests: int = DEFAULT_MAX_BRAVE_REQUESTS_PER_RUN,
) -> BraveBudget:
    planned = count_brave_sources(sources) if brave_enabled else 0
    if not brave_enabled:
        return BraveBudget(
            enabled=False,
            planned_requests=0,
            max_requests=max_requests,
            estimated_cost_usd=0.0,
            status="disabled",
        )
    if planned > max_requests:
        return BraveBudget(
            enabled=True,
            planned_requests=planned,
            max_requests=max_requests,
            estimated_cost_usd=estimate_brave_cost_usd(planned),
            status="limit_exceeded",
            message=f"planned Brave requests {planned} exceed max {max_requests}",
        )
    return BraveBudget(
        enabled=True,
        planned_requests=planned,
        max_requests=max_requests,
        estimated_cost_usd=estimate_brave_cost_usd(planned),
        status="ok",
    )


def count_billable_brave_requests(results: tuple[FetchResult, ...]) -> int:
    return sum(
        1
        for result in results
        if result.source.provider == "brave" and not result.status.startswith("skipped")
    )
