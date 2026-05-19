# ha-wattbox

Async Python client and Home Assistant integration for SnapAV **WattBox** PDUs over the modern Telnet `?Cmd` / `!Cmd` protocol, with SSH preferred when available.

[![CI](https://github.com/nixxet/ha-wattbox/actions/workflows/ci.yml/badge.svg)](https://github.com/nixxet/ha-wattbox/actions/workflows/ci.yml)
[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## Why another WattBox integration?

Several exist, none cover the full real-world hardware mix cleanly:

| Existing | Hardware | Issues for a mixed deployment |
|---|---|---|
| `eseglem/hass-wattbox` | Most | Pulls scrapli + httpx for transports you may not need |
| `Vhern/ha-wattbox-300-700` | WB-300/700 HTTP only | Wrong protocol for legacy/800 series |
| `GarthDB/ha-wattbox` | WB-800 only | Hardcodes "Wattbox 800 Series"; single-device; throws on `#Error` |

**This project:** verified against live WB-250-IPW-2 and WB-800-IPVM-12 hardware. SSH-first, Telnet fallback. Capability detection (treats `#Error` responses as "feature not supported on this model" — entities for unsupported features simply aren't created). Multi-device. Lockout-aware (refuses to retry into the WattBox's auth lockout).

## Two pieces

- **`wattbox_local/`** — standalone async Python library. Usable independently of Home Assistant.
- **`custom_components/wattbox/`** — Home Assistant integration that wraps the library.

## Library usage

```python
import asyncio
from wattbox_local import WattboxClient

async def main() -> None:
    async with WattboxClient("10.10.10.156", "wattbox", "...") as wb:
        info = await wb.identify()
        print(info.model, info.firmware, info.outlet_count)
        snapshot = await wb.snapshot()
        print(snapshot)
        await wb.set_outlet(4, on=False)

asyncio.run(main())
```

## Verified hardware

| Model | Firmware | SSH | Telnet | Outlets | Power metering | UPS |
|---|---|---|---|---|---|---|
| WB-250-IPW-2 | 2.9.0.2 | open | open | 2 | not supported (`#Error`) | n/a |
| WB-800-IPVM-12 | 2.10.0.0 | open | open | 12 | yes | yes (battery, runtime, alarm) |

## Status

Pre-alpha. Building Phase 1 (library) first.

## Development

```bash
python -m venv .venv
.venv/Scripts/activate  # Windows
pip install -e ".[dev]"
pre-commit install
pytest                  # unit tests only
WATTBOX_LIVE=1 pytest -m live   # live integration tests
```

## License

MIT
