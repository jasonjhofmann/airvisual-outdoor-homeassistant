"""AirVisual Outdoor: cloud polling for IQAir's outdoor monitor.

The Outdoor hardware has no local API (unlike the AirVisual Pro), so this
integration polls IQAir's keyless per-device node endpoint. Architecture
and API ground truth: ``docs/architecture.md`` in the repository.
"""

from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .api import AirVisualOutdoorClient
from .const import BACKFILL_MIN_INTERVAL, CONF_MAC, CONF_NODE_ID, DOMAIN
from .coordinator import AirVisualOutdoorConfigEntry, AirVisualOutdoorCoordinator
from .statistics import async_backfill_statistics

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(
    hass: HomeAssistant, entry: AirVisualOutdoorConfigEntry
) -> bool:
    """Set up one node from a config entry."""
    client = AirVisualOutdoorClient(
        session=async_get_clientsession(hass),
        node_id=entry.data[CONF_NODE_ID],
    )
    coordinator = AirVisualOutdoorCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _reconcile_device_connections(hass, entry)

    # Statistics gap-backfill: once now (entities just registered), then no
    # more often than BACKFILL_MIN_INTERVAL (55 min) via the coordinator
    # listener. Note "no more often than", not "hourly": `last_run` is a
    # closure local, so a reload legitimately re-runs it immediately. Background task on purpose —
    # a blocking task here would stall HA's startup wrap-up and can cascade
    # into freezing OTHER integrations. It is an ENTRY-owned background task
    # so unload/reload cancels an in-flight backfill; the hass-level variant
    # only dies at HA shutdown and would keep writing statistics for an
    # entry the user just removed.
    last_run: datetime | None = None

    async def _backfill() -> None:
        try:
            await async_backfill_statistics(hass, coordinator)
        except Exception:
            # Entry title, not just the domain: a multi-node install has one
            # of these per monitor and the traceback alone cannot say which.
            _LOGGER.exception("Statistics backfill failed for %s", entry.title)

    def _maybe_backfill() -> None:
        nonlocal last_run
        now = dt_util.utcnow()
        if last_run is not None and now - last_run < BACKFILL_MIN_INTERVAL:
            return
        last_run = now
        entry.async_create_background_task(
            hass, _backfill(), name=f"{DOMAIN} statistics backfill"
        )

    _maybe_backfill()
    coordinator.on_updated = _maybe_backfill

    return True


def _reconcile_device_connections(
    hass: HomeAssistant, entry: AirVisualOutdoorConfigEntry
) -> None:
    """Match the device's MAC connection to the entry's current config.

    ``DeviceInfo.connections`` is MERGED into the device entry on every
    setup, never replaced, so dropping the optional MAC in the reconfigure
    flow would otherwise leave the stale connection — and the network-device
    link it creates — in place forever.

    Rewriting connections wholesale is safe here because a device entry
    belongs to exactly one config entry (``DeviceEntry.config_entry_id``);
    this row is ours alone and cannot be carrying another integration's
    connections.
    """
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_device(identifiers={(DOMAIN, entry.data[CONF_NODE_ID])})
    if device is None:
        return
    configured: set[tuple[str, str]] = set()
    if mac := entry.data.get(CONF_MAC):
        configured = {(dr.CONNECTION_NETWORK_MAC, dr.format_mac(mac))}
    if device.connections != configured:
        dev_reg.async_update_device(device.id, new_connections=configured)


async def async_unload_entry(
    hass: HomeAssistant, entry: AirVisualOutdoorConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
