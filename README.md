# ha-wattbox

A complete, modern Home Assistant integration **and** standalone async Python library for SnapAV **WattBox** PDUs over the Telnet/SSH `?Cmd` / `!Cmd` protocol.

[![CI](https://github.com/nixxet/ha-wattbox/actions/workflows/ci.yml/badge.svg)](https://github.com/nixxet/ha-wattbox/actions/workflows/ci.yml)
[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![Home Assistant 2025.1+](https://img.shields.io/badge/Home%20Assistant-2025.1+-blue.svg)](https://www.home-assistant.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> Verified live against **WB-250-IPW-2** (fw 2.9.0.2) and **WB-800-IPVM-12 with UPS** (fw 2.10.0.0). Built from the vendor's [Integration Protocol PDF v2.4](https://github.com/michaelahern/wattbox-api/blob/main/vendor-docs/wattbox-api-v2.4.pdf), corrected with live captures wherever the spec disagrees with reality.

---

## What you get

### One integration, every shape of WattBox in your rack

- **Per-outlet switches** with the device's own outlet names
- **Per-outlet reset buttons** plus a device-level "Reset all"
- **Whole-device and per-outlet power metering** (W / A / V) — Energy Dashboard ready
- **UPS battery / load / runtime** sensors when a UPS is attached
- **UPS connectivity, mains-power, and alarm** binary sensors
- **API-lockout alerter** — surfaces the auth lockout as a `PROBLEM` binary sensor so you get notified instead of seeing silent entity-unavailable
- **Capability detection** — entities only get created for features the device actually has. A WB-250 won't get phantom power sensors. A WB-800 without a UPS won't get UPS sensors.

### Two transports, your call

| Transport | Port | When to use |
|---|---|---|
| **SSH** (default, recommended) | 22 | Encrypted. Works on every box. Requires the SSH password to be explicitly set in the WattBox web UI (≤13 chars per the vendor PDF). |
| **Telnet** | 23 | Cleartext fallback. No SSH-password setup needed. Lockout is more easily tripped — see [caveats](#operational-caveats-the-hard-won-stuff). |

---

## Quickstart — Home Assistant via HACS

1. Open **HACS → Integrations → ⋮ → Custom repositories**
2. URL `https://github.com/nixxet/ha-wattbox`, category **Integration**, click **Add**
3. Find **WattBox (local)** in the HACS store → **Download**
4. **Settings → System → Restart**
5. **Settings → Devices & services → + Add Integration → WattBox (local)**
6. Fill in:
   - **Host** — IP or DNS name of the WattBox
   - **Username** — default `wattbox`
   - **Password** — your integration password
   - **Transport** — `ssh` (recommended) or `telnet`
   - **Port** — leave blank; auto-defaults to 22 / 23
7. The integration probes the device, captures its **ServiceTag** as a stable unique-id, and creates one device with all its capability-appropriate entities.

Repeat step 5 for each additional WattBox — each becomes its own HA device.

---

## Library usage (without Home Assistant)

`wattbox_local` is a pure async library you can use anywhere.

```python
import asyncio
from wattbox_local import WattboxClient
from wattbox_local.transport import SSHTransport

async def main() -> None:
    transport = SSHTransport("10.10.10.156", "wattbox", "your-password")
    async with WattboxClient(
        "10.10.10.156", "wattbox", "your-password", transport=transport
    ) as wb:
        info = await wb.identify()
        print(f"{info.model} fw {info.firmware} — {info.outlet_count} outlets")

        snap = await wb.snapshot()
        for outlet in snap.outlets:
            print(f"  outlet {outlet.index} {outlet.name}: {'ON' if outlet.is_on else 'OFF'}")
        if snap.power:
            print(f"  PDU draw: {snap.power.power_watts:.1f} W @ {snap.power.voltage_volts:.1f} V")
        if snap.ups:
            print(f"  UPS: {snap.ups.battery_charge_pct}% charge, {snap.ups.battery_runtime_min} min runtime")

        # Flip outlet 4 off, then on
        await wb.set_outlet(4, on=False)
        await wb.set_outlet(4, on=True)

asyncio.run(main())
```

Higher-level operations are all on `WattboxClient`:

```python
await wb.toggle_outlet(3)                          # !OutletSet=3,TOGGLE
await wb.reset_outlet(5, delay=30)                 # 30-second power-cycle
await wb.reset_outlet(0)                           # reset ALL outlets
await wb.set_outlet_name(2, "Denon Receiver")      # rename from code
await wb.set_outlet_power_on_delay(7, 10)          # 10-sec boot delay
await wb.set_auto_reboot(True)
await wb.add_host("Modem", "8.8.8.8", outlets=[1, 2])
await wb.add_schedule(
    "Nightly modem reboot", outlets=[1], action=2,
    days=(False, True, True, True, True, True, False),  # M-F
    time="03:30",
)
```

Every command bounds-checks per the vendor PDF.

---

## Verified hardware

| Model | Firmware | SSH | Telnet | Outlets | Per-device metering | Per-outlet metering | UPS |
|---|---|---|---|---|---|---|---|
| **WB-250-IPW-2** | 2.9.0.2 | ✅ | ✅ | 2 | — | — | n/a |
| **WB-800-IPVM-12** | 2.10.0.0 | ✅ | ✅ | 12 | ✅ W/A/V | ✅ W/A/V | ✅ |

Capability detection handles the differences automatically. The same library / integration works on any WattBox in the OvrC firmware family.

---

## Why this exists

Other open-source WattBox integrations cover slivers of the hardware lineup:

| Existing | Strengths | Gaps |
|---|---|---|
| [`eseglem/hass-wattbox`](https://github.com/eseglem/hass-wattbox) | Maintained, broad pywattbox library | Pulls scrapli + httpx for transports you may not need |
| [`Vhern/ha-wattbox-300-700`](https://github.com/Vhern/ha-wattbox-300-700) | Clean WB-300/700 HTTP path | Wrong protocol for legacy & 800 series |
| [`GarthDB/ha-wattbox`](https://github.com/GarthDB/ha-wattbox) | Modern Telnet client, UPS support | Hardcodes "Wattbox 800 Series"; single-device; throws on `#Error` |

This project is built for the real-world mixed-model rack:
- **Capability-driven, not model-hardcoded** — `#Error` is treated as "feature not supported", not a fatal exception
- **Multi-device by design** — one HA device per physical WattBox, deduped by ServiceTag
- **Two transports behind one abstract base** — pick SSH or Telnet per box
- **Lockout-aware** — refuses to retry into the device's auth lockout (both the device's and a client-side budget)

---

## Architecture

```
ha-wattbox/
├── src/wattbox_local/                     # standalone library (source of truth)
│   ├── client.py             WattboxClient — capability detection, lockout budget, reconnect
│   ├── transport.py          Transport ABC + TelnetTransport + SSHTransport
│   ├── protocol.py           Pure encoders/parsers, no I/O
│   ├── models.py             Frozen dataclasses for everything
│   └── exceptions.py
├── custom_components/wattbox/             # HA integration
│   ├── wattbox_local/        Vendored copy of the library (sync via scripts/sync_lib.sh)
│   ├── __init__.py           async_setup_entry / async_unload_entry
│   ├── config_flow.py        UI setup + reauth + options
│   ├── coordinator.py        DataUpdateCoordinator[Snapshot]
│   ├── entity.py             Base entity, DeviceInfo keyed on ServiceTag
│   ├── switch.py             SwitchEntity per outlet
│   ├── button.py             Per-outlet reset + device-level "Reset all"
│   ├── sensor.py             Whole-device + per-outlet + UPS metrics
│   ├── binary_sensor.py      api_locked + UPS connectivity + mains power + alarm
│   └── translations/
├── tests/                                 # 146 unit tests, 12 live integration tests
├── docs/
│   ├── protocol.md           Canonical protocol reference (PDF + ?Help + live captures)
│   └── device-boot-log.md    Internal API surface captured from a device reboot trace
└── .github/workflows/ci.yml
```

### The library is the source of truth

`src/wattbox_local/` is the canonical library. `custom_components/wattbox/wattbox_local/` is a vendored copy so the integration is self-contained when installed via HACS. Run `scripts/sync_lib.sh` after changing the library to refresh the vendored copy.

---

## Operational caveats (the hard-won stuff)

These are documented because every one of them cost time to figure out live.

- **Lockout is per-protocol.** Telnet, SSH, and the web UI each track failed-auth attempts independently. A successful SSH login does not clear the Telnet lockout counter, and vice versa.
- **Session poisoning.** One bad auth attempt poisons the entire TCP session — even a subsequent correct attempt in the same session is rejected. The library never retries auth in the same session.
- **Lockout is timed and per-firmware.** Observed at ~5–15 minutes. The device emits `API is locked for X minutes and Y seconds.` so the library can parse the remaining duration. A reboot wipes all in-memory counters instantly.
- **OvrC overrides local config.** When OvrC cloud management is enabled, disabling Telnet (or SSH or SDDP) locally can be silently re-enabled on the next OvrC sync.
- **All outlets come back ON after reboot.** The boot log explicitly says so. Any "reboot the WattBox" service has to warn users their downstream gear will power-cycle on.
- **Connection cap = 10.** Per the vendor PDF, the integration listener accepts at most 10 simultaneous connections. Keep one long-lived connection per device.
- **SSH password ≤13 characters.** Vendor PDF constraint. Must be set explicitly via the web UI's account screen — the default integration password may not work for SSH until it's been set there.
- **Async pushes, not just replies.** When state changes (e.g. after `!OutletSet`), the device emits `~Cmd=value` notifications independent of any query. The transport accepts them as set acks but discards them when waiting for synchronous `?Cmd` replies.
- **Per-outlet power field order ≠ whole-device.** `?PowerStatus=A,W,V,flag` but `?OutletPowerStatus=N,W,A,V`. The library handles both correctly; if you're writing your own parser, beware.

See [`docs/protocol.md`](docs/protocol.md) for the complete command catalog and [`docs/device-boot-log.md`](docs/device-boot-log.md) for the internal C-API surface captured from a device's startup log.

---

## Status

| Layer | Status |
|---|---|
| `wattbox_local` Python library | ✅ Production-ready; 146 unit tests, 91 % branch coverage |
| Telnet transport | ✅ Verified live on WB-250 and WB-800 |
| SSH transport | ✅ Verified live on WB-250 and WB-800 |
| HA integration shell | ✅ Loadable via HACS; config flow + reauth + options + 4 platforms |
| Energy Dashboard entities | ✅ Power sensors carry the right device & state classes |
| Live integration tests | ✅ 12 tests, 5/5 SSH live tests pass on every box |
| GitHub Actions CI | ✅ ruff + mypy --strict + pytest on every push |
| Custom services (rename / schedule / host-add from HA UI) | 🟡 Library methods exist; HA-side services pending |
| Pre-commit hooks | ✅ |

---

## Development

```bash
# Library
python -m venv .venv
.venv/Scripts/activate                   # Windows; on Unix: source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install

# Test loop
pytest -q                                # 146 unit tests, no hardware
WATTBOX_LIVE=1 pytest -q                 # adds live integration tests against ~/.wb-creds

# Lint / type
ruff check .
ruff format .
mypy src
```

### Live test credentials

Live tests look for `~/.wb-creds` (preferred) or `tests/integration/.creds` (gitignored). INI format:

```ini
[10.10.10.150]
username = wattbox
password = ...
test_outlet = 2
```

`test_outlet` is the outlet number the toggle round-trip will exercise. Pick a non-critical one — tests always restore the prior state in a `finally` block.

### Syncing the vendored library

After editing `src/wattbox_local/`:

```bash
bash scripts/sync_lib.sh
```

---

## License

MIT — see [LICENSE](LICENSE).

## Credits

- [@michaelahern](https://github.com/michaelahern) for [wattbox-api](https://github.com/michaelahern/wattbox-api) — TypeScript reference + the canonical vendor PDF
- SnapAV / Snap One for publishing the [WattBox Integration Protocol v2.4](https://github.com/michaelahern/wattbox-api/blob/main/vendor-docs/wattbox-api-v2.4.pdf)
- Prior HA integration authors ([@eseglem](https://github.com/eseglem), [@GarthDB](https://github.com/GarthDB), [@Vhern](https://github.com/Vhern)) whose work informed the design even where this project ultimately took a different path
