"""Exception hierarchy for the WattBox client.

Distinguishes the categories an integration cares about:

- network / transport problems              -> WattboxConnectionError
- bad credentials                           -> WattboxAuthError
- device-side auth lockout                  -> WattboxLockoutError
- command rejected with #Error              -> WattboxCommandUnsupported
- malformed / unparseable device response   -> WattboxProtocolError
"""

from __future__ import annotations


class WattboxError(Exception):
    """Base class for all WattBox client errors."""


class WattboxConnectionError(WattboxError):
    """Failed to open or maintain the transport connection."""


class WattboxAuthError(WattboxError):
    """Device rejected the supplied credentials (`Invalid Login`)."""


class WattboxLockoutError(WattboxError):
    """Device or client is in an auth-lockout cooldown.

    Raised both when the device returns an `API locked` banner and when the
    client-side failure budget has been exhausted. Either way the right
    behaviour is to back off, not retry.
    """


class WattboxCommandUnsupported(WattboxError):
    """The device replied `#Error` to a command.

    Treated as a capability gap, not a fault. The HA integration uses this
    signal to decide which entities to create.
    """

    def __init__(self, command: str) -> None:
        super().__init__(f"Command not supported by device: {command}")
        self.command = command


class WattboxProtocolError(WattboxError):
    """Device returned a response we could not parse."""
