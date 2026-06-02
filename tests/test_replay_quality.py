from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


def _load_replay_quality_module():
    path = Path(__file__).resolve().parents[1] / "tools" / "replay_quality.py"
    spec = importlib.util.spec_from_file_location("replay_quality", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _Decision:
    event_signature = "event-1"
    decision = "send_candidate"
    score = 77.0
    reason = "send:recall_trusted_earnings"
    policy = "dispatch_rules_v1"
    payload = {
        "event": {
            "signature": "event-1",
            "event_type": "earnings",
            "subject": "crm",
            "effective_date": "2026-05-28",
            "action": "guidance_update",
            "object": "",
            "title": "Salesforce shares dip on soft revenue outlook",
            "url": "https://www.marketwatch.com/story/crm",
            "metadata": {"freshness": {"status": "unknown"}},
        },
        "evidence_count": 2,
        "candidate_ids": ["candidate-a", "candidate-b"],
        "ranked_candidate_ids": ["candidate-b", "candidate-a"],
        "providers": ["brave"],
        "sources": ["MarketWatch"],
        "grade": "B",
        "risk_flags": [],
        "source_tier": "trusted",
        "event_quality": "hard_event",
        "hard_event_reason": "recall_trusted_earnings",
        "soft_analysis_reason": "",
        "send_worthy_reason": "send:recall_trusted_earnings",
        "verification": {"status": "verified"},
        "score_reasons": ["trusted_source:+15"],
        "extractor_reasons": ["EARN:company_alias:guidance_update"],
    }

    def decision_id(self, run_id: str) -> str:
        return f"{run_id}:{self.event_signature}"


class ReplayQualityRowTests(unittest.TestCase):
    def test_row_from_decision_preserves_delivery_sort_metadata(self) -> None:
        replay_quality = _load_replay_quality_module()
        candidates_by_id = {
            "candidate-a": {
                "id": "candidate-a",
                "source": "MarketWatch",
                "provider": "brave",
                "category": "EARN",
                "title": "Earlier Salesforce earnings item",
                "url": "https://www.marketwatch.com/a",
                "published_at": "2026-05-28T09:00:00+09:00",
                "raw_json": '{"summary": "earlier summary"}',
            },
            "candidate-b": {
                "id": "candidate-b",
                "source": "MarketWatch",
                "provider": "brave",
                "category": "EARN",
                "title": "Ranked Salesforce guidance item",
                "url": "https://www.marketwatch.com/b",
                "published_at": "2026-05-28T09:30:00+09:00",
                "raw_json": '{"summary": "ranked summary"}',
            },
        }

        row = replay_quality._row_from_decision(
            run_id="run-1",
            decision=_Decision(),
            candidates_by_id=candidates_by_id,
        )

        self.assertEqual(row["event_quality"], "hard_event")
        self.assertEqual(row["hard_event_reason"], "recall_trusted_earnings")
        self.assertEqual(row["send_worthy_reason"], "send:recall_trusted_earnings")
        self.assertEqual(row["verification_status"], "verified")
        self.assertEqual(row["ranked_candidate_ids"], ["candidate-b", "candidate-a"])
        self.assertEqual(
            [item["candidate_id"] for item in row["evidence_items"]],
            ["candidate-b", "candidate-a"],
        )
