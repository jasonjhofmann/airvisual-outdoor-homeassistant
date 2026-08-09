# Changelog

All notable changes to this project will be documented in this file.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions
follow [SemVer](https://semver.org/) with a `v` prefix on tags.

## [Unreleased]

### Changed
- `docs/architecture.md`: the payload table spelled `µg/m³` with U+00B5 MICRO
  SIGN; Home Assistant's canonical value (`UnitOfDensity.MICROGRAMS_PER_CUBIC_METER`)
  is U+03BC GREEK SMALL LETTER MU. Docs-only — `sensor.py` and `statistics.py`
  take the unit from HA's constant, never a literal.

### Added
- `tests/test_init.py`: pins the backfill background task's crash
  containment — a failing `async_backfill_statistics` must be logged, not
  fail entry setup. Restores the 100% coverage the CI gate requires (the
  two `except`/log lines in `__init__.py` were the only uncovered
  statements, failing every PR at 99% since 2026-06-25).

## [0.1.0] - 2026-06-10

### Added
- Phase 4: 100% test coverage (49 tests) with the CI gate raised to match;
  README use-cases/examples/troubleshooting sections; quality scale declared
  platinum.
- Phase 3: diagnostics (redacted node id/MAC + latest reading +
  rate-limit budget), exception translations, main-pollutant icon,
  `quality_scale.yaml` (+ `quality_scale: bronze` manifest), in-tree IQAir
  brand assets, CI (ruff / mypy strict / pytest coverage gate) + HACS &
  Hassfest validation workflows, README rewrite (installation,
  configuration, data-update limits, removal), CONTRIBUTING.

### Changed
- A rate-limited poll cycle now keeps the last reading instead of marking
  entities unavailable — the 30/hour budget is global per node across all
  client IPs, so external consumers can drain it; the sample-timestamp
  staleness guard owns availability instead.
- Phase 2: full launch sensor set (AQI + main pollutant per the entry's
  scale, PM2.5/PM10/PM1, CO₂, temperature, humidity, pressure, last
  updated — 10 entities per node); statistics gap-backfill importing
  missing completed hours from the node's own 48 h `hourly` history
  (insert-missing-only, throttled to hourly, background task); poll
  interval locked at 300 s per the rate-limit characterization.
- Phase 1: working integration package — HA-import-free `api.py` (typed
  aiohttp client + frozen `NodeReading` dataclass, defensive parsing of the
  hot-pluggable module keys, typed error taxonomy, rate-limit header
  surfaced), per-node `DataUpdateCoordinator`, config flow (node id + name +
  config-time AQI scale + optional MAC) with reconfigure (scale/MAC; node id
  immutable), device entry (IQAir / AirVisual Outdoor, node-id serial,
  optional MAC connection for network-device merging), staleness-aware
  availability, and the CO₂ proof-of-wire sensor. 22 tests (api / config
  flow / sensor), 95% coverage, mypy-strict clean. Live-validated against a
  real node.
- Rate-limit ground truth correction: the 30-request budget is keyed per
  node id (per URL path), not per IP alone — a second monitor gets its own
  budget (documented in `docs/architecture.md`).
- Phase 0 scaffold: repository layout, Apache-2.0 license, HACS manifest,
  tool configuration (ruff / mypy strict / pytest), captured node-API fixture,
  and `docs/architecture.md` freezing the API ground truth (endpoint, payload
  shape, rate limiting, error modes, transport quirks) plus the proposed
  integration shape and its open questions.
