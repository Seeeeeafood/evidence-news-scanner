from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from news_scanner_v2.breaking_hints import (
    breaking_hint_fetch_results,
    breaking_hint_texts,
    parse_breaking_hint_line,
    read_recent_breaking_hints,
)


class BreakingHintsTests(unittest.TestCase):
    def test_parse_breaking_hint_line_extracts_time_label_category_and_title(self) -> None:
        hint = parse_breaking_hint_line(
            (
                "- [00:42] [strategic_partnership] 'Kawasaki Heavy To Partner "
                "With NVIDIA On Physical AI, Open U.S. Robot Center; Japan "
                "Industrial Group's Joint Development Initiative Includes "
                "Microsoft, Fujitsu' - Nikkei Asia (Benzinga)"
            ),
            path=Path("breaking_2026-05-22.md"),
            line_no=8,
            file_date=datetime(2026, 5, 22).date(),
        )

        self.assertIsNotNone(hint)
        assert hint is not None
        self.assertEqual(hint.label, "strategic_partnership")
        self.assertEqual(hint.category, "STRAT")
        self.assertEqual(hint.published_at, "2026-05-22T00:42:00+09:00")
        self.assertIn("Kawasaki Heavy To Partner With NVIDIA", hint.title)
        self.assertIn("Nikkei Asia", hint.title)

    def test_read_recent_breaking_hints_groups_fetch_results_by_category(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            news_dir = root / "workspace" / "memory" / "news"
            news_dir.mkdir(parents=True)
            (news_dir / "breaking_2026-05-22.md").write_text(
                "\n".join(
                    [
                        (
                            "- [00:22] [geopolitical:conflict] United States "
                            "Secretary of State Marco Rubio Says Tolling System "
                            "In Strait Of Hormuz Would Make Diplomatic Deal "
                            "Unfeasible (Benzinga)"
                        ),
                        (
                            "- [00:42] [strategic_partnership] Kawasaki Heavy "
                            "To Partner With NVIDIA On Physical AI, Open U.S. "
                            "Robot Center - Nikkei Asia (Benzinga)"
                        ),
                    ]
                )
            )

            hints = read_recent_breaking_hints(
                root,
                as_of=datetime(2026, 5, 22, 1, 0, tzinfo=ZoneInfo("Asia/Seoul")),
            )
            results = breaking_hint_fetch_results(
                hints,
                started_at="2026-05-21T16:00:00+00:00",
                finished_at="2026-05-21T16:00:01+00:00",
            )

        self.assertEqual([hint.category for hint in hints], ["GEO", "STRAT"])
        self.assertEqual(breaking_hint_texts(hints), [hint.raw_line for hint in hints])
        self.assertEqual([result.source.name for result in results], ["breaking-hints-geo", "breaking-hints-strat"])
        self.assertEqual(results[0].items[0].provider, "breaking_hint")
        self.assertIn("Marco Rubio", results[0].items[0].title)
        self.assertIn("Kawasaki Heavy", results[1].items[0].title)


if __name__ == "__main__":
    unittest.main()
