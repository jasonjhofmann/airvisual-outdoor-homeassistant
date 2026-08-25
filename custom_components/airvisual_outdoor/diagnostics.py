"""Diagnostics support for AirVisual Outdoor.

The node id is the only capability token the keyless API has, so it is
redacted along with the MAC even though neither is a classic secret. Note
that the loggers do NOT redact the node id — it is deliberately logged so a
multi-node setup can tell its coordinators apart, and CONTRIBUTING already
asks bug reporters for a ``curl`` of their own node URL.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import CONF_MAC, CONF_NODE_ID, STALENESS_THRESHOLD
from .coordinator import AirVisualOutdoorConfigEntry

TO_REDACT = {CONF_NODE_ID, CONF_MAC}


def _describe(err: BaseException | None) -> str | None:
    """Render an exception WITH its cause chain.

    The coordinator raises ``UpdateFailed(translation_key="update_failed")``,
    whose ``repr()`` is just ``UpdateFailed('update_failed')`` — the actual
    API error lives on ``__cause__``. Reporting only the outer exception
    puts a useless string in every bug report.
    """
    if err is None:
        return None
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = err
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts.append(f"{type(current).__name__}: {current}")
        current = current.__cause__
    return " <- ".join(parts)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: AirVisualOutdoorConfigEntry
) -> dict[str, Any]:
    """Return diagnostics: redacted entry data + the latest reading.

    The availability verdict is included explicitly. "Entities are
    unavailable" has two very different causes here — a failed poll versus a
    successful poll of a sample the cloud has been serving since the station
    died — and without ``last_exception`` and ``sample_age_seconds`` a
    downloaded diagnostic could not tell them apart.
    """
    coordinator = entry.runtime_data
    reading = coordinator.data
    age: float | None = None
    if reading is not None and reading.ts is not None:
        age = (dt_util.utcnow() - reading.ts).total_seconds()
    return {
        "entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "entry_version": f"{entry.version}.{entry.minor_version}",
        "last_update_success": coordinator.last_update_success,
        "last_exception": _describe(coordinator.last_exception),
        "update_interval_seconds": (
            coordinator.update_interval.total_seconds()
            if coordinator.update_interval is not None
            else None
        ),
        "sample_age_seconds": age,
        "staleness_threshold_seconds": STALENESS_THRESHOLD.total_seconds(),
        "sample_is_stale": (
            age is not None and age > STALENESS_THRESHOLD.total_seconds()
        ),
        "rate_limit_remaining": (
            reading.rate_limit_remaining if reading is not None else None
        ),
        "reading": asdict(reading) if reading is not None else None,
    }
