"""Diagnostic services for the Daikin Skyport integration.

Two low-level services for exploring/driving the raw Skyport cloud API on
a specific device — the same seam the entities use, exposed for
diagnostics and for features the entities don't model yet:

- daikinskyport.raw_write: PUT an arbitrary JSON body to
  /deviceData/{id}. Raises on a refused/failed write.
- daikinskyport.raw_read: fetch the device's full current deviceData and
  return it as the service response (supports response data).

Both take ``device_index`` (position in the account's device list, as
shown in the integration's debug logs) rather than an entity, because
they exist precisely for fields no entity exposes.
"""
from __future__ import annotations

import voluptuous as vol

from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError
import homeassistant.helpers.config_validation as cv

from .const import _LOGGER, DOMAIN, COORDINATOR

SERVICE_RAW_WRITE = "raw_write"
SERVICE_RAW_READ = "raw_read"

RAW_WRITE_SCHEMA = vol.Schema(
    {
        vol.Required("device_index"): vol.All(vol.Coerce(int), vol.Range(min=0)),
        vol.Required("body"): dict,
    }
)

RAW_READ_SCHEMA = vol.Schema(
    {
        vol.Required("device_index"): vol.All(vol.Coerce(int), vol.Range(min=0)),
    }
)


def _first_coordinator(hass: HomeAssistant):
    for entry_data in hass.data.get(DOMAIN, {}).values():
        if isinstance(entry_data, dict) and COORDINATOR in entry_data:
            return entry_data[COORDINATOR]
    raise HomeAssistantError("No Daikin Skyport coordinator is loaded.")


def async_register_services(hass: HomeAssistant) -> None:
    """Register the raw diagnostic services (idempotent)."""

    async def handle_raw_write(call: ServiceCall) -> None:
        coordinator = _first_coordinator(hass)
        index = call.data["device_index"]
        body = call.data["body"]
        client = coordinator.daikinskyport
        if index >= len(client.thermostats):
            raise HomeAssistantError(f"No device at index {index}.")
        _LOGGER.warning("raw_write to device %s: %s", index, body)
        result = await hass.async_add_executor_job(
            client.make_request, index, body, "raw service write"
        )
        if result is None:
            raise HomeAssistantError(
                f"Skyport refused raw write {body} for device index {index} "
                "(see log for the API response)."
            )

    async def handle_raw_read(call: ServiceCall) -> ServiceResponse:
        coordinator = _first_coordinator(hass)
        index = call.data["device_index"]
        client = coordinator.daikinskyport
        if index >= len(client.thermostats):
            raise HomeAssistantError(f"No device at index {index}.")
        device_id = client.thermostats[index]["id"]
        data = await hass.async_add_executor_job(
            client.get_thermostat_info, device_id
        )
        if data is None:
            raise HomeAssistantError(f"Device index {index} is offline.")
        return {"device_index": index, "data": data}

    if not hass.services.has_service(DOMAIN, SERVICE_RAW_WRITE):
        hass.services.async_register(
            DOMAIN, SERVICE_RAW_WRITE, handle_raw_write, schema=RAW_WRITE_SCHEMA
        )
    if not hass.services.has_service(DOMAIN, SERVICE_RAW_READ):
        hass.services.async_register(
            DOMAIN,
            SERVICE_RAW_READ,
            handle_raw_read,
            schema=RAW_READ_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )
