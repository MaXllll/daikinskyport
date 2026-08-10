"""TDD spec for the DENEB (Aurora ductless) fan entity — hybrid layout.

One fan entity per head, designed for clean HomeKit bridging:

- Exactly ONE preset mode ("Auto"): with a single preset the HomeKit
  bridge maps it to the native TargetFanState Auto/Manual selector
  (clean Apple UI) instead of grouped anonymous switch toggles.
- Slider = quiet + manual speeds 1..5 (speed_count 6). Bottom step is
  Quiet (quieter = lower). Device values: 11=quiet, 3..7=speeds 1..5,
  10=auto.
- Econo/Powerful/Comfort are standalone switch entities, NOT fan
  presets, so they bridge as properly-named accessories.
- is_on mirrors head power (iduOnOff).

Fan-speed fields are per-hvac-mode (DENEB_MODE_FAN_FIELD); the fixture is
in cool mode (iduOperatingMode=2 -> iduCoolFanSpeed, value 10 = auto).
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from homeassistant.exceptions import HomeAssistantError

from tests.conftest import make_fake_coordinator


@pytest.fixture
def deneb_payload():
    return json.loads(
        (Path(__file__).parent / "fixtures" / "deneb_device_data.json").read_text()
    )


def _make_fan(deneb_payload):
    from custom_components.daikinskyport.fan_deneb import DaikinDenebFan

    client = MagicMock()
    payload = dict(deneb_payload)
    client.thermostats = [payload]
    data = make_fake_coordinator(client)
    return DaikinDenebFan(data, 0, payload), client, payload


class TestState:
    def test_identity_per_head_device(self, deneb_payload):
        entity, _, _ = _make_fan(deneb_payload)
        assert entity.unique_id == "dddddddd-0000-0000-0000-000000000004-fan"
        assert entity._attr_has_entity_name is True
        assert entity.device_info["identifiers"] == {
            ("daikinskyport", "dddddddd-0000-0000-0000-000000000004")
        }

    def test_single_auto_preset_for_native_homekit_selector(self, deneb_payload):
        # CRITICAL: exactly one preset -> HomeKit native Auto/Manual.
        # Adding more presets here regresses the Home app to grouped
        # anonymous switch toggles.
        entity, _, _ = _make_fan(deneb_payload)
        assert entity.preset_modes == ["Auto"]
        assert entity.speed_count == 6

    def test_is_on_mirrors_head_power(self, deneb_payload):
        entity, _, payload = _make_fan(deneb_payload)
        assert entity.is_on is True  # fixture: iduOnOff True
        payload["iduOnOff"] = False
        assert entity.is_on is False

    def test_auto_from_fixture(self, deneb_payload):
        # fixture: cool mode, iduCoolFanSpeed=10 (auto)
        entity, _, _ = _make_fan(deneb_payload)
        assert entity.preset_mode == "Auto"
        assert entity.percentage == 0

    def test_quiet_is_bottom_slider_step(self, deneb_payload):
        entity, _, payload = _make_fan(deneb_payload)
        payload["iduCoolFanSpeed"] = 11
        assert entity.preset_mode is None
        assert entity.percentage == 16  # 100 // 6

    def test_manual_speeds_map_to_upper_steps(self, deneb_payload):
        entity, _, payload = _make_fan(deneb_payload)
        payload["iduCoolFanSpeed"] = 3  # speed 1 -> step 2 of 6
        assert entity.percentage == 33
        payload["iduCoolFanSpeed"] = 7  # speed 5 -> step 6 of 6
        assert entity.percentage == 100
        assert entity.preset_mode is None

    def test_follows_active_mode_fan_field(self, deneb_payload):
        entity, _, payload = _make_fan(deneb_payload)
        payload["iduOperatingMode"] = 1  # heat -> iduHeatFanSpeed (11 = quiet)
        assert entity.preset_mode is None
        assert entity.percentage == 16

    def test_unavailable_when_device_offline(self, deneb_payload):
        entity, client, _ = _make_fan(deneb_payload)
        client.is_device_available = MagicMock(return_value=False)
        assert entity.available is False


class TestCommands:
    def test_turn_on_when_off_powers_head(self, deneb_payload):
        entity, client, payload = _make_fan(deneb_payload)
        payload["iduOnOff"] = False
        entity.turn_on()
        client.set_deneb_power.assert_called_once_with(0, True)

    def test_plain_turn_on_when_already_on_is_noop(self, deneb_payload):
        entity, client, _ = _make_fan(deneb_payload)
        entity.turn_on()
        client.set_deneb_power.assert_not_called()
        client.set_deneb_fan.assert_not_called()

    def test_turn_off_powers_head_down(self, deneb_payload):
        entity, client, _ = _make_fan(deneb_payload)
        entity.turn_off()
        client.set_deneb_power.assert_called_once_with(0, False)

    def test_set_percentage_bottom_step_is_quiet(self, deneb_payload):
        entity, client, _ = _make_fan(deneb_payload)
        entity.set_percentage(16)
        client.set_deneb_fan.assert_called_once_with(0, "iduCoolFanSpeed", 11)

    def test_set_percentage_upper_steps_are_speeds(self, deneb_payload):
        entity, client, _ = _make_fan(deneb_payload)
        entity.set_percentage(33)  # step 2 -> speed 1
        client.set_deneb_fan.assert_called_with(0, "iduCoolFanSpeed", 3)
        entity.set_percentage(100)  # step 6 -> speed 5
        client.set_deneb_fan.assert_called_with(0, "iduCoolFanSpeed", 7)

    def test_set_percentage_zero_turns_off(self, deneb_payload):
        entity, client, _ = _make_fan(deneb_payload)
        entity.set_percentage(0)
        client.set_deneb_power.assert_called_once_with(0, False)
        client.set_deneb_fan.assert_not_called()

    def test_preset_auto_sets_auto(self, deneb_payload):
        entity, client, _ = _make_fan(deneb_payload)
        entity.set_preset_mode("Auto")
        client.set_deneb_fan.assert_called_once_with(0, "iduCoolFanSpeed", 10)

    def test_turn_on_with_preset_routes_to_auto(self, deneb_payload):
        # HomeKit TargetFanState=Auto arrives as turn_on(preset_mode="Auto")
        entity, client, _ = _make_fan(deneb_payload)
        entity.turn_on(preset_mode="Auto")
        client.set_deneb_fan.assert_called_once_with(0, "iduCoolFanSpeed", 10)

    def test_turn_on_with_percentage_routes_to_speed(self, deneb_payload):
        # HomeKit TargetFanState=Manual arrives as turn_on(percentage=...)
        entity, client, _ = _make_fan(deneb_payload)
        entity.turn_on(percentage=50)  # step 3 -> speed 2
        client.set_deneb_fan.assert_called_once_with(0, "iduCoolFanSpeed", 4)

    def test_unknown_preset_rejected(self, deneb_payload):
        from homeassistant.exceptions import ServiceValidationError

        entity, _, _ = _make_fan(deneb_payload)
        with pytest.raises(ServiceValidationError):
            entity.set_preset_mode("Econo")

    def test_failed_command_raises(self, deneb_payload):
        entity, client, _ = _make_fan(deneb_payload)
        client.set_deneb_fan.return_value = None
        with pytest.raises(HomeAssistantError):
            entity.set_percentage(40)


class TestPlatformRouting:
    def test_only_deneb_heads_get_fans(self, deneb_payload, oneplus_device_data):
        import asyncio

        from custom_components.daikinskyport import fan as fan_platform
        from custom_components.daikinskyport.fan_deneb import DaikinDenebFan

        oneplus = dict(oneplus_device_data)
        oneplus.setdefault("id", "11111111-0000-0000-0000-000000000001")
        oneplus.setdefault("name", "Main Floor")
        client = MagicMock()
        client.thermostats = [dict(deneb_payload), oneplus]
        client.get_thermostat = lambda i: client.thermostats[i]
        data = make_fake_coordinator(client)

        hass = MagicMock()
        entry = MagicMock()
        entry.entry_id = "test-entry"
        hass.data = {"daikinskyport": {"test-entry": {"coordinator": data}}}

        added = []

        def _add(entities, update=False):
            added.extend(entities)

        asyncio.get_event_loop().run_until_complete(
            fan_platform.async_setup_entry(hass, entry, _add)
        )
        assert len(added) == 1
        assert isinstance(added[0], DaikinDenebFan)
