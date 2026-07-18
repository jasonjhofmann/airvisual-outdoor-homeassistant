"""Tests for entry setup in ``__init__.py``.

Most setup behavior is exercised indirectly by the sensor/statistics tests;
this module covers what only setup itself owns — the backfill background
task's crash containment.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from tests.conftest import setup_integration


async def test_backfill_crash_is_contained_and_logged(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A crashing backfill must not fail setup — logged, not propagated.

    The backfill runs as a background task precisely so its failures cannot
    stall or fail HA startup; this pins the except-and-log containment in
    ``_backfill``.
    """
    with patch(
        "custom_components.airvisual_outdoor.async_backfill_statistics",
        side_effect=RuntimeError("recorder exploded"),
    ):
        await setup_integration(hass, mock_config_entry, mock_client)

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert "Statistics backfill failed" in caplog.text
    assert "recorder exploded" in caplog.text
