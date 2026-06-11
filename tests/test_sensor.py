"""Tests for the sensor platform — full launch set, scale gating, staleness."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from unittest.mock import MagicMock

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.airvisual_outdoor.api import NodeReading
from custom_components.airvisual_outdoor.const import (
    CONF_AQI_SCALE,
    CONF_NODE_ID,
    DOMAIN,
    SCALE_CN,
    STALENESS_THRESHOLD,
)

from .conftest import TEST_MAC, TEST_NODE_ID, setup_integration

CO2_UNIQUE_ID = f"{DOMAIN}_{TEST_NODE_ID}_co2"


def _co2_entity_id(hass: HomeAssistant) -> str:
    ent_reg = er.async_get(hass)
    entity_id = ent_reg.async_get_entity_id("sensor", DOMAIN, CO2_UNIQUE_ID)
    assert entity_id, "CO2 sensor not registered"
    return entity_id


async def test_co2_sensor_reports(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The CO₂ entity exists, slugs from the device name, and reads the API."""
    entity_id = _co2_entity_id(hass)
    assert entity_id == "sensor.backyard_co2"  # slug derives from device name
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "459"
    assert state.attributes["unit_of_measurement"] == "ppm"


# Expected (key, entity_id, state) for the US-scale launch set, from the
# captured fixture payload.
US_SET = [
    ("aqius", "sensor.backyard_aqi_us", "6"),
    ("pm25", "sensor.backyard_pm2_5", "1.0"),
    ("pm10", "sensor.backyard_pm10", "2.0"),
    ("pm1", "sensor.backyard_pm1", "1.0"),
    ("co2", "sensor.backyard_co2", "459"),
    ("temperature", "sensor.backyard_temperature", "33.2"),
    ("humidity", "sensor.backyard_humidity", "24.0"),
    # Native Pa; HA's unit system auto-displays atmospheric pressure in hPa.
    ("pressure", "sensor.backyard_pressure", "923.05"),
    ("mainus", "sensor.backyard_main_pollutant", "pm25"),
]


async def test_full_us_scale_set(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """All 10 launch entities exist with fixture-derived states (US scale)."""
    ent_reg = er.async_get(hass)
    for key, expected_entity_id, expected_state in US_SET:
        unique_id = f"{DOMAIN}_{TEST_NODE_ID}_{key}"
        entity_id = ent_reg.async_get_entity_id("sensor", DOMAIN, unique_id)
        assert entity_id == expected_entity_id, f"{key}: {entity_id}"
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == expected_state, f"{key}: {state.state}"

    # Timestamp sensor exists and parses (its value is freshened in conftest).
    ts_entity = ent_reg.async_get_entity_id(
        "sensor", DOMAIN, f"{DOMAIN}_{TEST_NODE_ID}_last_updated"
    )
    assert ts_entity == "sensor.backyard_last_updated"
    ts_state = hass.states.get(ts_entity)
    assert ts_state is not None
    assert ts_state.state not in ("unknown", "unavailable")

    # CN-scale entities must NOT exist on a US-scale entry.
    for cn_key in ("aqicn", "maincn"):
        assert (
            ent_reg.async_get_entity_id(
                "sensor", DOMAIN, f"{DOMAIN}_{TEST_NODE_ID}_{cn_key}"
            )
            is None
        )


async def test_cn_scale_creates_cn_entities(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """A CN-scale entry creates AQI (CN) + main pollutant from maincn."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Backyard",
        data={CONF_NODE_ID: TEST_NODE_ID, CONF_AQI_SCALE: SCALE_CN},
        unique_id=TEST_NODE_ID,
    )
    await setup_integration(hass, entry, mock_client)
    ent_reg = er.async_get(hass)

    aqi_cn = ent_reg.async_get_entity_id(
        "sensor", DOMAIN, f"{DOMAIN}_{TEST_NODE_ID}_aqicn"
    )
    assert aqi_cn == "sensor.backyard_aqi_cn"
    state = hass.states.get(aqi_cn)
    assert state is not None
    assert state.state == "2"  # fixture aqicn

    main_cn = ent_reg.async_get_entity_id(
        "sensor", DOMAIN, f"{DOMAIN}_{TEST_NODE_ID}_maincn"
    )
    assert main_cn is not None
    main_state = hass.states.get(main_cn)
    assert main_state is not None
    assert main_state.state == "pm10"  # fixture maincn ≠ mainus

    assert (
        ent_reg.async_get_entity_id("sensor", DOMAIN, f"{DOMAIN}_{TEST_NODE_ID}_aqius")
        is None
    )


async def test_device_entry_carries_identity(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Device entry: IQAir identity, node-id serial, config-supplied MAC."""
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_device(identifiers={(DOMAIN, TEST_NODE_ID)})
    assert device is not None
    assert device.manufacturer == "IQAir"
    assert device.model == "AirVisual Outdoor"
    assert device.serial_number == TEST_NODE_ID
    assert (dr.CONNECTION_NETWORK_MAC, TEST_MAC) in device.connections


async def test_stale_sample_goes_unavailable(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    sample_reading: NodeReading,
) -> None:
    """A 200-but-stale ``current`` block must not report as healthy."""
    stale = replace(
        sample_reading,
        ts=dt_util.utcnow() - STALENESS_THRESHOLD - timedelta(minutes=1),
    )
    from .conftest import build_mock_client

    client: MagicMock = build_mock_client(stale)
    await setup_integration(hass, mock_config_entry, client)
    state = hass.states.get(_co2_entity_id(hass))
    assert state is not None
    assert state.state == STATE_UNAVAILABLE


async def test_missing_sample_timestamp_goes_unavailable(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    sample_reading: NodeReading,
) -> None:
    """No ``ts`` at all = no proof of freshness = unavailable."""
    from .conftest import build_mock_client

    client = build_mock_client(replace(sample_reading, ts=None))
    await setup_integration(hass, mock_config_entry, client)
    state = hass.states.get(_co2_entity_id(hass))
    assert state is not None
    assert state.state == STATE_UNAVAILABLE
