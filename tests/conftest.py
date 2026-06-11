"""Shared pytest fixtures for the AirVisual Outdoor test suite."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.airvisual_outdoor.api import NodeReading, normalise
from custom_components.airvisual_outdoor.const import (
    CONF_AQI_SCALE,
    CONF_MAC,
    CONF_NODE_ID,
    DOMAIN,
    SCALE_US,
)

FIXTURES = Path(__file__).parent / "fixtures"

TEST_NODE_ID = "0123456789abcdef01234567"
TEST_MAC = "aa:bb:cc:dd:ee:ff"
TEST_TITLE = "Backyard"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Make HA load the integration from ``custom_components/`` in every test."""


@pytest.fixture
def node_response_dict() -> dict[str, Any]:
    """The captured live node payload (trimmed historical arrays)."""
    return json.loads((FIXTURES / "node_response.json").read_text())


@pytest.fixture
def sample_reading(node_response_dict: dict[str, Any]) -> NodeReading:
    """A normalised reading built from the fixture, freshened.

    The fixture's ``ts`` is a point-in-time capture; entities treat samples
    older than the staleness threshold as unavailable, so tests get a
    just-now timestamp by default.
    """
    reading = normalise(node_response_dict, rate_limit_remaining=29)
    return _fresh(reading)


def _fresh(reading: NodeReading) -> NodeReading:
    """The same reading stamped 'just now'."""
    from dataclasses import replace

    return replace(reading, ts=dt_util.utcnow())


def build_mock_client(reading: NodeReading) -> MagicMock:
    """A MagicMock standing in for ``AirVisualOutdoorClient``."""
    client = MagicMock()
    client.node_id = TEST_NODE_ID
    client.async_get_reading = AsyncMock(return_value=reading)
    return client


@pytest.fixture
def mock_client(sample_reading: NodeReading) -> MagicMock:
    """A healthy default client returning the sample reading."""
    return build_mock_client(sample_reading)


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """A config entry for the test node, US scale, MAC supplied."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=TEST_TITLE,
        data={
            CONF_NODE_ID: TEST_NODE_ID,
            CONF_AQI_SCALE: SCALE_US,
            CONF_MAC: TEST_MAC,
        },
        unique_id=TEST_NODE_ID,
    )


@contextmanager
def patch_client(client: MagicMock) -> Iterator[MagicMock]:
    """Patch the client class in both the entry-setup and config-flow paths."""
    with (
        patch(
            "custom_components.airvisual_outdoor.AirVisualOutdoorClient",
            return_value=client,
        ),
        patch(
            "custom_components.airvisual_outdoor.config_flow.AirVisualOutdoorClient",
            return_value=client,
        ),
    ):
        yield client


async def setup_integration(
    hass: HomeAssistant, entry: MockConfigEntry, client: MagicMock
) -> None:
    """Add the entry and run setup with the client patched in."""
    entry.add_to_hass(hass)
    with patch_client(client):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


@pytest.fixture
async def init_integration(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
) -> MockConfigEntry:
    """Set up the integration with the healthy default client."""
    await setup_integration(hass, mock_config_entry, mock_client)
    return mock_config_entry
