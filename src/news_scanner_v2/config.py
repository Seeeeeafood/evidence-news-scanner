from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

from .budget import DEFAULT_MAX_BRAVE_REQUESTS_PER_RUN
from .body_fetcher import (
    DEFAULT_BODY_FETCH_TIMEOUT_SECONDS,
    DEFAULT_MAX_BODY_FETCHES_PER_RUN,
)
from .verification import (
    DEFAULT_MAX_VERIFICATION_BRAVE_REQUESTS_PER_RUN,
    DEFAULT_VERIFICATION_TIMEOUT_SECONDS,
)


KST_TZ = "Asia/Seoul"

LEGACY_JOB_IDS = {
    "hourly": "f06099f3-6825-47f6-9410-81e9aebb6b04",
    "half_hour": "52ed935b-f85e-4203-bbac-ce4e87bc8913",
}

DEFAULT_LEGACY_ROOT = Path(os.environ.get("NEWS_SCANNER_LEGACY_ROOT", "."))
DEFAULT_RUNTIME_ROOT = Path(
    os.environ.get("NEWS_SCANNER_RUNTIME_ROOT", ".evidence-news-scanner")
)
DEFAULT_DB_PATH = Path(
    os.environ.get(
        "NEWS_SCANNER_DB",
        str(DEFAULT_RUNTIME_ROOT / "state" / "news_scanner_v2.sqlite"),
    )
)
DEFAULT_SHADOW_DIR = Path(
    os.environ.get(
        "NEWS_SCANNER_SHADOW_DIR",
        str(DEFAULT_RUNTIME_ROOT / "state" / "news_scanner_v2_shadow"),
    )
)
DEFAULT_LLM_MODEL = os.environ.get("NEWS_SCANNER_V2_LLM_MODEL", "gpt-5.5")
DEFAULT_LLM_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_DISCOVERY_QUERIES_PER_RUN = 3
DEFAULT_MAX_DISCOVERY_RESULTS_PER_QUERY = 10
DEFAULT_MAX_SCOUT_QUERIES_PER_RUN = 4
DEFAULT_MAX_PRICE_REACTION_TICKERS_PER_RUN = 12
DEFAULT_PRICE_REACTION_TIMEOUT_SECONDS = 8.0
DEFAULT_PRICE_REACTION_STALE_AFTER_DAYS = 5
DEFAULT_MARKET_SNAPSHOT_TIMEOUT_SECONDS = 8.0

COVERAGE_CATEGORIES = (
    "us_market",
    "watchlist_company_news",
    "portfolio_company_news",
    "earnings_guidance",
    "ipo_mna_regulation_litigation",
    "macro_cpi_pce_fomc_jobs_oil_rates_fx",
    "policy_tariffs_sanctions_geopolitics",
    "weekend_holiday_market_relevant_news",
)


@dataclass(frozen=True)
class RuntimeConfig:
    legacy_root: Path
    db_path: Path
    shadow_dir: Path
    dispatch_enabled: bool = False
    llm_enabled: bool = False
    lookback_hours: int = 72
    brave_api_key: str | None = None
    brave_enabled: bool = True
    max_brave_requests_per_run: int = DEFAULT_MAX_BRAVE_REQUESTS_PER_RUN
    body_fetch_enabled: bool = True
    max_body_fetches_per_run: int = DEFAULT_MAX_BODY_FETCHES_PER_RUN
    body_fetch_timeout_seconds: float = DEFAULT_BODY_FETCH_TIMEOUT_SECONDS
    llm_model: str = DEFAULT_LLM_MODEL
    discovery_llm_model: str = DEFAULT_LLM_MODEL
    editorial_llm_model: str = DEFAULT_LLM_MODEL
    theme_editor_llm_model: str = DEFAULT_LLM_MODEL
    summary_llm_model: str = DEFAULT_LLM_MODEL
    critic_llm_model: str = DEFAULT_LLM_MODEL
    llm_timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS
    discovery_planner_enabled: bool = True
    scout_lanes_enabled: bool = True
    max_scout_queries_per_run: int = DEFAULT_MAX_SCOUT_QUERIES_PER_RUN
    max_discovery_queries_per_run: int = DEFAULT_MAX_DISCOVERY_QUERIES_PER_RUN
    max_discovery_results_per_query: int = DEFAULT_MAX_DISCOVERY_RESULTS_PER_QUERY
    price_reaction_enabled: bool = True
    polygon_api_key: str | None = None
    max_price_reaction_tickers_per_run: int = DEFAULT_MAX_PRICE_REACTION_TICKERS_PER_RUN
    price_reaction_timeout_seconds: float = DEFAULT_PRICE_REACTION_TIMEOUT_SECONDS
    price_reaction_stale_after_days: int = DEFAULT_PRICE_REACTION_STALE_AFTER_DAYS
    market_snapshot_enabled: bool = False
    fmp_api_key: str | None = None
    market_snapshot_timeout_seconds: float = DEFAULT_MARKET_SNAPSHOT_TIMEOUT_SECONDS
    verification_enabled: bool = True
    max_verification_brave_requests_per_run: int = (
        DEFAULT_MAX_VERIFICATION_BRAVE_REQUESTS_PER_RUN
    )
    verification_timeout_seconds: float = DEFAULT_VERIFICATION_TIMEOUT_SECONDS


def _role_model(env_name: str, *, base_model: str, override: str | None) -> str:
    if override:
        return override
    return os.environ.get(env_name, base_model)


def llm_model_roles(config: RuntimeConfig) -> dict[str, str]:
    return {
        "base": config.llm_model,
        "discovery": config.discovery_llm_model,
        "editorial": config.editorial_llm_model,
        "theme_editor": config.theme_editor_llm_model,
        "summary": config.summary_llm_model,
        "critic": config.critic_llm_model,
    }


def build_config(
    *,
    legacy_root: Path | None = None,
    db_path: Path | None = None,
    shadow_dir: Path | None = None,
    dispatch_enabled: bool = False,
    llm_enabled: bool = False,
    lookback_hours: int = 72,
    brave_api_key: str | None = None,
    brave_enabled: bool = True,
    max_brave_requests_per_run: int = DEFAULT_MAX_BRAVE_REQUESTS_PER_RUN,
    body_fetch_enabled: bool = True,
    max_body_fetches_per_run: int = DEFAULT_MAX_BODY_FETCHES_PER_RUN,
    body_fetch_timeout_seconds: float = DEFAULT_BODY_FETCH_TIMEOUT_SECONDS,
    llm_model: str = DEFAULT_LLM_MODEL,
    discovery_llm_model: str | None = None,
    editorial_llm_model: str | None = None,
    theme_editor_llm_model: str | None = None,
    summary_llm_model: str | None = None,
    critic_llm_model: str | None = None,
    llm_timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS,
    discovery_planner_enabled: bool = True,
    scout_lanes_enabled: bool = True,
    max_scout_queries_per_run: int = DEFAULT_MAX_SCOUT_QUERIES_PER_RUN,
    max_discovery_queries_per_run: int = DEFAULT_MAX_DISCOVERY_QUERIES_PER_RUN,
    max_discovery_results_per_query: int = DEFAULT_MAX_DISCOVERY_RESULTS_PER_QUERY,
    price_reaction_enabled: bool = True,
    polygon_api_key: str | None = None,
    max_price_reaction_tickers_per_run: int = DEFAULT_MAX_PRICE_REACTION_TICKERS_PER_RUN,
    price_reaction_timeout_seconds: float = DEFAULT_PRICE_REACTION_TIMEOUT_SECONDS,
    price_reaction_stale_after_days: int = DEFAULT_PRICE_REACTION_STALE_AFTER_DAYS,
    market_snapshot_enabled: bool = False,
    fmp_api_key: str | None = None,
    market_snapshot_timeout_seconds: float = DEFAULT_MARKET_SNAPSHOT_TIMEOUT_SECONDS,
    verification_enabled: bool = True,
    max_verification_brave_requests_per_run: int = (
        DEFAULT_MAX_VERIFICATION_BRAVE_REQUESTS_PER_RUN
    ),
    verification_timeout_seconds: float = DEFAULT_VERIFICATION_TIMEOUT_SECONDS,
) -> RuntimeConfig:
    return RuntimeConfig(
        legacy_root=legacy_root or DEFAULT_LEGACY_ROOT,
        db_path=db_path or DEFAULT_DB_PATH,
        shadow_dir=shadow_dir or DEFAULT_SHADOW_DIR,
        dispatch_enabled=dispatch_enabled,
        llm_enabled=llm_enabled,
        lookback_hours=lookback_hours,
        brave_api_key=brave_api_key,
        brave_enabled=brave_enabled,
        max_brave_requests_per_run=max_brave_requests_per_run,
        body_fetch_enabled=body_fetch_enabled,
        max_body_fetches_per_run=max_body_fetches_per_run,
        body_fetch_timeout_seconds=body_fetch_timeout_seconds,
        llm_model=llm_model,
        discovery_llm_model=_role_model(
            "NEWS_SCANNER_V2_DISCOVERY_LLM_MODEL",
            base_model=llm_model,
            override=discovery_llm_model,
        ),
        editorial_llm_model=_role_model(
            "NEWS_SCANNER_V2_EDITORIAL_LLM_MODEL",
            base_model=llm_model,
            override=editorial_llm_model,
        ),
        theme_editor_llm_model=_role_model(
            "NEWS_SCANNER_V2_THEME_EDITOR_LLM_MODEL",
            base_model=llm_model,
            override=theme_editor_llm_model,
        ),
        summary_llm_model=_role_model(
            "NEWS_SCANNER_V2_SUMMARY_LLM_MODEL",
            base_model=llm_model,
            override=summary_llm_model,
        ),
        critic_llm_model=_role_model(
            "NEWS_SCANNER_V2_CRITIC_LLM_MODEL",
            base_model=llm_model,
            override=critic_llm_model,
        ),
        llm_timeout_seconds=llm_timeout_seconds,
        discovery_planner_enabled=discovery_planner_enabled,
        scout_lanes_enabled=scout_lanes_enabled,
        max_scout_queries_per_run=max_scout_queries_per_run,
        max_discovery_queries_per_run=max_discovery_queries_per_run,
        max_discovery_results_per_query=max_discovery_results_per_query,
        price_reaction_enabled=price_reaction_enabled,
        polygon_api_key=polygon_api_key,
        max_price_reaction_tickers_per_run=max_price_reaction_tickers_per_run,
        price_reaction_timeout_seconds=price_reaction_timeout_seconds,
        price_reaction_stale_after_days=price_reaction_stale_after_days,
        market_snapshot_enabled=market_snapshot_enabled,
        fmp_api_key=fmp_api_key,
        market_snapshot_timeout_seconds=market_snapshot_timeout_seconds,
        verification_enabled=verification_enabled,
        max_verification_brave_requests_per_run=max_verification_brave_requests_per_run,
        verification_timeout_seconds=verification_timeout_seconds,
    )
