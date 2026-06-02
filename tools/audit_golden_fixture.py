from __future__ import annotations

import argparse
import json
from pathlib import Path

from news_scanner_v2.quality_golden import audit_golden_fixture


DEFAULT_DB_PATH = Path(".evidence-news-scanner/state/news_scanner_v2.sqlite")
DEFAULT_FIXTURE_PATH = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / (
    "news_quality_golden_20260521.json"
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE_PATH)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when any fixture check fails.",
    )
    args = parser.parse_args()

    report = audit_golden_fixture(args.db_path, args.fixture)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    else:
        print(rendered, end="")
    if args.strict and report["summary"]["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
