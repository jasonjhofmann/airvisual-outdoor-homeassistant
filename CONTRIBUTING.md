# Contributing

## Bug reports

Open an issue with: HA version, integration version, the node API's raw
response if relevant (`curl https://device.iqair.com/v2/<your_node_id>` —
mind the 30/hour budget), and any log lines from
`custom_components.airvisual_outdoor`.

## Development setup

Everything runs through [uv](https://docs.astral.sh/uv/) — no venv to manage:

```bash
# Lint + format
uv run --python '>=3.14.2' --with ruff ruff check custom_components/ tests/
uv run --python '>=3.14.2' --with ruff ruff format custom_components/ tests/

# Strict typing
uv run --python '>=3.14.2' --with mypy --with homeassistant --with aiohttp \
  --with voluptuous mypy custom_components/airvisual_outdoor/

# Tests (with coverage)
uv run --python '>=3.14.2' --with pytest-homeassistant-custom-component \
  --with pytest-cov python -m pytest tests/ -v \
  --cov=custom_components.airvisual_outdoor --cov-report=term-missing
```

CI runs the same four gates on every push and PR.

## Conventions

- `api.py` stays free of Home Assistant imports — it must remain
  extractable into a standalone package.
- Every payload field parses as optional; modules are hot-pluggable and
  keys appear/disappear with module presence. New fields get the same
  defensive treatment (see `normalise()` and its tests).
- Read `docs/architecture.md` before changing polling, rate-limit handling,
  or the statistics backfill — the constraints there were measured, not
  assumed.
- Keep `strings.json` and `translations/en.json` identical (`cp` one onto
  the other), and update `CHANGELOG.md` in the same PR as the change.
  `tests/test_packaging.py` now enforces the parity, the HA-import-free
  rule above, and translation-key liveness — so a drift fails CI instead of
  shipping.
- `statistics.py` talks to the recorder's import API, which has sharp edges
  that are NOT in the HA docs (a required `unit_class` metadata key that
  only fails once a metadata row exists, a unique-index collision with the
  recorder's own hourly compile, and a `get_instance()` that raises when the
  recorder is disabled). The "Recorder contract" section of
  `docs/architecture.md` has the verified details — read it before changing
  that module.
