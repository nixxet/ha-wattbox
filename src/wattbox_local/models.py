"""Typed data models for WattBox device state.

All models are frozen dataclasses. They are the public surface of the
library — callers should program against these types rather than parsing
strings themselves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class BatteryHealth(StrEnum):
    """Health values returned by `?UPSStatus` field 3."""

    GOOD = "Good"
    FAIR = "Fair"
    BAD = "Bad"
    UNKNOWN = "Unknown"

    @classmethod
    def parse(cls, raw: str) -> BatteryHealth:
        try:
            return cls(raw.strip())
        except ValueError:
            return cls.UNKNOWN


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    """Static device identity captured at `identify()` time."""

    model: str
    firmware: str
    hostname: str
    service_tag: str
    outlet_count: int


@dataclass(frozen=True, slots=True)
class OutletState:
    """One outlet's name and on/off state."""

    index: int  # 1-based, matching the device's own numbering
    name: str
    is_on: bool


@dataclass(frozen=True, slots=True)
class PowerStatus:
    """Per-device aggregate power metering (whole-PDU, not per-outlet).

    Returned by `?PowerStatus`. Wire format per the vendor PDF v2.4:
    ``?PowerStatus=A,W,V,safe_flag``. Not all models expose this; the
    `WB-250-IPW-2` for example returns `#Error`. The library raises
    :class:`~wattbox_local.exceptions.WattboxCommandUnsupported` in that
    case, and the integration omits the corresponding sensors.
    """

    current_amps: float
    power_watts: float
    voltage_volts: float
    safe_voltage: bool  # the trailing 0/1 flag from the wire


@dataclass(frozen=True, slots=True)
class OutletPowerStatus:
    """Per-outlet power metering (WB-800 only).

    Returned by ``?OutletPowerStatus=N``. Wire format per the vendor PDF
    v2.4: ``?OutletPowerStatus=N,W,A,V`` — **note the watts/amps order is
    flipped vs whole-device `?PowerStatus`**; that's the device's choice,
    not ours.
    """

    outlet: int  # 1-based
    power_watts: float
    current_amps: float
    voltage_volts: float


@dataclass(frozen=True, slots=True)
class UPSStatus:
    """UPS battery status (only present on units with an attached UPS).

    Returned by `?UPSStatus` as a 7-field CSV:
    `battery_charge_pct, battery_load_pct, battery_health, power_lost,
     battery_runtime_min, alarm_enabled, alarm_muted`.
    """

    battery_charge_pct: int
    battery_load_pct: int
    battery_health: BatteryHealth
    power_lost: bool
    battery_runtime_min: int
    alarm_enabled: bool
    alarm_muted: bool


@dataclass(frozen=True, slots=True)
class Capabilities:
    """Which optional query commands this device actually answers.

    Populated by :meth:`WattboxClient.identify` by probing each optional
    command once and recording whether the device replied normally or with
    `#Error`. Used downstream to gate entity creation.
    """

    power_status: bool = False
    outlet_power_status: bool = False
    ups: bool = False
    auto_reboot: bool = False
    mute: bool = False
    safe_voltage: bool = False
    scheduled_reboot: bool = False


@dataclass(frozen=True, slots=True)
class Snapshot:
    """A single coordinated read of the device's full live state."""

    info: DeviceInfo
    capabilities: Capabilities
    outlets: list[OutletState] = field(default_factory=list)
    outlet_power: list[OutletPowerStatus] = field(default_factory=list)
    power: PowerStatus | None = None
    ups: UPSStatus | None = None
    ups_connected: bool | None = None
    auto_reboot: bool | None = None
