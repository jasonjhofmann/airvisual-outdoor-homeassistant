# Architecture

Home Assistant custom integration for the **IQAir AirVisual Outdoor** monitor,
polled through IQAir's public per-device node API. Cloud-only by necessity:
the Outdoor hardware exposes **no local API at all** (port-scanned 2026-06-09:
zero open TCP ports, unlike the AirVisual Pro which serves Samba on 139/445 —
which is why this device cannot live in HA core's `airvisual_pro` integration).

Quality target: **Platinum**, same bar as the sibling integrations
(aranet-cloud 0.7.0, sensoredlife 0.5.0, visiblair 0.6.x).

## API ground truth (probed 2026-06-09)

### Endpoint

```
GET https://device.iqair.com/v2/<node_id>
```

- `node_id` is a 24-char lowercase hex string. It appears in no payload field —
  the caller must know it out-of-band (IQAir Dashboard, or the station's
  public-map page network traffic).
- **No authentication.** Keyless, public. Works for any *published* station.
  (Unverified: whether unpublished/private devices are reachable here.)
- A legacy alias serves the identical payload:
  `https://www.airvisual.com/api/v2/node/<node_id>` (this is the URL
  `pyairvisual`'s `NodeCloudAPI` speaks; see "Why not pyairvisual" below).

### Payload shape

Top level: `name` (str, device display name, e.g. "AirVisual Outdoor"),
`current` (obj), `historical` (obj of arrays: `instant` ~60×1-min,
`hourly` 48, `daily` 30, `monthly` 12).

`current`:

| key | type | notes |
|---|---|---|
| `pm25`, `pm10`, `pm1` | obj | each `{conc: µg/m³, aqius: int, aqicn: int}` |
| `co2` | int (ppm) | **flat number, NOT `{conc:}`** — only present when a CO₂ module is installed |
| `tp` | float (°C) | temperature |
| `hm` | int (%) | relative humidity |
| `pr` | int (Pa) | atmospheric pressure |
| `aqius`, `aqicn` | int | composite AQI (US / China scales) |
| `mainus`, `maincn` | str | main pollutant per scale; **can differ from each other** (observed: `pm25` vs `pm10`) |
| `ts` | str ISO-8601 UTC | timestamp of the sample — drives availability |

Modules are hot-pluggable (PM ×2 slots + 2 option slots). Keys appear/disappear
with module presence → every field must be parsed as optional.

There is NO node id, MAC, model number, firmware version, coordinates, or any
other device metadata in the payload beyond `name`. DeviceInfo is therefore
config-supplied.

### Rate limiting — the binding constraint

Responses carry `x-ratelimit-limit: 30` / `x-ratelimit-remaining: N`
(no `x-ratelimit-reset` header). Probed 2026-06-09:
- **The bucket is keyed per node id (per URL path), not per IP alone** —
  requests for a bogus node id ran against their own fresh 30-budget while
  the real node's bucket sat 6 lower. A second monitor therefore gets its
  own budget; what DOES share one bucket is every consumer of the *same*
  node behind the same IP (e.g. a YAML `rest:` package polling alongside
  this integration during cutover).
- Valid GETs decrement 1:1; 404s decrement too (their own path's bucket).
- **Window: fixed calendar hour, full reset at :00 UTC** — CONFIRMED across
  four consecutive boundaries (04:00–07:00Z, 3 h of 10-min sampling on an
  isolated bucket): zero intra-hour refill (every decrement was the sample
  itself), then a snap to full immediately after each :00Z. Budget =
  **30 requests per node per clock hour.** The production bucket's spend
  rates during the same run matched the known consumers exactly (including
  an 18-req hour while the legacy YAML package and the integration briefly
  co-polled — within budget, as designed).
- **Keying: GLOBAL per node id, shared across ALL client IPs** (resolved
  2026-06-10 via a second egress: the first request from a fresh IP read
  the bucket already at 27 — exactly the home integration's spend that
  hour — not a fresh 30). Consequences: (a) ANY direct consumer of a
  published station's node endpoint, anywhere on the internet, drains the
  same budget as this integration — headroom is not optional; (b) never
  burst-test the real node id from any network.
- **Exhaustion shape (captured live)**: requests 1–30 succeed
  (`remaining` 29→0), request 31 returns **HTTP 429**, JSON
  `{"code": "too_many_requests", "message": "You have exceeded the request
  limit for your API Key. …"}` (generic message — the API is keyless),
  with `x-ratelimit-remaining: 0` still present and **NO `Retry-After`
  header**. Recovery = the next :00 UTC reset; there is nothing to honor
  beyond waiting, so the coordinator's normal retry cadence is the correct
  backoff.
- Phase 3 refinement (noted): on `RateLimitError` the coordinator currently
  fails the cycle, marking entities unavailable until a poll succeeds. A
  third-party drain of a published node could therefore blip entities
  unavailable for up to ~an hour. Consider keeping last-known data on 429
  (the `ts` staleness guard already protects against serving dead data).

**Poll interval: LOCKED at 300 s** (12 req/h/node, 60% headroom; matches the
legacy REST package's cadence, and even brief co-polling during cutover fits:
12 + 12 < 30). Phase 1 shipped a provisional 600 s; the 300 s lock lands with
the Phase 2 deploy (a const change needs the restart Phase 2 forces anyway).

Consequences for the design:
- One coordinator **per node** but a polite default interval (see Decisions).
- Client must surface `x-ratelimit-remaining` (diagnostics) and handle 429
  with backoff if it ever appears.

### Error modes

| condition | response |
|---|---|
| unknown or malformed node id | `404` + `{"code": "device_not_found", "message": "Not Found"}` |
| missing node id (`/v2/`) | `404` + `{"code": "not_found", ...}` |
| `HEAD` request | `405` (GET-only API) |

Clean JSON error envelopes with a `code` field → config-flow validation can
distinguish "bad node id" (`device_not_found`) from transport errors.

### Transport quirks

- `cache-control: no-store, no-cache` — no HTTP-cache layer needed or possible.
- AWS ALB behind nginx; sets `AWSALB`/`AWSALBCORS` cookies. A plain
  cookie-less GET works fine — do not persist cookies.
- `content-type: application/json; charset=utf-8` (correct, unlike VisiblAir).

## Why not pyairvisual

`pyairvisual` (the lib behind HA core's two AirVisual integrations) does have
a working `NodeCloudAPI` for this exact endpoint — verified live 2026-06-09,
including keyless operation. Rejected as a dependency because:
- last release 2023-12, maintainer largely inactive;
- drags `numpy` + `pysmb` for the Samba path we will never use;
- the surface we need is ONE keyless GET — a small typed client in
  `api.py` (aiohttp, stdlib json) is simpler than the dependency.

If this integration is ever upstreamed to HA core, core's library-first rule
applies and the client extracts mechanically into a tiny PyPI package
(or lands as a pyairvisual contribution) at that point — see Roadmap.

## Proposed shape (PENDING DISCUSSION — see open questions)

- **Domain**: `airvisual_outdoor`, display name "AirVisual Outdoor".
- **Entry topology**: one config entry per node. No account/hub entry and no
  subentries — the API is keyless per-device, so there is nothing shared to
  anchor a parent entry (contrast `airnow_station`, where the account key is
  the parent). A second unit (the planned ERV-intake monitor) is simply a
  second entry.
- **Config flow** (inputs settled 2026-06-09): node id (validated: 24-hex
  regex, then a live GET; `device_not_found` → form error) + name (defaults
  to payload `name`) + **AQI scale select: US (default) or CN** + **optional
  MAC address** (enables `DeviceInfo.connections` → HA merges the device with
  its UniFi client entry, same trick as the Pros' PR #173071).
  Reconfigure flow: yes (Platinum) — covers scale and MAC; the node id is
  immutable (a different node is a different physical device with its own
  history → new entry, not a reconfiguration).
  Reauth: N/A — keyless (rule exempt). Discovery: exempt — the LAN presence
  is mute (no open ports) and the node id is not derivable from the network.
- **Coordinator**: one `DataUpdateCoordinator` per entry; raw payload dict is
  NOT passed around — `api.py` parses into a frozen dataclass.
  `PARALLEL_UPDATES = 0` (coordinator-managed).
- **Availability**: entity-level guard on `current.ts` staleness (propose
  10 min) in addition to coordinator success — the API happily serves a stale
  `current` block if the device stops reporting.
- **Sensors** (launch set settled 2026-06-09; exactly 10 entities per node):
  - AQI, PM2.5, PM10, PM1, CO₂, temperature, humidity, pressure,
    main pollutant, last updated (timestamp).
  - **AQI scale is a config-time choice** (US default / CN): exactly ONE
    AQI sensor and ONE main-pollutant sensor are created, reading
    `aqius`/`mainus` or `aqicn`/`maincn` per the entry's scale. Do NOT
    create both scales (decided 2026-06-09). unique_ids stay scale-specific
    (`…_aqius` vs `…_aqicn`), so switching scale via reconfigure creates
    fresh registry rows. The AQI entity name embeds the scale — "AQI (US)" /
    "AQI (CN)" — since the two standards aren't comparable; the
    main-pollutant entity is named scale-neutrally ("Main pollutant").
  - Per-pollutant AQIs (`pm25.aqius` etc.): OMITTED (decided 2026-06-09).
  - Trend sensors (direction enums pyairvisual-style): SKIPPED (decided
    2026-06-09) — derived analytics, not device truth; HA's built-in `trend`
    helper / `derivative` sensor reproduce it from recorder data with a
    user-controlled window. Revisit post-1.0 only if a dashboard wants a
    ready-made chip.
  - unique_id format: `airvisual_outdoor_<node_id>_<key>` (domain-prefixed,
    per the visiblair convention).
  - `has_entity_name = True`: entity ids slug from the user's device name
    (device "Backyard" + name "CO2" → `sensor.backyard_co2`).
- **Diagnostics**: payload snapshot + last `x-ratelimit-remaining`. Nothing
  secret to redact (keyless), but redact the node id anyway — it is the only
  capability token there is.

### Open questions

1. ~~**Poll interval**~~ RESOLVED 2026-06-10: window characterized as a
   fixed hourly reset (see above) → **300 s locked**, shipped with Phase 2.
   ~~Exhaustion behavior~~ also RESOLVED 2026-06-10 (VPN-egress test): see
   the captured 429 shape above — the client's existing 429 → RateLimitError
   path matches reality; no `Retry-After` exists to honor.
2. ~~**Entity-id continuity vs the chosen device name**~~ RESOLVED
   2026-06-09: adopt the device-name-derived entity ids as-is. The legacy
   YAML package's entity ids retire with the package; every consumer of the
   old ids (dashboards etc.) is repointed at the Phase 2 cutover. No
   renames, no history migration — the old ids' recorder/LTS history simply
   ages out with the package.

(Settled 2026-06-09: per-pollutant AQIs omitted; trend sensors skipped;
AQI scale is config-time single-scale; optional MAC field confirmed —
see the Sensors and Config flow sections. **Historical arrays: USED, for
statistics gap-backfill — deferred to Phase 2**, see below.)

### Statistics gap-backfill (Phase 2 feature, decided 2026-06-09)

Every poll carries the device's own history (`instant` ~60×1-min, `hourly`
48 h, `daily` 30 d, `monthly` 12 mo). Instead of discarding it, Phase 2
back-injects missed hours into HA **long-term statistics** via the
external/import-statistics machinery, so HA downtime never leaves permanent
holes in the hourly mean/min/max record. Scope limits (by HA design, restate
in user docs): heals *statistics only* — raw recorder states cannot be
backfilled — and at hourly granularity, from the 48 h `hourly` window.
Implementation sketch: on each refresh, compare the payload's hourly array
against the entity's existing statistics rows; insert only missing hours;
never overwrite existing rows. Daily/monthly arrays stay unused (HA derives
longer aggregates from hourly).

## Roadmap (phases, after shape sign-off)

0. ✅ Scaffold: repo, license, docs, fixture, tool config.
1. Package skeleton: `api.py` (typed client + dataclass), `coordinator.py`,
   `config_flow.py`, `__init__.py` (`entry.runtime_data`), `manifest.json`,
   `strings.json`; CO₂-only proof-of-wire sensor; api + config-flow tests;
   rate-limit characterization (protocol above) → lock the poll interval.
2. Full entity set + availability semantics + entity tests; statistics
   gap-backfill from the `hourly` array (see section above).
3. Diagnostics, `quality_scale.yaml`, exception/entity translations, icons,
   brand assets (in-tree `brand/`, NOT a brands PR), CI (ruff + mypy strict +
   pytest; `actions/checkout@v5`), HACS + hassfest validation workflows.
4. Platinum climb: 100% test coverage, reconfigure tests, docs polish,
   README badges (dynamic shields.io), CHANGELOG discipline, release v0.1.0,
   live cutover (remove REST package → registry cleanup → add entries).
5. Later/optional: HACS default-registry PR; core-upstream conversation
   (target: `airvisual` cloud integration gaining node-id entries, NOT
   `airvisual_pro` — transport-split precedent).
