from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .events import normalize_text


_TRACKING_PREFIXES = ("utm_",)
_TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}


def canonicalize_url(url: str | None) -> str:
    if not url:
        return ""
    parts = urlsplit(url.strip())
    query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key in _TRACKING_KEYS or key.startswith(_TRACKING_PREFIXES):
            continue
        query.append((key, value))
    normalized_query = urlencode(query, doseq=True)
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path.rstrip("/") or parts.path,
            normalized_query,
            "",
        )
    )


@dataclass(frozen=True)
class CandidateItem:
    source: str
    category: str
    title: str
    provider: str = ""
    url: str = ""
    published_at: str = ""
    summary: str = ""

    @property
    def normalized_title(self) -> str:
        return normalize_text(self.title)

    @property
    def canonical_url(self) -> str:
        return canonicalize_url(self.url)

    @property
    def item_hash(self) -> str:
        payload = {
            "canonical_url": self.canonical_url,
            "normalized_title": self.normalized_title,
            "published_at": self.published_at[:10],
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return sha256(raw.encode("utf-8")).hexdigest()

    def record_id(self, run_id: str) -> str:
        raw = f"{run_id}|{self.item_hash}"
        return sha256(raw.encode("utf-8")).hexdigest()

    def as_record(self, run_id: str) -> dict[str, Any]:
        return {
            "id": self.record_id(run_id),
            "item_hash": self.item_hash,
            "source": self.source,
            "provider": self.provider,
            "category": self.category,
            "title": self.title,
            "normalized_title": self.normalized_title,
            "url": self.url,
            "canonical_url": self.canonical_url,
            "published_at": self.published_at,
            "summary": self.summary,
        }
