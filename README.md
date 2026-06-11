# AirVisual Outdoor for Home Assistant

[![GitHub Release](https://img.shields.io/github/v/release/jasonjhofmann/airvisual-outdoor-homeassistant?style=flat-square)](https://github.com/jasonjhofmann/airvisual-outdoor-homeassistant/releases)
[![CI](https://img.shields.io/github/actions/workflow/status/jasonjhofmann/airvisual-outdoor-homeassistant/ci.yml?style=flat-square&label=CI)](https://github.com/jasonjhofmann/airvisual-outdoor-homeassistant/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/jasonjhofmann/airvisual-outdoor-homeassistant?style=flat-square)](LICENSE)

Home Assistant custom integration for the **IQAir AirVisual Outdoor** air
quality monitor, via IQAir's public per-device node API.

## Why this exists

- The AirVisual Outdoor exposes **no local API** (unlike the AirVisual Pro,
  which serves Samba), so HA core's `airvisual_pro` integration cannot
  support it.
- HA core's `airvisual` cloud integration only returns city/coordinate
  aggregates — never your own device — and the per-station endpoints are
  locked to paid API plans.
- IQAir's keyless node API (`device.iqair.com/v2/<node_id>`) serves the
  device's own readings. This integration polls it and gives the unit a
  proper device entry with first-class entities.

## Requirements

- An AirVisual Outdoor unit **published as a station** on the IQAir map
  (publishing is what exposes the node API).
- The unit's **24-character node ID**. Find it in your IQAir dashboard URL,
  or in the network traffic of the unit's station page.
- Home Assistant 2025.1.0 or newer. No account, API key, or cloud login.

## Installation

Until the HACS default-registry listing lands, install as a HACS custom
repository:

1. HACS → ⋮ → *Custom repositories* → add this repo URL, category
   **Integration**.
2. Install "AirVisual Outdoor", restart Home Assistant.

Manual alternative: copy `custom_components/airvisual_outdoor/` into your
config's `custom_components/` and restart.

## Configuration

Settings → Devices & Services → **Add Integration** → "AirVisual Outdoor".

| Field | Notes |
|---|---|
| Node ID | The unit's 24-hex id. Validated with one live API call. |
| Name | Device name; defaults to the name the unit reports. **Entity ids derive from this name** — choose it deliberately. |
| AQI scale | US (EPA, default) or China (MEE). One scale per entry — exactly one AQI sensor and one main-pollutant sensor are created. Switch later via *Reconfigure* (creates fresh entities under the new scale). |
| MAC address | Optional. If set, HA merges the device with its network (DHCP/UniFi) client entry on one device card. |

Multiple monitors = multiple entries, one per node ID. Each node has its own
API request budget.

## Entities

10 sensors per monitor: AQI, main pollutant (both per the chosen scale),
PM2.5, PM10, PM1, CO₂*, temperature, humidity, pressure, and last updated.

\* CO₂ requires the optional CO₂ module; module-less readings report as
unknown (modules are hot-pluggable — the integration tolerates any
combination).

## Data updates & limitations

- **Polling**: every 300 s, not configurable. IQAir's node API enforces
  **30 requests per node per clock hour** (resets at :00 UTC), and that
  budget is shared by *every* consumer of the node worldwide — the cadence
  deliberately leaves headroom. If the budget is drained externally, the
  integration keeps the last reading and recovers at the next hour.
- **Statistics gap-backfill**: every payload carries the device's own 48 h
  of completed-hour aggregates. The integration imports missing hours into
  HA's long-term statistics, so HA downtime never leaves permanent holes in
  the hourly record. Limits (by HA design): statistics only — raw state
  history cannot be backfilled — at hourly granularity, for sensors with an
  hourly source (PM, CO₂, temperature, humidity, pressure; AQI and main
  pollutant have none).
- **Staleness**: if the device stops reporting, IQAir's API keeps serving
  the last reading with HTTP 200 indefinitely. Entities go unavailable when
  the sample is older than 10 minutes.
- The API exposes no model/firmware metadata, so the device page can't show
  firmware versions.

## Use cases & examples

The integration was built to make a published outdoor monitor a first-class
*control input*, not just a dashboard number:

- **Ventilation gating** — veto "open windows / run the ERV" automations
  when outdoor PM2.5 is elevated:

  ```yaml
  automation:
    - alias: "Ventilation — outdoor PM veto"
      triggers:
        - trigger: numeric_state
          entity_id: sensor.my_monitor_pm2_5
          above: 12
      actions:
        - action: fan.turn_off
          target:
            entity_id: fan.erv
  ```

- **CO₂ sanity reference** — outdoor CO₂ is the natural baseline for
  indoor-minus-outdoor ΔCO₂ ventilation math, instead of assuming a fixed
  ~420 ppm ambient.
- **Air-quality dashboards** — AQI gauge + per-pollutant trends, with the
  `last updated` timestamp entity making data freshness visible.

## Troubleshooting

- **"IQAir's API has no device with this node ID"** during setup: the id
  must be exactly 24 hex characters, and the unit must be *published* as a
  station — unpublished units are not served by the node API.
- **Entities unavailable, device unreachable in IQAir's app too**: the
  monitor stopped reporting (power/WiFi). The API keeps serving its last
  reading with HTTP 200; entities deliberately go unavailable once the
  sample is older than 10 minutes.
- **Entities unavailable right after setup**: check the log for
  `rate limit` — the node's 30/hour budget may be drained (it is shared by
  every consumer of the node, worldwide). It resets at the top of the hour.
- **A sensor reads "unknown"**: that module isn't installed (e.g. CO₂
  without the optional CO₂ module) or the unit omitted the key that cycle.
- **Hourly statistics have gaps anyway**: the backfill window is 48 h —
  outages longer than that can only be healed while they're still inside
  the window.

## Removal

Settings → Devices & Services → AirVisual Outdoor → ⋮ → *Delete*. Then
remove the HACS download (or the `custom_components/airvisual_outdoor/`
folder) and restart. No cloud-side cleanup exists or is needed — the
integration never writes anything to IQAir.

## Architecture

Design decisions, API ground truth (payload shape, rate limiting, error
envelopes), and the phased roadmap live in
[`docs/architecture.md`](docs/architecture.md).

## License

[Apache 2.0](LICENSE)
