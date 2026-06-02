from datetime import date, datetime, timezone
import unittest
from zoneinfo import ZoneInfo

from news_scanner_v2.price_reaction import (
    build_price_reaction_from_bars,
    enrich_decision_records_with_price_reactions,
    fetch_price_reaction,
)


def _ts(year: int, month: int, day: int, hour: int, minute: int = 0) -> int:
    return int(
        datetime(
            year,
            month,
            day,
            hour,
            minute,
            tzinfo=timezone.utc,
        ).timestamp()
        * 1000
    )


def _bar(
    year: int,
    month: int,
    day: int,
    close: float,
    *,
    hour: int = 20,
    minute: int = 0,
) -> dict:
    return {"t": _ts(year, month, day, hour, minute), "c": close}


class FakePriceClient:
    def __init__(self, daily_bars, intraday_bars):
        self.daily_bars = daily_bars
        self.intraday_bars = intraday_bars
        self.calls = []

    def aggregate_bars(self, *, ticker, multiplier, timespan, from_date, to_date):
        self.calls.append(
            {
                "ticker": ticker,
                "multiplier": multiplier,
                "timespan": timespan,
                "from_date": from_date,
                "to_date": to_date,
            }
        )
        if timespan == "day":
            return self.daily_bars
        return self.intraday_bars


class PriceReactionTests(unittest.TestCase):
    def test_intraday_price_reaction_uses_previous_daily_close(self) -> None:
        reaction = build_price_reaction_from_bars(
            ticker="UNH",
            daily_bars=[
                _bar(2026, 5, 12, 95.0),
                _bar(2026, 5, 13, 100.0),
            ],
            intraday_bars=[_bar(2026, 5, 14, 105.0, hour=14, minute=0)],
            as_of=datetime(2026, 5, 14, 23, 30, tzinfo=ZoneInfo("Asia/Seoul")),
        )

        self.assertEqual(reaction["status"], "ok")
        self.assertEqual(reaction["session"], "intraday_5min")
        self.assertEqual(reaction["previous_close"], 100.0)
        self.assertEqual(reaction["close"], 105.0)
        self.assertEqual(reaction["pct_change"], 5.0)
        self.assertEqual(reaction["direction"], "up")

    def test_missing_intraday_during_regular_session_fails_closed(self) -> None:
        reaction = build_price_reaction_from_bars(
            ticker="UNH",
            daily_bars=[
                _bar(2026, 5, 12, 95.0),
                _bar(2026, 5, 13, 100.0),
            ],
            intraday_bars=[],
            as_of=datetime(2026, 5, 14, 23, 30, tzinfo=ZoneInfo("Asia/Seoul")),
        )

        self.assertEqual(reaction["status"], "intraday_unavailable")

    def test_daily_fallback_outside_regular_session(self) -> None:
        reaction = build_price_reaction_from_bars(
            ticker="NVDA",
            daily_bars=[
                _bar(2026, 5, 13, 100.0),
                _bar(2026, 5, 14, 98.0),
            ],
            intraday_bars=[],
            as_of=datetime(2026, 5, 15, 11, 0, tzinfo=ZoneInfo("Asia/Seoul")),
        )

        self.assertEqual(reaction["status"], "ok")
        self.assertEqual(reaction["session"], "daily")
        self.assertEqual(reaction["pct_change"], -2.0)
        self.assertEqual(reaction["direction"], "down")

    def test_fetch_price_reaction_uses_injected_client_without_api_key(self) -> None:
        client = FakePriceClient(
            daily_bars=[
                _bar(2026, 5, 12, 95.0),
                _bar(2026, 5, 13, 100.0),
            ],
            intraday_bars=[_bar(2026, 5, 14, 97.5, hour=14, minute=0)],
        )

        reaction = fetch_price_reaction(
            ticker="UNH",
            api_key=None,
            as_of=datetime(2026, 5, 14, 23, 30, tzinfo=ZoneInfo("Asia/Seoul")),
            client=client,
        )

        self.assertEqual(reaction["status"], "ok")
        self.assertEqual(reaction["direction"], "down")
        self.assertEqual([call["timespan"] for call in client.calls], ["day", "minute"])
        self.assertEqual(client.calls[0]["from_date"], date(2026, 4, 23))

    def test_enrich_decision_records_attaches_price_reaction_to_payload(self) -> None:
        record = {
            "id": "d1",
            "run_id": "r1",
            "event_signature": "e1",
            "decision": "send_candidate",
            "payload": {
                "event": {
                    "event_type": "earnings",
                    "subject": "nvda",
                    "action": "guidance_raise",
                    "metadata": {},
                }
            },
        }
        client = FakePriceClient(
            daily_bars=[
                _bar(2026, 5, 12, 95.0),
                _bar(2026, 5, 13, 100.0),
            ],
            intraday_bars=[_bar(2026, 5, 14, 106.0, hour=14, minute=0)],
        )

        records, stats = enrich_decision_records_with_price_reactions(
            [record],
            enabled=True,
            api_key=None,
            as_of=datetime(2026, 5, 14, 23, 30, tzinfo=ZoneInfo("Asia/Seoul")),
            client=client,
        )

        reaction = records[0]["payload"]["price_reaction"]
        self.assertEqual(reaction["status"], "ok")
        self.assertEqual(reaction["ticker"], "NVDA")
        self.assertEqual(
            records[0]["payload"]["event"]["metadata"]["price_reaction"],
            reaction,
        )
        self.assertEqual(stats["price_reaction_status_counts"], {"ok": 1})


if __name__ == "__main__":
    unittest.main()
