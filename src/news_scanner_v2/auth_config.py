from __future__ import annotations

import os
from pathlib import Path


BRAVE_ENV_NAMES = (
    "BRAVE_SEARCH_API_KEY",
    "BRAVE_API_KEY",
    "BRAVE_SUBSCRIPTION_TOKEN",
)
TELEGRAM_ENV_NAMES = ("TELEGRAM_BOT_TOKEN",)
OPENAI_ENV_NAMES = ("OPENAI_API_KEY",)
POLYGON_ENV_NAMES = ("POLYGON_API_KEY",)
FMP_ENV_NAMES = (
    "FMP_API_KEY",
    "FINANCIAL_MODELING_PREP_API_KEY",
    "FINANCIALMODELINGPREP_API_KEY",
)


def load_brave_api_key(*, legacy_root: Path | None = None) -> str | None:
    for name in BRAVE_ENV_NAMES:
        value = os.environ.get(name)
        if value:
            return value.strip()
    return None


def load_telegram_bot_token(*, legacy_root: Path | None = None) -> str | None:
    for name in TELEGRAM_ENV_NAMES:
        value = os.environ.get(name)
        if value:
            return value.strip()
    return None


def load_openai_api_key() -> str | None:
    for name in OPENAI_ENV_NAMES:
        value = os.environ.get(name)
        if value:
            return value.strip()
    return None


def load_polygon_api_key(*, legacy_root: Path | None = None) -> str | None:
    for name in POLYGON_ENV_NAMES:
        value = os.environ.get(name)
        if value:
            return value.strip()
    return None


def load_fmp_api_key(*, legacy_root: Path | None = None) -> str | None:
    for name in FMP_ENV_NAMES:
        value = os.environ.get(name)
        if value:
            return value.strip()
    return None
