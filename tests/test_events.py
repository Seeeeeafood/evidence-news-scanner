import unittest

from news_scanner_v2.events import MarketEvent, normalize_text, normalize_token
from news_scanner_v2.models import canonicalize_url


class EventSignatureTests(unittest.TestCase):
    def test_normalizers_are_stable(self) -> None:
        self.assertEqual(normalize_text("  CPI   Surprise "), "cpi surprise")
        self.assertEqual(normalize_token("Trump/Xi Summit"), "trump_xi_summit")

    def test_signature_ignores_title_noise(self) -> None:
        first = MarketEvent(
            event_type="macro",
            subject="CPI",
            effective_date="2026-05-14",
            period="Apr 2026",
            title="US CPI comes in soft",
        )
        second = MarketEvent(
            event_type=" Macro ",
            subject=" cpi ",
            effective_date="2026-05-14",
            period="APR 2026",
            title="Different headline",
        )
        self.assertEqual(first.signature(), second.signature())

    def test_signature_changes_for_period(self) -> None:
        first = MarketEvent(
            event_type="earnings",
            subject="AAPL",
            effective_date="2026-05-14",
            period="Q2 2026",
        )
        second = MarketEvent(
            event_type="earnings",
            subject="AAPL",
            effective_date="2026-05-14",
            period="Q3 2026",
        )
        self.assertNotEqual(first.signature(), second.signature())

    def test_canonicalize_url_drops_tracking_keys(self) -> None:
        self.assertEqual(
            canonicalize_url("https://Example.com/path/?utm_source=x&a=1#frag"),
            "https://example.com/path?a=1",
        )


if __name__ == "__main__":
    unittest.main()
