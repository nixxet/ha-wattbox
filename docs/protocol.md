# WattBox `?Cmd` / `!Cmd` protocol (verified)

Verified live against WB-250-IPW-2 fw 2.9.0.2 and WB-800-IPVM-12 fw 2.10.0.0 on 2026-05-19.

## Transport

- **SSH** on port 22 (preferred — encrypted).
- **Telnet** on port 23 (fallback — cleartext).
- Default integration credentials: `wattbox` / `<set per device>`. The CLI/web UI password is the same as the SSH/Telnet password.

## Session

After connect, the device emits:

```
Please Login to Continue
Username:
```

Send username + `\r\n`. Then:

```
Password:
```

Send password + `\r\n`. On success:

```
Successfully Logged In!
```

On failure: `Invalid Login`. After repeated failures the device returns `API locked` (timed lockout — observed at least 15+ min).

## Commands

All commands are line-terminated. Query (`?`) returns `?Cmd=value`. Set (`!`) acknowledges with the same prefix or `OK`. Unsupported commands return `#Error`.

### Identity / capability

| Command | Example response | Notes |
|---|---|---|
| `?Firmware` | `?Firmware=2.10.0.0` | |
| `?Model` | `?Model=WB-800-IPVM-12` | |
| `?ServiceTag` | `?ServiceTag=ST211210871G842A` | Stable per device |
| `?Hostname` | `?Hostname=WattBox` | |
| `?OutletCount` | `?OutletCount=12` | |

### Outlet state

| Command | Example response |
|---|---|
| `?OutletStatus` | `?OutletStatus=1,1,1,1,1,1,1,1,1,1,1,1` (1 = on, 0 = off) |
| `?OutletName` | `?OutletName={Dish Hopper},{EA3},{Denon Rcv},{Cheap LED Strip},...` |
| `!OutletSet=N,ACTION` | ACTION is `ON` / `OFF` / `RESET` |

### Power metering

| Command | Example response | Available on |
|---|---|---|
| `?PowerStatus` | `?PowerStatus=0.27,81.88,123.69,0` → (current_A, power_W, voltage_V, safe_voltage_flag) | WB-800 (returns `#Error` on WB-250) |

### UPS (WB-800 with battery)

| Command | Example response | Field order |
|---|---|---|
| `?UPSStatus` | `?UPSStatus=100,8,Good,False,160,True,False` | battery_charge_pct, battery_load_pct, battery_health, power_lost, battery_runtime_min, alarm_enabled, alarm_muted |
| `?UPSConnection` | `?UPSConnection=1` | 1 = connected, 0 = not |

### Auto-reboot / scheduling

| Command | Example response | Notes |
|---|---|---|
| `?AutoReboot` | `?AutoReboot=0` | 0 = off, 1 = on |
| `!AutoReboot=N` | `OK` | |
| `?Mute` | `#Error` on tested firmware | |
| `?SafeVoltage` | `#Error` on tested firmware | |
| `?ScheduledReboot` | `#Error` on tested firmware | |

## Capability rules

The library calls each query command **once at identify time** and records which return `#Error`. Subsequent calls to unsupported commands raise `WattboxCommandUnsupported` immediately without round-tripping the device. The HA integration uses the capability map to decide which entities to create.

## Lockout handling

- The client tracks consecutive auth failures per host (in-process).
- After 3 consecutive failures, refuse new connections for 20 minutes (`is_locked_out=True`).
- Lockout is also detected from the wire when the device emits `API locked`.
- HA exposes `binary_sensor.<device>_api_locked` so the user is alerted instead of the integration silently retrying into the lockout.
