"""Constants for the WattBox HA integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final[str] = "wattbox"
MANUFACTURER: Final[str] = "SnapAV"

CONF_HOST: Final[str] = "host"
CONF_USERNAME: Final[str] = "username"
CONF_PASSWORD: Final[str] = "password"
CONF_PORT: Final[str] = "port"
CONF_SCAN_INTERVAL: Final[str] = "scan_interval"

DEFAULT_PORT: Final[int] = 23  # Telnet — SSH is Phase 3.
DEFAULT_USERNAME: Final[str] = "wattbox"
DEFAULT_SCAN_INTERVAL_S: Final[int] = 30
MIN_SCAN_INTERVAL_S: Final[int] = 10
MAX_SCAN_INTERVAL_S: Final[int] = 600

DEFAULT_SCAN_INTERVAL: Final[timedelta] = timedelta(seconds=DEFAULT_SCAN_INTERVAL_S)
