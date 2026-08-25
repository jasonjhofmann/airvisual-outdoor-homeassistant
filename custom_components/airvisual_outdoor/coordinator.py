"""DataUpdateCoordinator wiring for AirVisual Outdoor."""

from __future__ import annotations

import logging
from collections.abc import Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    AirVisualOutdoorClient,
    AirVisualOutdoorError,
    NodeReading,
    RateLimitError,
)
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN, STALENESS_THRESHOLD

_LOGGER = logging.getLogger(__name__)

type AirVisualOutdoorConfigEntry = ConfigEntry[AirVisualOutdoorCoordinator]


def sample_is_stale(reading: NodeReading | None) -> bool:
    """Whether the payload's own sample timestamp is too old to trust.

    The single definition of "stale" — the entity availability guard and the
    coordinator's staleness logging both call this, so the log can never
    disagree with what the user sees. A missing ``ts`` counts as stale:
    no proof of freshness is not proof of freshness.
    """
    if reading is None or reading.ts is None:
        return True
    # abs(): a timestamp in the FUTURE is not freshness. A device with a
    # skewed clock (or a cloud-side glitch) that stamps samples hours ahead
    # would otherwise satisfy `now - ts <= threshold` forever and pin every
    # entity "available" on data that never updates again.
    return abs(dt_util.utcnow() - reading.ts) > STALENESS_THRESHOLD


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
        self._stale = False
        #: Called after every SUCCESSFUL update. Set by ``async_setup_entry``
        #: to trigger the throttled statistics backfill. Deliberately not a
        #: coordinator listener: DataUpdateCoordinator polls only while it
        #: HAS listeners, so registering one for the backfill would keep the
        #: node's globally-shared request budget burning even when every
        #: entity is disabled and nothing consumes the data.
        self.on_updated: Callable[[], None] | None = None

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
        self._log_staleness(reading)
        if self.on_updated is not None:
            self.on_updated()
        return reading

    def _log_staleness(self, reading: NodeReading) -> None:
        """Say WHY entities went unavailable on an otherwise healthy poll.

        A dead station is the one failure mode that produces no log line
        anywhere: the cloud answers 200, the coordinator succeeds, and the
        entities quietly go unavailable via the entity-level guard. Without
        this the user has a device full of `unavailable` and an empty log.
        """
        stale = sample_is_stale(reading)
        if stale == self._stale:
            return
        self._stale = stale
        if stale:
            _LOGGER.warning(
                "Node %s is serving a stale sample (timestamp %s, older than"
                " %s); the API still answers 200, so the station itself has"
                " most likely stopped reporting. Entities are unavailable"
                " until it recovers",
                self.client.node_id,
                reading.ts,
                STALENESS_THRESHOLD,
            )
        else:
            _LOGGER.info(
                "Node %s is reporting fresh samples again", self.client.node_id
            )
