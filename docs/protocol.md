# WattBox `?Cmd` / `!Cmd` protocol reference

Authoritative source for the modern Telnet/SSH ASCII control protocol used by
WattBox WB-250-IPW-2 (fw 2.9.0.2) and WB-800-IPVM-12 (fw 2.10.0.0). Built from
each device's own `?Help` output plus the response shapes captured live on
2026-05-19.

## Session lifecycle

After connect, the device sends:

```
Please Login to Continue
Username:
```

Send `<user>\r\n`, then on `Password:` send `<pw>\r\n`. On success:

```
Successfully Logged In!
```

On failure: `Invalid Login`. Repeated failures lock the API:

```
API is locked for 4 minutes and 39 seconds.
```

Lockout is **per-protocol** — Telnet (port 23) and SSH (port 22) have
independent failure counters. The web UI is a third independent path.

Disconnect cleanly with `!Exit` before closing the socket. The library's
`TelnetTransport.close()` does this automatically.

## Wire format

- One command per line, `\r\n` or `\n` terminated.
- Query: `?Cmd` -> `?Cmd=value`.
- Query with arg: `?Cmd=arg` -> `?Cmd=arg,value...` (e.g. `?OutletPowerStatus=4`).
- Set: `!Cmd=arg` -> `OK` *or* `~RelatedCmd=newvalue` (async push echoing the
  side-effect — see "Async push notifications" below).
- Unsupported command: `#Error`.
- Async push (unsolicited state change): `~Cmd=value`.

### Async push notifications (the `~` prefix)

The device emits `~Cmd=value` whenever state changes — including as the only
acknowledgement of some set commands (notably `!OutletSet` is acked by
`~OutletStatus=...`). The library handles this by:

1. **Discarding** stale `~Cmd` pushes from prior state changes when reading
   the response to a `?Cmd` query.
2. **Accepting** the `~Cmd` push as a valid ack when sending a `!Cmd`
   (`Transport.send_command(..., allow_push=True)`).

### Response-name matching

Slow commands (`?UPSVoltageRange`, `?Https`) sometimes reply after our timeout
fires. To prevent a late reply polluting the next command's slot, the transport
matches each inbound `?Cmd=...` response against the command name it sent.
Mismatches are discarded.

## Command catalog

Source: each device's `?Help` output. Columns:

- **Cmd**: the exact spelling (case matters).
- **WB-250**: present in WB-250-IPW-2 fw 2.9.0.2 ?
- **WB-800**: present in WB-800-IPVM-12 fw 2.10.0.0 ?
- **Verified**: response shape captured live.
- **Status**: `implemented` (used by `WattboxClient`), `documented` (parseable
  but not yet wired into the high-level client), `unverified` (in `?Help` but
  not yet probed).

### Identity / capability

| Cmd | 250 | 800 | Response | Status |
|---|:-:|:-:|---|---|
| `?Firmware` | ✓ | ✓ | `?Firmware=2.10.0.0` | implemented |
| `?Model` | ✓ | ✓ | `?Model=WB-800-IPVM-12` | implemented |
| `?ServiceTag` | ✓ | ✓ | `?ServiceTag=ST211210871G842A` | implemented |
| `?Hostname` | ✓ | ✓ | `?Hostname=WattBox` | implemented |
| `?OutletCount` | ✓ | ✓ | `?OutletCount=12` | implemented |
| `?Help` | ✓ | ✓ | multi-line list of available commands | documented |

### Outlet state and control

| Cmd | 250 | 800 | Response | Status |
|---|:-:|:-:|---|---|
| `?OutletStatus` | ✓ | ✓ | `?OutletStatus=1,1,0,...` (one per outlet) | implemented |
| `?OutletName` | ✓ | ✓ | `?OutletName={Dish Hopper},{EA3},...` (brace-delimited) | implemented |
| `?OutletPowerOnDelay` | ✓ | ✓ | `?OutletPowerOnDelay=11,4,10,31,5,12,2,7,8,9,30,6` (sec per outlet) | documented |
| `!OutletSet=N,ACTION` | ✓ | ✓ | `OK` or `~OutletStatus=...` | implemented |
| `!OutletNameSet=N,NAME` | ✓ | ✓ | unverified — rename outlet N | unverified |
| `!OutletNameSetAll=N1,N2,...` | ✓ | ✓ | unverified — bulk rename | unverified |
| `!OutletPowerOnDelaySet=N,SECONDS` | ✓ | ✓ | unverified — per-outlet boot delay | unverified |
| `!OutletModeSet` | ✓ | ✓ | unverified — outlet mode (locked/unlocked) | unverified |
| `!OutletRebootSet` | ✓ | ✓ | unverified — outlet reboot behaviour | unverified |

Outlet indices are **1-based**. `ACTION` is `ON` / `OFF` / `RESET`.
`!OutletSet=0,RESET` power-cycles **all** outlets — use with care.

### Power metering

| Cmd | 250 | 800 | Response | Status |
|---|:-:|:-:|---|---|
| `?PowerStatus` | ✗ (`#Error`) | ✓ | `?PowerStatus=0.27,81.88,123.69,0` -> `A, W, V, safe_flag` | implemented |
| `?OutletPowerStatus=N` | ✗ | ✓ | `?OutletPowerStatus=N,A,W,V` | documented |

`?OutletPowerStatus` requires the outlet index. Bare `?OutletPowerStatus`
returns `#Error`; valid range is 1..N.

### UPS (only meaningful when a UPS is attached)

| Cmd | 250 | 800 | Response | Status |
|---|:-:|:-:|---|---|
| `?UPSConnection` | ✗ | ✓ | `?UPSConnection=1` (1=connected, 0=not) | implemented |
| `?UPSStatus` | ✗ | ✓ | `?UPSStatus=100,8,Good,False,160,True,False` -> `charge%, load%, health, power_lost, runtime_min, alarm_on, alarm_muted` | implemented |
| `?UPSRunStats` | ✗ | ✓ | `?UPSRunStats=-1,-1,-1,-1` (all -1 until first event) | documented |
| `?UPSVoltageRange` | ✗ | ✓ | `?UPSVoltageRange=N` (N = Normal range?) | unverified |
| `!UPSVoltageRange=...` | ✗ | ✓ | unverified — set the UPS voltage thresholds | unverified |

### Auto-reboot / scheduling / monitoring

| Cmd | 250 | 800 | Response | Status |
|---|:-:|:-:|---|---|
| `?AutoReboot` | ✓ | ✓ | `?AutoReboot=0` (0=off, 1=on) | implemented |
| `!AutoReboot=0\|1` | ✓ | ✓ | `OK` | implemented |
| `!AutoRebootTimeoutSet=...` | ✓ | ✓ | unverified — auto-reboot timeout/hit-count | unverified |
| `!HostAdd=...` | ✓ | ✓ | unverified — add ping-monitored host (the `wbPingHosts` callback from the boot log) | unverified |
| `!ScheduleAdd=...` | ✓ | ✓ | unverified — add a scheduled reboot | unverified |

There is **no `?Schedule(s)` / `?Hosts` query** on tested firmware — all such
probes returned `#Error`. Schedules can be added but apparently not listed via
this API. Likely listed only through the web UI / OvrC.

### Listener / protocol management

| Cmd | 250 | 800 | Response | Status |
|---|:-:|:-:|---|---|
| `?GetTelnet` | ✓ | ✓ | `?GetTelnet=1` (1=enabled) | documented |
| `!SetTelnet=0\|1` | ✓ | ✓ | unverified | unverified |
| `?GetSSH` | ✓ | ✓ | `?GetSSH=1` | documented |
| `!SetSSH=0\|1` | ✓ | ✓ | unverified | unverified |
| `!SetSDDP=0\|1` | ✓ | ✓ | unverified — SnapAV discovery protocol | unverified |
| `?Https` | ✓ | ✓ | `?Https=False` | documented |
| `!WebServerSet=...` | ✓ | ✓ | unverified | unverified |

**Caveat:** when OvrC management is enabled, OvrC re-asserts its preferred
state for these listeners. Disabling Telnet locally can be silently undone
on the next OvrC sync.

### Device administration (dangerous)

| Cmd | 250 | 800 | Notes |
|---|:-:|:-:|---|
| `!Reboot` | ✓ | ✓ | Soft-reboots the WattBox. **All outlets revert to ON on next boot** (see `device-boot-log.md`). |
| `!AccountSet` | ✓ | ✓ | Changes the Telnet/SSH password. Lock yourself out if wrong. |
| `!FirmwareUpdate` | ✓ | ✓ | Triggers firmware update. |
| `!NetworkSet` | ✓ | ✓ | Sets network config (IP/DHCP/etc). |
| `!Exit` | ✓ | ✓ | Clean session disconnect. `TelnetTransport.close()` sends this. |

### Network

| Cmd | 250 | 800 | Response | Status |
|---|:-:|:-:|---|---|
| `?NetworkGet` | ✓ | ✓ | unverified — read network config | unverified |

### Faceplate / environmental adapter (WB-800 only)

| Cmd | 250 | 800 | Response | Status |
|---|:-:|:-:|---|---|
| `?FaceplatePresent` | ✗ | ✓ | `?FaceplatePresent=0` (0=none) | documented |
| `?FaceplatePort` | ✗ | ✓ | `?FaceplatePort=0` | documented |
| `?FaceplateUUID` | ✗ | ✓ | `?FaceplateUUID=` (empty when no faceplate) | documented |
| `?FaceplateLedLevel` | ✗ | ✓ | `?FaceplateLedLevel=-1` (-1 when no faceplate) | documented |
| `!FaceplateLedLevelSet=...` | ✗ | ✓ | unverified — front-panel LED brightness | unverified |
| `?AdapterSensorData` | ✗ | ✓ | `?AdapterSensorData=None` (no adapter attached) | documented |
| `?AdapterConfig` | ✗ | ✓ | `#Error` without args; needs an arg | unverified |
| `?AdapterServiceTags` | ✗ | ✓ | `?AdapterServiceTags=None` | documented |
| `!AdapterConfig=...` | ✗ | ✓ | unverified | unverified |

### Internal / unsupported on tested firmware

These commands appear in `?Help` on neither model OR return `#Error` despite
being plausible:

- `?ScheduleList`, `?Schedules`, `?Schedule=N`, `?Hosts`, `?HostList` — all
  `#Error`. Cannot enumerate existing schedules / hosts via this API.
- `?Mute`, `?SafeVoltage`, `?ScheduledReboot` (legacy names) — all `#Error`.
- `?OutletPowerStatus` (no arg) — `#Error`. Must specify the outlet index.
