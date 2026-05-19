"""DataUpdateCoordinator wrapping :class:`wattbox_local.WattboxClient`."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from wattbox_local import (
    Snapshot,
    WattboxAuthError,
    WattboxClient,
    WattboxConnectionError,
    WattboxLockoutError,
)

from .const import DOMAIN

if TYPE_CHECKING:
    pass

_LOGGER = logging.getLogger(__name__)


class WattboxCoordinator(DataUpdateCoordinator[Snapshot]):
    """One coordinator per ConfigEntry (one per device)."""

    config_entry: ConfigEntry
    client: WattboxClient

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: WattboxClient,
        scan_interval: timedelta,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {entry.title}",
            update_interval=scan_interval,
            config_entry=entry,
        )
        self.client = client

    async def _async_update_data(self) -> Snapshot:
        try:
            return await self.client.snapshot()
        except WattboxAuthError as err:
            raise ConfigEntryAuthFailed(f"WattBox auth failed: {err}") from err
        except WattboxLockoutError as err:
            # Surface as UpdateFailed so HA marks entities unavailable; the
            # api_locked binary sensor (added by the binary_sensor platform)
            # exposes the lockout state explicitly to the user.
            raise UpdateFailed(f"WattBox locked out: {err}") from err
        except WattboxConnectionError as err:
            raise UpdateFailed(f"WattBox unreachable: {err}") from err

    async def async_shutdown(self) -> None:
        """Close the underlying client cleanly."""
        await super().async_shutdown()
        await self.client.close()
