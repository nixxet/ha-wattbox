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
CONF_TRANSPORT: Final[str] = "transport"
CONF_SCAN_INTERVAL: Final[str] = "scan_interval"

TRANSPORT_SSH: Final[str] = "ssh"
TRANSPORT_TELNET: Final[str] = "telnet"
TRANSPORTS: Final[tuple[str, ...]] = (TRANSPORT_SSH, TRANSPORT_TELNET)

DEFAULT_TRANSPORT: Final[str] = TRANSPORT_SSH  # prefer encrypted
DEFAULT_PORT_TELNET: Final[int] = 23
DEFAULT_PORT_SSH: Final[int] = 22
DEFAULT_USERNAME: Final[str] = "wattbox"
DEFAULT_SCAN_INTERVAL_S: Final[int] = 30
MIN_SCAN_INTERVAL_S: Final[int] = 10
MAX_SCAN_INTERVAL_S: Final[int] = 600

DEFAULT_SCAN_INTERVAL: Final[timedelta] = timedelta(seconds=DEFAULT_SCAN_INTERVAL_S)


def default_port_for(transport: str) -> int:
    return DEFAULT_PORT_SSH if transport == TRANSPORT_SSH else DEFAULT_PORT_TELNET
