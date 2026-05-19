"""Binary sensors: UPS connection / power lost / alarm / API lockout."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import WattboxConfigEntry
from .coordinator import WattboxCoordinator
from .entity import WattboxEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WattboxConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    caps = coordinator.data.capabilities

    entities: list[BinarySensorEntity] = [WattboxApiLockoutBinarySensor(coordinator)]
    if caps.ups:
        entities.extend(
            [
                WattboxUPSConnectedBinarySensor(coordinator),
                WattboxUPSPowerLostBinarySensor(coordinator),
                WattboxUPSAudibleAlarmBinarySensor(coordinator),
            ]
        )
    async_add_entities(entities)


class WattboxApiLockoutBinarySensor(WattboxEntity, BinarySensorEntity):
    """ON whenever the client's lockout cooldown is active.

    Surfaced explicitly so the user gets a notification instead of seeing
    silent entity-unavailable while the integration backs off. Reads from
    the in-process budget on :class:`WattboxClient` rather than from
    snapshot data, so it stays correct even when polling has stalled.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_name = "API locked out"

    def __init__(self, coordinator: WattboxCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = self._unique_id_for("api_locked")

    @property
    def is_on(self) -> bool:
        return self.coordinator.client.is_locked_out


class WattboxUPSConnectedBinarySensor(WattboxEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_name = "UPS connected"

    def __init__(self, coordinator: WattboxCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = self._unique_id_for("ups_connected")

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.ups_connected


class WattboxUPSPowerLostBinarySensor(WattboxEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.POWER
    _attr_name = "Mains power"

    def __init__(self, coordinator: WattboxCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = self._unique_id_for("mains_power")

    @property
    def is_on(self) -> bool | None:
        ups = self.coordinator.data.ups
        if ups is None:
            return None
        # ON = mains power is present (i.e. NOT lost). The HA `power`
        # device class follows that convention.
        return not ups.power_lost


class WattboxUPSAudibleAlarmBinarySensor(WattboxEntity, BinarySensorEntity):
    """Whether the UPS's audible alarm is armed (enabled and not muted).

    Reports a **configuration** state — "would the UPS beep on an event?"
    — not a live alarm condition. The WattBox firmware doesn't expose a
    "currently alarming" field; that's inferred from ``mains_power`` and
    battery state.

    No ``device_class`` set deliberately. ``SAFETY`` would render this as
    "Safe / Unsafe" which is misleading: the alarm being armed is not
    itself a danger.
    """

    _attr_name = "Audible alarm armed"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:bell-ring"

    def __init__(self, coordinator: WattboxCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = self._unique_id_for("ups_alarm")

    @property
    def is_on(self) -> bool | None:
        ups = self.coordinator.data.ups
        if ups is None:
            return None
        return ups.alarm_enabled and not ups.alarm_muted
