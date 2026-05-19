# ha-wattbox

Personal-context project. Standalone async Python library + Home Assistant integration for SnapAV WattBox PDUs. Workspace conventions live in `C:\ClaudeProjects\CLAUDE.md`.

## Layout

- `src/wattbox_local/` — standalone library (importable as `wattbox_local`)
- `custom_components/wattbox/` — Home Assistant integration shell that wraps the library
- `tests/` — pytest unit tests (no hardware required)
- `tests/integration/` — live tests against real hardware (opt-in via `WATTBOX_LIVE=1`)

## Verified hardware (probed live 2026-05-19)

| IP | Model | Hostname | Notes |
|---|---|---|---|
| 10.10.10.150 | WB-250-IPW-2 (fw 2.9.0.2) | WattBoxGarage | 2 outlets, no power metering |
| 10.10.10.156 | WB-800-IPVM-12 (fw 2.10.0.0) | WattBox | 12 outlets, power metering, UPS attached |
| 10.10.10.152 | WB-250-IPW-2 (presumed) | TBD | User-confirmed creds; verify outlet count once unlocked |

Credentials are loaded from `~/.wb-creds` (preferred — outside the repo) or `tests/integration/.creds` (gitignored). Never commit credentials.

## Lockout rule

These boxes lock out after a small number of failed auth attempts. Lockout is timed (15+ min). **The library MUST track failure budgets and refuse to retry into a lockout.** Do not test auth in a loop; do not retry on auth failure.

## Protocol

Modern Telnet ASCII `?Cmd` / `!Cmd` (also reachable over SSH on these models). One command per line. `#Error` response means the command is not supported on this model/firmware — treat as a capability gap, not an error. See `docs/protocol.md` for the full verified command set.

## Standards

- Python 3.13+ only. No back-compat shims.
- Strict typing (`mypy --strict`).
- `ruff` for lint + format. Pre-commit hooks enforce.
- No live tests in CI unit suite. Live tests are explicit and rate-limited.

## Git identity

Personal repo. `nixxet` / `robert@thenall.com` per workspace defaults. Remote must be a private GitHub repo when added.

---

*ha-wattbox | WattBox PDU library + HA integration | Active | 2026-05-19*
