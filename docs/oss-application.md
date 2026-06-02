# OpenAI Codex for OSS Application Notes

Use these notes as raw material for the application form.

## Repository

`https://github.com/YOUR_USERNAME/evidence-news-scanner`

## Project Summary

Evidence News Scanner is a local-first market news triage pipeline for
maintainers who need auditable news alerts. It collects candidates from search
and RSS, extracts normalized events, applies deterministic evidence gates,
stores every decision in SQLite, and optionally stages or sends a Telegram
digest.

## Why Codex Credits Help

The project has many contract-heavy paths: source discovery, event extraction,
dispatch scoring, evidence contracts, optional LLM annotation, and delivery
safety. Codex credits would be used to review PRs, generate regression tests for
edge-case market events, and maintain replay-quality fixtures without weakening
the deterministic gates.

## Maintainer Role

I am the primary maintainer and author. I use the project to keep a market-news
triage workflow auditable, testable, and safer than a prompt-only alert bot.

## Security Posture

The public repository removes private runtime paths and reads secrets only from
environment variables. Live delivery requires explicit `TELEGRAM_BOT_TOKEN` and
`--telegram-chat-id`; dry-run mode is available for safe testing.
