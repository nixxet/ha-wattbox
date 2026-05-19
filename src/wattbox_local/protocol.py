"""Pure encoder/decoder for the WattBox `?Cmd` / `!Cmd` protocol.

No I/O. This module owns every bit of string-mangling so the transports
stay dumb and the tests stay fast.

Wire format (verified live against WB-250-IPW-2 fw 2.9.0.2 and
WB-800-IPVM-12 fw 2.10.0.0):

- Query:   client sends ``?Cmd\\n`` -> device replies ``?Cmd=value\\n``
- Set:     client sends ``!Cmd=arg\\n`` -> device replies ``OK\\n`` (or ``?Cmd=...``)
- Error:   device replies ``#Error\\n`` when the command is not supported
- Login:   device emits ``Invalid Login`` / ``Successfully Logged In!`` /
           ``API locked`` banners during the auth phase

The `parse_response` family takes the *raw value string* (the part after
the ``=``) so the transport can strip the ``?Cmd=`` prefix before
dispatch. This keeps the parsers easy to unit-test from literal strings.
"""

from __future__ import annotations

from typing import Final

from .exceptions import WattboxCommandUnsupported, WattboxProtocolError
from .models import BatteryHealth, PowerStatus, UPSStatus

# --- response sentinels --------------------------------------------------

ERROR_SENTINEL: Final[str] = "#Error"
ACK_SENTINEL: Final[str] = "OK"

LOGIN_PROMPT_USER: Final[str] = "Username:"
LOGIN_PROMPT_PASS: Final[str] = "Password:"
LOGIN_OK: Final[str] = "Successfully Logged In!"
LOGIN_BAD: Final[str] = "Invalid Login"
LOGIN_LOCKED: Final[str] = "API locked"

# --- commands -----------------------------------------------------------

# Query commands always supported on tested firmware
CMD_FIRMWARE: Final[str] = "?Firmware"
CMD_MODEL: Final[str] = "?Model"
CMD_SERVICE_TAG: Final[str] = "?ServiceTag"
CMD_HOSTNAME: Final[str] = "?Hostname"
CMD_OUTLET_COUNT: Final[str] = "?OutletCount"
CMD_OUTLET_STATUS: Final[str] = "?OutletStatus"
CMD_OUTLET_NAME: Final[str] = "?OutletName"

# Optional / capability-gated query commands
CMD_POWER_STATUS: Final[str] = "?PowerStatus"
CMD_UPS_STATUS: Final[str] = "?UPSStatus"
CMD_UPS_CONNECTION: Final[str] = "?UPSConnection"
CMD_AUTO_REBOOT: Final[str] = "?AutoReboot"
CMD_MUTE: Final[str] = "?Mute"
CMD_SAFE_VOLTAGE: Final[str] = "?SafeVoltage"
CMD_SCHEDULED_REBOOT: Final[str] = "?ScheduledReboot"

# Set commands
SET_OUTLET: Final[str] = "!OutletSet"
SET_AUTO_REBOOT: Final[str] = "!AutoReboot"

# Outlet action verbs for !OutletSet=N,ACTION
OUTLET_ON: Final[str] = "ON"
OUTLET_OFF: Final[str] = "OFF"
OUTLET_RESET: Final[str] = "RESET"


# --- encoders -----------------------------------------------------------


def encode_outlet_set(index: int, action: str) -> str:
    """Build the wire form of ``!OutletSet=N,ACTION``.

    ``index`` is 1-based to match the device's own outlet numbering.
    ``action`` must be one of ``ON``/``OFF``/``RESET``.
    """
    if index < 1:
        raise ValueError(f"outlet index must be >= 1, got {index}")
    if action not in (OUTLET_ON, OUTLET_OFF, OUTLET_RESET):
        raise ValueError(f"invalid outlet action: {action!r}")
    return f"{SET_OUTLET}={index},{action}"


def encode_auto_reboot(enabled: bool) -> str:
    return f"{SET_AUTO_REBOOT}={1 if enabled else 0}"


# --- response splitter --------------------------------------------------


def split_response(line: str) -> tuple[str, str] | None:
    """Split a `?Cmd=value` line into (command, value).

    Returns ``None`` for non-key/value device output (banners, prompts).
    Raises :class:`WattboxCommandUnsupported` if the value is ``#Error``.
    The command is unknown at this layer, so callers wrap the lookup with
    the command name they sent.
    """
    stripped = line.strip()
    if not stripped:
        return None
    if stripped == ERROR_SENTINEL:
        raise WattboxCommandUnsupported("<unknown — caller did not name the command>")
    if "=" not in stripped:
        return None
    cmd, _, value = stripped.partition("=")
    return cmd, value


def expect_value(command: str, raw: str) -> str:
    """Strip the ``?Cmd=`` prefix from ``raw`` and return the value.

    Raises :class:`WattboxCommandUnsupported` if the device responded
    ``#Error``. Raises :class:`WattboxProtocolError` for anything else
    that doesn't look like ``command=value``.
    """
    stripped = raw.strip()
    if stripped == ERROR_SENTINEL:
        raise WattboxCommandUnsupported(command)
    prefix = f"{command}="
    if not stripped.startswith(prefix):
        raise WattboxProtocolError(f"expected response prefix {prefix!r}, got {stripped!r}")
    return stripped[len(prefix) :]


# --- parsers ------------------------------------------------------------


def parse_outlet_status(value: str) -> list[bool]:
    """Parse the value of ``?OutletStatus`` -> list of on/off bools."""
    if not value:
        return []
    out: list[bool] = []
    for piece in value.split(","):
        piece = piece.strip()
        if piece == "1":
            out.append(True)
        elif piece == "0":
            out.append(False)
        else:
            raise WattboxProtocolError(f"bad outlet state token: {piece!r}")
    return out


def parse_outlet_names(value: str) -> list[str]:
    """Parse ``?OutletName`` value (brace-delimited, comma-separated).

    Example: ``{Dish Hopper},{EA3},{Denon Rcv}`` ->
    ``['Dish Hopper', 'EA3', 'Denon Rcv']``.

    Names may legitimately contain commas; the braces are the real
    delimiters, so we tokenize by walking braces rather than splitting
    on commas.
    """
    if not value:
        return []
    names: list[str] = []
    i = 0
    n = len(value)
    while i < n:
        # skip whitespace and commas between entries
        while i < n and value[i] in ", \t":
            i += 1
        if i >= n:
            break
        if value[i] != "{":
            raise WattboxProtocolError(
                f"expected '{{' at position {i} in {value!r}, got {value[i]!r}"
            )
        end = value.find("}", i + 1)
        if end == -1:
            raise WattboxProtocolError(f"unterminated '{{' in {value!r}")
        names.append(value[i + 1 : end])
        i = end + 1
    return names


def parse_power_status(value: str) -> PowerStatus:
    """Parse ``?PowerStatus`` value -> :class:`PowerStatus`.

    Wire format: ``current_a, power_w, voltage_v, safe_voltage_flag``.
    Example: ``0.27,81.88,123.69,0``.
    """
    parts = [p.strip() for p in value.split(",")]
    if len(parts) != 4:
        raise WattboxProtocolError(
            f"expected 4 fields in ?PowerStatus, got {len(parts)}: {value!r}"
        )
    try:
        return PowerStatus(
            current_amps=float(parts[0]),
            power_watts=float(parts[1]),
            voltage_volts=float(parts[2]),
            safe_voltage=parts[3] == "1",
        )
    except ValueError as e:
        raise WattboxProtocolError(f"bad numeric in ?PowerStatus: {value!r}") from e


def parse_ups_status(value: str) -> UPSStatus:
    """Parse ``?UPSStatus`` value -> :class:`UPSStatus`.

    Wire format (7 fields):
    ``charge_pct, load_pct, health, power_lost, runtime_min,
       alarm_enabled, alarm_muted``.

    Example: ``100,8,Good,False,160,True,False``.
    """
    parts = [p.strip() for p in value.split(",")]
    if len(parts) != 7:
        raise WattboxProtocolError(f"expected 7 fields in ?UPSStatus, got {len(parts)}: {value!r}")
    try:
        return UPSStatus(
            battery_charge_pct=int(parts[0]),
            battery_load_pct=int(parts[1]),
            battery_health=BatteryHealth.parse(parts[2]),
            power_lost=_parse_bool(parts[3]),
            battery_runtime_min=int(parts[4]),
            alarm_enabled=_parse_bool(parts[5]),
            alarm_muted=_parse_bool(parts[6]),
        )
    except ValueError as e:
        raise WattboxProtocolError(f"bad field in ?UPSStatus: {value!r}") from e


def parse_ups_connection(value: str) -> bool:
    """Parse ``?UPSConnection`` value: ``1`` -> connected, ``0`` -> not."""
    stripped = value.strip()
    if stripped not in ("0", "1"):
        raise WattboxProtocolError(f"bad ?UPSConnection value: {value!r}")
    return stripped == "1"


def parse_auto_reboot(value: str) -> bool:
    """Parse ``?AutoReboot`` value: ``1`` -> on, ``0`` -> off."""
    stripped = value.strip()
    if stripped not in ("0", "1"):
        raise WattboxProtocolError(f"bad ?AutoReboot value: {value!r}")
    return stripped == "1"


def parse_int(value: str, *, command: str) -> int:
    try:
        return int(value.strip())
    except ValueError as e:
        raise WattboxProtocolError(f"non-integer value for {command}: {value!r}") from e


# --- helpers ------------------------------------------------------------


def _parse_bool(token: str) -> bool:
    """Parse the device's `True`/`False`/`0`/`1` boolean tokens."""
    t = token.strip().lower()
    if t in ("true", "1"):
        return True
    if t in ("false", "0"):
        return False
    raise ValueError(f"not a boolean token: {token!r}")
