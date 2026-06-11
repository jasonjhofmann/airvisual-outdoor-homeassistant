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
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .api import AirVisualOutdoorClient
from .const import BACKFILL_MIN_INTERVAL, CONF_NODE_ID, DOMAIN
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

    # Statistics gap-backfill: once now (entities just registered), then at
    # most hourly via the coordinator listener. Background task on purpose —
    # a blocking task here would stall HA's startup wrap-up and can cascade
    # into freezing OTHER integrations.
    last_run: datetime | None = None

    async def _backfill() -> None:
        try:
            await async_backfill_statistics(hass, coordinator)
        except Exception:
            _LOGGER.exception("Statistics backfill failed")

    def _maybe_backfill() -> None:
        nonlocal last_run
        now = dt_util.utcnow()
        if last_run is not None and now - last_run < BACKFILL_MIN_INTERVAL:
            return
        last_run = now
        hass.async_create_background_task(
            _backfill(), name=f"{DOMAIN} statistics backfill"
        )

    _maybe_backfill()
    entry.async_on_unload(coordinator.async_add_listener(_maybe_backfill))

    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: AirVisualOutdoorConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
