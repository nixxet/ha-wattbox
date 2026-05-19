"""Unit tests for SSHTransport.

We mock ``asyncssh.connect`` to return a fake connection + process pair so
the auth / prime / send_command / close path is exercised without a real
SSH server.
"""

from __future__ import annotations

import asyncio
from typing import Any

import asyncssh
import pytest

from wattbox_local import (
    WattboxAuthError,
    WattboxConnectionError,
    WattboxLockoutError,
)
from wattbox_local.transport import SSHTransport, open_ssh

# ---- fake asyncssh objects ---------------------------------------------


class _FakeStdout:
    """Scripts the lines the SSH server will send back, in order."""

    def __init__(self, lines: list[str]) -> None:
        # Each entry is one line; empty string means EOF.
        self._lines = list(lines)

    async def readline(self) -> str:
        if not self._lines:
            await asyncio.sleep(0)  # yield to other tasks while idle
            await asyncio.Event().wait()  # block forever
            return ""  # unreachable
        nxt = self._lines.pop(0)
        if nxt == "":
            return ""  # EOF
        return nxt if nxt.endswith("\n") else nxt + "\n"


class _FakeStdin:
    def __init__(self) -> None:
        self.written: list[str] = []

    def write(self, data: str) -> None:
        self.written.append(data)


class _FakeProcess:
    def __init__(self, lines: list[str]) -> None:
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout(lines)
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeConnection:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self.closed = False
        self._process: _FakeProcess | None = None

    async def create_process(self, **_kwargs: Any) -> _FakeProcess:
        self._process = _FakeProcess(self._lines)
        return self._process

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return


@pytest.fixture
def patch_asyncssh(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Install a fake ``asyncssh.connect``. Returns a setter for tests."""
    state: dict[str, Any] = {"connect_error": None}

    async def fake_connect(host: str, **_kwargs: Any) -> _FakeConnection:
        if state["connect_error"] is not None:
            err = state["connect_error"]
            state["connect_error"] = None
            raise err
        conn = _FakeConnection(state["lines"])
        state["conn"] = conn
        state["host"] = host
        return conn

    from wattbox_local import transport as tmod

    monkeypatch.setattr(tmod.asyncssh, "connect", fake_connect)

    def setter(
        *, lines: list[str] | None = None, connect_error: BaseException | None = None
    ) -> dict[str, Any]:
        state["lines"] = lines or []
        state["connect_error"] = connect_error
        return state

    return setter


# ---- prime / banner handling -------------------------------------------


async def test_connect_success_drains_banner(patch_asyncssh: Any) -> None:
    s = patch_asyncssh(
        lines=[
            "Connecting...\n",
            "Successfully Logged In!\n",
            "?Firmware=2.10.0.0\n",
        ]
    )
    t = SSHTransport("10.0.0.1", "wattbox", "secret")
    await t.connect()
    assert t.is_connected
    # Priming wrote the ?Firmware probe.
    assert s["conn"]._process.stdin.written == ["?Firmware\n"]
    await t.close()


async def test_connect_lockout_during_prime_raises(patch_asyncssh: Any) -> None:
    patch_asyncssh(lines=["API is locked for 4 minutes and 39 seconds.\n"])
    t = SSHTransport("10.0.0.1", "wattbox", "secret")
    with pytest.raises(WattboxLockoutError):
        await t.connect()


async def test_connect_permission_denied_maps_to_auth_error(
    patch_asyncssh: Any,
) -> None:
    patch_asyncssh(connect_error=asyncssh.PermissionDenied("nope"))
    t = SSHTransport("10.0.0.1", "wattbox", "wrong")
    with pytest.raises(WattboxAuthError):
        await t.connect()


async def test_connect_oserror_maps_to_connection_error(
    patch_asyncssh: Any,
) -> None:
    patch_asyncssh(connect_error=OSError("no route to host"))
    t = SSHTransport("10.0.0.1", "wattbox", "x")
    with pytest.raises(WattboxConnectionError):
        await t.connect()


async def test_connect_session_closes_during_prime(patch_asyncssh: Any) -> None:
    patch_asyncssh(lines=[""])  # EOF immediately
    t = SSHTransport("10.0.0.1", "wattbox", "x")
    with pytest.raises(WattboxConnectionError):
        await t.connect()


# ---- send_command ------------------------------------------------------


async def test_send_command_returns_response(patch_asyncssh: Any) -> None:
    s = patch_asyncssh(
        lines=[
            "Connecting...\n",
            "Successfully Logged In!\n",
            "?Firmware=2.10.0.0\n",  # consumed by prime
            "?Model=WB-800-IPVM-12\n",
        ]
    )
    t = SSHTransport("10.0.0.1", "wattbox", "x")
    await t.connect()
    assert await t.send_command("?Model") == "?Model=WB-800-IPVM-12"
    # Two writes: prime ?Firmware then user ?Model.
    assert s["conn"]._process.stdin.written == ["?Firmware\n", "?Model\n"]
    await t.close()


async def test_send_command_discards_stale_push(patch_asyncssh: Any) -> None:
    """A leftover ~OutletStatus push must not be returned for a ?OutletStatus query."""
    patch_asyncssh(
        lines=[
            "Connecting...\n",
            "Successfully Logged In!\n",
            "?Firmware=2.10.0.0\n",
            "~OutletStatus=1,0\n",  # stale push
            "?OutletStatus=1,1\n",  # real reply
        ]
    )
    t = SSHTransport("10.0.0.1", "wattbox", "x")
    await t.connect()
    assert await t.send_command("?OutletStatus") == "?OutletStatus=1,1"
    await t.close()


async def test_send_command_allow_push_for_set(patch_asyncssh: Any) -> None:
    patch_asyncssh(
        lines=[
            "Connecting...\n",
            "Successfully Logged In!\n",
            "?Firmware=2.10.0.0\n",
            "~OutletStatus=1,0\n",
        ]
    )
    t = SSHTransport("10.0.0.1", "wattbox", "x")
    await t.connect()
    assert await t.send_command("!OutletSet=2,OFF", allow_push=True) == "~OutletStatus=1,0"
    await t.close()


async def test_send_command_when_disconnected_raises(patch_asyncssh: Any) -> None:
    patch_asyncssh(lines=[])
    t = SSHTransport("10.0.0.1", "wattbox", "x")
    with pytest.raises(WattboxConnectionError):
        await t.send_command("?Model")


# ---- close -------------------------------------------------------------


async def test_close_sends_exit_and_tears_down(patch_asyncssh: Any) -> None:
    s = patch_asyncssh(
        lines=[
            "Connecting...\n",
            "Successfully Logged In!\n",
            "?Firmware=2.10.0.0\n",
        ]
    )
    t = SSHTransport("10.0.0.1", "wattbox", "x")
    await t.connect()
    await t.close()
    assert "!Exit\n" in s["conn"]._process.stdin.written
    assert s["conn"]._process.closed
    assert s["conn"].closed


async def test_close_idempotent(patch_asyncssh: Any) -> None:
    patch_asyncssh(
        lines=[
            "Connecting...\n",
            "Successfully Logged In!\n",
            "?Firmware=2.10.0.0\n",
        ]
    )
    t = SSHTransport("10.0.0.1", "wattbox", "x")
    await t.connect()
    await t.close()
    await t.close()  # no exception


# ---- context manager ---------------------------------------------------


async def test_open_ssh_context_manager(patch_asyncssh: Any) -> None:
    s = patch_asyncssh(
        lines=[
            "Connecting...\n",
            "Successfully Logged In!\n",
            "?Firmware=2.10.0.0\n",
            "?Model=WB-800-IPVM-12\n",
        ]
    )
    async with open_ssh("10.0.0.1", "wattbox", "x") as t:
        assert t.is_connected
        assert await t.send_command("?Model") == "?Model=WB-800-IPVM-12"
    assert s["conn"]._process.closed
