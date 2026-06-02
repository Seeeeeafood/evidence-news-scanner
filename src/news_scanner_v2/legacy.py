from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import KST_TZ, LEGACY_JOB_IDS


@dataclass(frozen=True)
class LegacyJobSnapshot:
    id: str
    name: str
    enabled: bool
    schedule_expr: str
    schedule_tz: str
    delivery_mode: str
    session_target: str
    wake_mode: str
    model: str
    timeout_seconds: int
    prompt_chars: int
    prompt_sha256_12: str
    state: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "enabled": self.enabled,
            "schedule_expr": self.schedule_expr,
            "schedule_tz": self.schedule_tz,
            "delivery_mode": self.delivery_mode,
            "session_target": self.session_target,
            "wake_mode": self.wake_mode,
            "model": self.model,
            "timeout_seconds": self.timeout_seconds,
            "prompt_chars": self.prompt_chars,
            "prompt_sha256_12": self.prompt_sha256_12,
            "state": self.state,
        }


def _ms_to_kst(ms: Any) -> str | None:
    if not isinstance(ms, (int, float)):
        return None
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(ZoneInfo(KST_TZ))
    return dt.isoformat()


def _sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _line_count(path: Path) -> int | None:
    if not path.exists() or not path.is_file():
        return None
    with path.open("rb") as handle:
        return sum(1 for _ in handle)


def _trace_file_count(root: Path, job_id: str) -> int:
    trace_root = root / "cron" / "traces" / job_id
    if not trace_root.exists():
        return 0
    return sum(
        1
        for path in trace_root.rglob("*")
        if path.is_file() and (path.name.endswith(".jsonl") or path.name.endswith(".jsonl.gz"))
    )


def load_legacy_jobs(root: Path) -> list[LegacyJobSnapshot]:
    jobs_path = root / "cron" / "jobs.json"
    if not jobs_path.exists():
        return []
    data = json.loads(jobs_path.read_text())
    selected = set(LEGACY_JOB_IDS.values())
    snapshots: list[LegacyJobSnapshot] = []

    for job in data.get("jobs", []):
        if job.get("id") not in selected:
            continue
        payload = job.get("payload", {})
        message = payload.get("message", "")
        schedule = job.get("schedule", {})
        delivery = job.get("delivery", {})
        state = dict(job.get("state", {}))
        state["lastRunAtKst"] = _ms_to_kst(state.get("lastRunAtMs"))
        state["nextRunAtKst"] = _ms_to_kst(state.get("nextRunAtMs"))

        snapshots.append(
            LegacyJobSnapshot(
                id=job.get("id", ""),
                name=job.get("name", ""),
                enabled=bool(job.get("enabled")),
                schedule_expr=schedule.get("expr", ""),
                schedule_tz=schedule.get("tz", ""),
                delivery_mode=delivery.get("mode", ""),
                session_target=job.get("sessionTarget", ""),
                wake_mode=job.get("wakeMode", ""),
                model=payload.get("model", ""),
                timeout_seconds=int(payload.get("timeoutSeconds", 0) or 0),
                prompt_chars=len(message),
                prompt_sha256_12=sha256(message.encode("utf-8")).hexdigest()[:12],
                state=state,
            )
        )

    return sorted(snapshots, key=lambda item: item.id)


def build_legacy_manifest(root: Path) -> dict[str, Any]:
    jobs_path = root / "cron" / "jobs.json"
    now = datetime.now(ZoneInfo(KST_TZ)).isoformat()
    jobs = load_legacy_jobs(root)

    run_logs = {}
    trace_files = {}
    for job in jobs:
        run_path = root / "cron" / "runs" / f"{job.id}.jsonl"
        run_logs[job.id] = {
            "path": str(run_path),
            "exists": run_path.exists(),
            "lines": _line_count(run_path),
        }
        trace_files[job.id] = {
            "path": str(root / "cron" / "traces" / job.id),
            "files": _trace_file_count(root, job.id),
        }

    return {
        "generated_at_kst": now,
        "legacy_root": str(root),
        "jobs_path": str(jobs_path),
        "jobs_path_exists": jobs_path.exists(),
        "jobs_sha256": _sha256_file(jobs_path),
        "jobs": [job.as_dict() for job in jobs],
        "run_logs": run_logs,
        "trace_files": trace_files,
        "preserved_paths": [
            str(root / "cron" / "jobs.json"),
            str(root / "cron" / "runs"),
            str(root / "cron" / "traces"),
            str(root / "workspace" / "memory" / "news"),
            str(root / "state" / "news_dedup.sqlite"),
        ],
    }
