"""Tests for entry setup in ``__init__.py``.

Most setup behavior is exercised indirectly by the sensor/statistics tests;
this module covers what only setup itself owns — the backfill background
task's crash containment, and leaving the device's connections alone.
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


async def test_backfill_task_is_owned_by_the_entry(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
) -> None:
    """Unloading the entry must cancel an in-flight backfill.

    ``hass.async_create_background_task`` only dies at HA shutdown, so a
    long backfill would keep writing statistics for an entry the user just
    removed. ``ConfigEntry.async_create_background_task`` is cancelled by
    ``_async_process_on_unload``.
    """
    import asyncio

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def _hang(*args: object, **kwargs: object) -> int:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return 0

    with patch(
        "custom_components.airvisual_outdoor.async_backfill_statistics",
        side_effect=_hang,
    ):
        await setup_integration(hass, mock_config_entry, mock_client)
        await started.wait()
        assert mock_config_entry.state is ConfigEntryState.LOADED

        assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert cancelled.is_set()


async def test_backfill_is_throttled_between_updates(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
) -> None:
    """Coordinator updates inside the throttle window do not re-run backfill.

    Polling is every 300 s but the 48 h hourly array only gains a row once
    an hour; re-scanning on every poll would be 12× the recorder work for
    nothing.
    """
    coordinator = init_integration.runtime_data
    with patch(
        "custom_components.airvisual_outdoor.async_backfill_statistics",
        return_value=0,
    ) as backfill:
        for _ in range(3):
            await coordinator.async_refresh()
            await hass.async_block_till_done()
    backfill.assert_not_called()


@pytest.mark.parametrize("with_mac", [True, False], ids=["mac", "no_mac"])
async def test_setup_leaves_other_connections_alone(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    with_mac: bool,
) -> None:
    """Setup never drops a connection this entry did not register.

    The device row can carry connections that are not ours: before HA 2026.8
    the optional MAC merged the device with another integration's device for
    the same unit, so both config entries share the row. Setup used to
    overwrite the whole set with the entry's own MAC, or with nothing when
    no MAC was configured, wiping the other integration's connections on
    every restart.
    """
    from homeassistant.helpers import device_registry as dr

    from custom_components.airvisual_outdoor.const import CONF_MAC

    from .conftest import (
        FOREIGN_CONNECTION,
        TEST_MAC,
        add_foreign_connection,
        node_device,
        patch_client,
    )

    data = dict(mock_config_entry.data)
    if not with_mac:
        del data[CONF_MAC]
    entry = MockConfigEntry(
        domain=mock_config_entry.domain,
        title=mock_config_entry.title,
        data=data,
        unique_id=mock_config_entry.unique_id,
    )
    await setup_integration(hass, entry, mock_client)
    add_foreign_connection(hass, entry)

    with patch_client(mock_client):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    expected = {FOREIGN_CONNECTION}
    if with_mac:
        expected.add((dr.CONNECTION_NETWORK_MAC, TEST_MAC))
    assert node_device(hass, entry).connections == expected


async def test_backfill_does_not_keep_the_poll_schedule_alive(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The backfill must not be a coordinator listener.

    DataUpdateCoordinator polls only while it HAS listeners. Registering one
    for the backfill would keep burning the node's request budget — which is
    shared globally with every other consumer of that station — even when
    every entity is disabled and nothing consumes the data.
    """
    from homeassistant.helpers import entity_registry as er

    coordinator = init_integration.runtime_data
    assert coordinator.on_updated is not None

    # Exactly one listener per entity, and nothing else. Before this was a
    # hook it was entities + 1, and that extra one alone was enough to keep
    # the 300 s poll scheduled forever.
    entities = er.async_entries_for_config_entry(
        er.async_get(hass), init_integration.entry_id
    )
    assert len(entities) == 10
    assert len(coordinator._listeners) == len(entities)
