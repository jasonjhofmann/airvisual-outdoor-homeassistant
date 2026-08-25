# Changelog

All notable changes to this project will be documented in this file.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions
follow [SemVer](https://semver.org/) with a `v` prefix on tags.

## [Unreleased]

## [0.2.1] - 2026-08-24

Whole-repository review. The headline item is that the statistics
gap-backfill — the integration's flagship feature — has been silently dead
after its first successful run on every supported Home Assistant version.

### Fixed

- **Statistics backfill stopped working after the first import.**
  `StatisticMetaData` requires a `unit_class` key and this integration never
  set one. The first import takes HA's `_add_metadata` path, which tolerates
  the omission; HA's own sensor recorder platform then fills the correct
  value in for the same entity. Every import after that takes
  `StatisticsMetaManager._update_metadata`, which reads
  `new_metadata["unit_class"]` unconditionally and raised
  `KeyError: 'unit_class'` **inside the recorder thread** — where this
  integration's `try`/`except` could not see it, because
  `async_import_statistics` only queues the job. Net effect: backfill
  appeared to work once and then never again, leaving a recorder traceback
  each hour. Metadata is now built as a `StatisticMetaData` literal with the
  correct per-sensor `unit_class`, re-derived from HA's own converter maps by
  a test so an upstream change fails CI rather than a user's database.
- **The `cast()` that hid it.** The metadata was assembled as a
  `dict[str, object]` and `cast()` to the TypedDict purely to accommodate a
  `has_mean` compatibility shim whose own docstring called it "vestigial"
  (it has been unreachable since the 2026.7.0 floor). The cast is what
  suppressed the `Missing key "unit_class"` error mypy --strict would
  otherwise have raised. Shim and cast are both gone, along with the test
  that existed only to execute the dead branch.
- **Backfill fought the recorder for the hour that just ended.** The
  recorder compiles its own hourly row for hour H shortly after H+1:00 with
  a bare INSERT against a unique `(metadata_id, start_ts)` index. Importing
  H first made that collide; HA swallows the error but logs *"Blocked
  attempt to insert duplicated statistic rows, please report at <core issue
  tracker>"* and rolls the compile period back — a custom-integration bug
  presenting to the user as a core bug. The most recently completed hour is
  now left to the recorder (`COMPILE_LAG`), which costs nothing: if HA was
  up the recorder produces a better min/mean/max row anyway, and if HA was
  down the next hourly pass imports it, still inside the 48 h window.
- **Backfill crashed on every poll when the recorder is disabled.**
  `get_instance()` is a bare `hass.data[DATA_INSTANCE]` lookup, so running
  HA without the recorder — supported, not an error — raised `KeyError` and
  logged a full traceback hourly. It is now a quiet no-op.
- **A zone-less API timestamp crashed every entity.** `_timestamp()` parsed
  a valid-but-naive `2026-06-10T03:52:07` into a naive `datetime`, which then
  raised `TypeError: can't subtract offset-naive and offset-aware datetimes`
  in the availability guard and `can't compare` in the backfill window —
  defeating the whole point of a parser documented to tolerate garbage. The
  endpoint is documented UTC, so a missing designator is now assumed UTC.
- **Switching AQI scale left junk behind.** Reconfiguring US → CN stopped
  creating the old scale's entities but HA kept their registry rows, so the
  user was left with a permanently `unavailable` `sensor.<device>_aqi_us` —
  and, because the abandoned main-pollutant row still owned the clean slug,
  the new scale's sensor was forced to `sensor.<device>_main_pollutant_2`
  forever. The abandoned rows are now removed before the new entities are
  added, so the id is reclaimed.
- **Clearing the optional MAC did nothing.** `DeviceInfo.connections` is
  merged into the device entry rather than replacing it, so emptying the MAC
  field in the reconfigure flow left the stale connection — and the network
  device link — in place. The device's connections are now reconciled
  against the entry on every setup.
- **In-flight backfills outlived their config entry.** The background task
  used `hass.async_create_background_task`, which is only cancelled at HA
  shutdown, so an unload or reload could leave a backfill writing statistics
  for an entry the user had just removed. It is now an entry-owned task.

### Changed

- **`Last updated` no longer goes unavailable on a stale sample.** Every
  other entity still does, but blanking the freshness sensor exactly when the
  station dies removed the only signal telling the user *how* stale the data
  was. It now keeps reporting the final sample's timestamp (and can drive an
  "offline for N minutes" automation); it still goes unavailable when the
  poll itself fails. README and `quality_scale.yaml` updated to match.
- **The drained-budget warning is edge-triggered.** The keep-last-reading
  branch deliberately holds `last_update_success` True, which also bypasses
  DataUpdateCoordinator's own once-per-episode log suppression — so a
  third-party drain of a published node emitted 12 identical warnings an
  hour for as long as it lasted. It now logs once when the budget drains and
  once when it recovers.
- Existing-statistics lookup is one recorder round-trip for all seven
  sensors instead of one per sensor (7 → 1 executor query per backfill).

### Security

- **The `@claude` workflows were open to the whole internet.** This
  repository is public, so any GitHub user could open an issue or comment
  containing `@claude` and spend the owner's Claude quota running an agent on
  a prompt they controlled. Both Claude workflows now require the author to
  be the repository owner, a member, or a collaborator; the review workflow
  additionally skips fork PRs (which get no secrets under `pull_request` and
  could only ever fail) and cancels superseded runs.
- `ci.yml` gained an explicit least-privilege `permissions: contents: read`
  (it previously inherited the repository default) and, with `validate.yml`,
  a concurrency group so a burst of pushes runs the gates once.

### Added

- `tests/test_packaging.py`: enforces the repository invariants CONTRIBUTING
  only asserted in prose — `strings.json` / `translations/en.json` parity,
  `api.py` staying free of Home Assistant imports, translation-key liveness
  in both directions, and manifest/hacs metadata agreement.
- `docs/architecture.md` gained a **Recorder contract** section recording the
  three non-obvious recorder behaviours above, verified against HA 2026.8
  source, so the next change to `statistics.py` starts from ground truth.

### Documentation

- `docs/architecture.md`: the "Phase 3 refinement (noted)" paragraph still
  described the pre-0.1.0 behaviour (429 marks entities unavailable) that
  0.1.0 replaced; the shape section was still headed "PENDING DISCUSSION"
  after shipping; roadmap phases 1–4 were unticked; the CI bullet still named
  `actions/checkout@v5` against a repo on v7.
- Every `--python 3.14` now reads `--python '>=3.14.2'` in `ci.yml` and
  `CONTRIBUTING.md`, so `pyproject.toml`'s claim that "every `--python` must
  track that floor" is finally true rather than aspirational — `3.14` alone
  could resolve 3.14.0, below Home Assistant's own `requires-python`.
- README/strings: documented that clearing the MAC removes the link, that a
  scale switch removes the old scale's entities, that `Last updated` survives
  staleness, and that the backfill leaves the most recent hour to HA.

## [0.2.0] - 2026-08-09

### Changed

- **BREAKING (user-facing): the minimum Home Assistant version rises from
  2025.1.0 to 2026.7.0**, and with it Python 3.14 — 2026.7.0 is the first
  core release shipping the `UnitOfDensity` / `UnitOfRatio` unit enums
  (they are absent at 2026.6.0), and that core's own `requires-python` is
  `>=3.14.2`. `hacs.json` now declares the new floor, so HACS will not
  offer this version to older cores; they stay on 0.1.1.
- Migrated off the deprecated `CONCENTRATION_*` unit constants onto the
  `UnitOfDensity` / `UnitOfRatio` StrEnums in `sensor.py` and
  `statistics.py` — `CONCENTRATION_MICROGRAMS_PER_CUBIC_METER` →
  `UnitOfDensity.MICROGRAMS_PER_CUBIC_METER` (PM2.5 / PM10 / PM1) and
  `CONCENTRATION_PARTS_PER_MILLION` → `UnitOfRatio.PARTS_PER_MILLION`
  (CO₂). Core deprecated all six `CONCENTRATION_*` constants upstream via
  `DeprecatedConstantEnum`, with **removal scheduled for HA Core 2027.8**.
  No unit string and no runtime behaviour changes: the deprecated names
  already resolve to these exact enum members with byte-identical values
  (`μg/m³`, `ppm`), so entity states, `unit_of_measurement` attributes and
  long-term statistics rows are unaffected. The swap only removes the
  startup deprecation warning and pre-empts the 2027.8 removal.
  `PERCENTAGE` was *not* deprecated upstream and is left exactly as-is.
- Toolchain moved to Python 3.14 to match the new floor: all four CI gates,
  the `CONTRIBUTING.md` dev commands, ruff `target-version` (`py313` →
  `py314` — it must equal the *oldest* supported Python) and mypy's
  `python_version`. A job left on 3.13 would resolve a pre-2026.7 core and
  fail at import on the missing unit enums.

## [0.1.1] - 2026-08-09

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

[Unreleased]: https://github.com/jasonjhofmann/airvisual-outdoor-homeassistant/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/jasonjhofmann/airvisual-outdoor-homeassistant/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/jasonjhofmann/airvisual-outdoor-homeassistant/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/jasonjhofmann/airvisual-outdoor-homeassistant/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/jasonjhofmann/airvisual-outdoor-homeassistant/releases/tag/v0.1.0
