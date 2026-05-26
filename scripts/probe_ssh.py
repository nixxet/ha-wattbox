"""Live SSH probe against the three house WattBoxes.

Reads credentials from .env at repo root (gitignored). Prints
model / firmware / outlet count / lockout status per host. Does
not toggle outlets — read-only. Safe to run repeatedly.

Usage:  python scripts/probe_ssh.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))


def _load_env() -> None:
    env_path = REPO_ROOT / ".env"
    if not env_path.is_file():
        sys.exit(f"missing {env_path}; copy .env.example and fill it in")
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


async def _probe(host: str) -> None:
    from wattbox_local import WattboxClient, WattboxError
    from wattbox_local.transport import SSHTransport

    user = os.environ["WATTBOX_USERNAME"]
    pw = os.environ["WATTBOX_PASSWORD"]
    transport = SSHTransport(host, user, pw, port=22)
    client = WattboxClient(host=host, username=user, password=pw, transport=transport)
    print(f"\n=== {host} ===")
    try:
        async with client:
            info = await client.identify()
            snap = await client.snapshot()
            print(f"  model:    {info.model}")
            print(f"  firmware: {info.firmware}")
            print(f"  hostname: {info.hostname}")
            print(f"  service:  {info.service_tag}")
            print(f"  outlets:  {len(snap.outlets)}")
            for o in snap.outlets:
                print(f"    [{o.index}] {o.name!r}  {'ON' if o.is_on else 'OFF'}")
    except WattboxError as e:
        print(f"  ERROR: {type(e).__name__}: {e}")


async def main() -> None:
    _load_env()
    hosts = [
        os.environ.get("WATTBOX_150_HOST"),
        os.environ.get("WATTBOX_156_HOST"),
        os.environ.get("WATTBOX_152_HOST"),
    ]
    for h in hosts:
        if h:
            await _probe(h)


if __name__ == "__main__":
    asyncio.run(main())
