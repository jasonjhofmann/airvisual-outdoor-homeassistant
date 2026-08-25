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


async def test_diagnostics_explains_availability(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client
) -> None:
    """A downloaded diagnostic can tell a failed poll from a stale sample.

    Both present to the user as "entities unavailable"; without the
    coordinator's last exception and the sample age they are
    indistinguishable in a bug report.
    """
    from custom_components.airvisual_outdoor.api import TransportError

    healthy = await async_get_config_entry_diagnostics(hass, init_integration)
    assert healthy["last_exception"] is None
    assert healthy["sample_is_stale"] is False
    assert healthy["sample_age_seconds"] < healthy["staleness_threshold_seconds"]
    assert healthy["update_interval_seconds"] == 300
    assert healthy["entry_version"] == "1.1"

    mock_client.async_get_reading.side_effect = TransportError("down")
    await init_integration.runtime_data.async_refresh()

    failed = await async_get_config_entry_diagnostics(hass, init_integration)
    assert failed["last_update_success"] is False
    # The cause chain, not just repr(UpdateFailed) == "UpdateFailed('update_failed')".
    assert failed["last_exception"] == (
        "UpdateFailed: Error talking to the IQAir node API: down"
        " <- TransportError: down"
    )
