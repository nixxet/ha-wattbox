# WattBox boot log intelligence

Captured 2026-05-19 during a planned reboot of `10.10.10.152` (WB-250-IPW-2,
fw 2.9.0.2). The boot trace is verbose enough to teach us things the
`?Cmd`/`!Cmd` surface alone doesn't reveal.

## Identifiers (this unit)

| Field | Value |
|---|---|
| Service Tag | `ST211709801D842B` |
| MAC | `14:3F:C3:01:53:55` |
| HAL version | `v2.0.0.4` |
| Sequential to `.150` | `.150` ST `…79`, `.152` ST `…80` — bought together |

## Architecture confirmed

- Runs **OvrC Runtime** (same firmware family across all WattBox models — matches the `Server: OvrC Embedded Server` HTTP header observed on `.150`/`.156`).
- 4 server workers listen (web UI + Telnet + SSH + the Control API).
- Web UI on port 80 (`[LOCAL UI] Starting on 80...`).
- **Wattbox Control API** initialized at `control_api.c:3207` — this is the parser behind `?Cmd`/`!Cmd`.
- SDDP (Simple Device Discovery Protocol — Control4 / SnapAV's mDNS-ish discovery) is running. We could use this for HA auto-discovery in Phase 2.

## Internal C callback inventory (what the device can do)

These are the underlying function names the device registers at boot. Each wraps to one or more user-facing API commands. **Bold rows are commands we haven't found yet in the Telnet `?Cmd` surface** — worth probing.

| Callback | Likely Telnet equivalent | Notes |
|---|---|---|
| `wbTurnOutletOn` / `wbTurnOutletOff` | `!OutletSet=N,ON/OFF` | Verified |
| `wbResetOutlet` / `wbResetOutlets` / `wbResetAllOutlets` | `!OutletSet=N,RESET` (and `=0,RESET`?) | Verified for single |
| `wbGetStatus` | `?OutletStatus` / `?PowerStatus` | Verified |
| `wbGetAllOutletsConfig` / `wbSetAllOutletsConfig` | **unknown** | Per-outlet config (boot delay, name, etc.) |
| `wbEnableAutoReboot` / `wbDisableAutoReboot` | `!AutoReboot=1/0` | Verified |
| **`wbGetTimeoutConfig` / `wbSetTimeoutConfig`** | unknown | Probably auto-reboot timeout / hit count |
| **`wbGetWebsitesConfig` / `wbSetWebsitesConfig`** | unknown | Auto-reboot ping-host list (firmware-native ping monitoring!) |
| **`wbPingHosts`** | unknown | On-demand ping of monitored hosts |
| **`wbGetSchedules` / `wbSetSchedules`** | `?ScheduledReboot` returns `#Error` on tested fw, but the function exists | Schedule API likely uses a different command name; needs probing |
| `wbGetConnectionStatus` | unknown | Cloud/network connection state — could surface as a binary sensor |
| `wbGetControlApiConfig` / `wbSetControlApiConfig` | unknown | API config — possibly the lockout threshold |
| `dxGetAbout`, `dxGetTimeSettings`, `dxSetNetworkSettings`, `dxUpdateFirmware`, `dxResetDevice`, `dxFactoryDefault`, ... | n/a from Telnet | OvrC cloud-side "dx" prefix; not part of the local control API |

The `wb*` set is the local control API. The `dx*` set is the OvrC cloud bridge. **Only `wb*` is interesting for our integration.**

## Safety: outlet behavior on reboot

```
SOFTWARE reboot, do outlet init
INIT: SET OUTLET 1 DATA to HIGH
INIT: SET OUTLET 2 DATA to HIGH
SWITCHING OUTLET 1 [ON]
SWITCHING OUTLET 2 [ON]
```

**On any reboot the device unconditionally drives all outlets ON.** Previous outlet state is *not* restored on reboot. If we ever expose a "reboot the WattBox" service in HA, the docs must warn that downstream gear will power-cycle on. (For WB-800 with per-outlet boot delays this is probably less abrupt but the *initial* state is still ON.)

## Lockout banner format

The exact text emitted when the API is locked:

```
API is locked for 4 minutes and 39 seconds.
```

The countdown is parsed by `protocol.parse_lockout_remaining_s`. The current matcher in `LOGIN_LOCKED` is the substring `"is locked for"`, robust to future minor wording tweaks.

## Phase-2 probes worth running (later)

Try these against a non-prod outlet on `.156`. Look for `?Cmd=...` style responses; map any `#Error`s to "unsupported on this firmware":

- `?TimeoutConfig`, `!TimeoutConfig=...`
- `?WebsitesConfig`, `!WebsitesConfig=...`
- `?Schedules`, `?Schedule=N`, `?ScheduleCount`
- `?ConnectionStatus`, `?CloudStatus`
- `?ControlApiConfig`

If any of these come back with real data, we expand the protocol vocabulary and add corresponding HA entities.
