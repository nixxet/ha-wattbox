"""Transport layer: how bytes get to and from the WattBox.

Architecture:

* :class:`Transport` — the abstract base used by :class:`WattboxClient`.
* :class:`_LineProtocolTransport` — shared mixin that owns the
  single-flight command lock, the response-name matching read loop,
  ``~Cmd`` push handling, and ``!Exit`` on close. Subclasses only need to
  implement ``connect()``, ``_read_line()``, ``_write_line()``, and
  ``_close_underlying()``.
* :class:`TelnetTransport` — concrete transport over ``telnetlib3``,
  walks the device's ``Username:``/``Password:`` banner sequence.
* :class:`SSHTransport` — concrete transport over ``asyncssh``;
  authentication happens at the SSH layer, post-auth we read the
  ``Connecting…``/``Successfully Logged In!`` banner the firmware emits
  before commands are accepted.

Design points worth knowing:

* Single-flight. WattBox firmware does not pipeline commands; sending a
  second command before the first response arrives produces garbage. The
  base serializes with an :class:`asyncio.Lock`.
* Session-poisoning. Observed live: one bad auth attempt poisons the
  TCP session even if the next attempt in the same session would have
  the right credentials. We therefore never retry auth in the same
  session — auth failure raises and the client opens a fresh session.
* Per-protocol lockout. Telnet, SSH, and web each track failed-auth
  counters independently. Telnet is the most easily tripped.
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

import asyncssh
import telnetlib3

from .exceptions import (
    WattboxAuthError,
    WattboxConnectionError,
    WattboxLockoutError,
    WattboxProtocolError,
)
from .protocol import (
    ACK_SENTINEL,
    ERROR_SENTINEL,
    LOGIN_BAD,
    LOGIN_LOCKED,
    LOGIN_OK,
    LOGIN_PROMPT_PASS,
    LOGIN_PROMPT_USER,
    command_name,
    response_command_name,
)

# Clean-session command sent during close(). Recognised by both Telnet
# and SSH paths on tested firmware.
EXIT_COMMAND: Final[str] = "!Exit"

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
    async def send_command(
        self,
        command: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_S,
        allow_push: bool = False,
    ) -> str:
        """Send a single command and return the first non-empty response line.

        For ``?Cmd`` queries the line is ``?Cmd=value``. For ``!Cmd`` sets
        it is typically ``OK`` — but for state-changing commands the
        device sometimes acks with the ``~Cmd=value`` async push instead.
        ``#Error`` is returned verbatim — the higher level decides whether
        to treat it as a capability gap.

        ``allow_push=False`` (default): stale ``~Cmd=value`` lines left
        over from prior state changes are skipped so they don't pollute
        the response window of a synchronous query.

        ``allow_push=True``: the next ``~Cmd=value`` push counts as a
        valid response. Set this when issuing a ``!Cmd`` whose only ack
        may be the push notification itself.
        """

    @property
    @abstractmethod
    def is_connected(self) -> bool: ...


class _LineProtocolTransport(Transport):
    """Shared base: command queuing, response matching, !Exit on close.

    Subclasses must implement:

    * :meth:`connect` — open the underlying connection and authenticate.
    * :meth:`_read_line` — return one line of decoded text without the
      trailing newline; raise :class:`WattboxConnectionError` on EOF /
      socket failure.
    * :meth:`_write_line` — write ``text + "\\n"`` and flush.
    * :meth:`_close_underlying` — best-effort tear down the connection.
    """

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self._cmd_lock = asyncio.Lock()
        self._closed = False

    # ---- subclass contract ----------------------------------------------

    @abstractmethod
    async def _read_line(self) -> str: ...

    @abstractmethod
    async def _write_line(self, text: str) -> None: ...

    @abstractmethod
    async def _close_underlying(self) -> None: ...

    # ---- common Transport behaviour ------------------------------------

    async def close(self) -> None:
        # Best-effort: send !Exit so the device drops the session
        # cleanly. Suppress everything — we're closing anyway.
        if self.is_connected and not self._closed:
            with contextlib.suppress(Exception):
                await self._write_line(EXIT_COMMAND)
        self._closed = True
        with contextlib.suppress(Exception):
            await self._close_underlying()

    async def send_command(
        self,
        command: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_S,
        allow_push: bool = False,
    ) -> str:
        if not self.is_connected:
            raise WattboxConnectionError(f"not connected to {self.host}")

        async with self._cmd_lock:
            try:
                await self._write_line(command)
            except (ConnectionError, OSError) as e:
                raise WattboxConnectionError(f"write failed to {self.host}: {e}") from e

            try:
                return await asyncio.wait_for(
                    self._read_response_for(command, allow_push=allow_push),
                    timeout=timeout,
                )
            except TimeoutError as e:
                raise WattboxConnectionError(
                    f"timeout waiting for reply to {command!r} from {self.host}"
                ) from e

    async def _read_response_for(self, command: str, *, allow_push: bool) -> str:
        """Read until a line corresponding to ``command`` arrives.

        Strict matching by command name so a late reply to a previously
        timed-out request can't be mistaken for the current command's
        response.

        Accepted lines:

        * ``OK`` — set-command ack
        * ``#Error`` — command not supported
        * ``?<sent-name>=value`` — the synchronous reply we asked for
        * If ``allow_push=True``: any ``~Cmd=value`` (set commands whose
          ack is a push notification, possibly with a *different* command
          name than the set — e.g. ``!OutletSet`` -> ``~OutletStatus``).
        * Anything else without a recognisable prefix — surfaced so the
          higher layer can decide.

        Discarded:

        * Blank lines.
        * ``~Cmd=value`` push notifications when ``allow_push`` is False.
        * ``?Cmd=value`` responses whose command name doesn't match what
          we just sent (late replies to a prior timed-out request).
        """
        sent_name = command_name(command)
        while True:
            stripped = await self._read_line()
            if not stripped:
                continue
            if stripped in (ACK_SENTINEL, ERROR_SENTINEL):
                return stripped
            this_name = response_command_name(stripped)
            if this_name is None:
                return stripped
            if stripped.startswith("?"):
                if this_name == sent_name:
                    return stripped
                _LOGGER.debug(
                    "discarding late ?%s reply while waiting for %s on %s",
                    this_name,
                    sent_name,
                    self.host,
                )
                continue
            if stripped.startswith("~"):
                if allow_push:
                    return stripped
                _LOGGER.debug(
                    "discarding stale ~%s push while waiting for ?%s on %s",
                    this_name,
                    sent_name,
                    self.host,
                )
                continue
            return stripped


# --- Telnet transport ---------------------------------------------------


class TelnetTransport(_LineProtocolTransport):
    """Telnet implementation using ``telnetlib3``.

    Cleartext. Suitable for trusted LAN segments only — prefer SSH on
    untrusted networks. Walks the device's
    ``Username:``/``Password:``/``Successfully Logged In!`` banner
    sequence at connect time.
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
        super().__init__(host, port)
        self._username = username
        self._password = password
        self._connect_timeout = connect_timeout
        # telnetlib3's type hints are bytes-mode; at runtime we use text-mode
        # (the default) so reader/writer transparently accept and return str.
        self._reader: Any = None
        self._writer: Any = None

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

        banner = await self._read_until_any(
            (LOGIN_PROMPT_USER, LOGIN_LOCKED, LOGIN_BAD),
            timeout=self._connect_timeout,
        )
        if LOGIN_LOCKED in banner:
            raise WattboxLockoutError(f"{self.host} reports API locked")
        if LOGIN_PROMPT_USER not in banner:
            raise WattboxProtocolError(f"did not see username prompt; banner={banner!r}")

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
        _LOGGER.debug("logged in to %s as %s (telnet)", self.host, self._username)

    async def _read_line(self) -> str:
        assert self._reader is not None
        while True:
            raw: str = await self._reader.readline()
            if raw == "":
                raise WattboxConnectionError(f"connection to {self.host} closed by peer")
            stripped: str = raw.strip()
            if stripped:
                return stripped

    async def _write_line(self, text: str) -> None:
        assert self._writer is not None
        self._writer.write(text + "\n")
        await self._writer.drain()

    async def _close_underlying(self) -> None:
        if self._writer is not None:
            with contextlib.suppress(Exception):
                self._writer.close()
        self._reader = None
        self._writer = None

    async def _read_until_any(self, needles: tuple[str, ...], *, timeout: float) -> str:
        """Read raw stream until any of `needles` appears or timeout."""
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


# --- SSH transport ------------------------------------------------------


class SSHTransport(_LineProtocolTransport):
    """SSH implementation using ``asyncssh`` with password auth.

    The WattBox SSH listener performs auth at the SSH layer (not via the
    Telnet-style banner walk). After auth succeeds the firmware emits two
    informational lines — ``Connecting...`` and
    ``Successfully Logged In!`` — before accepting commands. We drain
    those before the first user command.

    Per vendor PDF v2.4: SSH passwords are capped at 13 characters and
    must be set explicitly via the web UI's account screen — the default
    integration password may not work for SSH until it's been set there.
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        *,
        port: int = 22,
        connect_timeout: float = LOGIN_TIMEOUT_S,
    ) -> None:
        super().__init__(host, port)
        self._username = username
        self._password = password
        self._connect_timeout = connect_timeout
        self._conn: asyncssh.SSHClientConnection | None = None
        self._process: asyncssh.SSHClientProcess[str] | None = None

    @property
    def is_connected(self) -> bool:
        return self._conn is not None and self._process is not None and not self._closed

    async def connect(self) -> None:
        if self.is_connected:
            return
        try:
            self._conn = await asyncio.wait_for(
                asyncssh.connect(
                    self.host,
                    port=self.port,
                    username=self._username,
                    password=self._password,
                    known_hosts=None,
                    client_keys=None,
                    config=None,
                ),
                timeout=self._connect_timeout,
            )
        except asyncssh.PermissionDenied as e:
            raise WattboxAuthError(f"{self.host} rejected SSH credentials") from e
        except (TimeoutError, OSError, asyncssh.Error) as e:
            raise WattboxConnectionError(
                f"failed to SSH-connect to {self.host}:{self.port}: {e}"
            ) from e

        try:
            # No PTY — we want raw stdin/stdout without echo or line editing.
            self._process = await self._conn.create_process()
            await self._drain_post_auth_banner()
        except Exception:
            await self.close()
            raise

    async def _drain_post_auth_banner(self) -> None:
        """Prime the session and consume the firmware's startup banner.

        Verified live: WattBox SSH does NOT eagerly emit the banner —
        it only sends ``Connecting...\\r\\nSuccessfully Logged In!\\r\\n``
        in response to the first byte we write. So we send a guaranteed-
        valid command (``?Firmware``), then read lines until we see its
        own reply, discarding the banner along the way.
        """
        assert self._process is not None
        # Prime — directly, not through send_command (no cmd lock needed
        # since we hold the only reference, and send_command would
        # confuse itself trying to match the response while the banner
        # interleaves).
        self._process.stdin.write("?Firmware\n")
        deadline = asyncio.get_running_loop().time() + self._connect_timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise WattboxConnectionError(f"timeout priming SSH session on {self.host}")
            try:
                raw = await asyncio.wait_for(self._process.stdout.readline(), timeout=remaining)
            except TimeoutError as e:
                raise WattboxConnectionError(f"timeout priming SSH session on {self.host}") from e
            if not raw:
                raise WattboxConnectionError(f"SSH session to {self.host} closed during prime")
            stripped = raw.strip()
            _LOGGER.debug("ssh prime line from %s: %s", self.host, stripped)
            if LOGIN_LOCKED in stripped:
                raise WattboxLockoutError(f"{self.host} reports API locked")
            if LOGIN_BAD in stripped:
                # Shouldn't be possible after successful SSH auth, but be safe.
                raise WattboxAuthError(f"{self.host} rejected post-auth credentials")
            if stripped.startswith("?Firmware="):
                return  # session is live and clean

    async def _read_line(self) -> str:
        assert self._process is not None
        while True:
            raw: str = await self._process.stdout.readline()
            if raw == "":
                raise WattboxConnectionError(f"SSH session to {self.host} closed by peer")
            stripped = raw.strip()
            if stripped:
                return stripped

    async def _write_line(self, text: str) -> None:
        assert self._process is not None
        self._process.stdin.write(text + "\n")
        # asyncssh's SSHWriter has no drain() — write returns once data
        # is in the kernel-space write buffer.

    async def _close_underlying(self) -> None:
        if self._process is not None:
            with contextlib.suppress(Exception):
                self._process.close()
            self._process = None
        if self._conn is not None:
            with contextlib.suppress(Exception):
                self._conn.close()
                await self._conn.wait_closed()
            self._conn = None


# --- convenience context managers ---------------------------------------


@asynccontextmanager
async def open_telnet(
    host: str,
    username: str,
    password: str,
    *,
    port: int = 23,
) -> AsyncIterator[TelnetTransport]:
    """Open, yield, always close."""
    t = TelnetTransport(host, username, password, port=port)
    await t.connect()
    try:
        yield t
    finally:
        await t.close()


@asynccontextmanager
async def open_ssh(
    host: str,
    username: str,
    password: str,
    *,
    port: int = 22,
) -> AsyncIterator[SSHTransport]:
    """Open, yield, always close."""
    t = SSHTransport(host, username, password, port=port)
    await t.connect()
    try:
        yield t
    finally:
        await t.close()
