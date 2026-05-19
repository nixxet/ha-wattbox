"""WattBox outlets as HA switches.

One :class:`WattboxOutletSwitch` per outlet, created at setup time from
the device's outlet count. State refreshes from the coordinator;
on/off calls go directly to the client and the local state is
optimistically refreshed afterwards.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
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
    async_add_entities(
        WattboxOutletSwitch(coordinator, outlet_index=i)
        for i in range(1, coordinator.data.info.outlet_count + 1)
    )


class WattboxOutletSwitch(WattboxEntity, SwitchEntity):
    """A single outlet on the WattBox, exposed as a switch."""

    _attr_device_class = SwitchDeviceClass.OUTLET

    def __init__(self, coordinator: Any, outlet_index: int) -> None:
        super().__init__(coordinator)
        self._outlet_index = outlet_index
        self._attr_unique_id = self._unique_id_for(f"outlet_{outlet_index}")

    @property
    def name(self) -> str:
        for state in self.coordinator.data.outlets:
            if state.index == self._outlet_index:
                return state.name
        return f"Outlet {self._outlet_index}"

    @property
    def is_on(self) -> bool | None:
        for state in self.coordinator.data.outlets:
            if state.index == self._outlet_index:
                return state.is_on
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.client.set_outlet(self._outlet_index, on=True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.client.set_outlet(self._outlet_index, on=False)
        await self.coordinator.async_request_refresh()
