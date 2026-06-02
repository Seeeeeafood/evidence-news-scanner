from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
import re
import socket
from typing import Any
from urllib import error, request
from urllib.parse import urlsplit

from .fetcher import USER_AGENT


DEFAULT_MAX_BODY_FETCHES_PER_RUN = 6
DEFAULT_BODY_FETCH_TIMEOUT_SECONDS = 4.0
MAX_ARTICLE_BYTES = 2_000_000
MAX_STORED_BODY_CHARS = 5_000
MIN_USABLE_BODY_CHARS = 300
FULL_BODY_CHARS = 900

_SPACE_RE = re.compile(r"\s+")
_SCRIPT_STYLE_RE = re.compile(
    r"<(?:script|style|svg|noscript)\b[^>]*>.*?</(?:script|style|svg|noscript)>",
    re.I | re.S,
)
_TAG_RE = re.compile(r"<[^>]+>")
_FIXTURE_DOMAINS = {"example.com", "example.org", "example.net"}


@dataclass(frozen=True)
class BodyFetchResult:
    url: str
    status: str
    fetched_at: str
    body_text: str = ""
    text_chars: int = 0
    http_status: int | None = None
    error: str | None = None

    @property
    def usable(self) -> bool:
        return self.status in {"full", "partial"}

    def as_payload(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "status": self.status,
            "fetched_at": self.fetched_at,
            "text_chars": self.text_chars,
            "http_status": self.http_status,
            "error": self.error or "",
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_text(value: str) -> str:
    return _SPACE_RE.sub(" ", value).strip()


def _truncate_body(text: str) -> str:
    cleaned = _clean_text(text)
    if len(cleaned) <= MAX_STORED_BODY_CHARS:
        return cleaned
    return cleaned[:MAX_STORED_BODY_CHARS].rsplit(" ", 1)[0].strip()


def _charset_from_content_type(content_type: str) -> str:
    for part in content_type.split(";"):
        key, _, value = part.strip().partition("=")
        if key.lower() == "charset" and value.strip():
            return value.strip().strip('"')
    return "utf-8"


def _decode_response(raw: bytes, content_type: str) -> str:
    charset = _charset_from_content_type(content_type)
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


class _ArticleHTMLParser(HTMLParser):
    block_tags = {"p", "li", "h1", "h2", "h3"}
    ignored_tags = {"script", "style", "svg", "noscript", "nav", "footer", "header"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignore_depth = 0
        self._block_stack: list[str] = []
        self._current: list[str] = []
        self.blocks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered in self.ignored_tags:
            self._ignore_depth += 1
            return
        if self._ignore_depth:
            return
        if lowered in self.block_tags:
            self._block_stack.append(lowered)
            self._current = []

    def handle_data(self, data: str) -> None:
        if self._ignore_depth or not self._block_stack:
            return
        if data.strip():
            self._current.append(data)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in self.ignored_tags and self._ignore_depth:
            self._ignore_depth -= 1
            return
        if self._ignore_depth:
            return
        if self._block_stack and lowered == self._block_stack[-1]:
            block = _clean_text(" ".join(self._current))
            if len(block) >= 40:
                self.blocks.append(block)
            self._block_stack.pop()
            self._current = []


def extract_article_text(raw: bytes, *, content_type: str = "") -> str:
    html = _decode_response(raw, content_type)
    parser = _ArticleHTMLParser()
    parser.feed(html)
    seen: set[str] = set()
    blocks = []
    for block in parser.blocks:
        fingerprint = block.lower()[:120]
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        blocks.append(block)
    paragraph_text = _clean_text(" ".join(blocks))
    if len(paragraph_text) >= MIN_USABLE_BODY_CHARS:
        return _truncate_body(paragraph_text)

    stripped = _SCRIPT_STYLE_RE.sub(" ", html)
    fallback = _clean_text(_TAG_RE.sub(" ", stripped))
    return _truncate_body(fallback)


def _body_status(text: str) -> str:
    if len(text) >= FULL_BODY_CHARS:
        return "full"
    if len(text) >= MIN_USABLE_BODY_CHARS:
        return "partial"
    return "weak"


def _skipped_result(url: str, status: str, error_text: str = "") -> BodyFetchResult:
    return BodyFetchResult(
        url=url,
        status=status,
        fetched_at=_utc_now(),
        error=error_text,
    )


def _skip_reason(url: str) -> str:
    if not url.strip():
        return "skipped_empty_url"
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return "skipped_invalid_url"
    if parts.scheme not in {"http", "https"}:
        return "skipped_non_http"
    host = parts.netloc.lower().split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    if host in _FIXTURE_DOMAINS or host.endswith(".test") or host in {"localhost"}:
        return "skipped_fixture_url"
    return ""


def fetch_article_body(
    url: str,
    *,
    timeout_seconds: float = DEFAULT_BODY_FETCH_TIMEOUT_SECONDS,
) -> BodyFetchResult:
    skip_reason = _skip_reason(url)
    if skip_reason:
        return _skipped_result(url, skip_reason)

    req = request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.8",
        },
    )
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            raw = response.read(MAX_ARTICLE_BYTES)
            http_status = getattr(response, "status", None)
            content_type = response.headers.get("Content-Type", "")
    except error.HTTPError as exc:
        return BodyFetchResult(
            url=url,
            status="error_http",
            fetched_at=_utc_now(),
            http_status=exc.code,
            error=f"HTTP {exc.code}",
        )
    except (error.URLError, TimeoutError, socket.timeout, OSError) as exc:
        return BodyFetchResult(
            url=url,
            status="error_network",
            fetched_at=_utc_now(),
            error=f"{exc.__class__.__name__}: {exc}",
        )

    text = extract_article_text(raw, content_type=content_type)
    status = _body_status(text)
    return BodyFetchResult(
        url=url,
        status=status,
        fetched_at=_utc_now(),
        body_text=text if status in {"full", "partial"} else "",
        text_chars=len(text),
        http_status=http_status,
    )


def send_candidate_ids(decisions: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    selected = []
    for decision in decisions:
        if decision.get("decision") != "send_candidate":
            continue
        payload = decision.get("payload", {})
        if not isinstance(payload, dict):
            continue
        candidate_ids = payload.get("candidate_ids", [])
        if not isinstance(candidate_ids, list):
            continue
        for candidate_id in candidate_ids:
            text = str(candidate_id or "")
            if text and text not in seen:
                seen.add(text)
                selected.append(text)
    return selected


def _decision_score(decision: dict[str, Any]) -> float:
    try:
        return float(decision.get("score") or 0)
    except (TypeError, ValueError):
        return 0.0


def _decision_event_field(decision: dict[str, Any], key: str) -> str:
    payload = decision.get("payload", {})
    if not isinstance(payload, dict):
        return ""
    event = payload.get("event", {})
    if not isinstance(event, dict):
        return ""
    return str(event.get(key) or "")


def prioritized_send_candidate_ids(
    decisions: list[dict[str, Any]],
    *,
    sent_event_signatures: set[str],
) -> list[str]:
    prioritized = []
    for order, decision in enumerate(decisions):
        if decision.get("decision") != "send_candidate":
            continue
        payload = decision.get("payload", {})
        if not isinstance(payload, dict):
            continue
        candidate_ids = payload.get("candidate_ids", [])
        if not isinstance(candidate_ids, list):
            continue
        event_signature = str(decision.get("event_signature") or "")
        prioritized.append(
            (
                event_signature in sent_event_signatures,
                -_decision_score(decision),
                _decision_event_field(decision, "event_type"),
                _decision_event_field(decision, "subject"),
                event_signature,
                order,
                candidate_ids,
            )
        )

    seen: set[str] = set()
    selected = []
    for *_, candidate_ids in sorted(prioritized):
        for candidate_id in candidate_ids:
            text = str(candidate_id or "")
            if text and text not in seen:
                seen.add(text)
                selected.append(text)
    return selected


def enrich_candidate_records_with_bodies(
    records: list[dict[str, Any]],
    *,
    candidate_ids: list[str],
    max_fetches: int = DEFAULT_MAX_BODY_FETCHES_PER_RUN,
    timeout_seconds: float = DEFAULT_BODY_FETCH_TIMEOUT_SECONDS,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    selected_ids = candidate_ids[: max(0, max_fetches)]
    selected = set(selected_ids)
    stats = {
        "body_fetch_candidates": len(candidate_ids),
        "body_fetch_attempts": 0,
        "body_fetch_full": 0,
        "body_fetch_partial": 0,
        "body_fetch_weak": 0,
        "body_fetch_errors": 0,
        "body_fetch_skipped": max(0, len(candidate_ids) - len(selected_ids)),
    }
    if max_fetches <= 0 or not selected:
        stats["body_fetch_skipped"] = len(candidate_ids)
        return [dict(record) for record in records], stats

    enriched = []
    for record in records:
        item = dict(record)
        if str(item.get("id") or "") not in selected:
            enriched.append(item)
            continue
        result = fetch_article_body(
            str(item.get("url") or ""),
            timeout_seconds=timeout_seconds,
        )
        item["body_fetch"] = result.as_payload()
        if result.body_text:
            item["body_text"] = result.body_text
        if result.status in {"full", "partial", "weak", "error_http", "error_network"}:
            stats["body_fetch_attempts"] += 1
        if result.status == "full":
            stats["body_fetch_full"] += 1
        elif result.status == "partial":
            stats["body_fetch_partial"] += 1
        elif result.status == "weak":
            stats["body_fetch_weak"] += 1
        elif result.status.startswith("error_"):
            stats["body_fetch_errors"] += 1
        elif result.status.startswith("skipped_"):
            stats["body_fetch_skipped"] += 1
        enriched.append(item)
    return enriched, stats
