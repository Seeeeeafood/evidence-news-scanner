from __future__ import annotations

import csv
from datetime import datetime, timezone
import io
import json
from typing import Any
from urllib import error, parse, request
from zoneinfo import ZoneInfo


KST_TZ = "Asia/Seoul"
FMP_BASE_URL = "https://financialmodelingprep.com/stable"
STOOQ_QUOTE_URL = "https://stooq.com/q/l/"
SNAPSHOT_SCHEMA_VERSION = 1

FMP_QUOTE_SYMBOLS = {
    "sp500": "^GSPC",
    "nasdaq": "^IXIC",
    "dow": "^DJI",
    "brent": "BZUSD",
    "gold": "GCUSD",
    "vix": "^VIX",
    "usd_krw": "USDKRW",
}

STOOQ_QUOTE_SYMBOLS = {
    "wti": "cl.f",
    "dxy": "dx.f",
}

EXPECTED_VALUE_KEYS = (
    "sp500",
    "nasdaq",
    "dow",
    "wti",
    "brent",
    "gold",
    "dxy",
    "ten_year",
    "vix",
    "usd_krw",
)

DISPLAY_SPECS = {
    "sp500": {"label": "S&P", "precision": 0, "show_change_pct": True},
    "nasdaq": {"label": "NASDAQ", "precision": 0, "show_change_pct": True},
    "dow": {"label": "DOW", "precision": 0, "show_change_pct": True},
    "wti": {"label": "WTI", "prefix": "$", "precision": 1},
    "brent": {"label": "Brent", "prefix": "$", "precision": 1},
    "gold": {"label": "금", "prefix": "$", "precision": 0},
    "dxy": {"label": "DXY", "precision": 1},
    "ten_year": {"label": "10Y", "suffix": "%", "precision": 2},
    "vix": {"label": "VIX", "precision": 1},
    "usd_krw": {"label": "USD/KRW", "precision": 0},
}

DISPLAY_GROUPS = (
    ("indices", ("sp500", "nasdaq", "dow")),
    ("macro", ("wti", "brent", "gold", "dxy", "ten_year", "vix")),
    ("fx", ("usd_krw",)),
)

DISPLAY_PREFIXES = {
    "indices": "📊 지수",
    "macro": "💰 매크로",
    "fx": "💱 환율",
}


def _safe_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_text(value: object) -> str:
    return str(value or "").strip()


def _utc_timestamp_to_kst(timestamp: object) -> str:
    try:
        seconds = int(timestamp)
    except (TypeError, ValueError):
        return ""
    return datetime.fromtimestamp(seconds, tz=timezone.utc).astimezone(
        ZoneInfo(KST_TZ)
    ).isoformat()


def _request_text(url: str, *, timeout_seconds: float) -> str:
    req = request.Request(
        url,
        method="GET",
        headers={"User-Agent": "news-scanner-v2/1.0"},
    )
    with request.urlopen(req, timeout=timeout_seconds) as response:
        return response.read().decode("utf-8")


def _fetch_json(url: str, *, timeout_seconds: float) -> Any:
    raw = _request_text(url, timeout_seconds=timeout_seconds)
    return json.loads(raw)


def _fmp_quote_url(
    symbol: str,
    *,
    api_key: str,
    base_url: str = FMP_BASE_URL,
) -> str:
    return (
        f"{base_url.rstrip('/')}/quote?"
        + parse.urlencode({"symbol": symbol, "apikey": api_key})
    )


def _fmp_treasury_url(
    *,
    api_key: str,
    base_url: str = FMP_BASE_URL,
) -> str:
    return f"{base_url.rstrip('/')}/treasury-rates?" + parse.urlencode(
        {"apikey": api_key}
    )


def _stooq_quote_url(
    symbol: str,
    *,
    base_url: str = STOOQ_QUOTE_URL,
) -> str:
    return f"{base_url.rstrip('/')}?" + parse.urlencode(
        {"s": symbol, "f": "sd2t2ohlcv", "h": "", "e": "csv"}
    )


def _quote_from_fmp_payload(
    key: str,
    symbol: str,
    payload: Any,
) -> dict[str, Any]:
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        return {
            "status": "missing",
            "provider": "fmp",
            "symbol": symbol,
            "error": "empty_quote_payload",
        }
    item = payload[0]
    value = _safe_float(item.get("price"))
    if value is None:
        return {
            "status": "missing",
            "provider": "fmp",
            "symbol": symbol,
            "error": "missing_price",
        }
    source_time = _utc_timestamp_to_kst(item.get("timestamp"))
    return {
        "status": "ok",
        "provider": "fmp",
        "symbol": _safe_text(item.get("symbol")) or symbol,
        "name": _safe_text(item.get("name")),
        "value": value,
        "change": _safe_float(item.get("change")),
        "change_pct": _safe_float(item.get("changePercentage")),
        "source_time": source_time,
        "source_date": source_time[:10] if source_time else "",
        "field": key,
    }


def _quote_from_stooq_csv(
    key: str,
    symbol: str,
    raw_csv: str,
) -> dict[str, Any]:
    rows = list(csv.DictReader(io.StringIO(raw_csv)))
    if not rows:
        return {
            "status": "missing",
            "provider": "stooq",
            "symbol": symbol,
            "error": "empty_csv",
        }
    row = rows[0]
    close = _safe_float(row.get("Close"))
    if close is None:
        return {
            "status": "missing",
            "provider": "stooq",
            "symbol": symbol,
            "error": "missing_close",
        }
    source_date = _safe_text(row.get("Date"))
    source_time = _safe_text(row.get("Time"))
    return {
        "status": "ok",
        "provider": "stooq",
        "symbol": _safe_text(row.get("Symbol")) or symbol.upper(),
        "value": close,
        "source_date": source_date,
        "source_time": f"{source_date}T{source_time}" if source_date and source_time else "",
        "field": key,
    }


def _treasury_quote_from_fmp_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        return {
            "status": "missing",
            "provider": "fmp",
            "symbol": "US10Y",
            "error": "empty_treasury_payload",
        }
    item = payload[0]
    value = _safe_float(item.get("year10"))
    if value is None:
        return {
            "status": "missing",
            "provider": "fmp",
            "symbol": "US10Y",
            "error": "missing_year10",
        }
    return {
        "status": "ok",
        "provider": "fmp",
        "symbol": "US10Y",
        "value": value,
        "source_date": _safe_text(item.get("date")),
        "field": "ten_year",
    }


def _error_quote(
    key: str,
    *,
    provider: str,
    symbol: str,
    exc: BaseException,
) -> dict[str, Any]:
    detail = str(exc)
    if isinstance(exc, error.HTTPError):
        detail = f"HTTP {exc.code}"
    return {
        "status": "error",
        "provider": provider,
        "symbol": symbol,
        "field": key,
        "error": detail[:200],
    }


def _new_snapshot(as_of: datetime) -> dict[str, Any]:
    generated_at = datetime.now(ZoneInfo(KST_TZ)).isoformat()
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "as_of": as_of.isoformat(),
        "generated_at": generated_at,
        "status": "pending",
        "values": {},
        "provider_errors": [],
    }


def fetch_market_snapshot(
    *,
    as_of: datetime,
    fmp_api_key: str | None,
    timeout_seconds: float = 8.0,
    fmp_base_url: str = FMP_BASE_URL,
    stooq_base_url: str = STOOQ_QUOTE_URL,
) -> dict[str, Any]:
    snapshot = _new_snapshot(as_of)
    values: dict[str, Any] = snapshot["values"]

    if fmp_api_key:
        for key, symbol in FMP_QUOTE_SYMBOLS.items():
            try:
                payload = _fetch_json(
                    _fmp_quote_url(symbol, api_key=fmp_api_key, base_url=fmp_base_url),
                    timeout_seconds=timeout_seconds,
                )
                values[key] = _quote_from_fmp_payload(key, symbol, payload)
            except (error.HTTPError, error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                values[key] = _error_quote(
                    key,
                    provider="fmp",
                    symbol=symbol,
                    exc=exc,
                )
        try:
            payload = _fetch_json(
                _fmp_treasury_url(api_key=fmp_api_key, base_url=fmp_base_url),
                timeout_seconds=timeout_seconds,
            )
            values["ten_year"] = _treasury_quote_from_fmp_payload(payload)
        except (error.HTTPError, error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            values["ten_year"] = _error_quote(
                "ten_year",
                provider="fmp",
                symbol="US10Y",
                exc=exc,
            )
    else:
        snapshot["provider_errors"].append(
            {"provider": "fmp", "status": "skipped", "error": "api_key_missing"}
        )

    for key, symbol in STOOQ_QUOTE_SYMBOLS.items():
        try:
            raw_csv = _request_text(
                _stooq_quote_url(symbol, base_url=stooq_base_url),
                timeout_seconds=timeout_seconds,
            )
            values[key] = _quote_from_stooq_csv(key, symbol, raw_csv)
        except (error.HTTPError, error.URLError, TimeoutError, OSError) as exc:
            values[key] = _error_quote(
                key,
                provider="stooq",
                symbol=symbol,
                exc=exc,
            )

    return finalize_market_snapshot(snapshot)


def _value_usable(value: object) -> bool:
    return isinstance(value, dict) and value.get("status") in {"ok", "stale"} and (
        _safe_float(value.get("value")) is not None
    )


def _copy_previous_value(value: dict[str, Any], *, previous_as_of: str) -> dict[str, Any]:
    copied = dict(value)
    copied["status"] = "stale"
    copied["stale"] = True
    copied["stale_source"] = "previous_snapshot"
    copied["stale_from_as_of"] = previous_as_of
    return copied


def merge_missing_with_previous_snapshot(
    snapshot: dict[str, Any],
    previous_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    if not previous_snapshot:
        return finalize_market_snapshot(snapshot)
    current_values = snapshot.setdefault("values", {})
    previous_values = previous_snapshot.get("values")
    if not isinstance(current_values, dict) or not isinstance(previous_values, dict):
        return finalize_market_snapshot(snapshot)

    previous_as_of = _safe_text(previous_snapshot.get("as_of"))
    for key in EXPECTED_VALUE_KEYS:
        if _value_usable(current_values.get(key)):
            continue
        previous_value = previous_values.get(key)
        if isinstance(previous_value, dict) and _value_usable(previous_value):
            current_values[key] = _copy_previous_value(
                previous_value,
                previous_as_of=previous_as_of,
            )
    return finalize_market_snapshot(snapshot)


def finalize_market_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    values = snapshot.get("values")
    if not isinstance(values, dict):
        values = {}
        snapshot["values"] = values
    status_counts: dict[str, int] = {}
    for key in EXPECTED_VALUE_KEYS:
        value = values.get(key)
        status = str(value.get("status") if isinstance(value, dict) else "missing")
        if not status:
            status = "missing"
        status_counts[status] = status_counts.get(status, 0) + 1

    usable_count = sum(1 for key in EXPECTED_VALUE_KEYS if _value_usable(values.get(key)))
    stale_count = status_counts.get("stale", 0)
    missing_count = len(EXPECTED_VALUE_KEYS) - usable_count
    if usable_count == 0:
        snapshot["status"] = "unavailable"
    elif missing_count or stale_count:
        snapshot["status"] = "partial"
    else:
        snapshot["status"] = "ok"
    snapshot["value_status_counts"] = status_counts
    snapshot["usable_values"] = usable_count
    snapshot["expected_values"] = len(EXPECTED_VALUE_KEYS)
    snapshot["providers"] = sorted(
        {
            str(value.get("provider") or "")
            for value in values.values()
            if isinstance(value, dict) and value.get("provider")
        }
    )
    return snapshot


def summarize_market_snapshot(
    snapshot: dict[str, Any] | None,
    *,
    enabled: bool,
) -> dict[str, Any]:
    if not enabled:
        return {
            "market_snapshot_enabled": False,
            "market_snapshot_status": "disabled",
            "market_snapshot_values_ok": 0,
            "market_snapshot_values_expected": len(EXPECTED_VALUE_KEYS),
            "market_snapshot_providers": [],
        }
    if not snapshot:
        return {
            "market_snapshot_enabled": True,
            "market_snapshot_status": "missing",
            "market_snapshot_values_ok": 0,
            "market_snapshot_values_expected": len(EXPECTED_VALUE_KEYS),
            "market_snapshot_providers": [],
        }
    return {
        "market_snapshot_enabled": True,
        "market_snapshot_status": snapshot.get("status", "unknown"),
        "market_snapshot_values_ok": int(snapshot.get("usable_values") or 0),
        "market_snapshot_values_expected": int(
            snapshot.get("expected_values") or len(EXPECTED_VALUE_KEYS)
        ),
        "market_snapshot_providers": list(snapshot.get("providers") or []),
        "market_snapshot_value_status_counts": dict(
            snapshot.get("value_status_counts") or {}
        ),
    }


def _format_number(value: float, precision: int) -> str:
    if precision <= 0:
        return f"{value:,.0f}"
    return f"{value:,.{precision}f}"


def _format_change_pct(value: object) -> str:
    pct = _safe_float(value)
    if pct is None:
        return ""
    return f" ({pct:+.2f}%)"


def _format_snapshot_item(key: str, quote: object) -> str:
    spec = DISPLAY_SPECS[key]
    label = str(spec["label"])
    if not _value_usable(quote):
        return f"{label} N/A"
    assert isinstance(quote, dict)
    value = float(quote["value"])
    prefix = str(spec.get("prefix") or "")
    suffix = str(spec.get("suffix") or "")
    rendered = (
        f"{label} {prefix}"
        f"{_format_number(value, int(spec.get('precision') or 0))}{suffix}"
    )
    if spec.get("show_change_pct"):
        rendered += _format_change_pct(quote.get("change_pct"))
    if quote.get("status") == "stale":
        rendered += "(전회차)"
    return rendered


def render_market_snapshot_lines(snapshot: dict[str, Any] | None) -> list[str]:
    if not isinstance(snapshot, dict):
        return []
    values = snapshot.get("values")
    if not isinstance(values, dict):
        return []
    if not any(_value_usable(values.get(key)) for key in EXPECTED_VALUE_KEYS):
        return []

    lines = []
    for group, keys in DISPLAY_GROUPS:
        items = [_format_snapshot_item(key, values.get(key)) for key in keys]
        lines.append(f"{DISPLAY_PREFIXES[group]}: " + " | ".join(items))
    return lines
