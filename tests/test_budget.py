import unittest

from news_scanner_v2.budget import (
    count_brave_sources,
    estimate_brave_cost_usd,
    evaluate_brave_budget,
)
from news_scanner_v2.sources import BRAVE_NEWS_SOURCES, DEFAULT_SOURCES


class BudgetTests(unittest.TestCase):
    def test_default_brave_sources_are_capped_at_seven(self) -> None:
        self.assertEqual(count_brave_sources(DEFAULT_SOURCES), 7)
        budget = evaluate_brave_budget(DEFAULT_SOURCES, brave_enabled=True)
        self.assertEqual(budget.status, "ok")
        self.assertEqual(budget.planned_requests, 7)
        self.assertEqual(budget.estimated_cost_usd, 0.035)

    def test_estimate_brave_cost(self) -> None:
        self.assertEqual(estimate_brave_cost_usd(7), 0.035)
        self.assertEqual(estimate_brave_cost_usd(1260), 6.3)

    def test_budget_blocks_extra_brave_sources(self) -> None:
        sources = BRAVE_NEWS_SOURCES + BRAVE_NEWS_SOURCES[:1]
        budget = evaluate_brave_budget(
            sources,
            brave_enabled=True,
            max_requests=7,
        )
        self.assertEqual(budget.status, "limit_exceeded")
        self.assertEqual(budget.planned_requests, 8)

    def test_budget_disabled_has_zero_planned_requests(self) -> None:
        budget = evaluate_brave_budget(DEFAULT_SOURCES, brave_enabled=False)
        self.assertEqual(budget.status, "disabled")
        self.assertEqual(budget.planned_requests, 0)
        self.assertEqual(budget.estimated_cost_usd, 0.0)


if __name__ == "__main__":
    unittest.main()
