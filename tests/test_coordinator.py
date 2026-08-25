"""Tests for coordinator failure semantics, esp. rate-limit resilience."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.airvisual_outdoor.api import (
    RateLimitError,
    TransportError,
)


async def test_rate_limited_cycle_keeps_last_reading(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
) -> None:
    """A drained budget must not blip entities unavailable.

    The 30/hour budget is global per node across all client IPs, so a third
    party can drain it; the staleness guard owns availability instead.
    """
    coordinator = init_integration.runtime_data
    before = coordinator.data

    mock_client.async_get_reading.side_effect = RateLimitError("drained")
    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.data is before
    state = hass.states.get("sensor.backyard_co2")
    assert state is not None
    assert state.state == "459"


async def test_rate_limited_first_fetch_fails(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
) -> None:
    """With no prior reading there is nothing to keep — setup retries."""
    from homeassistant.config_entries import ConfigEntryState

    from .conftest import setup_integration

    mock_client.async_get_reading.side_effect = RateLimitError("drained")
    await setup_integration(hass, mock_config_entry, mock_client)
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_transport_error_marks_unavailable(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
) -> None:
    """Non-rate-limit API failures keep the normal unavailable semantics."""
    coordinator = init_integration.runtime_data

    mock_client.async_get_reading.side_effect = TransportError("down")
    await coordinator.async_refresh()

    assert coordinator.last_update_success is False
    state = hass.states.get("sensor.backyard_co2")
    assert state is not None
    assert state.state == STATE_UNAVAILABLE


async def test_rate_limit_warning_is_edge_triggered(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The drained-budget warning logs once per episode, not once per poll.

    The keep-last-reading branch deliberately holds ``last_update_success``
    True, which bypasses DataUpdateCoordinator's own once-per-episode log
    suppression. Without an explicit edge trigger a third-party drain emits
    12 identical warnings an hour for as long as it lasts.
    """
    coordinator = init_integration.runtime_data

    mock_client.async_get_reading.side_effect = RateLimitError("drained")
    caplog.clear()
    for _ in range(4):
        await coordinator.async_refresh()
    assert caplog.text.count("request budget exhausted") == 1

    # ...and one line on recovery, so the episode has a visible end.
    mock_client.async_get_reading.side_effect = None
    caplog.clear()
    await coordinator.async_refresh()
    assert caplog.text.count("request budget recovered") == 1

    # A second episode logs again.
    mock_client.async_get_reading.side_effect = RateLimitError("drained again")
    caplog.clear()
    await coordinator.async_refresh()
    assert caplog.text.count("request budget exhausted") == 1
