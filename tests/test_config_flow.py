"""Tests for the config + reconfigure flows."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.airvisual_outdoor.api import (
    DeviceNotFoundError,
    TransportError,
)
from custom_components.airvisual_outdoor.const import (
    CONF_AQI_SCALE,
    CONF_MAC,
    CONF_NODE_ID,
    DOMAIN,
    SCALE_CN,
    SCALE_US,
)

from .conftest import TEST_MAC, TEST_NODE_ID, patch_client


async def _start_user_flow(hass: HomeAssistant) -> str:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {}
    return str(result["flow_id"])


async def test_user_flow_happy_path(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """Node id + explicit name + scale + MAC create a normalised entry."""
    flow_id = await _start_user_flow(hass)
    with patch_client(mock_client):
        result = await hass.config_entries.flow.async_configure(
            flow_id,
            {
                CONF_NODE_ID: TEST_NODE_ID.upper(),  # case-insensitive input
                CONF_NAME: "Backyard",
                CONF_AQI_SCALE: SCALE_US,
                CONF_MAC: "AA-BB-CC-DD-EE-FF",  # dashes accepted, normalised
            },
        )
        await hass.async_block_till_done()  # entry setup inside the patch
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Backyard"
    assert result["data"] == {
        CONF_NODE_ID: TEST_NODE_ID,
        CONF_AQI_SCALE: SCALE_US,
        CONF_MAC: TEST_MAC,
    }
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.unique_id == TEST_NODE_ID


async def test_user_flow_name_defaults_from_payload(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """A blank name falls back to the payload's reported name; MAC optional."""
    flow_id = await _start_user_flow(hass)
    with patch_client(mock_client):
        result = await hass.config_entries.flow.async_configure(
            flow_id,
            {CONF_NODE_ID: TEST_NODE_ID, CONF_AQI_SCALE: SCALE_US},
        )
        await hass.async_block_till_done()  # entry setup inside the patch
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "AirVisual Outdoor"  # from the fixture payload
    assert CONF_MAC not in result["data"]


async def test_user_flow_invalid_node_id(hass: HomeAssistant) -> None:
    """A malformed node id errors on the field without hitting the API."""
    flow_id = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id,
        {CONF_NODE_ID: "not-a-node-id", CONF_AQI_SCALE: SCALE_US},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_NODE_ID: "invalid_node_id"}


async def test_user_flow_invalid_mac(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """A malformed MAC errors on the field."""
    flow_id = await _start_user_flow(hass)
    with patch_client(mock_client):
        result = await hass.config_entries.flow.async_configure(
            flow_id,
            {
                CONF_NODE_ID: TEST_NODE_ID,
                CONF_AQI_SCALE: SCALE_US,
                CONF_MAC: "not-a-mac",
            },
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_MAC: "invalid_mac"}


async def test_user_flow_device_not_found(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """The API's clean 404 surfaces as a node-id field error."""
    mock_client.async_get_reading.side_effect = DeviceNotFoundError("nope")
    flow_id = await _start_user_flow(hass)
    with patch_client(mock_client):
        result = await hass.config_entries.flow.async_configure(
            flow_id,
            {CONF_NODE_ID: TEST_NODE_ID, CONF_AQI_SCALE: SCALE_US},
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_NODE_ID: "device_not_found"}


async def test_user_flow_cannot_connect(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """Transport failures surface as a base error and the form is retryable."""
    mock_client.async_get_reading.side_effect = TransportError("down")
    flow_id = await _start_user_flow(hass)
    with patch_client(mock_client):
        result = await hass.config_entries.flow.async_configure(
            flow_id,
            {CONF_NODE_ID: TEST_NODE_ID, CONF_AQI_SCALE: SCALE_US},
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_duplicate_node_aborts(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
) -> None:
    """The same node id can only be configured once."""
    mock_config_entry.add_to_hass(hass)
    flow_id = await _start_user_flow(hass)
    with patch_client(mock_client):
        result = await hass.config_entries.flow.async_configure(
            flow_id,
            {CONF_NODE_ID: TEST_NODE_ID, CONF_AQI_SCALE: SCALE_US},
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reconfigure_flow_switches_scale_and_clears_mac(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
) -> None:
    """Reconfigure swaps the AQI scale; an emptied MAC field drops the MAC."""
    entry = init_integration
    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    with patch_client(mock_client):  # reload after abort runs setup again
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_AQI_SCALE: SCALE_CN},
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_AQI_SCALE] == SCALE_CN
    assert entry.data[CONF_NODE_ID] == TEST_NODE_ID  # identity untouched
    assert CONF_MAC not in entry.data


async def test_reconfigure_flow_invalid_mac(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Reconfigure validates the MAC like the user step does."""
    entry = init_integration
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_AQI_SCALE: SCALE_US, CONF_MAC: "garbage"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_MAC: "invalid_mac"}


async def test_reconfigure_flow_sets_new_mac(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
) -> None:
    """Reconfigure can set/replace the MAC (normalised on the way in)."""
    entry = init_integration
    result = await entry.start_reconfigure_flow(hass)
    with patch_client(mock_client):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_AQI_SCALE: SCALE_US, CONF_MAC: "11-22-33-44-55-66"},
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert entry.data[CONF_MAC] == "11:22:33:44:55:66"
