from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from .config import KST_TZ
from .fetcher import FetchResult
from .models import CandidateItem
from .sources import NewsSource


BREAKING_HINT_PROVIDER = "breaking_hint"
BREAKING_HINT_SOURCE_PREFIX = "breaking-hints"
DEFAULT_MAX_BREAKING_HINT_LINES = 16
DEFAULT_MAX_BREAKING_HINT_DAYS = 2

_TIME_RE = re.compile(r"\[(\d{1,2}):(\d{2})\]")
_LABEL_RE = re.compile(r"\[([A-Za-z0-9_:-]+)\]")
_BULLET_RE = re.compile(r"^\s*[-*]\s*")
_QUOTE_RE = re.compile(r"^[\"'`]+|[\"'`]+$")


@dataclass(frozen=True)
class BreakingHint:
    path: Path
    line_no: int
    file_date: date
    raw_line: str
    label: str
    category: str
    title: str
    published_at: str

    @property
    def source_name(self) -> str:
        return f"{BREAKING_HINT_SOURCE_PREFIX}-{self.category.lower()}"

    @property
    def source_url(self) -> str:
        return f"breaking-hint://{self.path.name}:{self.line_no}"

    def as_candidate(self) -> CandidateItem:
        summary = (
            f"label={self.label}; file={self.path.name}:{self.line_no}; "
            f"raw={self.raw_line[:600]}"
        )
        return CandidateItem(
            source=self.source_name,
            category=self.category,
            provider=BREAKING_HINT_PROVIDER,
            title=self.title,
            url=self.source_url,
            published_at=self.published_at,
            summary=summary,
        )


def _category_for_label(label: str, title: str) -> str:
    text = f"{label} {title}".lower()
    if any(token in text for token in ("earnings", "guidance", "eps", "revenue")):
        return "EARN"
    if any(
        token in text
        for token in (
            "m&a",
            "acquisition",
            "merger",
            "buyback",
            "repurchase",
            "ipo",
        )
    ):
        return "MA"
    if any(
        token in text
        for token in (
            "analyst",
            "price_target",
            "upgrade",
            "downgrade",
            "initiates",
        )
    ):
        return "ANAL"
    if any(token in text for token in ("mover", "shares_up", "shares_down")):
        return "MOVE"
    if any(
        token in text
        for token in (
            "macro",
            "fomc",
            "cpi",
            "pce",
            "treasury",
            "brent",
            "wti",
            "gold",
            "dxy",
            "vix",
            "usd/krw",
        )
    ):
        return "MACRO"
    if any(
        token in text
        for token in (
            "geopolitical",
            "regulatory",
            "sanction",
            "tariff",
            "conflict",
            "hormuz",
            "iran",
            "china",
            "taiwan",
            "nato",
        )
    ):
        return "GEO"
    if any(
        token in text
        for token in (
            "strategic",
            "partnership",
            "partner",
            "deal",
            "product_launch",
            "licensing",
            "physical ai",
            "robot",
        )
    ):
        return "STRAT"
    return "STRAT"


def _published_at_for_line(raw_line: str, file_date: date) -> str:
    match = _TIME_RE.search(raw_line)
    if not match:
        return datetime.combine(
            file_date,
            time(0, 0),
            tzinfo=ZoneInfo(KST_TZ),
        ).isoformat()
    hour = int(match.group(1))
    minute = int(match.group(2))
    return datetime.combine(
        file_date,
        time(hour, minute),
        tzinfo=ZoneInfo(KST_TZ),
    ).isoformat()


def _clean_title(value: str) -> str:
    text = " ".join(value.strip().split())
    text = _QUOTE_RE.sub("", text).strip()
    text = text.replace("’", "'").replace("“", '"').replace("”", '"')
    return " ".join(text.split())


def parse_breaking_hint_line(
    raw_line: str,
    *,
    path: Path,
    line_no: int,
    file_date: date,
) -> BreakingHint | None:
    line = " ".join(raw_line.strip().split())
    if len(line) < 20 or line.startswith("#"):
        return None

    text = _BULLET_RE.sub("", line)
    time_match = _TIME_RE.search(text)
    if time_match:
        text = text[time_match.end() :].strip()

    label = ""
    label_match = _LABEL_RE.search(text)
    if label_match:
        label = label_match.group(1).strip()
        title = text[label_match.end() :].strip()
    else:
        title = text

    title = _clean_title(title)
    if len(title) < 12:
        return None

    category = _category_for_label(label, title)
    return BreakingHint(
        path=path,
        line_no=line_no,
        file_date=file_date,
        raw_line=line,
        label=label,
        category=category,
        title=title,
        published_at=_published_at_for_line(line, file_date),
    )


def read_recent_breaking_hints(
    legacy_root: Path,
    *,
    as_of: datetime,
    max_days: int = DEFAULT_MAX_BREAKING_HINT_DAYS,
    max_lines: int = DEFAULT_MAX_BREAKING_HINT_LINES,
) -> list[BreakingHint]:
    if max_days <= 0 or max_lines <= 0:
        return []
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=ZoneInfo(KST_TZ))
    news_dir = legacy_root / "workspace" / "memory" / "news"
    hints: list[BreakingHint] = []
    seen: set[str] = set()
    for day_offset in range(max_days):
        file_date = (as_of - timedelta(days=day_offset)).astimezone(
            ZoneInfo(KST_TZ)
        ).date()
        path = news_dir / f"breaking_{file_date.isoformat()}.md"
        try:
            lines = path.read_text(errors="ignore").splitlines()
        except OSError:
            continue
        for line_no, raw_line in enumerate(lines, start=1):
            hint = parse_breaking_hint_line(
                raw_line,
                path=path,
                line_no=line_no,
                file_date=file_date,
            )
            if hint is None:
                continue
            key = hint.title.lower()
            if key in seen:
                continue
            seen.add(key)
            hints.append(hint)
            if len(hints) >= max_lines:
                return hints
    return hints


def breaking_hint_texts(
    hints: list[BreakingHint],
    *,
    max_lines: int = 12,
    max_chars: int = 500,
) -> list[str]:
    if max_lines <= 0:
        return []
    return [hint.raw_line[:max_chars] for hint in hints[:max_lines]]


def breaking_hint_fetch_results(
    hints: list[BreakingHint],
    *,
    started_at: str,
    finished_at: str,
) -> tuple[FetchResult, ...]:
    grouped: dict[str, list[BreakingHint]] = {}
    for hint in hints:
        grouped.setdefault(hint.category, []).append(hint)

    results: list[FetchResult] = []
    for category in sorted(grouped):
        category_hints = grouped[category]
        source = NewsSource(
            name=f"{BREAKING_HINT_SOURCE_PREFIX}-{category.lower()}",
            category=category,
            provider=BREAKING_HINT_PROVIDER,
            kind="local",
            url=str(category_hints[0].path),
        )
        results.append(
            FetchResult(
                source=source,
                status="ok",
                started_at=started_at,
                finished_at=finished_at,
                items=tuple(hint.as_candidate() for hint in category_hints),
            )
        )
    return tuple(results)
