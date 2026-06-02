from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, date, time, timedelta, timezone
import json
import re
from typing import Any, Protocol
from urllib import error, parse, request
from zoneinfo import ZoneInfo


POLYGON_BASE_URL = "https://api.polygon.io"
US_EASTERN_TZ = "America/New_York"
COMPANY_EVENT_TYPES = {
    "analyst",
    "corporate_action",
    "earnings",
    "mover",
    "strategic",
}
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,7}$")
REACTION_FLAT_THRESHOLD_PCT = 0.2
REGULAR_SESSION_STALE_MINUTES = 120
OUTSIDE_SESSION_STALE_MINUTES = 18 * 60


class PriceDataClient(Protocol):
    def aggregate_bars(
        self,
        *,
        ticker: str,
        multiplier: int,
        timespan: str,
        from_date: date,
        to_date: date,
    ) -> list[dict[str, Any]]:
        ...


@dataclass(frozen=True)
class PolygonPriceClient:
    api_key: str
    timeout_seconds: float = 8.0
    base_url: str = POLYGON_BASE_URL

    def aggregate_bars(
        self,
        *,
        ticker: str,
        multiplier: int,
        timespan: str,
        from_date: date,
        to_date: date,
    ) -> list[dict[str, Any]]:
        encoded_ticker = parse.quote(ticker.upper(), safe="")
        url = (
            f"{self.base_url.rstrip('/')}/v2/aggs/ticker/{encoded_ticker}"
            f"/range/{multiplier}/{timespan}/{from_date.isoformat()}"
            f"/{to_date.isoformat()}"
        )
        query = parse.urlencode(
            {
                "adjusted": "true",
                "sort": "asc",
                "limit": "50000",
                "apiKey": self.api_key,
            }
        )
        req = request.Request(f"{url}?{query}", method="GET")
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            raise RuntimeError(f"Polygon HTTP {exc.code}: {detail}") from exc
        except (error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"Polygon request failed: {exc}") from exc

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Polygon returned invalid JSON") from exc
        results = payload.get("results")
        return results if isinstance(results, list) else []


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _event_from_record(record: dict[str, Any]) -> dict[str, Any]:
    payload = _as_dict(record.get("payload"))
    return _as_dict(payload.get("event"))


def _event_type(record: dict[str, Any]) -> str:
    return _text(_event_from_record(record).get("event_type"))


def _subject_ticker(record: dict[str, Any]) -> str:
    subject = _text(_event_from_record(record).get("subject")).upper()
    if TICKER_RE.match(subject):
        return subject
    return ""


def price_reaction_required(record: dict[str, Any]) -> bool:
    return (
        str(record.get("decision") or "") == "send_candidate"
        and _event_type(record) in COMPANY_EVENT_TYPES
    )


def _bar_timestamp_ms(bar: dict[str, Any]) -> int | None:
    value = bar.get("t")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bar_close(bar: dict[str, Any]) -> float | None:
    value = bar.get("c")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bar_dt_utc(bar: dict[str, Any]) -> datetime | None:
    timestamp_ms = _bar_timestamp_ms(bar)
    if timestamp_ms is None:
        return None
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)


def _bar_date_et(bar: dict[str, Any]) -> date | None:
    dt = _bar_dt_utc(bar)
    if dt is None:
        return None
    return dt.astimezone(ZoneInfo(US_EASTERN_TZ)).date()


def _valid_sorted_bars(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    valid = [
        bar
        for bar in bars
        if _bar_timestamp_ms(bar) is not None and _bar_close(bar) is not None
    ]
    return sorted(valid, key=lambda bar: int(_bar_timestamp_ms(bar) or 0))


def _latest_daily_on_or_before(
    bars: list[dict[str, Any]],
    *,
    on_or_before: date,
) -> dict[str, Any] | None:
    eligible = [
        bar for bar in _valid_sorted_bars(bars) if (_bar_date_et(bar) or date.min) <= on_or_before
    ]
    return eligible[-1] if eligible else None


def _latest_daily_before(
    bars: list[dict[str, Any]],
    *,
    before_date: date,
) -> dict[str, Any] | None:
    eligible = [
        bar for bar in _valid_sorted_bars(bars) if (_bar_date_et(bar) or date.min) < before_date
    ]
    return eligible[-1] if eligible else None


def _regular_us_session(as_of_et: datetime) -> bool:
    if as_of_et.weekday() >= 5:
        return False
    current = as_of_et.time()
    return time(9, 30) <= current <= time(16, 15)


def _direction(pct_change: float) -> str:
    if pct_change >= REACTION_FLAT_THRESHOLD_PCT:
        return "up"
    if pct_change <= -REACTION_FLAT_THRESHOLD_PCT:
        return "down"
    return "flat"


def _round_float(value: float) -> float:
    return round(float(value), 4)


def _reaction_from_prices(
    *,
    ticker: str,
    close: float,
    previous_close: float,
    price_as_of_dt: datetime,
    as_of: datetime,
    session: str,
    stale: bool,
    stale_minutes: int | None = None,
    stale_days: int | None = None,
    stale_after_days: int | None = None,
) -> dict[str, Any]:
    pct_change = ((close - previous_close) / previous_close) * 100.0
    status = "stale" if stale else "ok"
    return {
        "status": status,
        "provider": "polygon",
        "ticker": ticker,
        "price_as_of": price_as_of_dt.date().isoformat(),
        "price_as_of_at": price_as_of_dt.isoformat(),
        "as_of": as_of.isoformat(),
        "session": session,
        "basis": "polygon_aggregate",
        "close": _round_float(close),
        "previous_close": _round_float(previous_close),
        "pct_change": _round_float(pct_change),
        "direction": _direction(pct_change),
        "stale": stale,
        "stale_minutes": stale_minutes,
        "stale_days": stale_days,
        "stale_after_days": stale_after_days,
    }


def build_price_reaction_from_bars(
    *,
    ticker: str,
    daily_bars: list[dict[str, Any]],
    intraday_bars: list[dict[str, Any]],
    as_of: datetime,
    stale_after_days: int = 5,
) -> dict[str, Any]:
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=ZoneInfo("Asia/Seoul"))
    as_of_utc = as_of.astimezone(timezone.utc)
    as_of_et = as_of.astimezone(ZoneInfo(US_EASTERN_TZ))
    as_of_et_date = as_of_et.date()

    intraday = [
        bar
        for bar in _valid_sorted_bars(intraday_bars)
        if (_bar_dt_utc(bar) or datetime.min.replace(tzinfo=timezone.utc)) <= as_of_utc
    ]
    latest_intraday = intraday[-1] if intraday else None
    if latest_intraday is not None:
        intraday_dt = _bar_dt_utc(latest_intraday)
        intraday_date = _bar_date_et(latest_intraday)
        close = _bar_close(latest_intraday)
        if intraday_dt is not None and intraday_date is not None and close is not None:
            previous_daily = _latest_daily_before(daily_bars, before_date=intraday_date)
            previous_close = _bar_close(previous_daily or {})
            if previous_close is None or previous_close <= 0:
                return {
                    "status": "no_baseline",
                    "provider": "polygon",
                    "ticker": ticker,
                    "required": True,
                    "reason": "missing_previous_daily_close",
                }
            stale_minutes = int((as_of_utc - intraday_dt).total_seconds() // 60)
            stale_limit = (
                REGULAR_SESSION_STALE_MINUTES
                if _regular_us_session(as_of_et)
                else OUTSIDE_SESSION_STALE_MINUTES
            )
            return _reaction_from_prices(
                ticker=ticker,
                close=close,
                previous_close=previous_close,
                price_as_of_dt=intraday_dt,
                as_of=as_of,
                session="intraday_5min",
                stale=stale_minutes > stale_limit,
                stale_minutes=stale_minutes,
                stale_after_days=stale_after_days,
            )

    if _regular_us_session(as_of_et):
        return {
            "status": "intraday_unavailable",
            "provider": "polygon",
            "ticker": ticker,
            "required": True,
            "reason": "no_intraday_bar_during_us_session",
        }

    daily = _valid_sorted_bars(daily_bars)
    if len(daily) < 2:
        return {
            "status": "no_bars",
            "provider": "polygon",
            "ticker": ticker,
            "required": True,
            "reason": "fewer_than_two_daily_bars",
        }

    latest_daily = _latest_daily_on_or_before(daily, on_or_before=as_of_et_date)
    if latest_daily is None:
        return {
            "status": "no_bars",
            "provider": "polygon",
            "ticker": ticker,
            "required": True,
            "reason": "no_daily_bar_before_as_of",
        }
    daily_date = _bar_date_et(latest_daily)
    if daily_date is None:
        return {
            "status": "no_bars",
            "provider": "polygon",
            "ticker": ticker,
            "required": True,
            "reason": "daily_bar_date_missing",
        }
    previous_daily = _latest_daily_before(daily, before_date=daily_date)
    close = _bar_close(latest_daily)
    previous_close = _bar_close(previous_daily or {})
    latest_daily_dt = _bar_dt_utc(latest_daily)
    if close is None or previous_close is None or previous_close <= 0 or latest_daily_dt is None:
        return {
            "status": "no_baseline",
            "provider": "polygon",
            "ticker": ticker,
            "required": True,
            "reason": "missing_daily_close",
        }

    stale_days = max(0, (as_of_et_date - daily_date).days)
    return _reaction_from_prices(
        ticker=ticker,
        close=close,
        previous_close=previous_close,
        price_as_of_dt=latest_daily_dt,
        as_of=as_of,
        session="daily",
        stale=stale_days > stale_after_days,
        stale_days=stale_days,
        stale_after_days=stale_after_days,
    )


def fetch_price_reaction(
    *,
    ticker: str,
    api_key: str | None,
    as_of: datetime,
    timeout_seconds: float = 8.0,
    stale_after_days: int = 5,
    client: PriceDataClient | None = None,
) -> dict[str, Any]:
    ticker = ticker.upper().strip()
    if not ticker:
        return {"status": "not_ticker", "required": True}
    if not api_key and client is None:
        return {
            "status": "missing_key",
            "provider": "polygon",
            "ticker": ticker,
            "required": True,
        }
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=ZoneInfo("Asia/Seoul"))
    as_of_et = as_of.astimezone(ZoneInfo(US_EASTERN_TZ))
    to_date = as_of_et.date()
    price_client = client or PolygonPriceClient(
        api_key=str(api_key),
        timeout_seconds=timeout_seconds,
    )
    try:
        daily_bars = price_client.aggregate_bars(
            ticker=ticker,
            multiplier=1,
            timespan="day",
            from_date=to_date - timedelta(days=21),
            to_date=to_date,
        )
        intraday_bars = price_client.aggregate_bars(
            ticker=ticker,
            multiplier=5,
            timespan="minute",
            from_date=to_date - timedelta(days=7),
            to_date=to_date,
        )
        return build_price_reaction_from_bars(
            ticker=ticker,
            daily_bars=daily_bars,
            intraday_bars=intraday_bars,
            as_of=as_of,
            stale_after_days=stale_after_days,
        )
    except Exception as exc:
        return {
            "status": "error",
            "provider": "polygon",
            "ticker": ticker,
            "required": True,
            "error": str(exc)[:500],
        }


def _attach_price_reaction(
    record: dict[str, Any],
    price_reaction: dict[str, Any],
) -> None:
    payload = _as_dict(record.get("payload")).copy()
    event = _as_dict(payload.get("event")).copy()
    metadata = _as_dict(event.get("metadata")).copy()
    reaction = dict(price_reaction)
    reaction.setdefault("required", price_reaction_required(record))
    metadata["price_reaction"] = reaction
    event["metadata"] = metadata
    payload["event"] = event
    payload["price_reaction"] = reaction
    payload["price_reaction_required"] = bool(reaction.get("required"))
    record["payload"] = payload


def enrich_decision_records_with_price_reactions(
    records: list[dict[str, Any]],
    *,
    enabled: bool,
    api_key: str | None,
    as_of: datetime,
    max_tickers: int = 12,
    timeout_seconds: float = 8.0,
    stale_after_days: int = 5,
    client: PriceDataClient | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    stats: dict[str, Any] = {
        "price_reaction_enabled": enabled,
        "price_reaction_configured": bool(api_key) or client is not None,
        "price_reaction_required_records": 0,
        "price_reaction_unique_tickers": 0,
        "price_reaction_attempted": 0,
        "price_reaction_skipped_limit": 0,
        "price_reaction_status_counts": {},
    }
    if not enabled:
        for record in records:
            if price_reaction_required(record):
                _attach_price_reaction(
                    record,
                    {
                        "status": "disabled",
                        "required": True,
                        "provider": "polygon",
                        "ticker": _subject_ticker(record),
                    },
                )
        return records, stats

    ticker_to_records: dict[str, list[dict[str, Any]]] = {}
    untickered_records: list[dict[str, Any]] = []
    for record in records:
        if not price_reaction_required(record):
            continue
        stats["price_reaction_required_records"] += 1
        ticker = _subject_ticker(record)
        if not ticker:
            untickered_records.append(record)
            continue
        ticker_to_records.setdefault(ticker, []).append(record)

    stats["price_reaction_unique_tickers"] = len(ticker_to_records)
    status_counts: dict[str, int] = {}
    for record in untickered_records:
        reaction = {"status": "not_ticker", "required": True}
        _attach_price_reaction(record, reaction)
        status_counts["not_ticker"] = status_counts.get("not_ticker", 0) + 1

    reactions_by_ticker: dict[str, dict[str, Any]] = {}
    for index, ticker in enumerate(sorted(ticker_to_records)):
        if index >= max_tickers:
            stats["price_reaction_skipped_limit"] += len(ticker_to_records[ticker])
            reaction = {
                "status": "skipped_limit",
                "required": True,
                "provider": "polygon",
                "ticker": ticker,
            }
        else:
            stats["price_reaction_attempted"] += 1
            reaction = fetch_price_reaction(
                ticker=ticker,
                api_key=api_key,
                as_of=as_of,
                timeout_seconds=timeout_seconds,
                stale_after_days=stale_after_days,
                client=client,
            )
        reactions_by_ticker[ticker] = reaction
        status = _text(reaction.get("status")) or "unknown"
        status_counts[status] = status_counts.get(status, 0) + len(
            ticker_to_records[ticker]
        )

    for ticker, ticker_records in ticker_to_records.items():
        reaction = reactions_by_ticker.get(ticker, {"status": "missing"})
        for record in ticker_records:
            _attach_price_reaction(record, reaction)

    stats["price_reaction_status_counts"] = dict(sorted(status_counts.items()))
    return records, stats
