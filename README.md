# Evidence News Scanner

Evidence News Scanner is a local-first market news triage pipeline. It turns
news search results and RSS items into normalized candidate events, applies
evidence and safety gates, stores decisions in SQLite, and can stage or send a
plain-text Telegram digest.

The project is designed for maintainers who want an auditable scanner instead
of a prompt-only news bot. LLM calls are optional and run after deterministic
filters; API keys are read only from environment variables.

## What It Does

- collects market-moving news candidates from Brave News Search and RSS feeds;
- normalizes candidate items into event records;
- scores events with deterministic dispatch rules;
- applies evidence contracts before delivery;
- records every run, candidate, decision, and delivery attempt in SQLite;
- supports shadow, dry-run, and live delivery modes;
- includes replay and quality audit tools for regression checks.

## Security Model

- No API keys, tokens, chat IDs, or private paths are stored in the repository.
- Secrets are loaded from environment variables only.
- Live Telegram delivery requires both `TELEGRAM_BOT_TOKEN` and an explicit
  `--telegram-chat-id`.
- Dry-run mode writes delivery-shaped records without contacting Telegram.
- LLM annotation is optional and skipped when `OPENAI_API_KEY` is not set.
- SQLite paths default to a local `.evidence-news-scanner/` directory.

## Quick Start

```bash
git clone https://github.com/YOUR_USERNAME/evidence-news-scanner.git
cd evidence-news-scanner
PYTHONPATH=src python3 -m news_scanner_v2 init-db
PYTHONPATH=src python3 -m news_scanner_v2 sources
PYTHONPATH=src python3 -m news_scanner_v2 run --mode shadow --disable-brave
```

With Brave News Search:

```bash
export BRAVE_SEARCH_API_KEY="..."
PYTHONPATH=src python3 -m news_scanner_v2 run --mode shadow
```

Dry-run delivery:

```bash
PYTHONPATH=src python3 -m news_scanner_v2 run --mode dry-run
```

Live Telegram delivery:

```bash
export TELEGRAM_BOT_TOKEN="..."
PYTHONPATH=src python3 -m news_scanner_v2 run \
  --mode live \
  --telegram-chat-id "<chat-id>"
```

Optional providers:

```bash
export OPENAI_API_KEY="..."      # optional LLM annotation
export POLYGON_API_KEY="..."     # optional price reaction gate
export FMP_API_KEY="..."         # optional market snapshot inputs
```

## Commands

```bash
PYTHONPATH=src python3 -m news_scanner_v2 init-db
PYTHONPATH=src python3 -m news_scanner_v2 sources
PYTHONPATH=src python3 -m news_scanner_v2 run --mode shadow
PYTHONPATH=src python3 -m news_scanner_v2 run --mode dry-run
PYTHONPATH=src python3 -m news_scanner_v2 run --mode live --telegram-chat-id "<chat-id>"
PYTHONPATH=src python3 -m news_scanner_v2 report decisions
PYTHONPATH=src python3 -m news_scanner_v2 report messages
PYTHONPATH=src python3 -m news_scanner_v2 report price-reaction
```

## Testing

The test suite uses the Python standard library `unittest` runner.

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

## Runtime Files

By default runtime files are written under:

```text
.evidence-news-scanner/state/news_scanner_v2.sqlite
.evidence-news-scanner/state/news_scanner_v2_shadow/
```

You can override them:

```bash
export NEWS_SCANNER_DB="/tmp/news_scanner.sqlite"
export NEWS_SCANNER_SHADOW_DIR="/tmp/news_scanner_shadow"
```

## Maintainer Notes

The core project intentionally separates:

- source discovery;
- deterministic extraction;
- dispatch scoring;
- evidence and delivery contracts;
- optional LLM annotation;
- replay and quality reports.

That makes regressions inspectable: a bad digest can be traced back to the raw
candidate, extracted event, dispatch decision, evidence contract, and final
delivery record.

## License

MIT. See `LICENSE`.
