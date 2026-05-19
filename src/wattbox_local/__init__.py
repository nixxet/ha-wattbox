"""Async client for SnapAV WattBox PDUs over SSH/Telnet."""

from __future__ import annotations

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
    OutletState,
    PowerStatus,
    Snapshot,
    UPSStatus,
)

__version__ = "0.1.0.dev0"

__all__ = [
    "BatteryHealth",
    "Capabilities",
    "DeviceInfo",
    "OutletState",
    "PowerStatus",
    "Snapshot",
    "UPSStatus",
    "WattboxAuthError",
    "WattboxCommandUnsupported",
    "WattboxConnectionError",
    "WattboxError",
    "WattboxLockoutError",
    "WattboxProtocolError",
    "__version__",
]
