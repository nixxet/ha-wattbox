"""Transport-layer unit tests.

We don't talk to a real WattBox here. Instead we replace ``telnetlib3.open_connection``
with a fake stream pair that scripts the exact byte sequences observed live, so the
login dance and command/response path are exercised without hardware.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from wattbox_local import (
    WattboxAuthError,
    WattboxConnectionError,
    WattboxLockoutError,
    WattboxProtocolError,
)
from wattbox_local.transport import TelnetTransport, open_telnet

# ---- fake reader/writer ------------------------------------------------


class _FakeReader:
    """Scripts inbound bytes: each step is either text or a callable.

    A callable step is invoked at the moment it is reached, so it can wait
    for the writer to push something before returning its own next chunk.
    """

    def __init__(self, script: list[str]) -> None:
        self._script = list(script)
        self._buf = ""
        self._closed = False

    def _refill(self) -> None:
        if not self._script:
            return
        nxt = self._script.pop(0)
        if nxt == "":
            self._closed = True
            return
        self._buf += nxt

    async def read(self, n: int) -> str:
        while not self._buf and not self._closed:
            self._refill()
            if not self._buf and not self._closed:
                await asyncio.sleep(0)  # let the writer run
        if not self._buf and self._closed:
            return ""
        chunk = self._buf[:n]
        self._buf = self._buf[n:]
        return chunk

    async def readline(self) -> str:
        while "\n" not in self._buf and not self._closed:
            self._refill()
            if "\n" not in self._buf and not self._closed:
                await asyncio.sleep(0)
        if not self._buf and self._closed:
            return ""
        idx = self._buf.find("\n")
        if idx == -1:
            line, self._buf = self._buf, ""
            return line
        line, self._buf = self._buf[: idx + 1], self._buf[idx + 1 :]
        return line


class _FakeWriter:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closed = False

    def write(self, data: str) -> None:
        self.sent.append(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def patch_telnet(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Install a fake ``telnetlib3.open_connection``.

    Returns a setter; tests call ``patch_telnet(script)`` to supply the
    scripted inbound chunks.
    """
    state: dict[str, Any] = {}

    async def fake_open(host: str, port: int) -> tuple[_FakeReader, _FakeWriter]:
        state["host"] = host
        state["port"] = port
        return _FakeReader(state["script"]), state["writer"]

    from wattbox_local import transport as t

    monkeypatch.setattr(t.telnetlib3, "open_connection", fake_open)

    def setter(script: list[str]) -> dict[str, Any]:
        state["script"] = script
        state["writer"] = _FakeWriter()
        return state

    return setter


# ---- login dance -------------------------------------------------------


async def test_login_success(patch_telnet: Any) -> None:
    s = patch_telnet(
        [
            "Please Login to Continue\r\nUsername: ",
            "Password: ",
            "Successfully Logged In!\r\n",
        ]
    )
    t = TelnetTransport("10.0.0.1", "wattbox", "secret")
    await t.connect()
    assert t.is_connected
    assert s["writer"].sent == ["wattbox\r\n", "secret\r\n"]
    await t.close()
    assert not t.is_connected


async def test_login_bad_credentials_raises_auth(patch_telnet: Any) -> None:
    patch_telnet(
        [
            "Please Login to Continue\r\nUsername: ",
            "Password: ",
            "Invalid Login\r\n",
        ]
    )
    t = TelnetTransport("10.0.0.1", "wattbox", "wrong")
    with pytest.raises(WattboxAuthError):
        await t.connect()
    assert not t.is_connected


async def test_login_lockout_at_banner_raises_lockout(patch_telnet: Any) -> None:
    patch_telnet(["API locked due to too many invalid login attempts.\r\n"])
    t = TelnetTransport("10.0.0.1", "wattbox", "secret")
    with pytest.raises(WattboxLockoutError):
        await t.connect()


async def test_login_lockout_after_password_raises_lockout(patch_telnet: Any) -> None:
    patch_telnet(
        [
            "Please Login to Continue\r\nUsername: ",
            "Password: ",
            "API locked\r\n",
        ]
    )
    t = TelnetTransport("10.0.0.1", "wattbox", "secret")
    with pytest.raises(WattboxLockoutError):
        await t.connect()


async def test_connect_timeout_raises_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def slow_open(host: str, port: int) -> Any:
        await asyncio.sleep(10)
        raise AssertionError("should have timed out")

    from wattbox_local import transport as tmod

    monkeypatch.setattr(tmod.telnetlib3, "open_connection", slow_open)
    t = TelnetTransport("10.0.0.1", "u", "p", connect_timeout=0.05)
    with pytest.raises(WattboxConnectionError):
        await t.connect()


async def test_peer_closes_during_login(patch_telnet: Any) -> None:
    patch_telnet(["Please Login to Continue\r\nUsername: ", "Password: ", ""])
    t = TelnetTransport("10.0.0.1", "u", "p")
    with pytest.raises(WattboxConnectionError):
        await t.connect()


async def test_unexpected_banner_raises_protocol(patch_telnet: Any) -> None:
    patch_telnet(
        [
            "Please Login to Continue\r\nUsername: ",
            "Password: ",
            "Welcome to Something Else\r\n",
        ]
    )
    t = TelnetTransport("10.0.0.1", "u", "p", connect_timeout=0.1)
    with pytest.raises((WattboxProtocolError, WattboxConnectionError)):
        await t.connect()


# ---- send_command ------------------------------------------------------


async def test_send_command_returns_response_line(patch_telnet: Any) -> None:
    s = patch_telnet(
        [
            "Please Login to Continue\r\nUsername: ",
            "Password: ",
            "Successfully Logged In!\r\n",
            "?Model=WB-800-IPVM-12\r\n",
        ]
    )
    t = TelnetTransport("10.0.0.1", "u", "p")
    await t.connect()
    response = await t.send_command("?Model")
    assert response == "?Model=WB-800-IPVM-12"
    assert s["writer"].sent[-1] == "?Model\n"
    await t.close()


async def test_send_command_skips_blank_lines(patch_telnet: Any) -> None:
    patch_telnet(
        [
            "Please Login to Continue\r\nUsername: ",
            "Password: ",
            "Successfully Logged In!\r\n",
            "\r\n\r\n?OutletStatus=1,0,1\r\n",
        ]
    )
    t = TelnetTransport("10.0.0.1", "u", "p")
    await t.connect()
    assert await t.send_command("?OutletStatus") == "?OutletStatus=1,0,1"
    await t.close()


async def test_send_command_when_disconnected_raises(patch_telnet: Any) -> None:
    t = TelnetTransport("10.0.0.1", "u", "p")
    with pytest.raises(WattboxConnectionError):
        await t.send_command("?Model")


async def test_send_command_timeout(patch_telnet: Any) -> None:
    # No response chunk after the login banner — read will hang.
    patch_telnet(
        [
            "Please Login to Continue\r\nUsername: ",
            "Password: ",
            "Successfully Logged In!\r\n",
        ]
    )
    t = TelnetTransport("10.0.0.1", "u", "p")
    await t.connect()
    with pytest.raises(WattboxConnectionError):
        await t.send_command("?Model", timeout=0.05)
    await t.close()


async def test_send_command_serializes_concurrent_callers(patch_telnet: Any) -> None:
    patch_telnet(
        [
            "Please Login to Continue\r\nUsername: ",
            "Password: ",
            "Successfully Logged In!\r\n",
            "?Model=WB-800-IPVM-12\r\n",
            "?Hostname=WattBox\r\n",
        ]
    )
    t = TelnetTransport("10.0.0.1", "u", "p")
    await t.connect()
    a, b = await asyncio.gather(t.send_command("?Model"), t.send_command("?Hostname"))
    assert {a, b} == {"?Model=WB-800-IPVM-12", "?Hostname=WattBox"}
    await t.close()


async def test_peer_closes_mid_command_raises(patch_telnet: Any) -> None:
    patch_telnet(
        [
            "Please Login to Continue\r\nUsername: ",
            "Password: ",
            "Successfully Logged In!\r\n",
            "",
        ]
    )
    t = TelnetTransport("10.0.0.1", "u", "p")
    await t.connect()
    with pytest.raises(WattboxConnectionError):
        await t.send_command("?Model")


# ---- context manager ---------------------------------------------------


async def test_open_telnet_context_manager_closes_on_exit(patch_telnet: Any) -> None:
    s = patch_telnet(
        [
            "Please Login to Continue\r\nUsername: ",
            "Password: ",
            "Successfully Logged In!\r\n",
            "?Firmware=2.10.0.0\r\n",
        ]
    )
    async with open_telnet("10.0.0.1", "u", "p") as t:
        assert t.is_connected
        assert await t.send_command("?Firmware") == "?Firmware=2.10.0.0"
    assert s["writer"].closed


async def test_open_telnet_closes_even_on_exception(patch_telnet: Any) -> None:
    s = patch_telnet(
        [
            "Please Login to Continue\r\nUsername: ",
            "Password: ",
            "Successfully Logged In!\r\n",
        ]
    )

    async def use() -> AsyncIterator[None]:
        async with open_telnet("10.0.0.1", "u", "p"):
            raise RuntimeError("boom")
            yield  # pragma: no cover

    with pytest.raises(RuntimeError):
        async for _ in use():  # pragma: no cover
            pass
    assert s["writer"].closed
