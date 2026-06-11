"""Constants for the AirVisual Outdoor integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "airvisual_outdoor"

CONF_NODE_ID: Final = "node_id"
CONF_AQI_SCALE: Final = "aqi_scale"
CONF_MAC: Final = "mac"

SCALE_US: Final = "us"
SCALE_CN: Final = "cn"

DEFAULT_NAME: Final = "AirVisual Outdoor"

# LOCKED from the rate-limit characterization (docs/architecture.md): the API
# allows 30 requests per node per clock hour (full reset at :00 UTC), so
# 12/hour leaves 60% headroom. Never user-tunable (HA owns its cadence).
DEFAULT_SCAN_INTERVAL: Final = timedelta(seconds=300)

# Completed-hour history entries older than this aren't worth importing —
# matches the depth of the API's `hourly` array (48 h).
BACKFILL_WINDOW: Final = timedelta(hours=48)

# Re-run the statistics gap-backfill at most this often.
BACKFILL_MIN_INTERVAL: Final = timedelta(minutes=55)

# The API serves a stale `current` block indefinitely if the device stops
# reporting; entities go unavailable when the sample is older than this.
STALENESS_THRESHOLD: Final = timedelta(minutes=10)
