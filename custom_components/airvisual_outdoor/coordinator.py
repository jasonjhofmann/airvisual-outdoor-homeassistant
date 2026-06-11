"""DataUpdateCoordinator wiring for AirVisual Outdoor."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    AirVisualOutdoorClient,
    AirVisualOutdoorError,
    NodeReading,
    RateLimitError,
)
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

type AirVisualOutdoorConfigEntry = ConfigEntry[AirVisualOutdoorCoordinator]


class AirVisualOutdoorCoordinator(DataUpdateCoordinator[NodeReading]):
    """Polls one node at the fixed cadence and holds its latest reading."""

    config_entry: AirVisualOutdoorConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: AirVisualOutdoorConfigEntry,
        client: AirVisualOutdoorClient,
    ) -> None:
        """Bind the coordinator to its entry and API client."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}:{client.node_id}",
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self.client = client

    async def _async_update_data(self) -> NodeReading:
        """Fetch the node's current reading.

        A rate-limited cycle keeps the last reading instead of failing: the
        30/hour budget is GLOBAL per node id across all client IPs, so any
        third party hitting a published station's endpoint can drain it —
        that shouldn't blip entities unavailable when the entity-level
        ``ts`` staleness guard already protects against serving dead data.

        Every other API failure mode — including ``device_not_found``,
        which can be a transient cloud-side publish glitch — maps to
        UpdateFailed so the coordinator retries at the next cycle instead
        of killing the entry.
        """
        try:
            return await self.client.async_get_reading()
        except RateLimitError as err:
            if self.data is not None:
                _LOGGER.warning(
                    "Node %s request budget exhausted (resets at the top of"
                    " the hour); keeping last reading: %s",
                    self.client.node_id,
                    err,
                )
                return self.data
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="rate_limited",
            ) from err
        except AirVisualOutdoorError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err
