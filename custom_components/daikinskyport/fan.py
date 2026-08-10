"""Daikin Skyport fan platform (DENEB ductless heads only).

One+ ducted thermostats expose their fan through the climate entity and
custom services; only DENEB heads get a dedicated fan entity here.
"""
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DaikinSkyportData
from .const import COORDINATOR, DOMAIN
from .device_types import is_deneb_payload
from .fan_deneb import DaikinDenebFan


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add fan entities for ductless heads from a config_entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: DaikinSkyportData = data[COORDINATOR]

    entities = []
    for index in range(len(coordinator.daikinskyport.thermostats)):
        thermostat = coordinator.daikinskyport.get_thermostat(index)
        if is_deneb_payload(thermostat):
            entities.append(DaikinDenebFan(coordinator, index, thermostat))
    if entities:
        async_add_entities(entities, True)
