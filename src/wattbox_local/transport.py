"""Transport layer: how bytes get to and from the WattBox.

`Transport` is the abstract base. `TelnetTransport` is the concrete
implementation used in Phase 1. A future `SSHTransport` will fit behind
the same ABC without breaking client code.

Design points worth knowing:

* Single-flight. WattBox firmware does not pipeline commands; sending a
  second command before the first response arrives produces garbage. The
  transport serializes with an :class:`asyncio.Lock`.
* Line-oriented. Every wire interaction is one outbound line ending in
  ``\\r\\n`` followed by zero or more inbound lines. The transport reads
  until it sees the response for the command it just sent, or times out.
* No retries here. Reconnect / retry policy lives in `WattboxClient`,
  where it can be coordinated with the lockout budget.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Final

import telnetlib3

from .exceptions import (
    WattboxAuthError,
    WattboxConnectionError,
    WattboxLockoutError,
    WattboxProtocolError,
)
from .protocol import (
    LOGIN_BAD,
    LOGIN_LOCKED,
    LOGIN_OK,
    LOGIN_PROMPT_PASS,
    LOGIN_PROMPT_USER,
)

_LOGGER = logging.getLogger(__name__)

# A read should never take longer than this. WattBox responses are
# typically sub-100ms on LAN; 5s is forgiving but still well under the
# integration's typical coordinator poll interval.
DEFAULT_TIMEOUT_S: Final[float] = 5.0

# How long to wait for the login banner / prompts before giving up.
LOGIN_TIMEOUT_S: Final[float] = 8.0


class Transport(ABC):
    """Abstract base for a line-oriented WattBox transport."""

    host: str
    port: int

    @abstractmethod
    async def connect(self) -> None:
        """Open the connection and authenticate.

        Must raise :class:`WattboxAuthError` for credential failure,
        :class:`WattboxLockoutError` if the device emits an `API locked`
        banner, and :class:`WattboxConnectionError` for network/timeout
        failure.
        """

    @abstractmethod
    async def close(self) -> None:
        """Close the connection. Safe to call multiple times."""

    @abstractmethod
    async def send_command(self, command: str, *, timeout: float = DEFAULT_TIMEOUT_S) -> str:
        """Send a single command and return the first non-empty response line.

        For ``?Cmd`` queries the line is ``?Cmd=value``. For ``!Cmd`` sets
        it is typically ``OK`` (or sometimes another ``?Cmd=value`` echo).
        ``#Error`` is returned verbatim — the higher level decides whether
        to treat it as a capability gap.
        """

    @property
    @abstractmethod
    def is_connected(self) -> bool: ...


class TelnetTransport(Transport):
    """Telnet implementation using ``telnetlib3``.

    Cleartext. Suitable for trusted LAN segments. Replace with
    SSHTransport for untrusted networks once Phase 2 lands.
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        *,
        port: int = 23,
        connect_timeout: float = LOGIN_TIMEOUT_S,
    ) -> None:
        self.host = host
        self.port = port
        self._username = username
        self._password = password
        self._connect_timeout = connect_timeout
        # telnetlib3's type hints are bytes-mode; at runtime we use text-mode
        # (the default) so reader/writer transparently accept and return str.
        # Keep the underlying handles as Any so mypy doesn't fight runtime reality.
        self._reader: Any = None
        self._writer: Any = None
        self._cmd_lock = asyncio.Lock()
        self._closed = False

    @property
    def is_connected(self) -> bool:
        return self._reader is not None and self._writer is not None and not self._closed

    async def connect(self) -> None:
        if self.is_connected:
            return
        try:
            self._reader, self._writer = await asyncio.wait_for(
                telnetlib3.open_connection(self.host, self.port),
                timeout=self._connect_timeout,
            )
        except (TimeoutError, OSError) as e:
            raise WattboxConnectionError(
                f"failed to connect to {self.host}:{self.port}: {e}"
            ) from e

        try:
            await self._login()
        except Exception:
            await self.close()
            raise

    async def _login(self) -> None:
        """Walk the Username:/Password: prompts and confirm login."""
        assert self._reader is not None
        assert self._writer is not None

        # 1. Read until the username prompt (or lockout banner).
        banner = await self._read_until_any(
            (LOGIN_PROMPT_USER, LOGIN_LOCKED, LOGIN_BAD),
            timeout=self._connect_timeout,
        )
        if LOGIN_LOCKED in banner:
            raise WattboxLockoutError(f"{self.host} reports API locked")
        if LOGIN_PROMPT_USER not in banner:
            raise WattboxProtocolError(f"did not see username prompt; banner={banner!r}")

        # 2. Send username, wait for password prompt.
        self._writer.write(self._username + "\r\n")
        await self._writer.drain()
        prompt = await self._read_until_any(
            (LOGIN_PROMPT_PASS, LOGIN_LOCKED),
            timeout=self._connect_timeout,
        )
        if LOGIN_LOCKED in prompt:
            raise WattboxLockoutError(f"{self.host} reports API locked")
        if LOGIN_PROMPT_PASS not in prompt:
            raise WattboxProtocolError(f"did not see password prompt; got={prompt!r}")

        # 3. Send password, wait for login result.
        self._writer.write(self._password + "\r\n")
        await self._writer.drain()
        result = await self._read_until_any(
            (LOGIN_OK, LOGIN_BAD, LOGIN_LOCKED),
            timeout=self._connect_timeout,
        )
        if LOGIN_LOCKED in result:
            raise WattboxLockoutError(f"{self.host} reports API locked")
        if LOGIN_BAD in result:
            raise WattboxAuthError(f"{self.host} rejected credentials")
        if LOGIN_OK not in result:
            raise WattboxProtocolError(f"unexpected login result: {result!r}")
        _LOGGER.debug("logged in to %s as %s", self.host, self._username)

    async def close(self) -> None:
        self._closed = True
        if self._writer is not None:
            with contextlib.suppress(Exception):
                self._writer.close()
        self._reader = None
        self._writer = None

    async def send_command(self, command: str, *, timeout: float = DEFAULT_TIMEOUT_S) -> str:
        if not self.is_connected:
            raise WattboxConnectionError(f"not connected to {self.host}")
        assert self._reader is not None
        assert self._writer is not None

        async with self._cmd_lock:
            self._writer.write(command + "\n")
            try:
                await self._writer.drain()
            except (ConnectionError, OSError) as e:
                raise WattboxConnectionError(f"write failed to {self.host}: {e}") from e

            # Read until we see something other than a blank line. WattBox
            # responses are one line: "?Cmd=value", "OK", or "#Error".
            try:
                line = await asyncio.wait_for(
                    self._read_one_response_line(),
                    timeout=timeout,
                )
            except TimeoutError as e:
                raise WattboxConnectionError(
                    f"timeout waiting for reply to {command!r} from {self.host}"
                ) from e
            return line

    async def _read_one_response_line(self) -> str:
        """Read lines until a non-blank one appears; return it stripped."""
        assert self._reader is not None
        while True:
            raw: str = await self._reader.readline()
            if raw == "":  # EOF
                raise WattboxConnectionError(f"connection to {self.host} closed by peer")
            stripped: str = raw.strip()
            if stripped:
                return stripped

    async def _read_until_any(self, needles: tuple[str, ...], *, timeout: float) -> str:
        """Read raw stream until any of `needles` appears or timeout.

        Returns the accumulated buffer (not just the line containing the
        needle) so the caller can inspect for multiple sentinels.
        """
        assert self._reader is not None
        buf = ""
        deadline = asyncio.get_running_loop().time() + timeout

        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise WattboxConnectionError(
                    f"timeout waiting for one of {needles!r} from {self.host}; buffer={buf!r}"
                )
            try:
                chunk: str = await asyncio.wait_for(self._reader.read(1024), timeout=remaining)
            except TimeoutError as e:
                raise WattboxConnectionError(
                    f"timeout waiting for one of {needles!r} from {self.host}; buffer={buf!r}"
                ) from e
            if chunk == "":
                raise WattboxConnectionError(
                    f"connection to {self.host} closed during login; buffer={buf!r}"
                )
            buf += chunk
            if any(n in buf for n in needles):
                return buf


@asynccontextmanager
async def open_telnet(
    host: str,
    username: str,
    password: str,
    *,
    port: int = 23,
) -> AsyncIterator[TelnetTransport]:
    """Convenience context manager: open, yield, always close."""
    t = TelnetTransport(host, username, password, port=port)
    await t.connect()
    try:
        yield t
    finally:
        await t.close()
