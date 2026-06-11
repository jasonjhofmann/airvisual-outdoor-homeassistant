"""Diagnostics support for AirVisual Outdoor.

The node id is the only capability token the keyless API has, so it is
redacted along with the MAC even though neither is a classic secret.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_MAC, CONF_NODE_ID
from .coordinator import AirVisualOutdoorConfigEntry

TO_REDACT = {CONF_NODE_ID, CONF_MAC}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: AirVisualOutdoorConfigEntry
) -> dict[str, Any]:
    """Return diagnostics: redacted entry data + the latest reading."""
    coordinator = entry.runtime_data
    reading = coordinator.data
    return {
        "entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "last_update_success": coordinator.last_update_success,
        "rate_limit_remaining": (
            reading.rate_limit_remaining if reading is not None else None
        ),
        "reading": asdict(reading) if reading is not None else None,
    }
