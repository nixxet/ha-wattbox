"""UI config + reauth + options flows for the WattBox integration.

Validation strategy: open a real :class:`WattboxClient`, call
``identify()``, capture the device's ServiceTag, and use that as the
config entry's ``unique_id`` so a second attempt to add the same physical
WattBox cleanly de-dupes regardless of IP or hostname change.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from wattbox_local import (
    WattboxAuthError,
    WattboxClient,
    WattboxConnectionError,
    WattboxLockoutError,
)
from wattbox_local.transport import SSHTransport, TelnetTransport

from .const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_TRANSPORT,
    CONF_USERNAME,
    DEFAULT_SCAN_INTERVAL_S,
    DEFAULT_TRANSPORT,
    DEFAULT_USERNAME,
    DOMAIN,
    MAX_SCAN_INTERVAL_S,
    MIN_SCAN_INTERVAL_S,
    TRANSPORT_SSH,
    TRANSPORTS,
    default_port_for,
)

_LOGGER = logging.getLogger(__name__)


def _user_schema(defaults: Mapping[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    transport = defaults.get(CONF_TRANSPORT, DEFAULT_TRANSPORT)
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
            vol.Required(CONF_USERNAME, default=defaults.get(CONF_USERNAME, DEFAULT_USERNAME)): str,
            vol.Required(CONF_PASSWORD, default=defaults.get(CONF_PASSWORD, "")): str,
            vol.Required(CONF_TRANSPORT, default=transport): vol.In(TRANSPORTS),
            vol.Optional(
                CONF_PORT, default=defaults.get(CONF_PORT, default_port_for(transport))
            ): vol.All(int, vol.Range(min=1, max=65535)),
        }
    )


def _build_transport(
    host: str, port: int, username: str, password: str, transport_kind: str
) -> SSHTransport | TelnetTransport:
    if transport_kind == TRANSPORT_SSH:
        return SSHTransport(host, username, password, port=port)
    return TelnetTransport(host, username, password, port=port)


def _reauth_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_USERNAME, default=DEFAULT_USERNAME): str,
            vol.Required(CONF_PASSWORD): str,
        }
    )


async def _probe(
    host: str, port: int, username: str, password: str, transport_kind: str
) -> tuple[str, str]:
    """Open a one-shot client, identify, return (service_tag, model).

    Raises the same WattBox* exceptions the high-level client raises so
    the caller can map them to flow errors.
    """
    transport = _build_transport(host, port, username, password, transport_kind)
    client = WattboxClient(host=host, username=username, password=password, transport=transport)
    try:
        await client.connect()
        info = await client.identify()
        return info.service_tag, info.model
    finally:
        await client.close()


class WattboxConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial UI setup and reauth for one WattBox."""

    VERSION = 1
    MINOR_VERSION = 1

    _reauth_entry: ConfigEntry | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            # Allow CONF_PORT to default per-transport if the user didn't override.
            transport_kind = user_input.get(CONF_TRANSPORT, DEFAULT_TRANSPORT)
            user_input.setdefault(CONF_PORT, default_port_for(transport_kind))
            try:
                service_tag, model = await _probe(
                    user_input[CONF_HOST],
                    user_input[CONF_PORT],
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                    transport_kind,
                )
            except WattboxAuthError:
                errors["base"] = "invalid_auth"
            except WattboxLockoutError:
                errors["base"] = "api_locked"
            except WattboxConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("unexpected error during WattBox config probe")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(service_tag)
                self._abort_if_unique_id_configured(updates=user_input)
                return self.async_create_entry(title=model, data=user_input)
        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(user_input),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._reauth_entry is not None
        entry = self._reauth_entry
        errors: dict[str, str] = {}
        if user_input is not None:
            transport_kind = entry.data.get(CONF_TRANSPORT, DEFAULT_TRANSPORT)
            try:
                service_tag, _ = await _probe(
                    entry.data[CONF_HOST],
                    entry.data.get(CONF_PORT, default_port_for(transport_kind)),
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                    transport_kind,
                )
            except WattboxAuthError:
                errors["base"] = "invalid_auth"
            except WattboxLockoutError:
                errors["base"] = "api_locked"
            except WattboxConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("unexpected error during WattBox reauth probe")
                errors["base"] = "unknown"
            else:
                if entry.unique_id and service_tag != entry.unique_id:
                    # Wrong physical device at this IP — refuse.
                    return self.async_abort(reason="unknown")
                self.hass.config_entries.async_update_entry(
                    entry,
                    data={**entry.data, **user_input},
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_reauth_schema(),
            errors=errors,
            description_placeholders={"host": entry.data[CONF_HOST]},
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return WattboxOptionsFlow(entry)


class WattboxOptionsFlow(OptionsFlow):
    """Edit poll interval after setup."""

    def __init__(self, entry: ConfigEntry) -> None:
        self.entry = entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        current = self.entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_S)
        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_INTERVAL, default=current): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL_S,
                        max=MAX_SCAN_INTERVAL_S,
                        unit_of_measurement="s",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
