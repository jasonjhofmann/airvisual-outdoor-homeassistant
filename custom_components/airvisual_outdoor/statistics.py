"""Statistics gap-backfill from the node's own ``hourly`` history.

Every poll carries 48 h of completed-hour aggregates. When HA was down (or
the entry is freshly added), those hours are missing from the recorder's
long-term statistics; this module imports exactly the missing hours so
outages never leave permanent holes.

Scope limits (by HA design): heals long-term *statistics only* — raw
recorder states cannot be backfilled — at hourly granularity, mean-only
(the API provides one value per hour). Only sensors with a direct hourly
counterpart are covered: PM2.5/PM10/PM1, CO₂, temperature, humidity,
pressure. AQI / main pollutant have no hourly source and are skipped.
Existing statistics rows are never overwritten.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import cast

from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    statistics_during_period,
)
from homeassistant.const import (
    CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    CONCENTRATION_PARTS_PER_MILLION,
    PERCENTAGE,
    UnitOfPressure,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.recorder import get_instance
from homeassistant.util import dt as dt_util

from .api import HourlyReading
from .const import BACKFILL_WINDOW, DOMAIN
from .coordinator import AirVisualOutdoorCoordinator

_LOGGER = logging.getLogger(__name__)

# sensor description key -> (hourly extractor, unit for the metadata row)
BACKFILL_SOURCES: dict[
    str, tuple[Callable[[HourlyReading], float | int | None], str]
] = {
    "pm25": (lambda h: h.pm25_conc, CONCENTRATION_MICROGRAMS_PER_CUBIC_METER),
    "pm10": (lambda h: h.pm10_conc, CONCENTRATION_MICROGRAMS_PER_CUBIC_METER),
    "pm1": (lambda h: h.pm1_conc, CONCENTRATION_MICROGRAMS_PER_CUBIC_METER),
    "co2": (lambda h: h.co2, CONCENTRATION_PARTS_PER_MILLION),
    "temperature": (lambda h: h.temperature, UnitOfTemperature.CELSIUS),
    "humidity": (lambda h: h.humidity, PERCENTAGE),
    "pressure": (lambda h: h.pressure, UnitOfPressure.PA),
}


def _metadata(entity_id: str, unit: str) -> StatisticMetaData:
    """Statistics metadata for an entity-owned (source=recorder) import.

    HA 2025.4 replaced ``has_mean`` with ``mean_type``; support both so the
    integration spans the deprecation window.
    """
    meta: dict[str, object] = {
        "source": "recorder",
        "statistic_id": entity_id,
        "name": None,
        "has_sum": False,
        "unit_of_measurement": unit,
    }
    try:
        from homeassistant.components.recorder.models import StatisticMeanType

        meta["mean_type"] = StatisticMeanType.ARITHMETIC
    except ImportError:
        meta["has_mean"] = True
    return cast(StatisticMetaData, meta)


async def async_backfill_statistics(
    hass: HomeAssistant, coordinator: AirVisualOutdoorCoordinator
) -> int:
    """Import missing completed hours into long-term statistics.

    Returns the number of imported rows. Insert-missing-only: hours that
    already have a statistics row are left untouched.
    """
    reading = coordinator.data
    if reading is None or not reading.hourly:
        return 0

    now = dt_util.utcnow()
    cutoff = now - BACKFILL_WINDOW
    current_hour_start = now.replace(minute=0, second=0, microsecond=0)
    candidates = [h for h in reading.hourly if cutoff <= h.ts < current_hour_start]
    if not candidates:
        return 0

    ent_reg = er.async_get(hass)
    node_id = coordinator.client.node_id
    window_start = min(h.ts for h in candidates)
    window_end = max(h.ts for h in candidates)

    imported = 0
    for key, (extract, unit) in BACKFILL_SOURCES.items():
        entity_id = ent_reg.async_get_entity_id(
            "sensor", DOMAIN, f"{DOMAIN}_{node_id}_{key}"
        )
        if entity_id is None:
            continue

        existing = await get_instance(hass).async_add_executor_job(
            statistics_during_period,
            hass,
            window_start,
            None,
            {entity_id},
            "hour",
            None,
            {"mean"},
        )
        have: set[float] = {row["start"] for row in existing.get(entity_id, [])}

        rows = [
            StatisticData(start=h.ts, mean=float(value))
            for h in candidates
            if (value := extract(h)) is not None and h.ts.timestamp() not in have
        ]
        if not rows:
            continue
        async_import_statistics(hass, _metadata(entity_id, unit), rows)
        imported += len(rows)

    if imported:
        _LOGGER.info(
            "Backfilled %d statistics rows for node %s (window %s..%s)",
            imported,
            node_id,
            window_start,
            window_end,
        )
    return imported
