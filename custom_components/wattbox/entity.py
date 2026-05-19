"""Base entity for WattBox platforms.

Provides the common :class:`~homeassistant.helpers.entity.DeviceInfo` block
and the unique-id prefix derived from the device's service tag, so all
entities for one physical WattBox group under one device in the HA UI.
"""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import WattboxCoordinator


class WattboxEntity(CoordinatorEntity[WattboxCoordinator]):
    """Base for every WattBox entity (switch/sensor/etc).

    Subclasses must set ``_attr_unique_id`` (suffix is up to them; this
    base supplies :meth:`_unique_id_for` as a convenience).
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: WattboxCoordinator) -> None:
        super().__init__(coordinator)
        info = coordinator.data.info
        self._service_tag = info.service_tag
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, info.service_tag)},
            manufacturer=MANUFACTURER,
            model=info.model,
            name=info.hostname or info.model,
            sw_version=info.firmware,
            serial_number=info.service_tag,
        )

    def _unique_id_for(self, suffix: str) -> str:
        """Compose a stable unique_id from the device service tag and a suffix."""
        return f"{self._service_tag}_{suffix}"
