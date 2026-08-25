"""Sensor platform — the full launch set (10 entities per node).

The AQI and main-pollutant sensors are scale-dependent: exactly one of each
is created, per the entry's config-time US/CN choice, with scale-specific
unique_ids so a reconfigure-time scale switch produces fresh registry rows.

With ``has_entity_name``, entity ids slug from the user's device name plus
the entity name (device "Backyard" + name "CO2" → ``sensor.backyard_co2``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
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
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .api import NodeReading
from .const import CONF_AQI_SCALE, DOMAIN
from .coordinator import AirVisualOutdoorConfigEntry, AirVisualOutdoorCoordinator
from .entity import AirVisualOutdoorEntity

PARALLEL_UPDATES = 0  # coordinator-managed


@dataclass(frozen=True, kw_only=True)
class AirVisualOutdoorSensorDescription(SensorEntityDescription):
    """Sensor description carrying its reading extractor and scale gate."""

    value_fn: Callable[[NodeReading], StateType | datetime]
    scale: str | None = None  # None = scale-independent, always created
    # Staleness-exempt entities stay available on a stale sample; see
    # AirVisualOutdoorEntity.available.
    staleness_exempt: bool = False


SENSORS: tuple[AirVisualOutdoorSensorDescription, ...] = (
    AirVisualOutdoorSensorDescription(
        key="aqius",
        translation_key="aqi_us",
        device_class=SensorDeviceClass.AQI,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda reading: reading.aqi_us,
        scale="us",
    ),
    AirVisualOutdoorSensorDescription(
        key="aqicn",
        translation_key="aqi_cn",
        device_class=SensorDeviceClass.AQI,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda reading: reading.aqi_cn,
        scale="cn",
    ),
    AirVisualOutdoorSensorDescription(
        key="pm25",
        translation_key="pm25",
        device_class=SensorDeviceClass.PM25,
        native_unit_of_measurement=UnitOfDensity.MICROGRAMS_PER_CUBIC_METER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda reading: reading.pm25.conc,
    ),
    AirVisualOutdoorSensorDescription(
        key="pm10",
        translation_key="pm10",
        device_class=SensorDeviceClass.PM10,
        native_unit_of_measurement=UnitOfDensity.MICROGRAMS_PER_CUBIC_METER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda reading: reading.pm10.conc,
    ),
    AirVisualOutdoorSensorDescription(
        key="pm1",
        translation_key="pm1",
        device_class=SensorDeviceClass.PM1,
        native_unit_of_measurement=UnitOfDensity.MICROGRAMS_PER_CUBIC_METER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda reading: reading.pm1.conc,
    ),
    AirVisualOutdoorSensorDescription(
        key="co2",
        translation_key="co2",
        device_class=SensorDeviceClass.CO2,
        native_unit_of_measurement=UnitOfRatio.PARTS_PER_MILLION,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda reading: reading.co2,
    ),
    AirVisualOutdoorSensorDescription(
        key="temperature",
        translation_key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda reading: reading.temperature,
    ),
    AirVisualOutdoorSensorDescription(
        key="humidity",
        translation_key="humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda reading: reading.humidity,
    ),
    AirVisualOutdoorSensorDescription(
        key="pressure",
        translation_key="pressure",
        device_class=SensorDeviceClass.ATMOSPHERIC_PRESSURE,
        native_unit_of_measurement=UnitOfPressure.PA,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda reading: reading.pressure,
    ),
    AirVisualOutdoorSensorDescription(
        key="mainus",
        translation_key="main_pollutant",
        value_fn=lambda reading: reading.main_pollutant_us,
        scale="us",
    ),
    AirVisualOutdoorSensorDescription(
        key="maincn",
        translation_key="main_pollutant",
        value_fn=lambda reading: reading.main_pollutant_cn,
        scale="cn",
    ),
    AirVisualOutdoorSensorDescription(
        key="last_updated",
        translation_key="last_updated",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda reading: reading.ts,
        staleness_exempt=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AirVisualOutdoorConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Wire the node's sensors for the entry's AQI scale."""
    coordinator = entry.runtime_data
    scale: str = entry.data[CONF_AQI_SCALE]
    _drop_other_scale_entities(hass, coordinator.client.node_id, scale)
    async_add_entities(
        AirVisualOutdoorSensor(coordinator, description)
        for description in SENSORS
        if description.scale in (None, scale)
    )


def _drop_other_scale_entities(hass: HomeAssistant, node_id: str, scale: str) -> None:
    """Remove registry rows left behind by a previous AQI-scale choice.

    Switching scale in the reconfigure flow stops creating the old scale's
    AQI / main-pollutant entities, but HA keeps their registry rows: the
    user is left with permanently ``unavailable`` entities, and — worse —
    the abandoned main-pollutant row still owns the clean
    ``sensor.<device>_main_pollutant`` id, so the new scale's sensor is
    forced to ``..._main_pollutant_2``. Reclaim both before adding entities.
    """
    ent_reg = er.async_get(hass)
    for description in SENSORS:
        if description.scale in (None, scale):
            continue
        unique_id = f"{DOMAIN}_{node_id}_{description.key}"
        if entity_id := ent_reg.async_get_entity_id("sensor", DOMAIN, unique_id):
            ent_reg.async_remove(entity_id)


class AirVisualOutdoorSensor(AirVisualOutdoorEntity, SensorEntity):
    """One value from the node's ``current`` block."""

    entity_description: AirVisualOutdoorSensorDescription

    def __init__(
        self,
        coordinator: AirVisualOutdoorCoordinator,
        description: AirVisualOutdoorSensorDescription,
    ) -> None:
        """Bind the description and build the registry unique_id."""
        super().__init__(coordinator)
        self.entity_description = description
        self._staleness_exempt = description.staleness_exempt
        node_id = coordinator.client.node_id
        self._attr_unique_id = f"{DOMAIN}_{node_id}_{description.key}"

    @property
    def native_value(self) -> StateType | datetime:
        """The described reading, or None while its module is absent."""
        return self.entity_description.value_fn(self.coordinator.data)
