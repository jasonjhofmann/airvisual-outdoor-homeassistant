"""Shared entity base: device registration + staleness-aware availability."""

from __future__ import annotations

from homeassistant.helpers.device_registry import (
    CONNECTION_NETWORK_MAC,
    DeviceInfo,
    format_mac,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import CONF_MAC, DOMAIN, STALENESS_THRESHOLD
from .coordinator import AirVisualOutdoorCoordinator


class AirVisualOutdoorEntity(CoordinatorEntity[AirVisualOutdoorCoordinator]):
    """Base entity bound to the node's device entry."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: AirVisualOutdoorCoordinator) -> None:
        """Register against the per-node device."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        node_id = coordinator.client.node_id
        device_info = DeviceInfo(
            identifiers={(DOMAIN, node_id)},
            name=entry.title,
            manufacturer="IQAir",
            model="AirVisual Outdoor",
            serial_number=node_id,
        )
        # Optional MAC (config-supplied — the API payload carries none):
        # lets HA merge this device with its UniFi/DHCP client entry.
        if mac := entry.data.get(CONF_MAC):
            device_info["connections"] = {(CONNECTION_NETWORK_MAC, format_mac(mac))}
        self._attr_device_info = device_info

    @property
    def available(self) -> bool:
        """Unavailable when polling fails OR the cloud serves a stale sample.

        The API keeps answering 200 with the last-known ``current`` block
        after a device stops reporting, so coordinator success alone would
        report a dead station as healthy.
        """
        if not super().available:
            return False
        ts = self.coordinator.data.ts
        if ts is None:
            return False
        return dt_util.utcnow() - ts <= STALENESS_THRESHOLD
