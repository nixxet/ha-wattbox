"""Sensors: whole-device power, per-outlet power, UPS metrics.

Every sensor is created conditionally based on the capability map the
client probed at identify time, so a WB-250 won't get power sensors and
a WB-800 without a UPS won't get UPS sensors.
"""

from __future__ import annotations

from typing import ClassVar

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfPower,
    UnitOfTime,
)
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

    entities: list[SensorEntity] = []

    if caps.power_status:
        entities.extend(
            [
                WattboxDevicePowerSensor(coordinator, kind="power"),
                WattboxDevicePowerSensor(coordinator, kind="current"),
                WattboxDevicePowerSensor(coordinator, kind="voltage"),
            ]
        )

    if caps.outlet_power_status:
        for i in range(1, coordinator.data.info.outlet_count + 1):
            for kind in ("power", "current", "voltage"):
                entities.append(WattboxOutletPowerSensor(coordinator, outlet_index=i, kind=kind))

    if caps.ups:
        entities.extend(
            [
                WattboxUPSBatterySensor(coordinator),
                WattboxUPSLoadSensor(coordinator),
                WattboxUPSRuntimeSensor(coordinator),
            ]
        )

    async_add_entities(entities)


# --- whole-device power -------------------------------------------------


class WattboxDevicePowerSensor(WattboxEntity, SensorEntity):
    """Whole-PDU power/voltage/current from ``?PowerStatus``."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    _KIND_CONFIG: ClassVar[dict[str, tuple[str, SensorDeviceClass, str]]] = {
        "power": ("Power", SensorDeviceClass.POWER, UnitOfPower.WATT),
        "current": ("Current", SensorDeviceClass.CURRENT, UnitOfElectricCurrent.AMPERE),
        "voltage": ("Voltage", SensorDeviceClass.VOLTAGE, UnitOfElectricPotential.VOLT),
    }

    def __init__(self, coordinator: WattboxCoordinator, *, kind: str) -> None:
        super().__init__(coordinator)
        name, device_class, unit = self._KIND_CONFIG[kind]
        self._kind = kind
        self._attr_name = name
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit
        self._attr_unique_id = self._unique_id_for(f"device_{kind}")

    @property
    def native_value(self) -> float | None:
        snap = self.coordinator.data
        if snap.power is None:
            return None
        return {
            "power": snap.power.power_watts,
            "current": snap.power.current_amps,
            "voltage": snap.power.voltage_volts,
        }[self._kind]


# --- per-outlet power ---------------------------------------------------


class WattboxOutletPowerSensor(WattboxEntity, SensorEntity):
    """Per-outlet power/voltage/current from ``?OutletPowerStatus=N``."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    _KIND_CONFIG: ClassVar[dict[str, tuple[str, SensorDeviceClass, str]]] = {
        "power": ("power", SensorDeviceClass.POWER, UnitOfPower.WATT),
        "current": ("current", SensorDeviceClass.CURRENT, UnitOfElectricCurrent.AMPERE),
        "voltage": ("voltage", SensorDeviceClass.VOLTAGE, UnitOfElectricPotential.VOLT),
    }

    def __init__(self, coordinator: WattboxCoordinator, *, outlet_index: int, kind: str) -> None:
        super().__init__(coordinator)
        kind_label, device_class, unit = self._KIND_CONFIG[kind]
        self._outlet_index = outlet_index
        self._kind = kind
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit
        self._attr_unique_id = self._unique_id_for(f"outlet_{outlet_index}_{kind}")
        self._kind_label = kind_label

    @property
    def name(self) -> str:
        for state in self.coordinator.data.outlets:
            if state.index == self._outlet_index:
                return f"{state.name} {self._kind_label}"
        return f"Outlet {self._outlet_index} {self._kind_label}"

    @property
    def native_value(self) -> float | None:
        for op in self.coordinator.data.outlet_power:
            if op.outlet == self._outlet_index:
                return {
                    "power": op.power_watts,
                    "current": op.current_amps,
                    "voltage": op.voltage_volts,
                }[self._kind]
        return None


# --- UPS ----------------------------------------------------------------


class _UPSSensorBase(WattboxEntity, SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT


class WattboxUPSBatterySensor(_UPSSensorBase):
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_name = "UPS battery"

    def __init__(self, coordinator: WattboxCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = self._unique_id_for("ups_battery")

    @property
    def native_value(self) -> int | None:
        ups = self.coordinator.data.ups
        return ups.battery_charge_pct if ups else None


class WattboxUPSLoadSensor(_UPSSensorBase):
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_name = "UPS load"

    def __init__(self, coordinator: WattboxCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = self._unique_id_for("ups_load")

    @property
    def native_value(self) -> int | None:
        ups = self.coordinator.data.ups
        return ups.battery_load_pct if ups else None


class WattboxUPSRuntimeSensor(_UPSSensorBase):
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_name = "UPS runtime"

    def __init__(self, coordinator: WattboxCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = self._unique_id_for("ups_runtime")

    @property
    def native_value(self) -> int | None:
        ups = self.coordinator.data.ups
        return ups.battery_runtime_min if ups else None
