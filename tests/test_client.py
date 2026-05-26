"""WattboxClient orchestration tests.

We swap the transport out for a scripted fake so we can drive the client
through every code path — capability detection, lockout, reconnect — without
needing a real device.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import pytest

from wattbox_local import (
    AUTH_BACKOFF_SCHEDULE_S,
    LOCKOUT_COOLDOWN_S,
    BatteryHealth,
    Capabilities,
    OutletState,
    PowerStatus,
    UPSStatus,
    WattboxAuthError,
    WattboxClient,
    WattboxCommandUnsupported,
    WattboxConnectionError,
    WattboxLockoutError,
)
from wattbox_local.transport import Transport


class _ScriptedTransport(Transport):
    """A transport whose answers come from a per-command lookup or callable.

    ``responses`` may map a command string to either a literal response
    string or a callable returning one. A missing entry raises a clear
    KeyError — that means the test forgot to script something.
    """

    def __init__(
        self,
        responses: dict[str, str | Callable[[], str]] | None = None,
        *,
        host: str = "fake",
        port: int = 23,
        connect_error: Exception | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self._responses = responses or {}
        self._connect_error = connect_error
        self._connected = False
        self.sent: list[str] = []
        self.connects = 0
        self.closes = 0

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        self.connects += 1
        if self._connect_error is not None:
            err = self._connect_error
            self._connect_error = None  # one-shot; lets reconnect tests recover
            raise err
        self._connected = True

    async def close(self) -> None:
        self.closes += 1
        self._connected = False

    async def send_command(
        self, command: str, *, timeout: float = 5.0, allow_push: bool = False
    ) -> str:
        if not self._connected:
            raise WattboxConnectionError(f"not connected: {self.host}")
        self.sent.append(command)
        if command not in self._responses:
            raise AssertionError(f"test did not script a response for {command!r}")
        val = self._responses[command]
        return val() if callable(val) else val


# ---- canned responses for a "full feature" WB-800 ----------------------

WB800_RESPONSES: dict[str, str | Callable[[], str]] = {
    "?Model": "?Model=WB-800-IPVM-12",
    "?Firmware": "?Firmware=2.10.0.0",
    "?Hostname": "?Hostname=WattBox",
    "?ServiceTag": "?ServiceTag=ST211210871G842A",
    "?OutletCount": "?OutletCount=12",
    "?OutletStatus": "?OutletStatus=1,1,0,1,1,1,1,1,1,1,1,1",
    "?OutletName": (
        "?OutletName={Dish Hopper},{EA3},{Denon Rcv},{Cheap LED Strip},"
        "{Outlet 5},{Nvidia Shield},{WTC UPS},{Dream Machine},"
        "{Vivint Camera},{Hikvision NVR},{PS5},{Unifi 24 Switch}"
    ),
    "?PowerStatus": "?PowerStatus=0.27,81.88,123.69,0",
    # Per-outlet power: ?OutletPowerStatus=N,W,A,V (vendor PDF v2.4 field order)
    **{
        f"?OutletPowerStatus={i}": f"?OutletPowerStatus={i},{i * 1.5:.2f},{i * 0.02:.2f},123.82"
        for i in range(1, 13)
    },
    "?UPSStatus": "?UPSStatus=100,8,Good,False,160,True,False",
    "?UPSConnection": "?UPSConnection=1",
    "?AutoReboot": "?AutoReboot=0",
    "?Mute": "#Error",
    "?SafeVoltage": "#Error",
    "?ScheduledReboot": "#Error",
}


# ---- canned responses for a WB-250 (no metering, no UPS) ---------------

WB250_RESPONSES: dict[str, str | Callable[[], str]] = {
    "?Model": "?Model=WB-250-IPW-2",
    "?Firmware": "?Firmware=2.9.0.2",
    "?Hostname": "?Hostname=WattBoxGarage",
    "?ServiceTag": "?ServiceTag=ST211709791D842B",
    "?OutletCount": "?OutletCount=2",
    "?OutletStatus": "?OutletStatus=1,1",
    "?OutletName": "?OutletName={Outlet 1},{Outlet 2}",
    "?PowerStatus": "#Error",
    # WB-250 has no per-outlet metering either.
    "?OutletPowerStatus=1": "#Error",
    "?UPSStatus": "#Error",
    "?UPSConnection": "#Error",
    "?AutoReboot": "?AutoReboot=0",
    "?Mute": "#Error",
    "?SafeVoltage": "#Error",
    "?ScheduledReboot": "#Error",
}


# ---- identify / capabilities -------------------------------------------


async def test_identify_wb800_populates_capabilities() -> None:
    t = _ScriptedTransport(dict(WB800_RESPONSES))
    c = WattboxClient("10.0.0.1", "u", "p", transport=t)
    async with c:
        info = await c.identify()
    assert info.model == "WB-800-IPVM-12"
    assert info.firmware == "2.10.0.0"
    assert info.outlet_count == 12
    assert c.capabilities == Capabilities(
        power_status=True,
        outlet_power_status=True,
        ups=True,
        auto_reboot=True,
        mute=False,
        safe_voltage=False,
        scheduled_reboot=False,
    )


async def test_identify_wb250_marks_missing_caps_unsupported() -> None:
    t = _ScriptedTransport(dict(WB250_RESPONSES))
    c = WattboxClient("10.0.0.1", "u", "p", transport=t)
    async with c:
        info = await c.identify()
    assert info.model == "WB-250-IPW-2"
    assert info.outlet_count == 2
    assert c.capabilities == Capabilities(
        power_status=False,
        outlet_power_status=False,
        ups=False,
        auto_reboot=True,
        mute=False,
        safe_voltage=False,
        scheduled_reboot=False,
    )


async def test_identify_is_cached() -> None:
    t = _ScriptedTransport(dict(WB250_RESPONSES))
    c = WattboxClient("10.0.0.1", "u", "p", transport=t)
    async with c:
        first = await c.identify()
        # Clear the script — subsequent identify must NOT round-trip.
        t._responses.clear()
        second = await c.identify()
    assert first is second


# ---- snapshot ----------------------------------------------------------


async def test_snapshot_wb800_full_surface() -> None:
    t = _ScriptedTransport(dict(WB800_RESPONSES))
    async with WattboxClient("10.0.0.1", "u", "p", transport=t) as c:
        snap = await c.snapshot()
    assert snap.info.model == "WB-800-IPVM-12"
    assert len(snap.outlets) == 12
    assert snap.outlets[0] == OutletState(index=1, name="Dish Hopper", is_on=True)
    assert snap.outlets[2] == OutletState(index=3, name="Denon Rcv", is_on=False)
    assert snap.power == PowerStatus(
        current_amps=0.27, power_watts=81.88, voltage_volts=123.69, safe_voltage=False
    )
    # Per-outlet power: one entry per outlet, in index order, fields W/A/V.
    assert len(snap.outlet_power) == 12
    assert snap.outlet_power[0].outlet == 1
    assert snap.outlet_power[0].power_watts == 1.5
    assert snap.outlet_power[0].current_amps == 0.02
    assert snap.outlet_power[0].voltage_volts == 123.82
    assert snap.outlet_power[11].outlet == 12
    assert snap.ups == UPSStatus(
        battery_charge_pct=100,
        battery_load_pct=8,
        battery_health=BatteryHealth.GOOD,
        power_lost=False,
        battery_runtime_min=160,
        alarm_enabled=True,
        alarm_muted=False,
    )
    assert snap.ups_connected is True
    assert snap.auto_reboot is False


async def test_snapshot_wb250_no_power_no_ups() -> None:
    t = _ScriptedTransport(dict(WB250_RESPONSES))
    async with WattboxClient("10.0.0.1", "u", "p", transport=t) as c:
        snap = await c.snapshot()
    assert snap.info.model == "WB-250-IPW-2"
    assert len(snap.outlets) == 2
    assert snap.power is None
    assert snap.outlet_power == []  # WB-250: capability off -> empty list, no probes
    assert snap.ups is None
    assert snap.ups_connected is None
    assert snap.auto_reboot is False  # capability said yes, value is "off"


async def test_snapshot_skips_unsupported_commands_after_identify() -> None:
    """Capability detection means power/ups commands aren't even sent for WB-250."""
    t = _ScriptedTransport(dict(WB250_RESPONSES))
    async with WattboxClient("10.0.0.1", "u", "p", transport=t) as c:
        await c.snapshot()
    sent_after_identify = [cmd for cmd in t.sent if cmd.startswith("?")]
    # Capability probes happen once at identify. Snapshot must NOT re-send
    # the optional reads it knows will fail.
    assert sent_after_identify.count("?PowerStatus") == 1  # probe only
    assert sent_after_identify.count("?OutletPowerStatus=1") == 1  # probe only
    assert sent_after_identify.count("?UPSStatus") == 1
    assert sent_after_identify.count("?OutletStatus") == 1  # snapshot only


# ---- writes ------------------------------------------------------------


async def test_set_outlet_on_off() -> None:
    responses: dict[str, str | Callable[[], str]] = dict(WB800_RESPONSES)
    responses["!OutletSet=4,ON"] = "OK"
    responses["!OutletSet=4,OFF"] = "OK"
    t = _ScriptedTransport(responses)
    async with WattboxClient("10.0.0.1", "u", "p", transport=t) as c:
        await c.set_outlet(4, on=True)
        await c.set_outlet(4, on=False)
    assert "!OutletSet=4,ON" in t.sent
    assert "!OutletSet=4,OFF" in t.sent


async def test_reset_outlet() -> None:
    responses: dict[str, str | Callable[[], str]] = dict(WB800_RESPONSES)
    responses["!OutletSet=7,RESET"] = "OK"
    t = _ScriptedTransport(responses)
    async with WattboxClient("10.0.0.1", "u", "p", transport=t) as c:
        await c.reset_outlet(7)
    assert "!OutletSet=7,RESET" in t.sent


async def test_set_auto_reboot() -> None:
    responses: dict[str, str | Callable[[], str]] = dict(WB800_RESPONSES)
    responses["!AutoReboot=1"] = "OK"
    t = _ScriptedTransport(responses)
    async with WattboxClient("10.0.0.1", "u", "p", transport=t) as c:
        await c.set_auto_reboot(True)
    assert "!AutoReboot=1" in t.sent


async def test_set_outlet_rejects_zero_index() -> None:
    t = _ScriptedTransport(dict(WB800_RESPONSES))
    async with WattboxClient("10.0.0.1", "u", "p", transport=t) as c:
        with pytest.raises(ValueError):
            await c.set_outlet(0, on=True)


async def test_set_command_rejected_with_error() -> None:
    responses: dict[str, str | Callable[[], str]] = dict(WB800_RESPONSES)
    responses["!OutletSet=4,ON"] = "#Error"
    t = _ScriptedTransport(responses)
    async with WattboxClient("10.0.0.1", "u", "p", transport=t) as c:
        with pytest.raises(WattboxCommandUnsupported):
            await c.set_outlet(4, on=True)


# ---- lockout budget ----------------------------------------------------


async def test_auth_failure_arms_exponential_backoff() -> None:
    c = WattboxClient(
        "10.0.0.1",
        "u",
        "p",
        transport=_ScriptedTransport(connect_error=WattboxAuthError("bad")),
    )
    # Every auth failure arms a cooldown; subsequent connect attempts
    # during the cooldown raise WattboxLockoutError, not WattboxAuthError.
    for expected in AUTH_BACKOFF_SCHEDULE_S + (AUTH_BACKOFF_SCHEDULE_S[-1],):
        c._locked_until = 0.0  # simulate cooldown elapsed
        c._transport._connect_error = WattboxAuthError("bad")  # type: ignore[attr-defined]
        with pytest.raises(WattboxAuthError):
            await c.connect()
        remaining = c._locked_until - time.monotonic()
        assert 0 < remaining <= expected
        assert remaining > expected - 1  # allow small clock slack
    # While cooldown is active, connect refuses to retry.
    with pytest.raises(WattboxLockoutError):
        await c.connect()


async def test_device_side_lockout_arms_client_cooldown() -> None:
    c = WattboxClient(
        "10.0.0.1",
        "u",
        "p",
        transport=_ScriptedTransport(connect_error=WattboxLockoutError("locked")),
    )
    with pytest.raises(WattboxLockoutError):
        await c.connect()
    assert c.is_locked_out
    assert c._locked_until - time.monotonic() <= LOCKOUT_COOLDOWN_S


async def test_successful_connect_resets_failure_budget() -> None:
    t = _ScriptedTransport(dict(WB250_RESPONSES))
    c = WattboxClient("10.0.0.1", "u", "p", transport=t)
    # simulate two prior failures
    c._auth_failures = 2
    await c.connect()
    assert c._auth_failures == 0
    await c.close()


# ---- reconnect ---------------------------------------------------------


class _DroppingTransport(_ScriptedTransport):
    """Drops the connection once on the next send_command call."""

    def __init__(self, responses: dict[str, str | Callable[[], str]]) -> None:
        super().__init__(responses)
        self._drop_armed = False

    def arm_drop(self) -> None:
        self._drop_armed = True

    async def send_command(
        self, command: str, *, timeout: float = 5.0, allow_push: bool = False
    ) -> str:
        if self._drop_armed:
            self._drop_armed = False
            self._connected = False
            raise WattboxConnectionError("simulated drop")
        return await super().send_command(command, timeout=timeout, allow_push=allow_push)


async def test_reconnect_once_on_dropped_connection() -> None:
    t = _DroppingTransport(dict(WB800_RESPONSES))
    c = WattboxClient("10.0.0.1", "u", "p", transport=t)
    async with c:
        await c.identify()  # populates info + caps via several round-trips
        t.arm_drop()
        # Should transparently reconnect and re-issue.
        states = await c.outlet_states()
    assert len(states) == 12
    assert t.connects >= 2  # original + reconnect


async def test_reconnect_failure_propagates() -> None:
    t = _ScriptedTransport(dict(WB800_RESPONSES))
    c = WattboxClient("10.0.0.1", "u", "p", transport=t)
    async with c:
        await c.identify()
        # Force transport to look disconnected with a hard reconnect failure.
        await t.close()
        t._connect_error = WattboxConnectionError("network gone")  # type: ignore[attr-defined]
        with pytest.raises(WattboxConnectionError):
            await c.outlet_states()
