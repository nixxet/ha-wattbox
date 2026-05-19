"""Live integration tests against real WattBox hardware.

Opt-in only. ``WATTBOX_LIVE=1 pytest -m live`` to run.

Each test:
- toggles only the outlet declared as ``test_outlet`` in ~/.wb-creds;
- always restores the prior state in a finally block;
- runs serially per device (no concurrent connections to the same box);
- assumes the device-side lockout is NOT currently armed.
"""

from __future__ import annotations

import asyncio

import pytest

from wattbox_local import (
    BatteryHealth,
    WattboxClient,
)

from .conftest import DeviceCreds

pytestmark = pytest.mark.live


async def _toggle_and_restore(client: WattboxClient, outlet: int) -> None:
    """Flip `outlet` to the opposite state, verify, restore."""
    snap = await client.snapshot()
    target = next(o for o in snap.outlets if o.index == outlet)
    original = target.is_on

    await client.set_outlet(outlet, on=not original)
    await asyncio.sleep(0.6)  # WattBox firmware needs a beat to settle state
    after = await client.outlet_states()
    flipped = next(o for o in after if o.index == outlet)
    try:
        assert flipped.is_on is (not original), (
            f"outlet {outlet} did not flip: was {original}, set to {not original}, got {flipped.is_on}"
        )
    finally:
        await client.set_outlet(outlet, on=original)
        await asyncio.sleep(0.6)
        restored = await client.outlet_states()
        assert next(o for o in restored if o.index == outlet).is_on is original


# ---- WB-250 (10.10.10.150) ---------------------------------------------


async def test_wb250_garage_identify_and_outlets(wb250_creds: DeviceCreds) -> None:
    async with WattboxClient(wb250_creds.host, wb250_creds.username, wb250_creds.password) as wb:
        info = await wb.identify()
        assert info.model == "WB-250-IPW-2"
        assert info.firmware.startswith("2.9.")
        assert info.outlet_count == 2
        caps = wb.capabilities
        assert caps is not None
        assert caps.power_status is False, "WB-250 should report PowerStatus unsupported"
        assert caps.ups is False
        snap = await wb.snapshot()
        assert snap.power is None
        assert snap.ups is None
        assert len(snap.outlets) == 2


async def test_wb250_garage_set_outlet_roundtrip(wb250_creds: DeviceCreds) -> None:
    async with WattboxClient(wb250_creds.host, wb250_creds.username, wb250_creds.password) as wb:
        await _toggle_and_restore(wb, wb250_creds.test_outlet)


# ---- WB-800 (10.10.10.156, with UPS) ----------------------------------


async def test_wb800_identify_full_capabilities(wb800_creds: DeviceCreds) -> None:
    async with WattboxClient(wb800_creds.host, wb800_creds.username, wb800_creds.password) as wb:
        info = await wb.identify()
        assert info.model == "WB-800-IPVM-12"
        assert info.outlet_count == 12
        caps = wb.capabilities
        assert caps is not None
        assert caps.power_status is True
        assert caps.ups is True
        assert caps.auto_reboot is True


async def test_wb800_full_snapshot_returns_real_values(wb800_creds: DeviceCreds) -> None:
    async with WattboxClient(wb800_creds.host, wb800_creds.username, wb800_creds.password) as wb:
        snap = await wb.snapshot()
        assert snap.power is not None
        assert 90.0 <= snap.power.voltage_volts <= 135.0
        assert snap.power.current_amps >= 0.0
        assert snap.ups is not None
        assert 0 <= snap.ups.battery_charge_pct <= 100
        assert snap.ups.battery_health in BatteryHealth
        assert snap.ups_connected is True
        assert len(snap.outlets) == 12
        for outlet in snap.outlets:
            assert outlet.name
            assert outlet.index in range(1, 13)
        # Per-outlet power: one entry per outlet, real voltage on each.
        assert len(snap.outlet_power) == 12
        for op in snap.outlet_power:
            assert op.outlet in range(1, 13)
            assert 90.0 <= op.voltage_volts <= 135.0
            assert op.power_watts >= 0.0
            assert op.current_amps >= 0.0


async def test_wb800_set_outlet_roundtrip(wb800_creds: DeviceCreds) -> None:
    async with WattboxClient(wb800_creds.host, wb800_creds.username, wb800_creds.password) as wb:
        await _toggle_and_restore(wb, wb800_creds.test_outlet)


# ---- WB-250 second (10.10.10.152) -------------------------------------


async def test_wb250b_identify(wb250b_creds: DeviceCreds) -> None:
    """Only runs once .152's lockout cooldown has cleared."""
    async with WattboxClient(wb250b_creds.host, wb250b_creds.username, wb250b_creds.password) as wb:
        info = await wb.identify()
        assert info.model == "WB-250-IPW-2"
        assert info.outlet_count == 2


async def test_wb250b_set_outlet_roundtrip(wb250b_creds: DeviceCreds) -> None:
    async with WattboxClient(wb250b_creds.host, wb250b_creds.username, wb250b_creds.password) as wb:
        await _toggle_and_restore(wb, wb250b_creds.test_outlet)
