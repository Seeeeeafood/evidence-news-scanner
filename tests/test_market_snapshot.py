from datetime import datetime
from zoneinfo import ZoneInfo
import unittest

from news_scanner_v2.market_snapshot import (
    finalize_market_snapshot,
    merge_missing_with_previous_snapshot,
    render_market_snapshot_lines,
    summarize_market_snapshot,
    _quote_from_fmp_payload,
    _quote_from_stooq_csv,
    _treasury_quote_from_fmp_payload,
)


class MarketSnapshotTests(unittest.TestCase):
    def test_fmp_quote_parser_keeps_value_change_and_timestamp(self) -> None:
        quote = _quote_from_fmp_payload(
            "sp500",
            "^GSPC",
            [
                {
                    "symbol": "^GSPC",
                    "name": "S&P 500",
                    "price": 7432.97,
                    "change": 79.3,
                    "changePercentage": 1.0792,
                    "timestamp": 1779307200,
                }
            ],
        )

        self.assertEqual(quote["status"], "ok")
        self.assertEqual(quote["provider"], "fmp")
        self.assertEqual(quote["symbol"], "^GSPC")
        self.assertEqual(quote["value"], 7432.97)
        self.assertEqual(quote["change_pct"], 1.0792)
        self.assertTrue(str(quote["source_time"]).endswith("+09:00"))

    def test_stooq_quote_parser_reads_wti_and_dxy_csv(self) -> None:
        quote = _quote_from_stooq_csv(
            "wti",
            "cl.f",
            (
                "Symbol,Date,Time,Open,High,Low,Close,Volume\n"
                "CL.F,2026-05-21,12:08:26,98.97,100.04,97.33,97.75,\n"
            ),
        )

        self.assertEqual(quote["status"], "ok")
        self.assertEqual(quote["provider"], "stooq")
        self.assertEqual(quote["symbol"], "CL.F")
        self.assertEqual(quote["value"], 97.75)
        self.assertEqual(quote["source_date"], "2026-05-21")

    def test_treasury_parser_extracts_ten_year(self) -> None:
        quote = _treasury_quote_from_fmp_payload(
            [{"date": "2026-05-20", "year10": 4.57}]
        )

        self.assertEqual(quote["status"], "ok")
        self.assertEqual(quote["provider"], "fmp")
        self.assertEqual(quote["symbol"], "US10Y")
        self.assertEqual(quote["value"], 4.57)

    def test_merge_missing_with_previous_snapshot_marks_stale(self) -> None:
        current = finalize_market_snapshot(
            {
                "as_of": "2026-05-21T22:30:00+09:00",
                "values": {
                    "sp500": {"status": "ok", "provider": "fmp", "value": 7432.97},
                    "usd_krw": {"status": "error", "provider": "fmp", "error": "HTTP 500"},
                },
            }
        )
        previous = finalize_market_snapshot(
            {
                "as_of": "2026-05-21T19:00:00+09:00",
                "values": {
                    "usd_krw": {
                        "status": "ok",
                        "provider": "fmp",
                        "value": 1501.2,
                    }
                },
            }
        )

        merged = merge_missing_with_previous_snapshot(current, previous)

        self.assertEqual(merged["values"]["usd_krw"]["status"], "stale")
        self.assertEqual(merged["values"]["usd_krw"]["value"], 1501.2)
        self.assertEqual(
            merged["values"]["usd_krw"]["stale_from_as_of"],
            "2026-05-21T19:00:00+09:00",
        )
        self.assertEqual(merged["status"], "partial")

    def test_render_market_snapshot_lines_uses_v1_style_blocks(self) -> None:
        snapshot = finalize_market_snapshot(
            {
                "as_of": datetime(2026, 5, 21, 22, 30, tzinfo=ZoneInfo("Asia/Seoul")).isoformat(),
                "values": {
                    "sp500": {"status": "ok", "provider": "fmp", "value": 7432.97, "change_pct": 1.0792},
                    "nasdaq": {"status": "ok", "provider": "fmp", "value": 26270.36, "change_pct": 1.5448},
                    "dow": {"status": "ok", "provider": "fmp", "value": 50009.35, "change_pct": 1.30755},
                    "wti": {"status": "ok", "provider": "stooq", "value": 97.75},
                    "brent": {"status": "ok", "provider": "fmp", "value": 104.17},
                    "gold": {"status": "ok", "provider": "fmp", "value": 4533.8},
                    "dxy": {"status": "ok", "provider": "stooq", "value": 99.075},
                    "ten_year": {"status": "ok", "provider": "fmp", "value": 4.57},
                    "vix": {"status": "ok", "provider": "fmp", "value": 17.19},
                    "usd_krw": {"status": "ok", "provider": "fmp", "value": 1503.68},
                },
            }
        )

        lines = render_market_snapshot_lines(snapshot)

        self.assertEqual(
            lines,
            [
                "📊 지수: S&P 7,433 (+1.08%) | NASDAQ 26,270 (+1.54%) | DOW 50,009 (+1.31%)",
                "💰 매크로: WTI $97.8 | Brent $104.2 | 금 $4,534 | DXY 99.1 | 10Y 4.57% | VIX 17.2",
                "💱 환율: USD/KRW 1,504",
            ],
        )

    def test_summary_reports_disabled_and_partial(self) -> None:
        disabled = summarize_market_snapshot(None, enabled=False)
        self.assertEqual(disabled["market_snapshot_status"], "disabled")

        partial = summarize_market_snapshot(
            finalize_market_snapshot(
                {
                    "values": {
                        "sp500": {"status": "ok", "provider": "fmp", "value": 7432.97}
                    }
                }
            ),
            enabled=True,
        )

        self.assertEqual(partial["market_snapshot_status"], "partial")
        self.assertEqual(partial["market_snapshot_values_ok"], 1)


if __name__ == "__main__":
    unittest.main()
