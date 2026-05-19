"""High-level async WattBox client.

Layers the protocol parsers and the transport into a single object the
caller can program against:

    async with WattboxClient("10.0.0.10", "wattbox", "secret") as wb:
        info = await wb.identify()
        snap = await wb.snapshot()
        await wb.set_outlet(3, on=True)

Responsibilities not handled by lower layers:

* **Capability detection.** On first ``identify()`` each optional query is
  sent once and the result recorded in :class:`Capabilities`. Subsequent
  ``snapshot()`` calls skip commands the device cannot answer instead of
  paying a round-trip every time.
* **Lockout budget.** Tracks consecutive auth failures per client
  instance. After ``MAX_AUTH_FAILURES`` strikes within
  ``LOCKOUT_COOLDOWN_S``, refuses to attempt another connection until the
  cooldown elapses — even if the device itself would still accept one.
  This protects the user from chained mistakes triggering the device's
  own (longer, opaque) lockout.
* **Reconnect.** ``send`` reconnects transparently on a dropped
  connection, once. If reconnect itself fails, the error bubbles up.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from typing import Final, Self

from .exceptions import (
    WattboxAuthError,
    WattboxCommandUnsupported,
    WattboxConnectionError,
    WattboxLockoutError,
)
from .models import (
    Capabilities,
    DeviceInfo,
    OutletPowerStatus,
    OutletState,
    PowerStatus,
    Snapshot,
    UPSStatus,
)
from .protocol import (
    CMD_AUTO_REBOOT,
    CMD_FIRMWARE,
    CMD_HOSTNAME,
    CMD_MODEL,
    CMD_MUTE,
    CMD_OUTLET_COUNT,
    CMD_OUTLET_NAME,
    CMD_OUTLET_POWER_STATUS,
    CMD_OUTLET_STATUS,
    CMD_POWER_STATUS,
    CMD_SAFE_VOLTAGE,
    CMD_SCHEDULED_REBOOT,
    CMD_SERVICE_TAG,
    CMD_UPS_CONNECTION,
    CMD_UPS_STATUS,
    OUTLET_OFF,
    OUTLET_ON,
    OUTLET_RESET,
    OUTLET_TOGGLE,
    encode_auto_reboot,
    encode_outlet_set,
    expect_value,
    parse_auto_reboot,
    parse_int,
    parse_outlet_names,
    parse_outlet_power_status,
    parse_outlet_status,
    parse_power_status,
    parse_ups_connection,
    parse_ups_status,
)
from .transport import TelnetTransport, Transport

_LOGGER = logging.getLogger(__name__)

# Lockout budget tuning. Conservative: WattBox firmware appears to lock
# after ~3 bad attempts; we cut off at 3 to avoid ever tripping it
# ourselves. 20 minutes mirrors what we've observed on the device side.
MAX_AUTH_FAILURES: Final[int] = 3
LOCKOUT_COOLDOWN_S: Final[float] = 1200.0  # 20 minutes


class WattboxClient:
    """Async high-level client for one WattBox device."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        *,
        port: int = 23,
        transport: Transport | None = None,
    ) -> None:
        self.host = host
        self._transport: Transport = transport or TelnetTransport(
            host, username, password, port=port
        )
        # Capability map populated on first identify().
        self._capabilities: Capabilities | None = None
        self._info: DeviceInfo | None = None
        # Lockout budget state (per client instance).
        self._auth_failures: int = 0
        self._locked_until: float = 0.0
        # Reconnect serialization.
        self._reconnect_lock = asyncio.Lock()

    # ---- public state -------------------------------------------------

    @property
    def is_connected(self) -> bool:
        return self._transport.is_connected

    @property
    def is_locked_out(self) -> bool:
        """True while the client-side lockout cooldown is active."""
        return time.monotonic() < self._locked_until

    @property
    def capabilities(self) -> Capabilities | None:
        """Capability map from the last successful ``identify()``."""
        return self._capabilities

    @property
    def info(self) -> DeviceInfo | None:
        return self._info

    # ---- lifecycle ----------------------------------------------------

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def connect(self) -> None:
        """Open the transport. Respects the client-side lockout budget."""
        if self.is_locked_out:
            cooldown = self._locked_until - time.monotonic()
            raise WattboxLockoutError(
                f"{self.host} client-side lockout active for another {cooldown:.0f}s"
            )
        try:
            await self._transport.connect()
        except WattboxAuthError:
            self._record_auth_failure()
            raise
        except WattboxLockoutError:
            # Device-side lockout: arm our own cooldown to match so we
            # don't immediately retry into it.
            self._locked_until = time.monotonic() + LOCKOUT_COOLDOWN_S
            raise
        # successful connect resets the failure budget
        self._auth_failures = 0

    async def close(self) -> None:
        await self._transport.close()

    # ---- high-level reads --------------------------------------------

    async def identify(self) -> DeviceInfo:
        """Read static device identity and probe capabilities.

        Cached on the instance. Re-runs only if the cache is empty (i.e.
        after reconnect-with-clear or a fresh client).
        """
        if self._info is not None and self._capabilities is not None:
            return self._info

        info = DeviceInfo(
            model=await self._query_str(CMD_MODEL),
            firmware=await self._query_str(CMD_FIRMWARE),
            hostname=await self._query_str(CMD_HOSTNAME),
            service_tag=await self._query_str(CMD_SERVICE_TAG),
            outlet_count=await self._query_int(CMD_OUTLET_COUNT),
        )
        caps = await self._probe_capabilities()
        self._info = info
        self._capabilities = caps
        return info

    async def snapshot(self) -> Snapshot:
        """Coordinated read of everything supported by this device."""
        info = await self.identify()
        assert self._capabilities is not None
        caps = self._capabilities

        states = await self._read_outlet_states()
        power = await self._maybe_read_power(caps)
        outlet_power = await self._maybe_read_outlet_power(caps, info.outlet_count)
        ups, ups_connected = await self._maybe_read_ups(caps)
        auto_reboot = await self._maybe_read_auto_reboot(caps)

        return Snapshot(
            info=info,
            capabilities=caps,
            outlets=states,
            outlet_power=outlet_power,
            power=power,
            ups=ups,
            ups_connected=ups_connected,
            auto_reboot=auto_reboot,
        )

    async def outlet_states(self) -> list[OutletState]:
        """Read just the per-outlet status + names. Cheaper than ``snapshot()``."""
        return await self._read_outlet_states()

    # ---- high-level writes -------------------------------------------

    async def set_outlet(self, index: int, *, on: bool) -> None:
        """Switch one outlet on or off (1-based index)."""
        await self._send_set(encode_outlet_set(index, OUTLET_ON if on else OUTLET_OFF))

    async def toggle_outlet(self, index: int) -> None:
        """Flip one outlet's state (1-based index). Vendor-supported action."""
        await self._send_set(encode_outlet_set(index, OUTLET_TOGGLE))

    async def reset_outlet(self, index: int, *, delay: int | None = None) -> None:
        """Power-cycle one outlet.

        ``delay`` (1-600 seconds) overrides the outlet's configured
        power-on delay for this reset only. Pass ``index=0`` to reset
        every outlet on the device.
        """
        await self._send_set(encode_outlet_set(index, OUTLET_RESET, delay=delay))

    async def set_auto_reboot(self, enabled: bool) -> None:
        await self._send_set(encode_auto_reboot(enabled))

    # ---- internals ---------------------------------------------------

    async def _read_outlet_states(self) -> list[OutletState]:
        info = await self.identify()
        statuses = parse_outlet_status(
            expect_value(CMD_OUTLET_STATUS, await self._send(CMD_OUTLET_STATUS))
        )
        names = parse_outlet_names(expect_value(CMD_OUTLET_NAME, await self._send(CMD_OUTLET_NAME)))
        # Defensive: device may return fewer names than outlets on some firmwares.
        out: list[OutletState] = []
        for i in range(info.outlet_count):
            name = names[i] if i < len(names) else f"Outlet {i + 1}"
            is_on = statuses[i] if i < len(statuses) else False
            out.append(OutletState(index=i + 1, name=name, is_on=is_on))
        return out

    async def _maybe_read_power(self, caps: Capabilities) -> PowerStatus | None:
        if not caps.power_status:
            return None
        try:
            return parse_power_status(
                expect_value(CMD_POWER_STATUS, await self._send(CMD_POWER_STATUS))
            )
        except WattboxCommandUnsupported:
            # Device unexpectedly changed its mind — downgrade silently.
            return None

    async def _maybe_read_outlet_power(
        self, caps: Capabilities, outlet_count: int
    ) -> list[OutletPowerStatus]:
        """Read per-outlet power for every outlet, in index order.

        Each outlet requires its own round-trip
        (``?OutletPowerStatus=N``), so this is the chattiest part of a
        snapshot. Skipped entirely when the device doesn't support it.
        """
        if not caps.outlet_power_status:
            return []
        result: list[OutletPowerStatus] = []
        for i in range(1, outlet_count + 1):
            cmd = f"{CMD_OUTLET_POWER_STATUS}={i}"
            try:
                raw = await self._send(cmd)
                result.append(parse_outlet_power_status(expect_value(cmd, raw)))
            except WattboxCommandUnsupported:
                # Per-outlet support may degrade mid-poll for individual
                # outlets on some firmwares; skip rather than fail the snapshot.
                _LOGGER.debug("outlet %d power readout unsupported on %s", i, self.host)
        return result

    async def _maybe_read_ups(self, caps: Capabilities) -> tuple[UPSStatus | None, bool | None]:
        if not caps.ups:
            return None, None
        try:
            ups = parse_ups_status(expect_value(CMD_UPS_STATUS, await self._send(CMD_UPS_STATUS)))
            connected = parse_ups_connection(
                expect_value(CMD_UPS_CONNECTION, await self._send(CMD_UPS_CONNECTION))
            )
            return ups, connected
        except WattboxCommandUnsupported:
            return None, None

    async def _maybe_read_auto_reboot(self, caps: Capabilities) -> bool | None:
        if not caps.auto_reboot:
            return None
        try:
            return parse_auto_reboot(
                expect_value(CMD_AUTO_REBOOT, await self._send(CMD_AUTO_REBOOT))
            )
        except WattboxCommandUnsupported:
            return None

    async def _probe_capabilities(self) -> Capabilities:
        """Send each optional query once, recording which the device supports.

        For ``?OutletPowerStatus`` we probe with outlet 1 since the bare
        command returns ``#Error`` (it requires an argument).
        """
        return Capabilities(
            power_status=await self._is_supported(CMD_POWER_STATUS),
            outlet_power_status=await self._is_supported(f"{CMD_OUTLET_POWER_STATUS}=1"),
            ups=await self._is_supported(CMD_UPS_STATUS),
            auto_reboot=await self._is_supported(CMD_AUTO_REBOOT),
            mute=await self._is_supported(CMD_MUTE),
            safe_voltage=await self._is_supported(CMD_SAFE_VOLTAGE),
            scheduled_reboot=await self._is_supported(CMD_SCHEDULED_REBOOT),
        )

    async def _is_supported(self, command: str) -> bool:
        """True if the device answers without ``#Error``.

        Doesn't try to parse the value — capability detection only cares
        whether the command is recognised. Some commands (e.g.
        ``?OutletPowerStatus=N``) require args that the bare-name
        ``expect_value`` parser can't strip.
        """
        raw = await self._send(command)
        return not raw.strip().startswith("#Error")

    async def _query_str(self, command: str) -> str:
        return expect_value(command, await self._send(command))

    async def _query_int(self, command: str) -> int:
        return parse_int(expect_value(command, await self._send(command)), command=command)

    async def _send_set(self, line: str) -> None:
        """Send a `!Cmd` and ignore the ack.

        The ack may be ``OK`` *or* a ``~Cmd=value`` async push containing
        the new state; we pass ``allow_push=True`` so the transport
        doesn't discard the push as stale.
        """
        response = await self._send(line, allow_push=True)
        # Most !Cmds reply "OK"; a few reply with the ~push. Only #Error
        # means the caller asked for something the device cannot do.
        if response.strip().startswith("#Error"):
            raise WattboxCommandUnsupported(line)

    async def _send(self, line: str, *, allow_push: bool = False) -> str:
        """Send one line via the transport. Transparent one-shot reconnect."""
        if not self._transport.is_connected:
            await self._reconnect()
        try:
            return await self._transport.send_command(line, allow_push=allow_push)
        except WattboxConnectionError:
            _LOGGER.warning("connection to %s dropped mid-command; reconnecting once", self.host)
            await self._reconnect()
            return await self._transport.send_command(line, allow_push=allow_push)

    async def _reconnect(self) -> None:
        async with self._reconnect_lock:
            if self._transport.is_connected:
                return
            await self._transport.close()
            await self.connect()

    def _record_auth_failure(self) -> None:
        self._auth_failures += 1
        if self._auth_failures >= MAX_AUTH_FAILURES:
            self._locked_until = time.monotonic() + LOCKOUT_COOLDOWN_S
            _LOGGER.error(
                "client-side lockout armed for %s after %d failures",
                self.host,
                self._auth_failures,
            )


__all__: Sequence[str] = (
    "LOCKOUT_COOLDOWN_S",
    "MAX_AUTH_FAILURES",
    "WattboxClient",
)
