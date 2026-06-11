"""Tests for the node API client and its defensive parser."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import aiohttp
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.airvisual_outdoor.api import (
    API_URL_BASE,
    AirVisualOutdoorClient,
    DeviceNotFoundError,
    ParseError,
    RateLimitError,
    TransportError,
    normalise,
)

from .conftest import TEST_NODE_ID

NODE_URL = f"{API_URL_BASE}/{TEST_NODE_ID}"


def _client(hass: HomeAssistant) -> AirVisualOutdoorClient:
    return AirVisualOutdoorClient(
        session=async_get_clientsession(hass), node_id=TEST_NODE_ID
    )


# --- normalise() ------------------------------------------------------------


def test_normalise_full_payload(node_response_dict: dict[str, Any]) -> None:
    """The captured fixture parses completely."""
    reading = normalise(node_response_dict, rate_limit_remaining=29)
    assert reading.name == "AirVisual Outdoor"
    assert reading.co2 == 459
    assert reading.pm25.conc == 1
    assert reading.pm25.aqi_us == 6
    assert reading.pm10.aqi_cn is not None
    assert reading.temperature == pytest.approx(33.2)
    assert reading.humidity == pytest.approx(24)
    assert reading.pressure == pytest.approx(92305)
    assert reading.aqi_us == 6
    assert reading.main_pollutant_us == "pm25"
    assert reading.ts == datetime(2026, 6, 10, 3, 52, 7, tzinfo=UTC)
    assert reading.rate_limit_remaining == 29


def test_normalise_missing_modules(node_response_dict: dict[str, Any]) -> None:
    """Hot-pluggable module keys parse to None when absent."""
    current = node_response_dict["current"]
    del current["co2"]
    del current["pm10"]
    del current["ts"]
    reading = normalise(node_response_dict, rate_limit_remaining=None)
    assert reading.co2 is None
    assert reading.pm10.conc is None
    assert reading.pm10.aqi_us is None
    assert reading.ts is None
    assert reading.pm25.conc == 1  # untouched channels still parse


def test_normalise_garbage_values(node_response_dict: dict[str, Any]) -> None:
    """Non-numeric garbage degrades to None, never raises."""
    current = node_response_dict["current"]
    current["co2"] = "soon"
    current["tp"] = None
    current["pm25"] = "not-a-dict"
    current["ts"] = "yesterday-ish"
    reading = normalise(node_response_dict, rate_limit_remaining=None)
    assert reading.co2 is None
    assert reading.temperature is None
    assert reading.pm25.conc is None
    assert reading.ts is None


def test_normalise_no_current_block() -> None:
    """A payload without ``current`` is a ParseError, not a partial reading."""
    with pytest.raises(ParseError):
        normalise({"name": "AirVisual Outdoor"}, rate_limit_remaining=None)


def test_normalise_hourly(node_response_dict: dict[str, Any]) -> None:
    """Hourly entries parse, sort ascending, and tolerate the flat pm1."""
    reading = normalise(node_response_dict, rate_limit_remaining=None)
    assert len(reading.hourly) == 2  # fixture is trimmed to 2 entries
    assert reading.hourly[0].ts < reading.hourly[1].ts
    newest = reading.hourly[-1]
    assert newest.pm25_conc is not None
    assert newest.pressure is not None


def test_normalise_hourly_defective_entries(
    node_response_dict: dict[str, Any],
) -> None:
    """Entries without a parseable ts are dropped; absent array → empty."""
    node_response_dict["historical"]["hourly"][0]["ts"] = "garbage"
    reading = normalise(node_response_dict, rate_limit_remaining=None)
    assert len(reading.hourly) == 1

    del node_response_dict["historical"]
    reading = normalise(node_response_dict, rate_limit_remaining=None)
    assert reading.hourly == ()


# --- client HTTP behaviour ---------------------------------------------------


async def test_fetch_happy_path(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    node_response_dict: dict[str, Any],
) -> None:
    """A 200 with the rate-limit header yields a full reading."""
    aioclient_mock.get(
        NODE_URL,
        json=node_response_dict,
        headers={"x-ratelimit-remaining": "23"},
    )
    reading = await _client(hass).async_get_reading()
    assert reading.co2 == 459
    assert reading.rate_limit_remaining == 23


async def test_fetch_device_not_found(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The API's clean 404 envelope maps to DeviceNotFoundError."""
    aioclient_mock.get(
        NODE_URL,
        status=404,
        json={"code": "device_not_found", "message": "Not Found"},
    )
    with pytest.raises(DeviceNotFoundError, match="device_not_found"):
        await _client(hass).async_get_reading()


async def test_fetch_rate_limited(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """HTTP 429 maps to RateLimitError."""
    aioclient_mock.get(NODE_URL, status=429, text="slow down")
    with pytest.raises(RateLimitError):
        await _client(hass).async_get_reading()


async def test_fetch_server_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 5xx maps to TransportError."""
    aioclient_mock.get(NODE_URL, status=502, text="bad gateway")
    with pytest.raises(TransportError, match="HTTP 502"):
        await _client(hass).async_get_reading()


async def test_fetch_network_failure(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A connection-level failure maps to TransportError."""
    aioclient_mock.get(NODE_URL, exc=aiohttp.ClientError("unreachable"))
    with pytest.raises(TransportError):
        await _client(hass).async_get_reading()


async def test_fetch_non_json_body(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 200 with an HTML body maps to ParseError."""
    aioclient_mock.get(NODE_URL, text="<html>challenge wall</html>")
    with pytest.raises(ParseError):
        await _client(hass).async_get_reading()


def test_normalise_non_string_name(node_response_dict: dict[str, Any]) -> None:
    """A non-string or empty name degrades to None."""
    node_response_dict["name"] = 12345
    reading = normalise(node_response_dict, rate_limit_remaining=None)
    assert reading.name is None


def test_normalise_hourly_not_a_list(node_response_dict: dict[str, Any]) -> None:
    """A malformed hourly container yields an empty tuple."""
    node_response_dict["historical"]["hourly"] = "not-a-list"
    reading = normalise(node_response_dict, rate_limit_remaining=None)
    assert reading.hourly == ()


def test_normalise_hourly_non_dict_entry(
    node_response_dict: dict[str, Any],
) -> None:
    """Non-dict hourly entries are dropped."""
    node_response_dict["historical"]["hourly"].insert(0, "garbage")
    reading = normalise(node_response_dict, rate_limit_remaining=None)
    assert len(reading.hourly) == 2


async def test_fetch_timeout(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A timeout maps to TransportError."""
    aioclient_mock.get(NODE_URL, exc=TimeoutError())
    with pytest.raises(TransportError, match="timeout"):
        await _client(hass).async_get_reading()


async def test_fetch_non_object_json(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 200 with a JSON array body maps to ParseError."""
    aioclient_mock.get(NODE_URL, text="[1, 2, 3]")
    with pytest.raises(ParseError, match="non-object"):
        await _client(hass).async_get_reading()


async def test_fetch_garbage_rate_limit_header(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    node_response_dict: dict[str, Any],
) -> None:
    """A non-numeric rate-limit header degrades to None."""
    aioclient_mock.get(
        NODE_URL,
        json=node_response_dict,
        headers={"x-ratelimit-remaining": "soon"},
    )
    reading = await _client(hass).async_get_reading()
    assert reading.rate_limit_remaining is None


async def test_fetch_low_budget_logs_warning(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    node_response_dict: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A nearly-drained budget emits one warning."""
    aioclient_mock.get(
        NODE_URL,
        json=node_response_dict,
        headers={"x-ratelimit-remaining": "1"},
    )
    reading = await _client(hass).async_get_reading()
    assert reading.rate_limit_remaining == 1
    assert "nearly exhausted" in caplog.text


async def test_fetch_404_with_unparseable_body(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 404 with a non-JSON body still raises DeviceNotFoundError."""
    aioclient_mock.get(NODE_URL, status=404, text="<html>nope</html>")
    with pytest.raises(DeviceNotFoundError, match="unparseable"):
        await _client(hass).async_get_reading()


async def test_fetch_404_without_error_code(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 404 whose JSON body lacks a code field reports that."""
    aioclient_mock.get(NODE_URL, status=404, json={"message": "Not Found"})
    with pytest.raises(DeviceNotFoundError, match="no error code"):
        await _client(hass).async_get_reading()
