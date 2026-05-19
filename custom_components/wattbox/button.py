"""Reset buttons — one per outlet plus a device-level Reset All."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import WattboxConfigEntry
from .entity import WattboxEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WattboxConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[ButtonEntity] = [
        WattboxOutletResetButton(coordinator, outlet_index=i)
        for i in range(1, coordinator.data.info.outlet_count + 1)
    ]
    entities.append(WattboxResetAllButton(coordinator))
    async_add_entities(entities)


class WattboxOutletResetButton(WattboxEntity, ButtonEntity):
    """Power-cycle a single outlet."""

    _attr_device_class = ButtonDeviceClass.RESTART

    def __init__(self, coordinator: Any, outlet_index: int) -> None:
        super().__init__(coordinator)
        self._outlet_index = outlet_index
        self._attr_unique_id = self._unique_id_for(f"reset_outlet_{outlet_index}")
        self._attr_translation_key = "reset_outlet"

    @property
    def name(self) -> str:
        for state in self.coordinator.data.outlets:
            if state.index == self._outlet_index:
                return f"Reset {state.name}"
        return f"Reset Outlet {self._outlet_index}"

    async def async_press(self) -> None:
        await self.coordinator.client.reset_outlet(self._outlet_index)
        await self.coordinator.async_request_refresh()


class WattboxResetAllButton(WattboxEntity, ButtonEntity):
    """Power-cycle every outlet on the device (``!OutletSet=0,RESET``)."""

    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_translation_key = "reset_all_outlets"

    def __init__(self, coordinator: Any) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = self._unique_id_for("reset_all_outlets")
        self._attr_name = "Reset all outlets"

    async def async_press(self) -> None:
        await self.coordinator.client.reset_outlet(0)
        await self.coordinator.async_request_refresh()
