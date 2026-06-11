"""Tests for diagnostics: redaction + payload presence."""

from __future__ import annotations

from homeassistant.components.diagnostics import REDACTED
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.airvisual_outdoor.diagnostics import (
    async_get_config_entry_diagnostics,
)


async def test_diagnostics_redacts_identifiers(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Node id and MAC are redacted; readings come through."""
    result = await async_get_config_entry_diagnostics(hass, init_integration)

    assert result["entry_data"]["node_id"] == REDACTED
    assert result["entry_data"]["mac"] == REDACTED
    assert result["entry_data"]["aqi_scale"] == "us"

    assert result["last_update_success"] is True
    assert result["rate_limit_remaining"] == 29
    reading = result["reading"]
    assert reading["co2"] == 459
    assert reading["pm25"]["conc"] == 1
    assert len(reading["hourly"]) == 2
