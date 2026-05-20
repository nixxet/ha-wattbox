"""HA service handlers wrapping WattboxClient bonus features.

The four services exposed here cover writes that don't belong on a
single switch/sensor entity (renaming outlets, scheduling actions, etc).
All four take a ``device_id`` (HA device registry id) and resolve it
back to the owning coordinator.
"""

from __future__ import annotations

from typing import Final

import voluptuous as vol
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN
from .coordinator import WattboxCoordinator
from .wattbox_local.protocol import (
    SCHEDULE_ACTION_OFF,
    SCHEDULE_ACTION_ON,
    SCHEDULE_ACTION_RESET,
)

SERVICE_SET_OUTLET_NAME: Final[str] = "set_outlet_name"
SERVICE_SET_OUTLET_POWER_ON_DELAY: Final[str] = "set_outlet_power_on_delay"
SERVICE_ADD_SCHEDULE: Final[str] = "add_schedule"
SERVICE_ADD_HOST: Final[str] = "add_host"

ATTR_DEVICE_ID: Final[str] = "device_id"
ATTR_OUTLET: Final[str] = "outlet"
ATTR_OUTLETS: Final[str] = "outlets"
ATTR_SECONDS: Final[str] = "seconds"
ATTR_ACTION: Final[str] = "action"
ATTR_DAYS: Final[str] = "days"
ATTR_DATE: Final[str] = "date"
ATTR_TIME: Final[str] = "time"
ATTR_IP: Final[str] = "ip"

_ACTIONS: Final[dict[str, int]] = {
    "off": SCHEDULE_ACTION_OFF,
    "on": SCHEDULE_ACTION_ON,
    "reset": SCHEDULE_ACTION_RESET,
}

_DAYS: Final[tuple[str, ...]] = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")

_SCHEMA_SET_OUTLET_NAME = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_OUTLET): vol.All(int, vol.Range(min=1)),
        vol.Required(CONF_NAME): vol.All(cv.string, vol.Length(min=1)),
    }
)

_SCHEMA_SET_OUTLET_POWER_ON_DELAY = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_OUTLET): vol.All(int, vol.Range(min=1)),
        vol.Required(ATTR_SECONDS): vol.All(int, vol.Range(min=1, max=600)),
    }
)

_SCHEMA_ADD_SCHEDULE = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(CONF_NAME): vol.All(cv.string, vol.Length(min=1)),
        vol.Required(ATTR_OUTLETS): vol.All(
            cv.ensure_list, [vol.All(int, vol.Range(min=1))], vol.Length(min=1)
        ),
        vol.Required(ATTR_ACTION): vol.In(_ACTIONS),
        vol.Exclusive(ATTR_DAYS, "when"): vol.All(
            cv.ensure_list, [vol.In(_DAYS)], vol.Length(min=1)
        ),
        vol.Exclusive(ATTR_DATE, "when"): cv.string,
        vol.Required(ATTR_TIME): cv.string,
    }
)

_SCHEMA_ADD_HOST = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(CONF_NAME): vol.All(cv.string, vol.Length(min=1)),
        vol.Required(ATTR_IP): cv.string,
        vol.Required(ATTR_OUTLETS): vol.All(
            cv.ensure_list, [vol.All(int, vol.Range(min=1))], vol.Length(min=1)
        ),
    }
)


def _coordinator_for_device(hass: HomeAssistant, device_id: str) -> WattboxCoordinator:
    """Resolve an HA device_id to its WattBox coordinator.

    Raises HomeAssistantError if the id is unknown or not a WattBox.
    """
    registry = dr.async_get(hass)
    device = registry.async_get(device_id)
    if device is None:
        raise HomeAssistantError(f"Unknown device_id: {device_id}")
    service_tag: str | None = None
    for domain, ident in device.identifiers:
        if domain == DOMAIN:
            service_tag = ident
            break
    if service_tag is None:
        raise HomeAssistantError(f"Device {device_id} is not a WattBox device")
    for entry_id in device.config_entries:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            continue
        coordinator: WattboxCoordinator | None = entry.runtime_data
        if coordinator is not None and coordinator.data.info.service_tag == service_tag:
            return coordinator
    raise HomeAssistantError(f"No loaded WattBox config entry for device {device_id}")


def async_register_services(hass: HomeAssistant) -> None:
    """Register the four WattBox services. Idempotent."""
    if hass.services.has_service(DOMAIN, SERVICE_SET_OUTLET_NAME):
        return

    async def _set_outlet_name(call: ServiceCall) -> None:
        coord = _coordinator_for_device(hass, call.data[ATTR_DEVICE_ID])
        await coord.client.set_outlet_name(call.data[ATTR_OUTLET], call.data[CONF_NAME])
        await coord.async_request_refresh()

    async def _set_outlet_power_on_delay(call: ServiceCall) -> None:
        coord = _coordinator_for_device(hass, call.data[ATTR_DEVICE_ID])
        await coord.client.set_outlet_power_on_delay(
            call.data[ATTR_OUTLET], call.data[ATTR_SECONDS]
        )

    async def _add_schedule(call: ServiceCall) -> None:
        coord = _coordinator_for_device(hass, call.data[ATTR_DEVICE_ID])
        days_in = call.data.get(ATTR_DAYS)
        date_in = call.data.get(ATTR_DATE)
        if (days_in is None) == (date_in is None):
            raise HomeAssistantError("Provide exactly one of 'days' (recurring) or 'date' (once)")
        days_tuple: tuple[bool, bool, bool, bool, bool, bool, bool] | None = None
        if days_in is not None:
            selected = {d.lower() for d in days_in}
            days_tuple = tuple(d in selected for d in _DAYS)  # type: ignore[assignment]
        await coord.client.add_schedule(
            call.data[CONF_NAME],
            list(call.data[ATTR_OUTLETS]),
            _ACTIONS[call.data[ATTR_ACTION]],
            days=days_tuple,
            date=date_in,
            time=call.data[ATTR_TIME],
        )

    async def _add_host(call: ServiceCall) -> None:
        coord = _coordinator_for_device(hass, call.data[ATTR_DEVICE_ID])
        await coord.client.add_host(
            call.data[CONF_NAME], call.data[ATTR_IP], list(call.data[ATTR_OUTLETS])
        )

    hass.services.async_register(
        DOMAIN, SERVICE_SET_OUTLET_NAME, _set_outlet_name, schema=_SCHEMA_SET_OUTLET_NAME
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_OUTLET_POWER_ON_DELAY,
        _set_outlet_power_on_delay,
        schema=_SCHEMA_SET_OUTLET_POWER_ON_DELAY,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_ADD_SCHEDULE, _add_schedule, schema=_SCHEMA_ADD_SCHEDULE
    )
    hass.services.async_register(DOMAIN, SERVICE_ADD_HOST, _add_host, schema=_SCHEMA_ADD_HOST)


def async_unregister_services(hass: HomeAssistant, unloading_entry_id: str) -> None:
    """Remove services when the last WattBox config entry is being unloaded."""
    remaining = [
        e for e in hass.config_entries.async_entries(DOMAIN) if e.entry_id != unloading_entry_id
    ]
    if remaining:
        return
    for svc in (
        SERVICE_SET_OUTLET_NAME,
        SERVICE_SET_OUTLET_POWER_ON_DELAY,
        SERVICE_ADD_SCHEDULE,
        SERVICE_ADD_HOST,
    ):
        hass.services.async_remove(DOMAIN, svc)
