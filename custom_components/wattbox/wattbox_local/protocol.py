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

import re
from typing import Final

from .exceptions import WattboxCommandUnsupported, WattboxProtocolError
from .models import BatteryHealth, OutletPowerStatus, PowerStatus, UPSStatus

# --- response sentinels --------------------------------------------------

ERROR_SENTINEL: Final[str] = "#Error"
ACK_SENTINEL: Final[str] = "OK"

LOGIN_PROMPT_USER: Final[str] = "Username:"
LOGIN_PROMPT_PASS: Final[str] = "Password:"
LOGIN_OK: Final[str] = "Successfully Logged In!"
LOGIN_BAD: Final[str] = "Invalid Login"
# The device actually emits "API is locked for X minutes and Y seconds."
# We match the substring "is locked for" so the constant is robust to
# future minor wording tweaks. The countdown is parsed out separately by
# `parse_lockout_remaining_s`.
LOGIN_LOCKED: Final[str] = "is locked for"

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
CMD_OUTLET_POWER_STATUS: Final[str] = "?OutletPowerStatus"
CMD_UPS_STATUS: Final[str] = "?UPSStatus"
CMD_UPS_CONNECTION: Final[str] = "?UPSConnection"
CMD_AUTO_REBOOT: Final[str] = "?AutoReboot"
CMD_MUTE: Final[str] = "?Mute"
CMD_SAFE_VOLTAGE: Final[str] = "?SafeVoltage"
CMD_SCHEDULED_REBOOT: Final[str] = "?ScheduledReboot"

# Optional query: per-outlet boot delay (CSV of seconds, one per outlet)
CMD_OUTLET_POWER_ON_DELAY: Final[str] = "?OutletPowerOnDelay"

# Set commands
SET_OUTLET: Final[str] = "!OutletSet"
SET_AUTO_REBOOT: Final[str] = "!AutoReboot"
SET_OUTLET_NAME: Final[str] = "!OutletNameSet"
SET_OUTLET_NAME_ALL: Final[str] = "!OutletNameSetAll"
SET_OUTLET_POWER_ON_DELAY: Final[str] = "!OutletPowerOnDelaySet"
SET_OUTLET_MODE: Final[str] = "!OutletModeSet"
SET_OUTLET_REBOOT: Final[str] = "!OutletRebootSet"
SET_AUTO_REBOOT_TIMEOUT: Final[str] = "!AutoRebootTimeoutSet"
SET_SCHEDULE_ADD: Final[str] = "!ScheduleAdd"
SET_HOST_ADD: Final[str] = "!HostAdd"

# Outlet action verbs for !OutletSet=N,ACTION[,DELAY]
OUTLET_ON: Final[str] = "ON"
OUTLET_OFF: Final[str] = "OFF"
OUTLET_TOGGLE: Final[str] = "TOGGLE"
OUTLET_RESET: Final[str] = "RESET"

# RESET delay bounds per vendor PDF v2.4 (seconds).
RESET_DELAY_MIN: Final[int] = 1
RESET_DELAY_MAX: Final[int] = 600

# Per-outlet power-on delay bounds (seconds), per vendor PDF v2.4.
POWER_ON_DELAY_MIN: Final[int] = 1
POWER_ON_DELAY_MAX: Final[int] = 600


# Outlet mode values for !OutletModeSet (vendor PDF v2.4).
OUTLET_MODE_ENABLED: Final[int] = 0
OUTLET_MODE_DISABLED: Final[int] = 1
OUTLET_MODE_RESET_ONLY: Final[int] = 2
OUTLET_MODES: Final[frozenset[int]] = frozenset(
    {OUTLET_MODE_ENABLED, OUTLET_MODE_DISABLED, OUTLET_MODE_RESET_ONLY}
)

# Per-outlet reboot operation for !OutletRebootSet (vendor PDF v2.4).
OUTLET_REBOOT_OR: Final[int] = 0  # any selected hosts time-out
OUTLET_REBOOT_AND: Final[int] = 1  # all selected hosts time out
OUTLET_REBOOT_OPS: Final[frozenset[int]] = frozenset({OUTLET_REBOOT_OR, OUTLET_REBOOT_AND})

# !AutoRebootTimeoutSet ranges (vendor PDF v2.4).
AUTO_REBOOT_TIMEOUT_MIN_S: Final[int] = 1
AUTO_REBOOT_TIMEOUT_MAX_S: Final[int] = 60
AUTO_REBOOT_COUNT_MIN: Final[int] = 1
AUTO_REBOOT_COUNT_MAX: Final[int] = 10
AUTO_REBOOT_PING_DELAY_MIN_MIN: Final[int] = 1
AUTO_REBOOT_PING_DELAY_MAX_MIN: Final[int] = 30
AUTO_REBOOT_ATTEMPTS_MAX: Final[int] = 10  # 0 == unlimited

# Schedule actions / frequencies (vendor PDF v2.4).
SCHEDULE_ACTION_OFF: Final[int] = 0
SCHEDULE_ACTION_ON: Final[int] = 1
SCHEDULE_ACTION_RESET: Final[int] = 2
SCHEDULE_ACTIONS: Final[frozenset[int]] = frozenset(
    {SCHEDULE_ACTION_OFF, SCHEDULE_ACTION_ON, SCHEDULE_ACTION_RESET}
)
SCHEDULE_FREQ_ONCE: Final[int] = 0
SCHEDULE_FREQ_RECURRING: Final[int] = 1


# --- encoders -----------------------------------------------------------


def encode_outlet_set(index: int, action: str, *, delay: int | None = None) -> str:
    """Build the wire form of ``!OutletSet=N,ACTION[,DELAY]``.

    ``index`` is 1-based; pass ``0`` with ``action=RESET`` to reset every
    outlet (per the vendor PDF). ``action`` must be one of
    ``ON``/``OFF``/``TOGGLE``/``RESET``. ``delay`` is only valid with
    ``RESET`` and must be in the inclusive range
    ``[RESET_DELAY_MIN, RESET_DELAY_MAX]`` seconds — it overrides the
    outlet's configured power-on delay for this reset only.
    """
    if index < 0:
        raise ValueError(f"outlet index must be >= 0, got {index}")
    if index == 0 and action != OUTLET_RESET:
        raise ValueError("outlet index 0 is only valid with action=RESET")
    if action not in (OUTLET_ON, OUTLET_OFF, OUTLET_TOGGLE, OUTLET_RESET):
        raise ValueError(f"invalid outlet action: {action!r}")
    if delay is not None:
        if action != OUTLET_RESET:
            raise ValueError("delay is only valid with action=RESET")
        if not (RESET_DELAY_MIN <= delay <= RESET_DELAY_MAX):
            raise ValueError(
                f"reset delay must be in [{RESET_DELAY_MIN}, {RESET_DELAY_MAX}] seconds, "
                f"got {delay}"
            )
        return f"{SET_OUTLET}={index},{action},{delay}"
    return f"{SET_OUTLET}={index},{action}"


def encode_auto_reboot(enabled: bool) -> str:
    return f"{SET_AUTO_REBOOT}={1 if enabled else 0}"


def encode_outlet_name_set(index: int, name: str) -> str:
    """Build ``!OutletNameSet=N,{NAME}``.

    The vendor PDF v2.4 example writes the name bare, but the device's
    own ``?OutletName`` response and ``!OutletNameSetAll`` both use
    ``{...}`` to delimit names — without braces, a name containing a
    space gets truncated/stripped on the wire. Bracing mirrors the
    response format and survives spaces intact.
    """
    if index < 1:
        raise ValueError(f"outlet index must be >= 1, got {index}")
    if not name:
        raise ValueError("name must be non-empty")
    if any(c in name for c in "{}\r\n"):
        raise ValueError("name must not contain '{', '}', or newlines")
    return f"{SET_OUTLET_NAME}={index},{{{name}}}"


def encode_outlet_name_set_all(names: list[str]) -> str:
    """Build ``!OutletNameSetAll={N1},{N2},...`` (brackets required, vendor PDF).

    The list must contain exactly one name per outlet, in outlet order
    starting from outlet 1.
    """
    if not names:
        raise ValueError("at least one name required")
    for n in names:
        if not n:
            raise ValueError("names must be non-empty")
        if any(c in n for c in "{}\r\n"):
            raise ValueError("names must not contain '{', '}', or newlines")
    return f"{SET_OUTLET_NAME_ALL}=" + ",".join(f"{{{n}}}" for n in names)


def encode_outlet_power_on_delay_set(index: int, seconds: int) -> str:
    """Build ``!OutletPowerOnDelaySet=N,SECONDS``. SECONDS in [1,600]."""
    if index < 1:
        raise ValueError(f"outlet index must be >= 1, got {index}")
    if not (POWER_ON_DELAY_MIN <= seconds <= POWER_ON_DELAY_MAX):
        raise ValueError(
            f"power-on delay must be in [{POWER_ON_DELAY_MIN}, {POWER_ON_DELAY_MAX}] "
            f"seconds, got {seconds}"
        )
    return f"{SET_OUTLET_POWER_ON_DELAY}={index},{seconds}"


def encode_outlet_mode_set(index: int, mode: int) -> str:
    """Build ``!OutletModeSet=N,MODE``. MODE: 0=Enabled, 1=Disabled, 2=Reset-Only."""
    if index < 1:
        raise ValueError(f"outlet index must be >= 1, got {index}")
    if mode not in OUTLET_MODES:
        raise ValueError(f"mode must be one of {sorted(OUTLET_MODES)}, got {mode}")
    return f"{SET_OUTLET_MODE}={index},{mode}"


def encode_outlet_reboot_set(ops: list[int]) -> str:
    """Build ``!OutletRebootSet=OP,OP,...`` — one OP per outlet on the device.

    OP per vendor PDF: 0 = Or (any selected hosts time-out), 1 = And (all
    selected hosts time out). The list length must equal the device's
    outlet count; the caller is responsible for matching that.
    """
    if not ops:
        raise ValueError("at least one OP required")
    for op in ops:
        if op not in OUTLET_REBOOT_OPS:
            raise ValueError(f"each OP must be one of {sorted(OUTLET_REBOOT_OPS)}, got {op}")
    return f"{SET_OUTLET_REBOOT}=" + ",".join(str(op) for op in ops)


def encode_auto_reboot_timeout_set(
    timeout_s: int, count: int, ping_delay_min: int, reboot_attempts: int
) -> str:
    """Build ``!AutoRebootTimeoutSet=TIMEOUT,COUNT,PING_DELAY,REBOOT_ATTEMPTS``.

    Bounds per vendor PDF v2.4:

    * ``timeout_s``     [1, 60] seconds — host response timeout.
    * ``count``         [1, 10]         — consecutive timeouts before reboot.
    * ``ping_delay_min`` [1, 30] minutes — wait between auto-reboot retries.
    * ``reboot_attempts`` 0 = unlimited, otherwise [1, 10].
    """
    if not (AUTO_REBOOT_TIMEOUT_MIN_S <= timeout_s <= AUTO_REBOOT_TIMEOUT_MAX_S):
        raise ValueError(
            f"timeout must be [{AUTO_REBOOT_TIMEOUT_MIN_S},"
            f"{AUTO_REBOOT_TIMEOUT_MAX_S}], got {timeout_s}"
        )
    if not (AUTO_REBOOT_COUNT_MIN <= count <= AUTO_REBOOT_COUNT_MAX):
        raise ValueError(
            f"count must be [{AUTO_REBOOT_COUNT_MIN},{AUTO_REBOOT_COUNT_MAX}], got {count}"
        )
    if not (AUTO_REBOOT_PING_DELAY_MIN_MIN <= ping_delay_min <= AUTO_REBOOT_PING_DELAY_MAX_MIN):
        raise ValueError(
            f"ping_delay_min must be [{AUTO_REBOOT_PING_DELAY_MIN_MIN},"
            f"{AUTO_REBOOT_PING_DELAY_MAX_MIN}], got {ping_delay_min}"
        )
    if reboot_attempts < 0 or reboot_attempts > AUTO_REBOOT_ATTEMPTS_MAX:
        raise ValueError(
            f"reboot_attempts must be 0 (unlimited) or [1, {AUTO_REBOOT_ATTEMPTS_MAX}], "
            f"got {reboot_attempts}"
        )
    return f"{SET_AUTO_REBOOT_TIMEOUT}={timeout_s},{count},{ping_delay_min},{reboot_attempts}"


def encode_host_add(name: str, ip: str, outlets: list[int]) -> str:
    """Build ``!HostAdd=NAME,IP,{N,N,...}`` for ping-host monitoring.

    Per vendor PDF: brackets required around the outlets array. The
    integration must already have called ``!AutoReboot=1`` for hosts to
    actually trigger reboots.
    """
    if not name:
        raise ValueError("name must be non-empty")
    if not ip:
        raise ValueError("ip must be non-empty")
    if any(c in name for c in ",{}\r\n") or any(c in ip for c in ",{}\r\n"):
        raise ValueError("name and ip must not contain ',', '{', '}', or newlines")
    if not outlets:
        raise ValueError("at least one outlet required")
    for o in outlets:
        if o < 1:
            raise ValueError(f"outlet indices must be >= 1, got {o}")
    outlet_csv = ",".join(str(o) for o in outlets)
    return f"{SET_HOST_ADD}={name},{ip},{{{outlet_csv}}}"


def encode_schedule_add(
    name: str,
    outlets: list[int],
    action: int,
    *,
    days: tuple[bool, bool, bool, bool, bool, bool, bool] | None = None,
    date: str | None = None,
    time: str,
) -> str:
    """Build ``!ScheduleAdd={NAME},{OUTLETS},{ACTION},{FREQ},{DAYS|DATE},{TIME}``.

    Per vendor PDF v2.4. Exactly one of ``days`` or ``date`` must be
    provided — they determine FREQ (1=Recurring with day mask, 0=Once
    with date).

    * ``days`` is a 7-tuple ``(sun, mon, tue, wed, thu, fri, sat)``.
    * ``date`` is ``yyyy/mm/dd`` for a one-shot schedule.
    * ``time`` is 24-hour ``hh:mm`` (e.g. ``13:30`` = 1:30pm).
    """
    if not name:
        raise ValueError("name must be non-empty")
    if any(c in name for c in ",{}\r\n"):
        raise ValueError("name must not contain ',', '{', '}', or newlines")
    if not outlets:
        raise ValueError("at least one outlet required")
    for o in outlets:
        if o < 1:
            raise ValueError(f"outlet indices must be >= 1, got {o}")
    if action not in SCHEDULE_ACTIONS:
        raise ValueError(f"action must be one of {sorted(SCHEDULE_ACTIONS)}, got {action}")
    if (days is None) == (date is None):
        raise ValueError("exactly one of `days` (recurring) or `date` (once) must be set")
    if not _TIME_RE.fullmatch(time):
        raise ValueError(f"time must match 'hh:mm' 24-hour format, got {time!r}")

    if days is not None:
        freq = SCHEDULE_FREQ_RECURRING
        day_csv = ",".join("1" if d else "0" for d in days)
        when = "{" + day_csv + "}"
    else:
        assert date is not None
        if not _DATE_RE.fullmatch(date):
            raise ValueError(f"date must match 'yyyy/mm/dd', got {date!r}")
        freq = SCHEDULE_FREQ_ONCE
        when = "{" + date + "}"

    outlet_csv = ",".join(str(o) for o in outlets)
    return (
        f"{SET_SCHEDULE_ADD}={{{name}}},{{{outlet_csv}}},{{{action}}},{{{freq}}},{when},{{{time}}}"
    )


_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_DATE_RE = re.compile(r"^\d{4}/(?:0[1-9]|1[0-2])/(?:0[1-9]|[12]\d|3[01])$")


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
    """Strip the ``?Cmd=`` (or ``~Cmd=``) prefix from ``raw`` and return value.

    WattBox firmware uses ``?`` to prefix synchronous responses and ``~``
    to prefix asynchronous push notifications (emitted after a state
    change). Both carry the same payload shape for the same command name,
    so both are accepted here.

    ``command`` may include arguments (e.g. ``?OutletPowerStatus=1``);
    only the command name is used for prefix matching.

    Raises :class:`WattboxCommandUnsupported` if the device responded
    ``#Error``. Raises :class:`WattboxProtocolError` for anything else
    that doesn't look like ``Cmd=value``.
    """
    stripped = raw.strip()
    if stripped == ERROR_SENTINEL:
        raise WattboxCommandUnsupported(command)
    # ``?Model`` -> ``Model``; ``?OutletPowerStatus=1`` -> ``OutletPowerStatus``.
    bare = command_name(command)
    for prefix in (f"?{bare}=", f"~{bare}="):
        if stripped.startswith(prefix):
            return stripped[len(prefix) :]
    raise WattboxProtocolError(
        f"expected response prefix '?{bare}=' or '~{bare}=', got {stripped!r}"
    )


def is_async_push(line: str) -> bool:
    """True if ``line`` is a ``~Cmd=value`` async notification.

    Used by transports to identify stale notifications that should be
    discarded between commands.
    """
    stripped = line.strip()
    return stripped.startswith("~") and "=" in stripped


def response_command_name(line: str) -> str | None:
    """Extract the command name from a response line.

    Examples:
        ``?Model=WB-800-IPVM-12`` -> ``Model``
        ``~OutletStatus=1,0``     -> ``OutletStatus``
        ``OK``                    -> ``None`` (sentinel, no name)
        ``#Error``                -> ``None``
        ``Successfully Logged...`` -> ``None`` (banner)
    """
    stripped = line.strip()
    if not stripped or stripped in (ACK_SENTINEL, ERROR_SENTINEL):
        return None
    if stripped[0] not in "?~":
        return None
    name = stripped[1:].split("=", 1)[0]
    return name or None


def command_name(command: str) -> str:
    """Bare name of a command (drops the ``?``/``!``/``~`` prefix and any args).

    ``?Model`` -> ``Model``; ``!OutletSet=2,ON`` -> ``OutletSet``.
    """
    stripped = command.strip()
    if stripped and stripped[0] in "?!~":
        stripped = stripped[1:]
    return stripped.split("=", 1)[0]


_LOCKOUT_RE = re.compile(
    r"locked for (?:(\d+)\s*minutes?)?(?:\s*and\s*)?(?:(\d+)\s*seconds?)?",
    re.IGNORECASE,
)


def parse_lockout_remaining_s(banner: str) -> int | None:
    """Pull the countdown out of an ``API is locked for ...`` banner.

    Returns total seconds remaining, or ``None`` if the banner doesn't
    contain a parseable duration.
    """
    match = _LOCKOUT_RE.search(banner)
    if not match:
        return None
    minutes = int(match.group(1) or 0)
    seconds = int(match.group(2) or 0)
    total = minutes * 60 + seconds
    return total if total > 0 else None


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

    Wire format per vendor PDF v2.4:
    ``current_a, power_w, voltage_v, safe_voltage_flag``.
    Example: ``60.00,600.00,110.00,1``.
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


def parse_outlet_power_status(value: str) -> OutletPowerStatus:
    """Parse ``?OutletPowerStatus=N`` value -> :class:`OutletPowerStatus`.

    Wire format per vendor PDF v2.4:
    ``outlet, power_w, current_a, voltage_v``.
    Example: ``1,1.01,0.02,116.50`` -> outlet 1, 1.01W, 0.02A, 116.50V.

    Note the W/A field order is **flipped** vs whole-device ``?PowerStatus``
    (which is A,W,V,flag). That's the device's choice, not ours.
    """
    parts = [p.strip() for p in value.split(",")]
    if len(parts) != 4:
        raise WattboxProtocolError(
            f"expected 4 fields in ?OutletPowerStatus, got {len(parts)}: {value!r}"
        )
    try:
        return OutletPowerStatus(
            outlet=int(parts[0]),
            power_watts=float(parts[1]),
            current_amps=float(parts[2]),
            voltage_volts=float(parts[3]),
        )
    except ValueError as e:
        raise WattboxProtocolError(f"bad numeric in ?OutletPowerStatus: {value!r}") from e


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


def parse_power_on_delays(value: str) -> list[int]:
    """Parse ``?OutletPowerOnDelay`` value -> per-outlet boot delays (sec).

    Wire format observed live: CSV of integer seconds, one per outlet
    (e.g. ``11,4,10,31,5,12,2,7,8,9,30,6`` for a 12-outlet device).
    """
    if not value:
        return []
    try:
        return [int(p.strip()) for p in value.split(",")]
    except ValueError as e:
        raise WattboxProtocolError(f"bad numeric in ?OutletPowerOnDelay: {value!r}") from e


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
