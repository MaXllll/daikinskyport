"""Characterization tests for the existing One+ Thermostat climate entity.

Instantiates the entity class directly (no full HA setup) and pins its
state mapping and the exact client calls its commands produce.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.components.climate.const import FAN_AUTO

from custom_components.daikinskyport.climate import (
    PRESET_SCHEDULE,
    Thermostat,
)
from custom_components.daikinskyport.const import DAIKIN_HVAC_MODE_AUXHEAT

from tests.conftest import make_fake_coordinator


@pytest.fixture
def oneplus_entity(oneplus_device_data):
    client = MagicMock()
    data = make_fake_coordinator(client)
    entity = Thermostat(data, 0, dict(oneplus_device_data))
    return entity, client


class TestStateMapping:
    def test_identity(self, oneplus_entity):
        entity, _ = oneplus_entity
        assert entity.unique_id == "0000aaaa-1111-2222-3333-444455556666-climate"
        assert entity.name == "Main Floor"

    def test_temperatures(self, oneplus_entity):
        entity, _ = oneplus_entity
        assert entity.current_temperature == 21.5
        # mode=1 (heat) -> target is the heat setpoint
        assert entity.hvac_mode == HVACMode.HEAT
        assert entity.target_temperature == 20.0
        assert entity.target_temperature_low is None
        assert entity.target_temperature_high is None

    def test_hvac_action_heating(self, oneplus_entity):
        entity, _ = oneplus_entity
        # equipmentStatus=3 -> heating
        assert entity.hvac_action == HVACAction.HEATING

    def test_available_modes_from_capabilities(self, oneplus_entity):
        entity, _ = oneplus_entity
        # Fixture: ctSystemCapHeat + 1 cool stage -> auto/heat/cool/off
        assert entity.hvac_modes == [
            HVACMode.AUTO,
            HVACMode.HEAT,
            HVACMode.COOL,
            HVACMode.OFF,
        ]

    def test_fan_and_preset(self, oneplus_entity):
        entity, _ = oneplus_entity
        assert entity.fan_mode == FAN_AUTO
        # schedEnabled + no override + not away -> Schedule preset
        assert entity.preset_mode == PRESET_SCHEDULE

    def test_humidity(self, oneplus_entity):
        entity, _ = oneplus_entity
        assert entity.current_humidity == 40
        assert entity.target_humidity == 45


class TestCommands:
    def test_set_hvac_mode_delegates_to_client(self, oneplus_entity):
        entity, client = oneplus_entity
        entity.set_hvac_mode(HVACMode.COOL)
        client.set_hvac_mode.assert_called_once_with(0, 2)

    def test_set_temperature_heat_mode_sets_temp_hold(self, oneplus_entity):
        entity, client = oneplus_entity
        entity.set_temperature(temperature=22.0)
        assert client.set_temp_hold.called
        args = client.set_temp_hold.call_args[0]
        # (index, cool_temp, heat_temp, duration): heat mode adjusts heat setpoint
        assert args[0] == 0
        assert args[2] == 22.0

    def test_aux_heat_switch_uses_auxheat_mode(self, oneplus_device_data):
        from custom_components.daikinskyport.switch import DaikinSkyportAuxHeat

        client = MagicMock()
        client.thermostats = [dict(oneplus_device_data)]
        client.get_thermostat.return_value = dict(oneplus_device_data)
        data = make_fake_coordinator(client)
        switch = DaikinSkyportAuxHeat(data, "Main Floor", 0)
        # No HA event loop in this harness; state write is out of scope here.
        switch.schedule_update_ha_state = MagicMock()
        switch.turn_on()
        client.set_hvac_mode.assert_called_once_with(0, DAIKIN_HVAC_MODE_AUXHEAT)
