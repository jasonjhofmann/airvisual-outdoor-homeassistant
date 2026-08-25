"""Tests for the statistics gap-backfill (insert-missing-hours-only)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.recorder.statistics import (
    STATISTIC_UNIT_TO_UNIT_CONVERTER,
)
from homeassistant.components.sensor.const import UNIT_CONVERTERS
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.airvisual_outdoor.sensor import SENSORS
from custom_components.airvisual_outdoor.statistics import (
    BACKFILL_SOURCES,
    async_backfill_statistics,
)

# The fixture's two hourly entries (UTC hour starts).
HOUR_1 = datetime(2026, 6, 10, 1, tzinfo=UTC)
HOUR_2 = datetime(2026, 6, 10, 2, tzinfo=UTC)

# Freeze two hours past HOUR_2 so both fixture hours clear COMPILE_LAG.
FROZEN_NOW = "2026-06-10 04:30:00+00:00"

_STATS_NS = "custom_components.airvisual_outdoor.statistics"


def _recorder_patch() -> Any:
    """A fake recorder instance whose executor just calls the function."""
    instance = MagicMock()

    async def _run(func: Callable[..., Any], *args: Any) -> Any:
        return func(*args)

    instance.async_add_executor_job = _run
    return patch(f"{_STATS_NS}.get_instance", return_value=instance)


def _patches() -> tuple[Any, Any, Any]:
    """Patch the recorder boundary: instance, existing-row lookup, import sink."""
    during = patch(f"{_STATS_NS}.statistics_during_period", return_value={})
    imp = patch(f"{_STATS_NS}.async_import_statistics")
    return _recorder_patch(), during, imp


def _existing_rows(
    *starts: datetime,
) -> Callable[..., dict[str, list[dict[str, float]]]]:
    """A statistics_during_period stub reporting ``starts`` for every entity."""

    def _existing(
        hass_: HomeAssistant, start: Any, end: Any, ids: set[str], *args: Any
    ) -> dict[str, list[dict[str, float]]]:
        return {eid: [{"start": s.timestamp()} for s in starts] for eid in ids}

    return _existing


@pytest.fixture
def recorder_loaded(hass: HomeAssistant) -> None:
    """Pretend the recorder is set up (the backfill no-ops without it)."""
    hass.config.components.add("recorder")


async def test_backfill_imports_missing_hours(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    recorder_loaded: None,
    freezer: FrozenDateTimeFactory,
) -> None:
    """With no existing rows, every backfillable sensor gets both hours."""
    freezer.move_to(FROZEN_NOW)
    coordinator = init_integration.runtime_data
    rec, during, imp = _patches()
    with rec, during, imp as import_mock:
        imported = await async_backfill_statistics(hass, coordinator)

    # 7 backfillable sensors x 2 fixture hours. Pinned as literals on
    # purpose: deriving them from BACKFILL_SOURCES would make the assertion
    # hold for any value of the constant under test.
    assert imported == 14
    assert import_mock.call_count == 7
    _, metadata, rows = import_mock.call_args_list[0][0]
    assert metadata["source"] == "recorder"
    assert [row["start"] for row in rows] == [HOUR_1, HOUR_2]


async def test_backfill_uses_one_recorder_roundtrip(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    recorder_loaded: None,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Existing rows are looked up for ALL entities in a single query.

    One executor round-trip per sensor would be seven recorder-thread
    queries every hour, forever, for identical work.
    """
    freezer.move_to(FROZEN_NOW)
    coordinator = init_integration.runtime_data
    with (
        _recorder_patch(),
        patch(f"{_STATS_NS}.statistics_during_period", return_value={}) as during,
        patch(f"{_STATS_NS}.async_import_statistics"),
    ):
        await async_backfill_statistics(hass, coordinator)

    assert during.call_count == 1
    requested_ids = during.call_args[0][3]
    assert requested_ids == {
        "sensor.backyard_pm2_5",
        "sensor.backyard_pm10",
        "sensor.backyard_pm1",
        "sensor.backyard_co2",
        "sensor.backyard_temperature",
        "sensor.backyard_humidity",
        "sensor.backyard_pressure",
    }


async def test_backfill_skips_existing_hours(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    recorder_loaded: None,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Hours that already have a statistics row are never re-imported."""
    freezer.move_to(FROZEN_NOW)
    coordinator = init_integration.runtime_data

    with (
        _recorder_patch(),
        patch(
            f"{_STATS_NS}.statistics_during_period",
            side_effect=_existing_rows(HOUR_1),
        ),
        patch(f"{_STATS_NS}.async_import_statistics") as import_mock,
    ):
        imported = await async_backfill_statistics(hass, coordinator)

    # Only HOUR_2 remains per sensor.
    assert imported == 7
    for call in import_mock.call_args_list:
        rows = call[0][2]
        assert [row["start"] for row in rows] == [HOUR_2]


async def test_backfill_skips_current_and_just_completed_hour(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    recorder_loaded: None,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The in-progress hour AND the one before it are left to the recorder.

    HOUR_2 is the hour that just completed at 03:30, so the recorder is
    about to compile its own row for it; importing first collides with the
    unique (metadata_id, start_ts) index. Only HOUR_1 may be imported.
    """
    freezer.move_to("2026-06-10 03:30:00+00:00")
    coordinator = init_integration.runtime_data
    rec, during, imp = _patches()
    with rec, during, imp as import_mock:
        imported = await async_backfill_statistics(hass, coordinator)

    assert imported == 7  # HOUR_1 only
    for call in import_mock.call_args_list:
        rows = call[0][2]
        assert [row["start"] for row in rows] == [HOUR_1]


async def test_backfill_outside_window_is_noop(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    recorder_loaded: None,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Hours older than the 48 h window are ignored entirely."""
    freezer.move_to("2026-06-15 00:00:00+00:00")
    coordinator = init_integration.runtime_data
    rec, during, imp = _patches()
    with rec, during, imp as import_mock:
        imported = await async_backfill_statistics(hass, coordinator)

    assert imported == 0
    assert import_mock.call_count == 0


async def test_backfill_noop_without_hourly_data(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    recorder_loaded: None,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A reading without hourly history is a no-op."""
    from dataclasses import replace

    freezer.move_to(FROZEN_NOW)
    coordinator = init_integration.runtime_data
    coordinator.data = replace(coordinator.data, hourly=())
    assert await async_backfill_statistics(hass, coordinator) == 0


async def test_backfill_noop_without_recorder(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Running HA without the recorder is supported, not an error.

    ``get_instance()`` is a bare ``hass.data[DATA_INSTANCE]`` lookup, so
    reaching it would raise KeyError and log a traceback every hour.
    """
    freezer.move_to(FROZEN_NOW)
    assert "recorder" not in hass.config.components
    coordinator = init_integration.runtime_data
    with patch(f"{_STATS_NS}.get_instance") as get_instance:
        assert await async_backfill_statistics(hass, coordinator) == 0
    get_instance.assert_not_called()


async def test_backfill_skips_unregistered_entities(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    recorder_loaded: None,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A node with no registered entities imports nothing."""
    freezer.move_to(FROZEN_NOW)
    coordinator = init_integration.runtime_data
    coordinator.client.node_id = "feedfacefeedfacefeedface"  # nothing registered
    rec, during, imp = _patches()
    with rec, during, imp as import_mock:
        imported = await async_backfill_statistics(hass, coordinator)
    assert imported == 0
    assert import_mock.call_count == 0


async def test_backfill_all_hours_existing_imports_nothing(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    recorder_loaded: None,
    freezer: FrozenDateTimeFactory,
) -> None:
    """When every candidate hour already has a row, nothing is imported."""
    freezer.move_to(FROZEN_NOW)
    coordinator = init_integration.runtime_data

    with (
        _recorder_patch(),
        patch(
            f"{_STATS_NS}.statistics_during_period",
            side_effect=_existing_rows(HOUR_1, HOUR_2),
        ),
        patch(f"{_STATS_NS}.async_import_statistics") as import_mock,
    ):
        imported = await async_backfill_statistics(hass, coordinator)
    assert imported == 0
    assert import_mock.call_count == 0


# --- metadata contract ------------------------------------------------------


def test_metadata_carries_every_required_key(
    hass: HomeAssistant,
) -> None:
    """Every key ``StatisticMetaData`` requires is present.

    ``unit_class`` in particular: the recorder reads it unconditionally when
    the metadata row already exists (``StatisticsMetaManager._update_metadata``
    does ``new_metadata["unit_class"]``), so omitting it raises KeyError
    inside the recorder thread — where this integration's own try/except
    cannot see it, because async_import_statistics only queues the job.
    """
    from custom_components.airvisual_outdoor.statistics import _metadata

    required = {
        "source",
        "statistic_id",
        "name",
        "has_sum",
        "mean_type",
        "unit_of_measurement",
        "unit_class",
    }
    for source in BACKFILL_SOURCES:
        meta = _metadata(f"sensor.backyard_{source.key}", source)
        assert required <= set(meta), f"{source.key}: missing {required - set(meta)}"
        assert meta["statistic_id"] == f"sensor.backyard_{source.key}"


def test_unit_class_matches_home_assistant() -> None:
    """Each pinned ``unit_class`` equals what HA derives for that sensor.

    The recorder keys statistics metadata by entity id, which this
    integration's sensor platform also owns. If the imported metadata
    disagreed with the sensor platform's, the two would rewrite the same
    row against each other on every compile.
    """

    def _ha_unit_class(device_class: Any, unit: Any) -> str | None:
        """HA's own derivation (homeassistant.components.sensor.recorder)."""
        if (
            device_class
            and (conv := UNIT_CONVERTERS.get(device_class))
            and unit in conv.VALID_UNITS
        ):
            return conv.UNIT_CLASS
        if conv := STATISTIC_UNIT_TO_UNIT_CONVERTER.get(unit):
            return conv.UNIT_CLASS
        return None

    by_key = {d.key: d for d in SENSORS}
    for source in BACKFILL_SOURCES:
        description = by_key[source.key]
        expected = _ha_unit_class(
            description.device_class, description.native_unit_of_measurement
        )
        assert source.unit_class == expected, source.key


def test_backfill_sources_match_sensor_units() -> None:
    """The backfill's unit for a key is the sensor entity's own unit.

    They are declared in two places; this pins them together so a unit
    migration that touches only one file fails CI instead of silently
    rewriting statistics metadata.
    """
    by_key = {d.key: d for d in SENSORS}
    for source in BACKFILL_SOURCES:
        assert source.key in by_key, f"{source.key} has no sensor description"
        assert source.unit == by_key[source.key].native_unit_of_measurement


# Expected (entity_id, unit, [HOUR_1 mean, HOUR_2 mean]) straight from the
# captured fixture's two hourly entries.
EXPECTED_IMPORT = {
    "sensor.backyard_pm2_5": ("μg/m³", [1.0, 1.0]),
    "sensor.backyard_pm10": ("μg/m³", [2.0, 2.0]),
    "sensor.backyard_pm1": ("μg/m³", [1.0, 1.0]),
    "sensor.backyard_co2": ("ppm", [490.0, 460.0]),
    "sensor.backyard_temperature": ("°C", [35.0, 33.0]),
    "sensor.backyard_humidity": ("%", [27.0, 36.0]),
    "sensor.backyard_pressure": ("Pa", [92167.0, 92221.0]),
}


async def test_backfill_imports_the_right_channel_and_unit(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    recorder_loaded: None,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Each statistic gets ITS OWN hourly channel, under its own unit.

    The other backfill tests only assert row starts and counts, so the
    extractor lambdas ran and their outputs were discarded: swapping
    ``pm25_conc`` for ``pm10_conc``, or importing temperature under ``%``,
    passed every one of them at 100% line and branch coverage.
    """
    freezer.move_to(FROZEN_NOW)
    coordinator = init_integration.runtime_data
    rec, during, imp = _patches()
    with rec, during, imp as import_mock:
        await async_backfill_statistics(hass, coordinator)

    got = {
        metadata["statistic_id"]: (
            metadata["unit_of_measurement"],
            [row["mean"] for row in rows],
        )
        for _, metadata, rows in (call[0] for call in import_mock.call_args_list)
    }
    assert got == EXPECTED_IMPORT
