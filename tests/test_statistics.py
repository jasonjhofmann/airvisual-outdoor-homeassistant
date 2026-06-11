"""Tests for the statistics gap-backfill (insert-missing-hours-only)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.airvisual_outdoor.statistics import (
    BACKFILL_SOURCES,
    async_backfill_statistics,
)

# The fixture's two hourly entries (UTC hour starts).
HOUR_1 = datetime(2026, 6, 10, 1, tzinfo=UTC)
HOUR_2 = datetime(2026, 6, 10, 2, tzinfo=UTC)

# Freeze inside the NEXT hour: both fixture hours are completed + in-window.
FROZEN_NOW = "2026-06-10 03:30:00+00:00"

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


async def test_backfill_imports_missing_hours(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """With no existing rows, every backfillable sensor gets both hours."""
    freezer.move_to(FROZEN_NOW)
    coordinator = init_integration.runtime_data
    rec, during, imp = _patches()
    with rec, during, imp as import_mock:
        imported = await async_backfill_statistics(hass, coordinator)

    # 7 backfillable sensors × 2 fixture hours
    assert imported == len(BACKFILL_SOURCES) * 2
    assert import_mock.call_count == len(BACKFILL_SOURCES)
    _, metadata, rows = import_mock.call_args_list[0][0]
    assert metadata["source"] == "recorder"
    assert [row["start"] for row in rows] == [HOUR_1, HOUR_2]


async def test_backfill_skips_existing_hours(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Hours that already have a statistics row are never re-imported."""
    freezer.move_to(FROZEN_NOW)
    coordinator = init_integration.runtime_data

    def _existing(
        hass_: HomeAssistant, start: Any, end: Any, ids: set[str], *args: Any
    ) -> dict[str, list[dict[str, float]]]:
        entity_id = next(iter(ids))
        return {entity_id: [{"start": HOUR_1.timestamp()}]}

    with (
        _recorder_patch(),
        patch(f"{_STATS_NS}.statistics_during_period", side_effect=_existing),
        patch(f"{_STATS_NS}.async_import_statistics") as import_mock,
    ):
        imported = await async_backfill_statistics(hass, coordinator)

    # Only HOUR_2 remains per sensor.
    assert imported == len(BACKFILL_SOURCES)
    for call in import_mock.call_args_list:
        rows = call[0][2]
        assert [row["start"] for row in rows] == [HOUR_2]


async def test_backfill_skips_future_and_current_hour(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The in-progress hour is never imported (its aggregate isn't final)."""
    freezer.move_to("2026-06-10 02:30:00+00:00")  # HOUR_2 is in progress
    coordinator = init_integration.runtime_data
    rec, during, imp = _patches()
    with rec, during, imp as import_mock:
        imported = await async_backfill_statistics(hass, coordinator)

    assert imported == len(BACKFILL_SOURCES)  # HOUR_1 only
    for call in import_mock.call_args_list:
        rows = call[0][2]
        assert [row["start"] for row in rows] == [HOUR_1]


async def test_backfill_outside_window_is_noop(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
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
    freezer: FrozenDateTimeFactory,
) -> None:
    """A reading without hourly history is a no-op."""
    from dataclasses import replace

    freezer.move_to(FROZEN_NOW)
    coordinator = init_integration.runtime_data
    coordinator.data = replace(coordinator.data, hourly=())
    assert await async_backfill_statistics(hass, coordinator) == 0


async def test_backfill_skips_unregistered_entities(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
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
    freezer: FrozenDateTimeFactory,
) -> None:
    """When every candidate hour already has a row, nothing is imported."""
    freezer.move_to(FROZEN_NOW)
    coordinator = init_integration.runtime_data

    def _existing(
        hass_: HomeAssistant, start: Any, end: Any, ids: set[str], *args: Any
    ) -> dict[str, list[dict[str, float]]]:
        entity_id = next(iter(ids))
        return {
            entity_id: [
                {"start": HOUR_1.timestamp()},
                {"start": HOUR_2.timestamp()},
            ]
        }

    with (
        _recorder_patch(),
        patch(f"{_STATS_NS}.statistics_during_period", side_effect=_existing),
        patch(f"{_STATS_NS}.async_import_statistics") as import_mock,
    ):
        imported = await async_backfill_statistics(hass, coordinator)
    assert imported == 0
    assert import_mock.call_count == 0


def test_metadata_falls_back_to_has_mean_without_meantype() -> None:
    """Pre-2025.4 cores without StatisticMeanType get has_mean=True."""
    import sys
    from types import ModuleType

    from custom_components.airvisual_outdoor.statistics import _metadata

    stub = ModuleType("homeassistant.components.recorder.models")
    stub.StatisticData = dict  # type: ignore[attr-defined]
    stub.StatisticMetaData = dict  # type: ignore[attr-defined]
    with patch.dict(sys.modules, {"homeassistant.components.recorder.models": stub}):
        meta = _metadata("sensor.test", "ppm")
    assert meta["has_mean"] is True
    assert "mean_type" not in meta
