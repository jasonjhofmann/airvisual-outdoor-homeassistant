"""Shared entity base: device registration + staleness-aware availability."""

from __future__ import annotations

from homeassistant.helpers.device_registry import (
    CONNECTION_NETWORK_MAC,
    DeviceInfo,
    format_mac,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_MAC, DOMAIN
from .coordinator import AirVisualOutdoorCoordinator, sample_is_stale


class AirVisualOutdoorEntity(CoordinatorEntity[AirVisualOutdoorCoordinator]):
    """Base entity bound to the node's device entry."""

    _attr_has_entity_name = True
    #: Subclasses set this from their entity description. A staleness-exempt
    #: entity reports the age of a dead station instead of hiding it.
    _staleness_exempt: bool = False

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

        Staleness-exempt entities are the exception: the "last updated"
        timestamp is the one reading whose whole job is to make staleness
        visible, and blanking it exactly when the station dies leaves the
        user with no way to see HOW stale the data is (or to key an
        "offline for N minutes" automation off it). It still goes
        unavailable when the poll itself fails.
        """
        if not super().available:
            return False
        if self._staleness_exempt:
            return True
        return not sample_is_stale(self.coordinator.data)
