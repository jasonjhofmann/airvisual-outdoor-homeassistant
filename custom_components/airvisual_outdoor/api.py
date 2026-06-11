"""Typed client for IQAir's public per-device node API.

One keyless GET: ``https://device.iqair.com/v2/<node_id>``. No pyairvisual —
the lib is stale (2023-12) and drags numpy + pysmb for a Samba path the
Outdoor hardware doesn't have. See ``docs/architecture.md`` for the full API
ground truth (payload shape, rate limiting, error envelopes, quirks).

This module is deliberately HA-import-free so it stays extractable into a
standalone package if the integration is ever upstreamed to core.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

API_URL_BASE = "https://device.iqair.com/v2"
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)

_RATE_LIMIT_HEADER = "x-ratelimit-remaining"


class AirVisualOutdoorError(Exception):
    """Base error for the node API client."""


class DeviceNotFoundError(AirVisualOutdoorError):
    """The API answered 404 ``device_not_found`` — the node id is wrong."""


class RateLimitError(AirVisualOutdoorError):
    """The per-node request budget is exhausted (HTTP 429)."""


class TransportError(AirVisualOutdoorError):
    """The API could not be reached or answered with a server error."""


class ParseError(AirVisualOutdoorError):
    """The API answered 200 but the payload wasn't the expected JSON."""


@dataclass(frozen=True, slots=True)
class PollutantReading:
    """One particulate channel: concentration plus both AQI scales."""

    conc: float | None
    aqi_us: int | None
    aqi_cn: int | None


@dataclass(frozen=True, slots=True)
class HourlyReading:
    """One completed hour from the node's ``historical.hourly`` array.

    Note the shape difference from ``current``: hourly ``pm25``/``pm10`` are
    ``{conc, aqius, aqicn}`` dicts but ``pm1`` is a flat number, and there is
    no composite AQI / main-pollutant in hourly entries.
    """

    ts: datetime
    pm25_conc: float | None
    pm10_conc: float | None
    pm1_conc: float | None
    co2: int | None
    temperature: float | None
    humidity: float | None
    pressure: float | None


@dataclass(frozen=True, slots=True)
class NodeReading:
    """A normalised snapshot of the node's ``current`` block.

    Modules are hot-pluggable (2 PM slots + 2 option slots), so every field
    is optional — keys appear and disappear with module presence.
    """

    name: str | None
    ts: datetime | None
    pm25: PollutantReading
    pm10: PollutantReading
    pm1: PollutantReading
    co2: int | None
    temperature: float | None
    humidity: float | None
    pressure: float | None
    aqi_us: int | None
    aqi_cn: int | None
    main_pollutant_us: str | None
    main_pollutant_cn: str | None
    rate_limit_remaining: int | None
    hourly: tuple[HourlyReading, ...]


def _pollutant(block: Any) -> PollutantReading:
    """Normalise one ``{conc, aqius, aqicn}`` block (or its absence)."""
    if not isinstance(block, dict):
        return PollutantReading(conc=None, aqi_us=None, aqi_cn=None)
    return PollutantReading(
        conc=_number(block.get("conc")),
        aqi_us=_int(block.get("aqius")),
        aqi_cn=_int(block.get("aqicn")),
    )


def _number(value: Any) -> float | None:
    """A float, or None for absent/non-numeric values."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _int(value: Any) -> int | None:
    """An int, or None for absent/non-numeric values."""
    number = _number(value)
    return None if number is None else int(number)


def _string(value: Any) -> str | None:
    """A non-empty string, or None."""
    if isinstance(value, str) and value:
        return value
    return None


def _header_int(value: str | None) -> int | None:
    """Parse a numeric HTTP header value (headers are always strings)."""
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _timestamp(value: Any) -> datetime | None:
    """Parse the API's ISO-8601 UTC ``ts``, tolerating absence/garbage."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _hourly(payload: dict[str, Any]) -> tuple[HourlyReading, ...]:
    """Parse ``historical.hourly`` defensively; unusable entries are dropped."""
    historical = payload.get("historical")
    if not isinstance(historical, dict):
        return ()
    entries = historical.get("hourly")
    if not isinstance(entries, list):
        return ()
    readings: list[HourlyReading] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        ts = _timestamp(entry.get("ts"))
        if ts is None:
            continue
        readings.append(
            HourlyReading(
                ts=ts,
                pm25_conc=_pollutant(entry.get("pm25")).conc,
                pm10_conc=_pollutant(entry.get("pm10")).conc,
                pm1_conc=_number(entry.get("pm1")),
                co2=_int(entry.get("co2")),
                temperature=_number(entry.get("tp")),
                humidity=_number(entry.get("hm")),
                pressure=_number(entry.get("pr")),
            )
        )
    return tuple(sorted(readings, key=lambda r: r.ts))


def normalise(payload: dict[str, Any], rate_limit_remaining: int | None) -> NodeReading:
    """Build a :class:`NodeReading` from a raw node payload, defensively."""
    current = payload.get("current")
    if not isinstance(current, dict):
        raise ParseError("payload has no 'current' block")
    return NodeReading(
        name=_string(payload.get("name")),
        ts=_timestamp(current.get("ts")),
        pm25=_pollutant(current.get("pm25")),
        pm10=_pollutant(current.get("pm10")),
        pm1=_pollutant(current.get("pm1")),
        co2=_int(current.get("co2")),
        temperature=_number(current.get("tp")),
        humidity=_number(current.get("hm")),
        pressure=_number(current.get("pr")),
        aqi_us=_int(current.get("aqius")),
        aqi_cn=_int(current.get("aqicn")),
        main_pollutant_us=_string(current.get("mainus")),
        main_pollutant_cn=_string(current.get("maincn")),
        rate_limit_remaining=rate_limit_remaining,
        hourly=_hourly(payload),
    )


class AirVisualOutdoorClient:
    """Minimal async client bound to one node id."""

    def __init__(self, session: aiohttp.ClientSession, node_id: str) -> None:
        """Store the shared session and the node this client polls."""
        self._session = session
        self.node_id = node_id

    async def async_get_reading(self) -> NodeReading:
        """Fetch and normalise the node's current readings.

        Raises the module's typed errors; never returns partial garbage —
        a payload without a ``current`` block is a :class:`ParseError`.
        """
        url = f"{API_URL_BASE}/{self.node_id}"
        try:
            async with self._session.get(url, timeout=REQUEST_TIMEOUT) as resp:
                remaining = _header_int(resp.headers.get(_RATE_LIMIT_HEADER))
                body = await resp.text()
                if resp.status == 404:
                    raise DeviceNotFoundError(
                        f"node {self.node_id} not found ({_error_code(body)})"
                    )
                if resp.status == 429:
                    raise RateLimitError(
                        f"rate limit exhausted for node {self.node_id}"
                    )
                if resp.status >= 400:
                    raise TransportError(f"HTTP {resp.status} from node API")
        except TimeoutError as err:
            raise TransportError(f"timeout talking to node API: {err}") from err
        except aiohttp.ClientError as err:
            raise TransportError(f"cannot reach node API: {err}") from err

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as err:
            raise ParseError(f"node API returned non-JSON: {err}") from err
        if not isinstance(payload, dict):
            raise ParseError("node API returned non-object JSON")

        if remaining is not None and remaining <= 2:
            _LOGGER.warning(
                "Node %s rate-limit budget nearly exhausted (%s requests left)",
                self.node_id,
                remaining,
            )
        return normalise(payload, remaining)


def _error_code(body: str) -> str:
    """Extract the API's error ``code`` from a 4xx body, best-effort."""
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return "unparseable error body"
    if isinstance(parsed, dict) and isinstance(parsed.get("code"), str):
        return str(parsed["code"])
    return "no error code"
