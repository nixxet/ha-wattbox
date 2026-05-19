"""WattBox (local) integration."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed

from .const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_TRANSPORT,
    CONF_USERNAME,
    DEFAULT_SCAN_INTERVAL_S,
    DEFAULT_TRANSPORT,
    TRANSPORT_SSH,
    default_port_for,
)
from .coordinator import WattboxCoordinator
from .wattbox_local import WattboxAuthError, WattboxClient, WattboxLockoutError
from .wattbox_local.transport import SSHTransport, TelnetTransport

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SENSOR,
    Platform.SWITCH,
]

type WattboxConfigEntry = ConfigEntry[WattboxCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: WattboxConfigEntry) -> bool:
    """Set up a WattBox from a config entry."""
    transport_kind = entry.data.get(CONF_TRANSPORT, DEFAULT_TRANSPORT)
    port = entry.data.get(CONF_PORT, default_port_for(transport_kind))
    host = entry.data[CONF_HOST]
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]

    if transport_kind == TRANSPORT_SSH:
        transport = SSHTransport(host, username, password, port=port)
    else:
        transport = TelnetTransport(host, username, password, port=port)

    client = WattboxClient(host=host, username=username, password=password, transport=transport)

    try:
        await client.connect()
        await client.identify()
    except WattboxAuthError as err:
        await client.close()
        raise ConfigEntryAuthFailed(str(err)) from err
    except (WattboxLockoutError, Exception):
        await client.close()
        raise

    scan_interval = timedelta(
        seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_S)
    )
    coordinator = WattboxCoordinator(hass, entry, client, scan_interval)

    # First refresh is awaited so entities have data on first read.
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: WattboxConfigEntry) -> bool:
    """Tear down a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator = entry.runtime_data
        await coordinator.async_shutdown()
    return unloaded


async def _async_options_updated(hass: HomeAssistant, entry: WattboxConfigEntry) -> None:
    """Reload when options (e.g. scan interval) change."""
    await hass.config_entries.async_reload(entry.entry_id)
