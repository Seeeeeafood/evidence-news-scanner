from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from .budget import (
    count_billable_brave_requests,
    estimate_brave_cost_usd,
    evaluate_brave_budget,
)
from .breaking_hints import (
    breaking_hint_fetch_results,
    breaking_hint_texts,
    read_recent_breaking_hints,
)
from .body_fetcher import (
    enrich_candidate_records_with_bodies,
    prioritized_send_candidate_ids,
)
from .config import KST_TZ, RuntimeConfig, llm_model_roles
from .db import (
    connect,
    finish_run,
    init_db,
    insert_candidate_items,
    insert_dispatch_decisions,
    insert_events_and_links,
    insert_market_snapshot,
    insert_news_seeds,
    insert_run,
    insert_source_attempt,
    load_latest_market_snapshot,
)
from .dispatch import decide_dispatch
from .discovery import (
    build_high_recall_scout_queries,
    create_discovery_plan,
    scout_sources_from_queries,
)
from .extractor import extract_events
from .fetcher import FetchResult, fetch_sources
from .io import atomic_write_json
from .legacy import build_legacy_manifest
from .news_seed import build_news_seeds, summarize_news_seeds
from .auth_config import (
    load_brave_api_key,
    load_fmp_api_key,
    load_openai_api_key,
    load_polygon_api_key,
)
from .market_snapshot import (
    fetch_market_snapshot,
    merge_missing_with_previous_snapshot,
    summarize_market_snapshot,
)
from .price_reaction import enrich_decision_records_with_price_reactions
from .sources import DEFAULT_SOURCES, required_category_status
from .verification import verify_hard_event_records


def _attempt_id(run_id: str, result: FetchResult) -> str:
    return f"{run_id}:{result.source.name}"


def _parse_item_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _filter_recent_items(
    fetch_results: tuple[FetchResult, ...],
    *,
    as_of: datetime,
    lookback_hours: int,
) -> list:
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=ZoneInfo(KST_TZ))
    lower_bound = as_of - timedelta(hours=lookback_hours)
    upper_bound = as_of + timedelta(hours=1)

    kept = []
    for result in fetch_results:
        for item in result.items:
            item_time = _parse_item_time(item.published_at)
            if item_time is None:
                kept.append(item)
                continue
            if lower_bound <= item_time.astimezone(as_of.tzinfo) <= upper_bound:
                kept.append(item)
    return kept


def _count_by_source(items: list) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        counts[item.source] = counts.get(item.source, 0) + 1
    return counts


def _count_by_category(records: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        category = str(record["category"])
        counts[category] = counts.get(category, 0) + 1
    return counts


def _count_events_by_type(records: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        event = record["event"]
        if isinstance(event, dict):
            event_type = str(event.get("event_type") or "")
            if event_type:
                counts[event_type] = counts.get(event_type, 0) + 1
    return counts


def _count_decisions(records: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        decision = str(record.get("decision") or "")
        if decision:
            counts[decision] = counts.get(decision, 0) + 1
    return counts


def _empty_body_fetch_stats() -> dict[str, int]:
    return {
        "body_fetch_candidates": 0,
        "body_fetch_unsent_events": 0,
        "body_fetch_previously_sent_events": 0,
        "body_fetch_attempts": 0,
        "body_fetch_full": 0,
        "body_fetch_partial": 0,
        "body_fetch_weak": 0,
        "body_fetch_errors": 0,
        "body_fetch_skipped": 0,
    }


def _empty_verification_stats(
    *,
    enabled: bool,
    configured: bool,
    max_requests: int,
) -> dict[str, object]:
    return {
        "verification_enabled": enabled,
        "verification_configured": configured,
        "verification_candidates": 0,
        "verification_attempted": 0,
        "verification_verified": 0,
        "verification_unverified": 0,
        "verification_errors": 0,
        "verification_skipped_limit": 0,
        "verification_brave_max_requests": max_requests,
        "verification_brave_requests_used": 0,
    }


def _is_skipped(result: FetchResult) -> bool:
    return result.status.startswith("skipped")


def _is_error(result: FetchResult) -> bool:
    return result.status != "ok" and not _is_skipped(result)


def _empty_discovery_summary(
    *,
    enabled: bool,
    active: bool,
    status: str,
    requested_max: int,
    request_budget: int,
) -> dict[str, object]:
    return {
        "discovery_planner_enabled": enabled,
        "discovery_planner_active": active,
        "discovery_planner_status": status,
        "discovery_requested_max": requested_max,
        "discovery_brave_request_budget": request_budget,
        "discovery_query_count": 0,
        "discovery_queries": [],
        "discovery_fetch_attempts": 0,
        "discovery_fetch_items": 0,
        "discovery_fetch_errors": 0,
        "discovery_model": "",
        "discovery_prompt_version": "",
        "discovery_payload_chars": 0,
    }


def _empty_scout_summary(
    *,
    enabled: bool,
    active: bool,
    requested_max: int,
    request_budget: int,
) -> dict[str, object]:
    return {
        "scout_lanes_enabled": enabled,
        "scout_lanes_active": active,
        "scout_requested_max": requested_max,
        "scout_brave_request_budget": request_budget,
        "scout_query_count": 0,
        "scout_queries": [],
        "scout_fetch_attempts": 0,
        "scout_fetch_items": 0,
        "scout_fetch_errors": 0,
        "scout_hint_count": 0,
    }


def _empty_breaking_hint_summary() -> dict[str, object]:
    return {
        "breaking_hint_enabled": True,
        "breaking_hint_files_checked_days": 2,
        "breaking_hint_line_count": 0,
        "breaking_hint_sources": 0,
        "breaking_hint_items": 0,
        "breaking_hint_items_by_category": {},
    }


def _breaking_hint_summary(
    fetch_results: tuple[FetchResult, ...],
    *,
    hint_count: int,
) -> dict[str, object]:
    return {
        "breaking_hint_enabled": True,
        "breaking_hint_files_checked_days": 2,
        "breaking_hint_line_count": hint_count,
        "breaking_hint_sources": len(fetch_results),
        "breaking_hint_items": sum(len(result.items) for result in fetch_results),
        "breaking_hint_items_by_category": {
            result.source.category: len(result.items) for result in fetch_results
        },
    }


def _fetch_result_hint_texts(
    fetch_results: tuple[FetchResult, ...],
    *,
    max_items: int = 20,
) -> list[str]:
    hints: list[str] = []
    for result in fetch_results:
        for item in result.items[:6]:
            title = str(item.title or "").strip()
            if not title:
                continue
            summary = str(item.summary or "").strip()
            hints.append(f"{result.source.category}: {title} {summary}".strip()[:700])
            if len(hints) >= max_items:
                return hints
    return hints


def _scout_summary(
    queries: list[dict[str, object]],
    fetch_results: tuple[FetchResult, ...],
    *,
    enabled: bool,
    active: bool,
    requested_max: int,
    request_budget: int,
    hint_count: int,
) -> dict[str, object]:
    return {
        "scout_lanes_enabled": enabled,
        "scout_lanes_active": active,
        "scout_requested_max": requested_max,
        "scout_brave_request_budget": request_budget,
        "scout_query_count": len(queries),
        "scout_queries": [
            {
                "lane": str(query.get("lane") or ""),
                "category": str(query.get("category") or ""),
                "query": str(query.get("query") or ""),
                "max_results": int(query.get("max_results") or 0),
                "reason": str(query.get("reason") or ""),
            }
            for query in queries
        ],
        "scout_fetch_attempts": len(fetch_results),
        "scout_fetch_items": sum(len(result.items) for result in fetch_results),
        "scout_fetch_errors": sum(1 for result in fetch_results if _is_error(result)),
        "scout_hint_count": hint_count,
    }


def _discovery_summary(
    plan: dict[str, object],
    fetch_results: tuple[FetchResult, ...],
    *,
    enabled: bool,
    active: bool,
    request_budget: int,
) -> dict[str, object]:
    raw_queries = plan.get("queries")
    queries = []
    if isinstance(raw_queries, list):
        for query in raw_queries:
            if not isinstance(query, dict):
                continue
            queries.append(
                {
                    "category": str(query.get("category") or ""),
                    "query": str(query.get("query") or ""),
                    "max_results": int(query.get("max_results") or 0),
                    "reason": str(query.get("reason") or ""),
                }
            )
    summary = {
        "discovery_planner_enabled": enabled,
        "discovery_planner_active": active,
        "discovery_planner_status": str(plan.get("status") or "unknown"),
        "discovery_requested_max": int(plan.get("requested") or request_budget),
        "discovery_brave_request_budget": request_budget,
        "discovery_query_count": len(queries),
        "discovery_queries": queries,
        "discovery_fetch_attempts": len(fetch_results),
        "discovery_fetch_items": sum(len(result.items) for result in fetch_results),
        "discovery_fetch_errors": sum(
            1 for result in fetch_results if _is_error(result)
        ),
        "discovery_model": str(plan.get("model") or ""),
        "discovery_prompt_version": str(plan.get("prompt_version") or ""),
        "discovery_payload_chars": int(plan.get("payload_chars") or 0),
    }
    error = plan.get("error")
    if error:
        summary["discovery_error"] = str(error)
    return summary


def _send_event_signatures(decisions: list[dict[str, object]]) -> set[str]:
    return {
        str(decision.get("event_signature") or "")
        for decision in decisions
        if decision.get("decision") == "send_candidate"
        and str(decision.get("event_signature") or "")
    }


def _existing_sent_event_signatures(
    db_path: Path,
    *,
    event_signatures: set[str],
    channel: str = "telegram",
    status: str = "sent",
) -> set[str]:
    if not event_signatures:
        return set()
    values = sorted(event_signatures)
    placeholders = ",".join("?" for _ in values)
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT DISTINCT event_signature
            FROM deliveries
            WHERE channel = ?
              AND status = ?
              AND event_signature IN ({placeholders})
            """,
            [channel, status, *values],
        ).fetchall()
    return {str(row["event_signature"]) for row in rows}


def _recent_delivery_texts(db_path: Path, *, limit: int = 3) -> list[str]:
    if limit <= 0:
        return []
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT payload_json
            FROM deliveries
            WHERE status = 'sent'
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit * 3,),
        ).fetchall()
    texts: list[str] = []
    seen: set[str] = set()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        message = payload.get("message")
        if not isinstance(message, dict):
            continue
        text = " ".join(str(message.get("text") or "").split())
        if not text or text in seen:
            continue
        seen.add(text)
        texts.append(text)
        if len(texts) >= limit:
            break
    return texts


def run_shadow(
    config: RuntimeConfig,
    *,
    as_of: datetime,
    mode: str = "shadow",
) -> dict[str, object]:
    init_db(config.db_path)
    legacy_snapshot = build_legacy_manifest(config.legacy_root)
    prompt_hashes = {
        job.get("prompt_sha256_12") for job in legacy_snapshot.get("jobs", [])
    }
    legacy_prompt_hash = ",".join(sorted(hash for hash in prompt_hashes if hash))

    run_id = str(uuid4())
    started_at = datetime.now(ZoneInfo(KST_TZ)).isoformat()
    brave_budget = evaluate_brave_budget(
        DEFAULT_SOURCES,
        brave_enabled=config.brave_enabled,
        max_requests=config.max_brave_requests_per_run,
    )
    discovery_planner_active = (
        config.discovery_planner_enabled
        and config.llm_enabled
        and config.brave_enabled
    )
    extra_request_budget = max(
        0,
        int(config.max_brave_requests_per_run) - brave_budget.planned_requests,
    )
    scout_lanes_active = config.scout_lanes_enabled and config.brave_enabled
    scout_request_budget = (
        min(max(0, int(config.max_scout_queries_per_run)), extra_request_budget)
        if scout_lanes_active
        else 0
    )
    discovery_request_budget = (
        min(
            max(0, int(config.max_discovery_queries_per_run)),
            max(0, extra_request_budget - scout_request_budget),
        )
        if discovery_planner_active
        else 0
    )

    if brave_budget.status == "limit_exceeded":
        summary = {
            "run_id": run_id,
            "mode": mode,
            "as_of": as_of.isoformat(),
            "db_path": str(config.db_path),
            "dispatch_enabled": config.dispatch_enabled,
            "llm_enabled": config.llm_enabled,
            "llm_models": llm_model_roles(config),
            "legacy_jobs": len(legacy_snapshot.get("jobs", [])),
            "legacy_prompt_hash": legacy_prompt_hash,
            "brave_enabled": config.brave_enabled,
            "brave_configured": False,
            "brave_attempts": 0,
            "brave_requests_planned": brave_budget.planned_requests,
            "brave_requests_planned_total_max": brave_budget.planned_requests,
            "brave_requests_used": 0,
            "brave_max_requests_per_run": brave_budget.max_requests,
            "brave_estimated_cost_usd": brave_budget.estimated_cost_usd,
            "brave_budget_status": brave_budget.status,
            **_empty_discovery_summary(
                enabled=config.discovery_planner_enabled,
                active=False,
                status="skipped_budget_blocked",
                requested_max=config.max_discovery_queries_per_run,
                request_budget=0,
            ),
            **_empty_scout_summary(
                enabled=config.scout_lanes_enabled,
                active=False,
                requested_max=config.max_scout_queries_per_run,
                request_budget=0,
            ),
            **_empty_breaking_hint_summary(),
            "source_attempts": 0,
            "source_errors": 0,
            "source_skipped": 0,
            "candidate_items_raw": 0,
            "candidate_items_seen": 0,
            "candidate_items_inserted": 0,
            "candidate_items_by_category": {},
            "events_extracted": 0,
            "events_inserted": 0,
            "event_links_inserted": 0,
            "events_by_type": {},
            "dispatch_decisions_evaluated": 0,
            "dispatch_decisions_inserted": 0,
            "dispatch_decisions_by_decision": {},
            "price_reaction_enabled": config.price_reaction_enabled,
            "price_reaction_configured": False,
            "price_reaction_required_records": 0,
            "price_reaction_unique_tickers": 0,
            "price_reaction_attempted": 0,
            "price_reaction_skipped_limit": 0,
            "price_reaction_status_counts": {},
            **_empty_verification_stats(
                enabled=config.verification_enabled and config.brave_enabled,
                configured=False,
                max_requests=config.max_verification_brave_requests_per_run,
            ),
            **_empty_body_fetch_stats(),
            **summarize_market_snapshot(None, enabled=config.market_snapshot_enabled),
            "lookback_hours": config.lookback_hours,
            "required_categories": required_category_status(set()),
            "required_categories_ok": False,
            "status": "blocked_budget",
            "error": brave_budget.message,
            "note": "shadow run blocked before network calls by Brave cost guard",
        }
        with connect(config.db_path) as conn:
            insert_run(
                conn,
                run_id=run_id,
                started_at=started_at,
                as_of=as_of.isoformat(),
                mode=mode,
                dispatch_enabled=config.dispatch_enabled,
                llm_enabled=config.llm_enabled,
                legacy_prompt_hash=legacy_prompt_hash or None,
                legacy_snapshot=legacy_snapshot,
            )
            finish_run(
                conn,
                run_id=run_id,
                status="blocked_budget",
                finished_at=datetime.now(ZoneInfo(KST_TZ)).isoformat(),
                error=brave_budget.message,
            )
        config.shadow_dir.mkdir(parents=True, exist_ok=True)
        out_path = (
            Path(config.shadow_dir)
            / f"{as_of.strftime('%Y%m%d_%H%M%S')}_{run_id}.json"
        )
        atomic_write_json(out_path, summary)
        summary["shadow_output"] = str(out_path)
        return summary

    brave_api_key = config.brave_api_key
    if brave_api_key is None and config.brave_enabled:
        brave_api_key = load_brave_api_key(legacy_root=config.legacy_root)
    fetch_results = fetch_sources(
        DEFAULT_SOURCES,
        brave_api_key=brave_api_key if config.brave_enabled else None,
    )
    breaking_hints = read_recent_breaking_hints(config.legacy_root, as_of=as_of)
    breaking_started_at = datetime.now(timezone.utc).isoformat()
    breaking_finished_at = datetime.now(timezone.utc).isoformat()
    breaking_fetch_results = breaking_hint_fetch_results(
        breaking_hints,
        started_at=breaking_started_at,
        finished_at=breaking_finished_at,
    )
    if breaking_fetch_results:
        fetch_results = fetch_results + breaking_fetch_results
    breaking_hint_stats = _breaking_hint_summary(
        breaking_fetch_results,
        hint_count=len(breaking_hints),
    )
    scout_hint_texts = breaking_hint_texts(breaking_hints) + _fetch_result_hint_texts(fetch_results)
    scout_queries = build_high_recall_scout_queries(
        as_of=as_of,
        hint_texts=scout_hint_texts,
        max_queries=scout_request_budget,
        max_results_per_query=config.max_discovery_results_per_query,
    )
    scout_sources = scout_sources_from_queries(scout_queries)
    scout_fetch_results: tuple[FetchResult, ...] = ()
    if scout_sources:
        scout_fetch_results = fetch_sources(
            scout_sources,
            brave_api_key=brave_api_key if config.brave_enabled else None,
        )
        fetch_results = fetch_results + scout_fetch_results
    scout_stats = _scout_summary(
        scout_queries,
        scout_fetch_results,
        enabled=config.scout_lanes_enabled,
        active=scout_lanes_active,
        requested_max=config.max_scout_queries_per_run,
        request_budget=scout_request_budget,
        hint_count=len(scout_hint_texts),
    )
    discovery_plan = create_discovery_plan(
        fetch_results=fetch_results,
        as_of=as_of,
        enabled=discovery_planner_active,
        api_key=load_openai_api_key() if discovery_planner_active else None,
        model=config.discovery_llm_model,
        timeout_seconds=config.llm_timeout_seconds,
        max_queries=discovery_request_budget,
        max_results_per_query=config.max_discovery_results_per_query,
        recent_delivery_texts=_recent_delivery_texts(config.db_path),
    )
    discovery_sources = tuple(discovery_plan.get("sources") or ())
    discovery_fetch_results: tuple[FetchResult, ...] = ()
    if discovery_sources:
        discovery_fetch_results = fetch_sources(
            discovery_sources,
            brave_api_key=brave_api_key if config.brave_enabled else None,
        )
        fetch_results = fetch_results + discovery_fetch_results
    discovery_stats = _discovery_summary(
        discovery_plan,
        discovery_fetch_results,
        enabled=config.discovery_planner_enabled,
        active=discovery_planner_active,
        request_budget=discovery_request_budget,
    )
    fetched_at = datetime.now(ZoneInfo(KST_TZ)).isoformat()
    recent_items = _filter_recent_items(
        fetch_results,
        as_of=as_of,
        lookback_hours=config.lookback_hours,
    )
    candidate_records = [
        item.as_record(run_id)
        for item in recent_items
    ]
    preliminary_extracted_events = extract_events(candidate_records, as_of=as_of)
    preliminary_dispatch_decisions = [
        decision.as_record(run_id)
        for decision in decide_dispatch(
            preliminary_extracted_events,
            candidates=candidate_records,
        )
    ]
    if config.body_fetch_enabled:
        send_event_signatures = _send_event_signatures(preliminary_dispatch_decisions)
        sent_event_signatures = _existing_sent_event_signatures(
            config.db_path,
            event_signatures=send_event_signatures,
        )
        candidate_records, body_fetch_stats = enrich_candidate_records_with_bodies(
            candidate_records,
            candidate_ids=prioritized_send_candidate_ids(
                preliminary_dispatch_decisions,
                sent_event_signatures=sent_event_signatures,
            ),
            max_fetches=config.max_body_fetches_per_run,
            timeout_seconds=config.body_fetch_timeout_seconds,
        )
        body_fetch_stats["body_fetch_unsent_events"] = len(
            send_event_signatures - sent_event_signatures
        )
        body_fetch_stats["body_fetch_previously_sent_events"] = len(
            send_event_signatures & sent_event_signatures
        )
    else:
        body_fetch_stats = _empty_body_fetch_stats()
    extracted_events = extract_events(candidate_records, as_of=as_of)
    news_seeds = build_news_seeds(raw_items=candidate_records, as_of=as_of)
    news_seed_summary = summarize_news_seeds(news_seeds)
    dispatch_decisions = [
        decision.as_record(run_id)
        for decision in decide_dispatch(extracted_events, candidates=candidate_records)
    ]
    polygon_api_key = config.polygon_api_key
    if polygon_api_key is None and config.price_reaction_enabled:
        polygon_api_key = load_polygon_api_key(legacy_root=config.legacy_root)
    dispatch_decisions, price_reaction_stats = (
        enrich_decision_records_with_price_reactions(
            dispatch_decisions,
            enabled=config.price_reaction_enabled,
            api_key=polygon_api_key,
            as_of=as_of,
            max_tickers=config.max_price_reaction_tickers_per_run,
            timeout_seconds=config.price_reaction_timeout_seconds,
            stale_after_days=config.price_reaction_stale_after_days,
        )
    )
    verification_enabled = config.verification_enabled and config.brave_enabled
    dispatch_decisions, verification_stats, verification_fetch_results = (
        verify_hard_event_records(
            dispatch_decisions,
            enabled=verification_enabled,
            api_key=brave_api_key if verification_enabled else None,
            max_requests=config.max_verification_brave_requests_per_run,
            timeout_seconds=config.verification_timeout_seconds,
        )
    )
    kept_by_source = _count_by_source(recent_items)
    kept_by_category = _count_by_category(candidate_records)
    events_by_type = _count_events_by_type(extracted_events)
    decisions_by_decision = _count_decisions(dispatch_decisions)
    fetched_categories = {
        result.source.category for result in fetch_results if result.status == "ok"
    }
    required_status = required_category_status(fetched_categories)
    all_fetch_results = fetch_results + verification_fetch_results
    brave_requests_used = count_billable_brave_requests(all_fetch_results)
    market_snapshot: dict[str, object] | None = None
    if config.market_snapshot_enabled:
        fmp_api_key = config.fmp_api_key or load_fmp_api_key(
            legacy_root=config.legacy_root
        )
        try:
            market_snapshot = fetch_market_snapshot(
                as_of=as_of,
                fmp_api_key=fmp_api_key,
                timeout_seconds=config.market_snapshot_timeout_seconds,
            )
        except Exception as exc:
            market_snapshot = {
                "schema_version": 1,
                "as_of": as_of.isoformat(),
                "generated_at": datetime.now(ZoneInfo(KST_TZ)).isoformat(),
                "status": "error",
                "values": {},
                "provider_errors": [
                    {
                        "provider": "market_snapshot",
                        "status": "error",
                        "error": str(exc)[:200],
                    }
                ],
            }

    with connect(config.db_path) as conn:
        insert_run(
            conn,
            run_id=run_id,
            started_at=started_at,
            as_of=as_of.isoformat(),
            mode=mode,
            dispatch_enabled=config.dispatch_enabled,
            llm_enabled=config.llm_enabled,
            legacy_prompt_hash=legacy_prompt_hash or None,
            legacy_snapshot=legacy_snapshot,
        )
        market_snapshot_inserted = 0
        if market_snapshot is not None:
            previous_market_snapshot = load_latest_market_snapshot(conn)
            market_snapshot = merge_missing_with_previous_snapshot(
                market_snapshot,
                previous_market_snapshot,
            )
            market_snapshot_inserted = insert_market_snapshot(
                conn,
                run_id=run_id,
                as_of=as_of.isoformat(),
                created_at=fetched_at,
                snapshot=market_snapshot,
            )
        for result in all_fetch_results:
            insert_source_attempt(
                conn,
                attempt_id=_attempt_id(run_id, result),
                run_id=run_id,
                source=result.source.name,
                provider=result.source.provider,
                category=result.source.category,
                url=result.source.url,
                query=result.source.query,
                status=result.status,
                item_count=len(result.items),
                kept_count=kept_by_source.get(result.source.name, 0),
                error=result.error,
                started_at=result.started_at,
                finished_at=result.finished_at,
            )
        inserted_candidates = insert_candidate_items(
            conn,
            run_id=run_id,
            fetched_at=fetched_at,
            items=candidate_records,
        )
        news_seeds_inserted = insert_news_seeds(
            conn,
            run_id=run_id,
            created_at=fetched_at,
            seeds=news_seeds,
        )
        events_inserted, event_links_inserted = insert_events_and_links(
            conn,
            run_id=run_id,
            created_at=fetched_at,
            extracted=extracted_events,
        )
        dispatch_decisions_inserted = insert_dispatch_decisions(
            conn,
            created_at=fetched_at,
            decisions=dispatch_decisions,
        )
        finish_run(
            conn,
            run_id=run_id,
            status="ok",
            finished_at=datetime.now(ZoneInfo(KST_TZ)).isoformat(),
        )

    summary = {
        "run_id": run_id,
        "mode": mode,
        "as_of": as_of.isoformat(),
        "db_path": str(config.db_path),
        "dispatch_enabled": config.dispatch_enabled,
        "llm_enabled": config.llm_enabled,
        "llm_models": llm_model_roles(config),
        "legacy_jobs": len(legacy_snapshot.get("jobs", [])),
        "legacy_prompt_hash": legacy_prompt_hash,
        "brave_enabled": config.brave_enabled,
        "brave_configured": bool(brave_api_key),
        "brave_source_slots": sum(
            1 for result in all_fetch_results if result.source.provider == "brave"
        ),
        "brave_attempts": sum(
            1
            for result in all_fetch_results
            if result.source.provider == "brave" and not _is_skipped(result)
        ),
        "brave_requests_planned": brave_budget.planned_requests,
        "brave_requests_planned_total_max": (
            brave_budget.planned_requests
            + scout_request_budget
            + discovery_request_budget
            + (
                config.max_verification_brave_requests_per_run
                if verification_enabled
                else 0
            )
        ),
        "brave_requests_used": brave_requests_used,
        "brave_max_requests_per_run": brave_budget.max_requests,
        "brave_estimated_cost_usd": estimate_brave_cost_usd(brave_requests_used),
        "brave_budget_status": brave_budget.status,
        **breaking_hint_stats,
        **scout_stats,
        **discovery_stats,
        "source_attempts": len(all_fetch_results),
        "source_errors": sum(1 for result in all_fetch_results if _is_error(result)),
        "source_skipped": sum(1 for result in all_fetch_results if _is_skipped(result)),
        "candidate_items_raw": sum(len(result.items) for result in fetch_results),
        "candidate_items_seen": len(candidate_records),
        "candidate_items_inserted": inserted_candidates,
        "candidate_items_by_category": kept_by_category,
        "events_extracted": len(extracted_events),
        "pre_body_events_extracted": len(preliminary_extracted_events),
        "events_inserted": events_inserted,
        "event_links_inserted": event_links_inserted,
        "events_by_type": events_by_type,
        **news_seed_summary,
        "news_seeds_inserted": news_seeds_inserted,
        "dispatch_decisions_evaluated": len(dispatch_decisions),
        "pre_body_dispatch_decisions_evaluated": len(preliminary_dispatch_decisions),
        "dispatch_decisions_inserted": dispatch_decisions_inserted,
        "dispatch_decisions_by_decision": decisions_by_decision,
        **price_reaction_stats,
        **summarize_market_snapshot(
            market_snapshot,
            enabled=config.market_snapshot_enabled,
        ),
        "market_snapshot_inserted": market_snapshot_inserted,
        **verification_stats,
        **body_fetch_stats,
        "lookback_hours": config.lookback_hours,
        "required_categories": required_status,
        "required_categories_ok": all(required_status.values()),
        "status": "ok",
        "note": "scanner fetch/normalize/extract/QC complete; delivery handled by mode",
    }

    config.shadow_dir.mkdir(parents=True, exist_ok=True)
    out_path = Path(config.shadow_dir) / f"{as_of.strftime('%Y%m%d_%H%M%S')}_{run_id}.json"
    atomic_write_json(out_path, summary)
    summary["shadow_output"] = str(out_path)
    return summary
