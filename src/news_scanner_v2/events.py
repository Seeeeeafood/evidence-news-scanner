from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any


_SPACE_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"[^a-z0-9._:-]+")


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return _SPACE_RE.sub(" ", value.strip().lower())


def normalize_token(value: str | None) -> str:
    text = normalize_text(value)
    return _TOKEN_RE.sub("_", text).strip("_")


@dataclass(frozen=True)
class MarketEvent:
    event_type: str
    subject: str
    effective_date: str
    scope: str = "market"
    period: str = ""
    action: str = ""
    object: str = ""
    stage: str = ""
    source: str = ""
    title: str = ""
    url: str = ""
    published_at: str = ""
    metadata: dict[str, Any] | None = None

    def signature_payload(self) -> dict[str, str]:
        return {
            "event_type": normalize_token(self.event_type),
            "subject": normalize_token(self.subject),
            "effective_date": normalize_token(self.effective_date),
            "scope": normalize_token(self.scope),
            "period": normalize_token(self.period),
            "action": normalize_token(self.action),
            "object": normalize_token(self.object),
            "stage": normalize_token(self.stage),
        }

    def signature(self) -> str:
        payload = self.signature_payload()
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return sha256(raw.encode("utf-8")).hexdigest()

    def as_record(self) -> dict[str, Any]:
        record = {
            "signature": self.signature(),
            "payload": self.signature_payload(),
            "source": self.source,
            "title": self.title,
            "url": self.url,
            "published_at": self.published_at,
        }
        if self.metadata:
            record["metadata"] = self.metadata
        return record
