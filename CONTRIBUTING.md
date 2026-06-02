# Contributing

## Development

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

Keep provider calls behind dependency injection or environment variables so
tests can run offline. Do not add required network calls to the default test
suite.

## Pull Request Checklist

- No committed secrets, tokens, chat IDs, local absolute paths, SQLite files, or
  generated output.
- Tests pass with `PYTHONPATH=src python3 -m unittest discover -s tests`.
- New delivery behavior has a dry-run or contract test.
- LLM changes preserve deterministic gates before model output is trusted.
