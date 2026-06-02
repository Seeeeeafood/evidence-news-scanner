from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import re
from urllib.parse import urlencode
import socket
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from .models import CandidateItem
from .sources import NewsSource


USER_AGENT = "EvidenceNewsScanner/0.1 (+https://github.com/Seeeeeafood/evidence-news-scanner)"
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class FetchResult:
    source: NewsSource
    status: str
    started_at: str
    finished_at: str
    items: tuple[CandidateItem, ...] = ()
    error: str | None = None
    http_status: int | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strip_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(node: ET.Element, *names: str) -> str:
    targets = {name.lower() for name in names}
    for child in list(node):
        if _strip_tag(child.tag) in targets:
            return _clean_text(child.text or "")
    return ""


def _clean_text(value: str) -> str:
    without_tags = _TAG_RE.sub(" ", value)
    return _SPACE_RE.sub(" ", without_tags).strip()


def _parse_date(value: str) -> str:
    if not value:
        return ""
    try:
        return parsedate_to_datetime(value).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, IndexError, AttributeError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat()
        except ValueError:
            return ""


def parse_feed_xml(raw: bytes, source: NewsSource) -> tuple[CandidateItem, ...]:
    root = ET.fromstring(raw)
    items: list[CandidateItem] = []

    for node in root.iter():
        tag = _strip_tag(node.tag)
        if tag not in {"item", "entry"}:
            continue

        title = _child_text(node, "title")
        link = _child_text(node, "link")
        if not link:
            for child in list(node):
                if _strip_tag(child.tag) == "link":
                    link = child.attrib.get("href", "")
                    if link:
                        break

        published = _parse_date(
            _child_text(node, "pubDate", "published", "updated", "dc:date")
        )
        summary = _child_text(node, "description", "summary", "content")

        if title:
            items.append(
                CandidateItem(
                    source=source.name,
                    category=source.category,
                    provider=source.provider,
                    title=title,
                    url=link,
                    published_at=published,
                    summary=summary,
                )
            )

    return tuple(items)


def _parse_brave_date(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    text = value.strip()
    parsed = _parse_date(text)
    if parsed:
        return parsed
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _brave_summary(result: dict) -> str:
    snippets = result.get("extra_snippets")
    if isinstance(snippets, list) and snippets:
        return _clean_text(" ".join(str(item) for item in snippets[:3]))
    description = result.get("description")
    if isinstance(description, str):
        return _clean_text(description)
    return ""


def parse_brave_news_json(raw: bytes, source: NewsSource) -> tuple[CandidateItem, ...]:
    data = json.loads(raw.decode("utf-8"))
    raw_results = data.get("results", [])
    if not isinstance(raw_results, list):
        raw_results = []

    items: list[CandidateItem] = []
    for result in raw_results:
        if not isinstance(result, dict):
            continue
        title = _clean_text(str(result.get("title") or ""))
        url = str(result.get("url") or "")
        published = _parse_brave_date(
            result.get("page_age")
            or result.get("age")
            or result.get("published")
            or result.get("date")
        )
        if title:
            items.append(
                CandidateItem(
                    source=source.name,
                    category=source.category,
                    provider=source.provider,
                    title=title,
                    url=url,
                    published_at=published,
                    summary=_brave_summary(result),
                )
            )
    return tuple(items)


def _brave_url(source: NewsSource) -> str:
    params = {
        "q": source.query,
        "country": source.country,
        "search_lang": source.search_lang,
        "ui_lang": "en-US",
        "freshness": source.freshness,
        "count": str(source.count),
        "safesearch": "moderate",
        "extra_snippets": "true",
    }
    return f"{source.url}?{urlencode(params)}"


def fetch_brave_news(source: NewsSource, *, api_key: str | None) -> FetchResult:
    started_at = _utc_now()
    if not api_key:
        return FetchResult(
            source=source,
            status="skipped_missing_key",
            started_at=started_at,
            finished_at=_utc_now(),
            error="Brave API key not configured",
        )

    request = urllib.request.Request(
        _brave_url(source),
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "User-Agent": USER_AGENT,
            "X-Subscription-Token": api_key,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=source.timeout_seconds) as response:
            raw = response.read(2_000_000)
            status = getattr(response, "status", None)
        items = parse_brave_news_json(raw, source)
        return FetchResult(
            source=source,
            status="ok",
            started_at=started_at,
            finished_at=_utc_now(),
            items=items,
            http_status=status,
        )
    except urllib.error.HTTPError as exc:
        return FetchResult(
            source=source,
            status="error",
            started_at=started_at,
            finished_at=_utc_now(),
            error=f"HTTPError: HTTP {exc.code}",
            http_status=exc.code,
        )
    except (
        urllib.error.URLError,
        TimeoutError,
        socket.timeout,
        json.JSONDecodeError,
        OSError,
    ) as exc:
        return FetchResult(
            source=source,
            status="error",
            started_at=started_at,
            finished_at=_utc_now(),
            error=f"{exc.__class__.__name__}: {exc}",
        )


def fetch_source(source: NewsSource, *, brave_api_key: str | None = None) -> FetchResult:
    if source.kind == "brave_news":
        return fetch_brave_news(source, api_key=brave_api_key)

    started_at = _utc_now()
    request = urllib.request.Request(source.url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=source.timeout_seconds) as response:
            raw = response.read(2_000_000)
        items = parse_feed_xml(raw, source)
        return FetchResult(
            source=source,
            status="ok",
            started_at=started_at,
            finished_at=_utc_now(),
            items=items,
        )
    except (urllib.error.URLError, TimeoutError, ET.ParseError, OSError) as exc:
        return FetchResult(
            source=source,
            status="error",
            started_at=started_at,
            finished_at=_utc_now(),
            error=f"{exc.__class__.__name__}: {exc}",
        )


def fetch_sources(
    sources: tuple[NewsSource, ...],
    *,
    brave_api_key: str | None = None,
) -> tuple[FetchResult, ...]:
    return tuple(fetch_source(source, brave_api_key=brave_api_key) for source in sources)
