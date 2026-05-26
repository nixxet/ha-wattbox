"""Async client for SnapAV WattBox PDUs over SSH/Telnet."""

from __future__ import annotations

from .client import AUTH_BACKOFF_SCHEDULE_S, LOCKOUT_COOLDOWN_S, WattboxClient
from .exceptions import (
    WattboxAuthError,
    WattboxCommandUnsupported,
    WattboxConnectionError,
    WattboxError,
    WattboxLockoutError,
    WattboxProtocolError,
)
from .models import (
    BatteryHealth,
    Capabilities,
    DeviceInfo,
    OutletPowerStatus,
    OutletState,
    PowerStatus,
    Snapshot,
    UPSStatus,
)

__version__ = "0.1.0.dev0"

__all__ = [
    "AUTH_BACKOFF_SCHEDULE_S",
    "LOCKOUT_COOLDOWN_S",
    "BatteryHealth",
    "Capabilities",
    "DeviceInfo",
    "OutletPowerStatus",
    "OutletState",
    "PowerStatus",
    "Snapshot",
    "UPSStatus",
    "WattboxAuthError",
    "WattboxClient",
    "WattboxCommandUnsupported",
    "WattboxConnectionError",
    "WattboxError",
    "WattboxLockoutError",
    "WattboxProtocolError",
    "__version__",
]
