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

The most recently completed hour is deliberately NOT imported — see
:data:`COMPILE_LAG` for why.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from homeassistant.components.recorder.const import DOMAIN as RECORDER_DOMAIN
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    statistics_during_period,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfDensity,
    UnitOfPressure,
    UnitOfRatio,
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

# Never import the hour that just completed. The recorder compiles its OWN
# hourly row for hour H shortly after H+1:00 by summarising that hour's
# short-term statistics, and it does so with a bare INSERT against a UNIQUE
# (metadata_id, start_ts) index. Importing H first makes that INSERT collide;
# HA swallows the IntegrityError but logs a "Blocked attempt to insert
# duplicated statistic rows, please report at <core issue tracker>" warning
# and rolls the whole compile period back. Skipping one hour costs nothing:
# if HA was up for hour H the recorder has the states and produces a better
# (min/mean/max) row itself, and if HA was down for hour H there is no
# short-term data to collide with and the next pass imports it, still well
# inside BACKFILL_WINDOW.
COMPILE_LAG: Final = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class BackfillSource:
    """One sensor whose hourly counterpart can be back-imported.

    ``unit`` and ``device_class`` MUST match the sensor entity's own
    ``native_unit_of_measurement`` / ``device_class`` in ``sensor.py``: the
    recorder keys statistics metadata by entity id, so a mismatch here makes
    every import rewrite the row the sensor platform just wrote (and back
    again on the next compile). ``tests/test_statistics.py`` pins the two
    tables against each other so drift fails CI rather than a user's
    database.

    ``unit_class`` is the recorder's unit-conversion class for that pairing.
    It is a REQUIRED key of ``StatisticMetaData``; omitting it raises
    ``KeyError`` inside the recorder thread the moment the metadata row
    already exists. The values are pinned literally (rather than derived at
    import time from HA internals) and re-derived from HA's own maps by
    ``test_unit_class_matches_home_assistant``.
    """

    key: str
    extract: Callable[[HourlyReading], float | int | None]
    unit: str
    unit_class: str | None


BACKFILL_SOURCES: tuple[BackfillSource, ...] = (
    BackfillSource(
        key="pm25",
        extract=lambda h: h.pm25_conc,
        unit=UnitOfDensity.MICROGRAMS_PER_CUBIC_METER,
        unit_class="concentration",
    ),
    BackfillSource(
        key="pm10",
        extract=lambda h: h.pm10_conc,
        unit=UnitOfDensity.MICROGRAMS_PER_CUBIC_METER,
        unit_class="concentration",
    ),
    BackfillSource(
        key="pm1",
        extract=lambda h: h.pm1_conc,
        unit=UnitOfDensity.MICROGRAMS_PER_CUBIC_METER,
        unit_class="concentration",
    ),
    BackfillSource(
        key="co2",
        extract=lambda h: h.co2,
        unit=UnitOfRatio.PARTS_PER_MILLION,
        unit_class="unitless",
    ),
    BackfillSource(
        key="temperature",
        extract=lambda h: h.temperature,
        unit=UnitOfTemperature.CELSIUS,
        unit_class="temperature",
    ),
    BackfillSource(
        key="humidity",
        extract=lambda h: h.humidity,
        unit=PERCENTAGE,
        unit_class="unitless",
    ),
    BackfillSource(
        key="pressure",
        extract=lambda h: h.pressure,
        unit=UnitOfPressure.PA,
        unit_class="pressure",
    ),
)


def _is_hour_aligned(ts: datetime) -> bool:
    """Whether a timestamp sits exactly on the top of the hour."""
    return ts.minute == 0 and ts.second == 0 and ts.microsecond == 0


def _metadata(entity_id: str, source: BackfillSource) -> StatisticMetaData:
    """Statistics metadata for an entity-owned (source=recorder) import.

    Built as a ``StatisticMetaData`` literal on purpose: it is a TypedDict
    with REQUIRED keys, so mypy --strict catches a missing one here. An
    earlier revision assembled a ``dict[str, object]`` and ``cast()`` it,
    which silently hid the absence of ``unit_class``.
    """
    return StatisticMetaData(
        source="recorder",
        statistic_id=entity_id,
        name=None,
        has_sum=False,
        mean_type=StatisticMeanType.ARITHMETIC,
        unit_of_measurement=source.unit,
        unit_class=source.unit_class,
    )


async def async_backfill_statistics(
    hass: HomeAssistant, coordinator: AirVisualOutdoorCoordinator
) -> int:
    """Import missing completed hours into long-term statistics.

    Returns the number of imported rows. Insert-missing-only: hours that
    already have a statistics row are left untouched. A no-op (returning 0)
    when the recorder is not set up — running HA without it is a supported
    choice, not an error worth a traceback every hour.
    """
    if RECORDER_DOMAIN not in hass.config.components:
        _LOGGER.debug("Recorder is not set up; skipping statistics backfill")
        return 0

    reading = coordinator.data
    if reading is None or not reading.hourly:
        return 0

    now = dt_util.utcnow()
    cutoff = now - BACKFILL_WINDOW
    newest = now.replace(minute=0, second=0, microsecond=0) - COMPILE_LAG
    in_window = [h for h in reading.hourly if cutoff <= h.ts < newest]

    # HA validates every imported row's start SYNCHRONOUSLY and rejects the
    # whole call: recorder/statistics.py::_async_import_statistics raises
    # HomeAssistantError("Invalid timestamp: timestamps must be from the top
    # of the hour") — so a single off-hour entry from the API would kill the
    # backfill for ALL seven sensors, every hour, forever. Drop the offender
    # instead of the batch. (Naive stamps, which it also rejects, cannot get
    # this far: api.py normalises them to UTC.)
    candidates = [h for h in in_window if _is_hour_aligned(h.ts)]
    if len(candidates) != len(in_window):
        _LOGGER.warning(
            "Node %s returned %d hourly entries that are not on the hour;"
            " skipping them (the rest of the backfill is unaffected)",
            coordinator.client.node_id,
            len(in_window) - len(candidates),
        )
    if not candidates:
        return 0

    ent_reg = er.async_get(hass)
    node_id = coordinator.client.node_id
    window_start = min(h.ts for h in candidates)
    window_end = max(h.ts for h in candidates)

    # entity_id -> source, for the entities that actually exist.
    targets: dict[str, BackfillSource] = {}
    for source in BACKFILL_SOURCES:
        entity_id = ent_reg.async_get_entity_id(
            "sensor", DOMAIN, f"{DOMAIN}_{node_id}_{source.key}"
        )
        if entity_id is not None:
            targets[entity_id] = source
    if not targets:
        return 0

    # ONE recorder round-trip for every entity, not one per entity.
    existing = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        window_start,
        None,
        set(targets),
        "hour",
        None,
        {"mean"},
    )

    imported = 0
    for entity_id, source in targets.items():
        have: set[float] = {row["start"] for row in existing.get(entity_id, [])}
        rows = [
            StatisticData(start=h.ts, mean=float(value))
            for h in candidates
            if (value := source.extract(h)) is not None and h.ts.timestamp() not in have
        ]
        if not rows:
            continue
        async_import_statistics(hass, _metadata(entity_id, source), rows)
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
