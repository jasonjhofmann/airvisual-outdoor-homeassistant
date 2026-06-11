"""Config + reconfigure flows for AirVisual Outdoor.

User pastes the unit's 24-hex node id (from the IQAir dashboard / station
page); we validate with one live keyless GET. One config entry per node —
the node id is the unique_id, so adding the same unit twice is rejected.

The AQI scale (US default / CN) is chosen here and decides which single
AQI + main-pollutant entity pair gets created — never both scales.
The optional MAC enables device-registry merging with the unit's
UniFi/DHCP client entry (the API payload exposes no MAC itself).

There is no OptionsFlow — HA Core convention says the integration owns its
poll cadence, so :data:`~.const.DEFAULT_SCAN_INTERVAL` is not user-tunable.
No reauth flow either: the API is keyless (quality rule exempt).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_NAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import (
    AirVisualOutdoorClient,
    DeviceNotFoundError,
    NodeReading,
    ParseError,
    RateLimitError,
    TransportError,
)
from .const import (
    CONF_AQI_SCALE,
    CONF_MAC,
    CONF_NODE_ID,
    DEFAULT_NAME,
    DOMAIN,
    SCALE_CN,
    SCALE_US,
)

_LOGGER = logging.getLogger(__name__)

_NODE_ID_RE = re.compile(r"^[0-9a-f]{24}$")
_MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}([:-][0-9A-Fa-f]{2}){5}$")

_AQI_SCALE_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[SCALE_US, SCALE_CN],
        mode=SelectSelectorMode.DROPDOWN,
        translation_key="aqi_scale",
    )
)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NODE_ID): str,
        vol.Optional(CONF_NAME): str,
        vol.Required(CONF_AQI_SCALE, default=SCALE_US): _AQI_SCALE_SELECTOR,
        vol.Optional(CONF_MAC): str,
    }
)

STEP_RECONFIGURE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_AQI_SCALE): _AQI_SCALE_SELECTOR,
        vol.Optional(CONF_MAC): str,
    }
)


class AirVisualOutdoorConfigFlow(ConfigFlow, domain=DOMAIN):
    """Initial setup + reconfigure."""

    VERSION = 1
    MINOR_VERSION = 1

    async def async_step_user(
        self,
        user_input: Mapping[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Prompt for node id, name, AQI scale, and optional MAC."""
        errors: dict[str, str] = {}

        if user_input is not None:
            node_id: str = user_input[CONF_NODE_ID].strip().lower()
            mac_raw: str = user_input.get(CONF_MAC, "").strip()

            if not _NODE_ID_RE.match(node_id):
                errors[CONF_NODE_ID] = "invalid_node_id"
            if mac_raw and not _MAC_RE.match(mac_raw):
                errors[CONF_MAC] = "invalid_mac"

            if not errors:
                await self.async_set_unique_id(node_id)
                self._abort_if_unique_id_configured()
                try:
                    reading = await self._validate(node_id)
                except DeviceNotFoundError:
                    errors[CONF_NODE_ID] = "device_not_found"
                except (TransportError, RateLimitError, ParseError) as err:
                    _LOGGER.warning("Node API validation failed: %s", err)
                    errors["base"] = "cannot_connect"
                else:
                    name: str = (
                        user_input.get(CONF_NAME, "").strip()
                        or reading.name
                        or DEFAULT_NAME
                    )
                    data: dict[str, Any] = {
                        CONF_NODE_ID: node_id,
                        CONF_AQI_SCALE: user_input[CONF_AQI_SCALE],
                    }
                    if mac_raw:
                        data[CONF_MAC] = format_mac(mac_raw)
                    return self.async_create_entry(title=name, data=data)

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reconfigure(
        self,
        user_input: Mapping[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """User-initiated AQI-scale / MAC update (the node id stays fixed).

        A different node id is a different physical device with its own
        history — that's a new entry, not a reconfiguration.
        """
        errors: dict[str, str] = {}
        existing = self._get_reconfigure_entry()

        if user_input is not None:
            mac_raw: str = user_input.get(CONF_MAC, "").strip()
            if mac_raw and not _MAC_RE.match(mac_raw):
                errors[CONF_MAC] = "invalid_mac"
            else:
                data: dict[str, Any] = {
                    CONF_NODE_ID: existing.data[CONF_NODE_ID],
                    CONF_AQI_SCALE: user_input[CONF_AQI_SCALE],
                }
                if mac_raw:
                    data[CONF_MAC] = format_mac(mac_raw)
                return self.async_update_reload_and_abort(existing, data=data)

        suggested = {
            CONF_AQI_SCALE: existing.data[CONF_AQI_SCALE],
            CONF_MAC: existing.data.get(CONF_MAC, ""),
        }
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                STEP_RECONFIGURE_SCHEMA, suggested
            ),
            errors=errors,
            description_placeholders={"node_id": existing.data[CONF_NODE_ID]},
        )

    async def _validate(self, node_id: str) -> NodeReading:
        """One live fetch to confirm the node id resolves."""
        session = async_get_clientsession(self.hass)
        client = AirVisualOutdoorClient(session=session, node_id=node_id)
        return await client.async_get_reading()
