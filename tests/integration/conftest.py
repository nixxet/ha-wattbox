"""Shared fixtures for live integration tests.

Live tests are skipped unless the user explicitly opts in by setting
``WATTBOX_LIVE=1`` in the environment. This keeps CI safe (no hardware
needed) and prevents accidental lockouts during normal unit runs.

Credentials are loaded from, in order:
1. ``~/.wb-creds``                              (preferred — outside repo)
2. ``tests/integration/.creds``                 (fallback — gitignored)

Either file is INI-format:

    [10.10.10.156]
    username = wattbox
    password = ...
    test_outlet = 4
"""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass
from pathlib import Path

import pytest

LIVE_ENV_VAR = "WATTBOX_LIVE"
CREDS_PATHS = (
    Path.home() / ".wb-creds",
    Path(__file__).parent / ".creds",
)


@dataclass(frozen=True, slots=True)
class DeviceCreds:
    host: str
    username: str
    password: str
    test_outlet: int


def _load_creds() -> dict[str, DeviceCreds]:
    cp = configparser.ConfigParser()
    found = False
    for path in CREDS_PATHS:
        if path.is_file():
            cp.read(path)
            found = True
    if not found:
        return {}
    out: dict[str, DeviceCreds] = {}
    for host in cp.sections():
        section = cp[host]
        out[host] = DeviceCreds(
            host=host,
            username=section["username"],
            password=section["password"],
            test_outlet=int(section["test_outlet"]),
        )
    return out


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get(LIVE_ENV_VAR) == "1":
        return
    skip_live = pytest.mark.skip(reason=f"set {LIVE_ENV_VAR}=1 to run live hardware tests")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


@pytest.fixture(scope="session")
def creds() -> dict[str, DeviceCreds]:
    c = _load_creds()
    if os.environ.get(LIVE_ENV_VAR) == "1" and not c:
        pytest.skip(
            f"{LIVE_ENV_VAR}=1 but no creds found at {CREDS_PATHS}; "
            "create ~/.wb-creds (INI: [host]/username/password/test_outlet)"
        )
    return c


@pytest.fixture
def wb250_creds(creds: dict[str, DeviceCreds]) -> DeviceCreds:
    if "10.10.10.150" not in creds:
        pytest.skip("no creds for 10.10.10.150 (WB-250) in ~/.wb-creds")
    return creds["10.10.10.150"]


@pytest.fixture
def wb800_creds(creds: dict[str, DeviceCreds]) -> DeviceCreds:
    if "10.10.10.156" not in creds:
        pytest.skip("no creds for 10.10.10.156 (WB-800) in ~/.wb-creds")
    return creds["10.10.10.156"]


@pytest.fixture
def wb250b_creds(creds: dict[str, DeviceCreds]) -> DeviceCreds:
    if "10.10.10.152" not in creds:
        pytest.skip("no creds for 10.10.10.152 (WB-250 second) in ~/.wb-creds")
    return creds["10.10.10.152"]
