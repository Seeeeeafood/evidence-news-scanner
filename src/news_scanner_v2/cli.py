from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

from .auth_config import (
    load_fmp_api_key,
    load_openai_api_key,
    load_polygon_api_key,
    load_telegram_bot_token,
)
from .body_fetcher import DEFAULT_MAX_BODY_FETCHES_PER_RUN
from .budget import DEFAULT_MAX_BRAVE_REQUESTS_PER_RUN
from .composer import load_message_preview, render_message_preview
from .config import (
    DEFAULT_DB_PATH,
    DEFAULT_LEGACY_ROOT,
    DEFAULT_LLM_MODEL,
    DEFAULT_LLM_TIMEOUT_SECONDS,
    DEFAULT_MARKET_SNAPSHOT_TIMEOUT_SECONDS,
    DEFAULT_MAX_DISCOVERY_QUERIES_PER_RUN,
    DEFAULT_MAX_DISCOVERY_RESULTS_PER_QUERY,
    DEFAULT_MAX_PRICE_REACTION_TICKERS_PER_RUN,
    DEFAULT_MAX_SCOUT_QUERIES_PER_RUN,
    DEFAULT_PRICE_REACTION_STALE_AFTER_DAYS,
    DEFAULT_PRICE_REACTION_TIMEOUT_SECONDS,
    DEFAULT_SHADOW_DIR,
    KST_TZ,
    build_config,
)
from .verification import (
    DEFAULT_MAX_VERIFICATION_BRAVE_REQUESTS_PER_RUN,
    DEFAULT_VERIFICATION_TIMEOUT_SECONDS,
)
from .db import init_db
from .delivery import (
    DEFAULT_TELEGRAM_CHAT_ID,
    create_dry_run_deliveries,
    create_live_deliveries,
    render_delivery_summary,
)
from .legacy import build_legacy_manifest
from .pipeline import run_shadow
from .reports import (
    ReportError,
    load_decision_rows,
    load_price_reaction_report,
    render_decision_report,
    render_price_reaction_report,
)
from .sources import DEFAULT_SOURCES


def _parse_as_of(value: str) -> datetime:
    if value == "now":
        return datetime.now(ZoneInfo(KST_TZ))
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(KST_TZ))
    return parsed


def _add_common_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--legacy-root", type=Path, default=DEFAULT_LEGACY_ROOT)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--shadow-dir", type=Path, default=DEFAULT_SHADOW_DIR)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="news-scanner-v2")
    sub = parser.add_subparsers(dest="command", required=True)

    baseline = sub.add_parser("baseline", help="Print legacy cron baseline")
    baseline.add_argument("--legacy-root", type=Path, default=DEFAULT_LEGACY_ROOT)
    baseline.add_argument("--json", action="store_true")

    init = sub.add_parser("init-db", help="Create or migrate the v2 SQLite DB")
    init.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)

    sub.add_parser("sources", help="Print configured v2 news sources")

    report = sub.add_parser("report", help="Read-only reports from the scanner DB")
    report_sub = report.add_subparsers(dest="report_command", required=True)
    decisions = report_sub.add_parser(
        "decisions",
        help="Print dispatch decisions for labeling",
    )
    decisions.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    decisions.add_argument("--run-id", default="latest")
    decisions.add_argument(
        "--format",
        choices=("markdown", "csv", "json"),
        default="markdown",
    )
    decisions.add_argument(
        "--decision",
        action="append",
        choices=("send_candidate", "review", "reject"),
        help="Filter by decision. Can be repeated.",
    )
    decisions.add_argument("--limit", type=int)
    decisions.add_argument("--output", type=Path)

    messages = report_sub.add_parser(
        "messages",
        help="Compose read-only message previews from dispatch decisions",
    )
    messages.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    messages.add_argument("--run-id", default="latest")
    messages.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
    )
    messages.add_argument(
        "--decision",
        action="append",
        choices=("send_candidate", "review", "reject"),
        help="Decision to preview. Defaults to send_candidate. Can be repeated.",
    )
    messages.add_argument("--limit", type=int)
    messages.add_argument("--output", type=Path)

    price_reaction = report_sub.add_parser(
        "price-reaction",
        help="Summarize company price-reaction gates for delivery QC",
    )
    price_reaction.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    price_reaction.add_argument("--run-id", default="latest")
    price_reaction.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
    )
    price_reaction.add_argument(
        "--decision",
        action="append",
        choices=("send_candidate", "review", "reject"),
        help="Decision to inspect. Defaults to send_candidate. Can be repeated.",
    )
    price_reaction.add_argument("--limit", type=int)
    price_reaction.add_argument("--output", type=Path)

    deliver = sub.add_parser(
        "deliver",
        help="Create delivery records without contacting external channels",
    )
    deliver.add_argument("--mode", choices=("dry-run",), default="dry-run")
    deliver.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    deliver.add_argument("--run-id", default="latest")
    deliver.add_argument("--channel", default="telegram")
    deliver.add_argument(
        "--decision",
        action="append",
        choices=("send_candidate", "review", "reject"),
        help="Decision to stage. Defaults to send_candidate. Can be repeated.",
    )
    deliver.add_argument("--limit", type=int)
    deliver.add_argument("--telegram-chat-id", default=DEFAULT_TELEGRAM_CHAT_ID)
    deliver.add_argument("--output", type=Path)

    run = sub.add_parser("run", help="Run the scanner")
    _add_common_paths(run)
    run.add_argument("--mode", choices=("shadow", "dry-run", "live"), default="shadow")
    run.add_argument("--as-of", default="now")
    run.add_argument("--lookback-hours", type=int, default=72)
    run.add_argument("--disable-brave", action="store_true")
    run.add_argument(
        "--max-brave-requests",
        type=int,
        default=DEFAULT_MAX_BRAVE_REQUESTS_PER_RUN,
    )
    run.add_argument("--max-live-messages", type=int, default=7)
    run.add_argument("--enable-llm", action="store_true")
    run.add_argument("--llm-model", default=DEFAULT_LLM_MODEL)
    run.add_argument("--discovery-llm-model")
    run.add_argument("--editorial-llm-model")
    run.add_argument("--theme-editor-llm-model")
    run.add_argument("--summary-llm-model")
    run.add_argument("--critic-llm-model")
    run.add_argument(
        "--llm-timeout-seconds",
        type=float,
        default=DEFAULT_LLM_TIMEOUT_SECONDS,
    )
    run.add_argument("--disable-discovery-planner", action="store_true")
    run.add_argument(
        "--max-discovery-queries",
        type=int,
        default=DEFAULT_MAX_DISCOVERY_QUERIES_PER_RUN,
    )
    run.add_argument(
        "--max-discovery-results",
        type=int,
        default=DEFAULT_MAX_DISCOVERY_RESULTS_PER_QUERY,
    )
    run.add_argument("--disable-scout-lanes", action="store_true")
    run.add_argument(
        "--max-scout-queries",
        type=int,
        default=DEFAULT_MAX_SCOUT_QUERIES_PER_RUN,
    )
    run.add_argument("--disable-body-fetch", action="store_true")
    run.add_argument(
        "--max-body-fetches",
        type=int,
        default=DEFAULT_MAX_BODY_FETCHES_PER_RUN,
    )
    run.add_argument("--disable-price-reaction", action="store_true")
    run.add_argument(
        "--max-price-reaction-tickers",
        type=int,
        default=DEFAULT_MAX_PRICE_REACTION_TICKERS_PER_RUN,
    )
    run.add_argument(
        "--price-reaction-timeout-seconds",
        type=float,
        default=DEFAULT_PRICE_REACTION_TIMEOUT_SECONDS,
    )
    run.add_argument(
        "--price-reaction-stale-after-days",
        type=int,
        default=DEFAULT_PRICE_REACTION_STALE_AFTER_DAYS,
    )
    run.add_argument("--disable-market-snapshot", action="store_true")
    run.add_argument(
        "--market-snapshot-timeout-seconds",
        type=float,
        default=DEFAULT_MARKET_SNAPSHOT_TIMEOUT_SECONDS,
    )
    run.add_argument("--disable-verification-rescue", action="store_true")
    run.add_argument(
        "--max-verification-requests",
        type=int,
        default=DEFAULT_MAX_VERIFICATION_BRAVE_REQUESTS_PER_RUN,
    )
    run.add_argument(
        "--verification-timeout-seconds",
        type=float,
        default=DEFAULT_VERIFICATION_TIMEOUT_SECONDS,
    )
    run.add_argument("--telegram-chat-id", default=DEFAULT_TELEGRAM_CHAT_ID)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "baseline":
        manifest = build_legacy_manifest(args.legacy_root)
        if args.json:
            print(json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False))
        else:
            print(f"legacy_root={manifest['legacy_root']}")
            print(f"jobs={len(manifest['jobs'])}")
            for job in manifest["jobs"]:
                print(
                    f"{job['id']} {job['name']} "
                    f"{job['schedule_expr']} {job['schedule_tz']} "
                    f"prompt={job['prompt_sha256_12']}"
                )
        return 0

    if args.command == "init-db":
        init_db(args.db_path)
        print(f"db_initialized={args.db_path}")
        return 0

    if args.command == "sources":
        for source in DEFAULT_SOURCES:
            print(f"{source.category}\t{source.name}\t{source.url}")
        return 0

    if args.command == "report":
        if args.report_command == "decisions":
            try:
                report = load_decision_rows(
                    args.db_path,
                    run_id=args.run_id,
                    decisions=set(args.decision) if args.decision else None,
                    limit=args.limit,
                )
                rendered = render_decision_report(report, output_format=args.format)
            except ReportError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(rendered)
            else:
                print(rendered, end="")
            return 0
        if args.report_command == "messages":
            try:
                preview = load_message_preview(
                    args.db_path,
                    run_id=args.run_id,
                    decisions=set(args.decision) if args.decision else None,
                    limit=args.limit,
                )
                rendered = render_message_preview(
                    preview,
                    output_format=args.format,
                )
            except ReportError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(rendered)
            else:
                print(rendered, end="")
            return 0
        if args.report_command == "price-reaction":
            try:
                report = load_price_reaction_report(
                    args.db_path,
                    run_id=args.run_id,
                    decisions=set(args.decision) if args.decision else None,
                    limit=args.limit,
                )
                rendered = render_price_reaction_report(
                    report,
                    output_format=args.format,
                )
            except ReportError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(rendered)
            else:
                print(rendered, end="")
            return 0
        parser.error("unknown report command")

    if args.command == "deliver":
        try:
            if args.mode == "dry-run":
                summary = create_dry_run_deliveries(
                    args.db_path,
                    run_id=args.run_id,
                    channel=args.channel,
                    decisions=set(args.decision) if args.decision else None,
                    limit=args.limit,
                )
            else:
                parser.error("unknown deliver mode")
            rendered = render_delivery_summary(summary)
        except ReportError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered)
        else:
            print(rendered, end="")
        return 0

    if args.command == "run":
        config = build_config(
            legacy_root=args.legacy_root,
            db_path=args.db_path,
            shadow_dir=args.shadow_dir,
            dispatch_enabled=False,
            llm_enabled=args.enable_llm,
            llm_model=args.llm_model,
            discovery_llm_model=args.discovery_llm_model,
            editorial_llm_model=args.editorial_llm_model,
            theme_editor_llm_model=args.theme_editor_llm_model,
            summary_llm_model=args.summary_llm_model,
            critic_llm_model=args.critic_llm_model,
            llm_timeout_seconds=args.llm_timeout_seconds,
            lookback_hours=args.lookback_hours,
            brave_enabled=not args.disable_brave,
            max_brave_requests_per_run=args.max_brave_requests,
            body_fetch_enabled=not args.disable_body_fetch,
            max_body_fetches_per_run=args.max_body_fetches,
            discovery_planner_enabled=not args.disable_discovery_planner,
            scout_lanes_enabled=not args.disable_scout_lanes,
            max_scout_queries_per_run=args.max_scout_queries,
            max_discovery_queries_per_run=args.max_discovery_queries,
            max_discovery_results_per_query=args.max_discovery_results,
            price_reaction_enabled=not args.disable_price_reaction,
            polygon_api_key=load_polygon_api_key(legacy_root=args.legacy_root)
            if not args.disable_price_reaction
            else None,
            max_price_reaction_tickers_per_run=args.max_price_reaction_tickers,
            price_reaction_timeout_seconds=args.price_reaction_timeout_seconds,
            price_reaction_stale_after_days=args.price_reaction_stale_after_days,
            market_snapshot_enabled=not args.disable_market_snapshot,
            fmp_api_key=load_fmp_api_key(legacy_root=args.legacy_root)
            if not args.disable_market_snapshot
            else None,
            market_snapshot_timeout_seconds=args.market_snapshot_timeout_seconds,
            verification_enabled=not args.disable_verification_rescue,
            max_verification_brave_requests_per_run=args.max_verification_requests,
            verification_timeout_seconds=args.verification_timeout_seconds,
        )
        summary = run_shadow(
            config,
            as_of=_parse_as_of(args.as_of),
            mode=args.mode,
        )
        if args.mode == "dry-run" and summary.get("status") == "ok":
            openai_api_key = load_openai_api_key() if args.enable_llm else None
            try:
                delivery_summary = create_dry_run_deliveries(
                    config.db_path,
                    run_id=str(summary["run_id"]),
                    channel="telegram",
                    llm_enabled=args.enable_llm,
                    llm_api_key=openai_api_key,
                    llm_model=config.llm_model,
                    llm_editorial_model=config.editorial_llm_model,
                    llm_theme_editor_model=config.theme_editor_llm_model,
                    llm_summary_model=config.summary_llm_model,
                    llm_timeout_seconds=config.llm_timeout_seconds,
                )
            except ReportError as exc:
                summary["delivery_dry_run"] = {
                    "status": "error",
                    "error": str(exc),
                }
                print(json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False))
                return 1
            summary["delivery_dry_run"] = {
                "status": delivery_summary["status"],
                "channel": delivery_summary["channel"],
                "requested": delivery_summary["requested"],
                "inserted": delivery_summary["inserted"],
                "skipped_existing": delivery_summary["skipped_existing"],
                "contract": delivery_summary["contract"],
                "safety": delivery_summary["safety"],
                "llm_models": delivery_summary["llm_models"],
                "llm_theme_editor": delivery_summary["llm_theme_editor"],
                "llm_editorial": delivery_summary["llm_editorial"],
                "llm_annotation": delivery_summary["llm_annotation"],
                "llm_summary_rejected": delivery_summary["llm_summary_rejected"],
                "final_publish_dropped": delivery_summary["final_publish_dropped"],
            }
        elif args.mode == "dry-run":
            summary["delivery_dry_run"] = {
                "status": "skipped",
                "reason": f"run_status:{summary.get('status')}",
            }
        elif args.mode == "live" and summary.get("status") == "ok":
            token = load_telegram_bot_token(legacy_root=config.legacy_root)
            if not token:
                summary["delivery_live"] = {
                    "status": "error",
                    "error": "Telegram bot token not configured",
                }
                print(json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False))
                return 1
            openai_api_key = load_openai_api_key() if args.enable_llm else None
            try:
                delivery_summary = create_live_deliveries(
                    config.db_path,
                    bot_token=token,
                    chat_id=args.telegram_chat_id,
                    run_id=str(summary["run_id"]),
                    channel="telegram",
                    limit=args.max_live_messages,
                    llm_enabled=args.enable_llm,
                    llm_api_key=openai_api_key,
                    llm_model=config.llm_model,
                    llm_editorial_model=config.editorial_llm_model,
                    llm_theme_editor_model=config.theme_editor_llm_model,
                    llm_summary_model=config.summary_llm_model,
                    llm_timeout_seconds=config.llm_timeout_seconds,
                )
            except ReportError as exc:
                summary["delivery_live"] = {
                    "status": "error",
                    "error": str(exc),
                }
                print(json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False))
                return 1
            summary["delivery_live"] = {
                "status": delivery_summary["status"],
                "channel": delivery_summary["channel"],
                "chat_id": delivery_summary["chat_id"],
                "requested": delivery_summary["requested"],
                "selected": delivery_summary["selected"],
                "sent": delivery_summary["sent"],
                "inserted": delivery_summary["inserted"],
                "skipped_previously_sent": delivery_summary[
                    "skipped_previously_sent"
                ],
                "message_ids": delivery_summary["message_ids"],
                "contract": delivery_summary["contract"],
                "safety": delivery_summary["safety"],
                "llm_models": delivery_summary["llm_models"],
                "llm_theme_editor": delivery_summary["llm_theme_editor"],
                "llm_editorial": delivery_summary["llm_editorial"],
                "llm_annotation": delivery_summary["llm_annotation"],
                "llm_summary_rejected": delivery_summary["llm_summary_rejected"],
                "final_publish_dropped": delivery_summary["final_publish_dropped"],
                "market_snapshot_status": delivery_summary.get(
                    "market_snapshot_status"
                ),
            }
        elif args.mode == "live":
            summary["delivery_live"] = {
                "status": "skipped",
                "reason": f"run_status:{summary.get('status')}",
            }
        print(json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False))
        return 0

    parser.error("unknown command")
    return 2
