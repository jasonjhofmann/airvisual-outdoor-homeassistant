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
        self._rate_limited = False

    async def _async_update_data(self) -> NodeReading:
        """Fetch the node's current reading.

        A rate-limited cycle keeps the last reading instead of failing: the
        30/hour budget is GLOBAL per node id across all client IPs, so any
        third party hitting a published station's endpoint can drain it —
        that shouldn't blip entities unavailable when the entity-level
        ``ts`` staleness guard already protects against serving dead data.

        Because that branch deliberately keeps ``last_update_success`` True,
        it also bypasses DataUpdateCoordinator's own once-per-episode log
        suppression, so the warning is edge-triggered here by hand: once when
        the budget drains and once when it recovers. Logging every cycle
        would emit 12 identical warnings an hour for as long as an external
        consumer keeps the bucket empty.

        Every other API failure mode — including ``device_not_found``,
        which can be a transient cloud-side publish glitch — maps to
        UpdateFailed so the coordinator retries at the next cycle instead
        of killing the entry.
        """
        try:
            reading = await self.client.async_get_reading()
        except RateLimitError as err:
            if self.data is not None:
                if not self._rate_limited:
                    self._rate_limited = True
                    _LOGGER.warning(
                        "Node %s request budget exhausted (resets at the top"
                        " of the hour); keeping the last reading until it"
                        " recovers: %s",
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

        if self._rate_limited:
            self._rate_limited = False
            _LOGGER.info("Node %s request budget recovered", self.client.node_id)
        return reading
