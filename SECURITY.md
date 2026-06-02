# Security Policy

## Secret Handling

This project must not store secrets in the repository. Runtime credentials are
read from environment variables:

- `BRAVE_SEARCH_API_KEY`
- `OPENAI_API_KEY`
- `POLYGON_API_KEY`
- `FMP_API_KEY`
- `TELEGRAM_BOT_TOKEN`

Do not commit `.env`, SQLite files, logs, generated shadow output, private chat
IDs, API responses containing account data, or local operator paths.

## Delivery Safety

Live Telegram delivery requires:

- `TELEGRAM_BOT_TOKEN` in the environment;
- an explicit `--telegram-chat-id`;
- a successful evidence contract.

Use `--mode dry-run` for testing. Dry-run mode writes delivery-shaped rows to
SQLite without contacting Telegram.

## Reporting Issues

Open a GitHub issue for non-sensitive bugs. If a report contains a token,
private URL, account identifier, or live chat ID, do not post it publicly; send
a minimal redacted report instead.
