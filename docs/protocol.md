# WattBox `?Cmd` / `!Cmd` protocol reference

Built from three authoritative sources, in priority order:

1. **SnapAV WattBox Integration Protocol PDF v2.4** (`rev20210527`) — the
   vendor specification. Defines every command's wire format. Locally
   archived at `.workdir/wattbox-api/vendor-docs/wattbox-api-v2.4.pdf`
   (clone of [michaelahern/wattbox-api](https://github.com/michaelahern/wattbox-api)).
2. **`?Help` output** captured from real WB-250-IPW-2 (fw 2.9.0.2) and
   WB-800-IPVM-12 (fw 2.10.0.0) on 2026-05-19 — the device's own
   per-firmware command list.
3. **Live probe captures** — verified response shapes, including for
   commands the PDF leaves vague.

Where the PDF and live observation disagree, **live observation wins** and
the discrepancy is called out.

## Operational constraints (from vendor PDF)

- **Maximum 10 simultaneous connections** to the integration listener.
  HA may exceed this if multiple clients hammer the device — keep one
  long-lived connection per box.
- **SSH passwords limited to 13 characters** (PDF: "There is a 13-character
  limit on passwords used for SSH user credentials"). Telnet has no
  documented limit. If you can't SSH with a longer password, that's why.
- **Lockout is per-protocol** (verified live): Telnet and SSH have
  independent failure counters, the web UI is a third independent path.
- **OvrC overrides local listener config** (verified live): disabling
  Telnet locally can be silently re-enabled on the next OvrC sync.

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
| `!OutletSet=N,ACTION[,DELAY]` | ✓ | ✓ | `OK` or `~OutletStatus=...` | implemented |
| `!OutletNameSet=N,NAME` | ✓ | ✓ | `OK` (PDF). Renames outlet N. | documented |
| `!OutletNameSetAll={N1},{N2},...` | ✓ | ✓ | `OK` (PDF). Brackets required around each name; order matters from outlet 1. | documented |
| `!OutletPowerOnDelaySet=N,SECONDS` | ✓ | ✓ | `OK` (PDF). SECONDS in [1, 600]. | documented |
| `!OutletModeSet=N,MODE` | ✓ | ✓ | `OK` (PDF). MODE: 0=Enabled, 1=Disabled, 2=Reset Only. | documented |
| `!OutletRebootSet=OP,OP,...` | ✓ | ✓ | `OK` (PDF). One OP per outlet. OP: 0=Or (any host times out), 1=And (all hosts time out). | documented |

`ACTION` per PDF: **`ON` / `OFF` / `TOGGLE` / `RESET`**. Outlet indices are
1-based. `!OutletSet=0,RESET` power-cycles **all** outlets — use with care.
For `RESET`, the optional `DELAY` (1–600 seconds) overrides the outlet's
configured power-on delay for this reset only.

### Power metering

| Cmd | 250 | 800 | Response | Status |
|---|:-:|:-:|---|---|
| `?PowerStatus` | ✗ (`#Error`) | ✓ | `?PowerStatus=A, W, V, safe_flag`. PDF example: `60.00,600.00,110.00,1` | implemented |
| `?OutletPowerStatus=N` | ✗ | ✓ | `?OutletPowerStatus=N, W, A, V`. PDF example: `1,1.01,0.02,116.50` -> outlet 1, 1.01 W, 0.02 A, 116.50 V | implemented |

**Field-order gotcha.** Whole-device `?PowerStatus` is **A,W,V,flag** but
per-outlet `?OutletPowerStatus` is **N,W,A,V** — watts and amps swap
positions. This is the vendor's choice, documented in the PDF v2.4. The
library handles both correctly; if you're writing your own parser, mind
the difference.

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
| `!AutoRebootTimeoutSet=TIMEOUT,COUNT,PING_DELAY,REBOOT_ATTEMPTS` | ✓ | ✓ | `OK` (PDF). TIMEOUT [1,60]s; COUNT [1,10]; PING_DELAY [1,30]min; REBOOT_ATTEMPTS 0 (unlimited) or [1,10]. | documented |
| `!HostAdd=NAME,IP,{N,N,...}` | ✓ | ✓ | `OK` (PDF). NAME label; IP host/IP to ping; outlet array with required braces. | documented |
| `!ScheduleAdd={NAME},{N,N,...},{ACTION},{FREQ},{DAYS\|DATE},{TIME}` | ✓ | ✓ | `OK` (PDF). ACTION: 0=Off, 1=On, 2=Reset. FREQ: 0=Once, 1=Recurring. Recurring DAYS = 7-bool array [s,m,t,w,t,f,s] e.g. `{0,1,0,1,0,1,0}` for MWF. Once DATE = `{yyyy/mm/dd}`. TIME 24-hour `hh:mm`. | documented |

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
| `!NetworkSet=HOST[,IP,SUBNET,GATEWAY,DNS1,DNS2]` | ✓ | ✓ | `OK` then device reboots (PDF). DHCP: send HOST only. Static: HOST + IP + SUBNET + GATEWAY + DNS1 required; DNS2 optional (defaults to 8.8.8.8). | documented |

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
