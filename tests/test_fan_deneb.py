"""TDD spec for the DENEB (Aurora ductless) fan entity.

One fan entity per head, designed for clean HomeKit bridging:

- percentage slider = manual fan speeds 1..5 (device values 3..7),
  speed_count 5 so HomeKit gets 20% steps. No more "0% means auto".
- preset_modes = ["Auto", "Quiet", "Econo", "Powerful"]. In the HomeKit
  bridge each preset renders as a labeled toggle inside the fan tile.
- is_on mirrors head power (iduOnOff): the fan tile IS the head's fan.
- Preset semantics are exclusive (matching the IR remote, where Powerful
  overrides Econo): activating one clears the conflicting boosts.
- HomeKit deactivates a preset by calling plain turn_on: on an already-on
  head that clears Powerful, then Econo, then Quiet->Auto, else no-op.

Fan-speed fields are per-hvac-mode (DENEB_MODE_FAN_FIELD); the fixture is
in cool mode (iduOperatingMode=2 -> iduCoolFanSpeed, value 10 = auto).
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, call

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

    def test_speed_count_and_presets(self, deneb_payload):
        entity, _, _ = _make_fan(deneb_payload)
        assert entity.speed_count == 5
        assert entity.preset_modes == ["Auto", "Quiet", "Econo", "Powerful"]

    def test_is_on_mirrors_head_power(self, deneb_payload):
        entity, _, payload = _make_fan(deneb_payload)
        assert entity.is_on is True  # fixture: iduOnOff True
        payload["iduOnOff"] = False
        assert entity.is_on is False

    def test_preset_auto_from_fixture(self, deneb_payload):
        # fixture: cool mode, iduCoolFanSpeed=10 (auto), no boosts
        entity, _, _ = _make_fan(deneb_payload)
        assert entity.preset_mode == "Auto"
        assert entity.percentage == 0

    def test_preset_quiet(self, deneb_payload):
        entity, _, payload = _make_fan(deneb_payload)
        payload["iduCoolFanSpeed"] = 11
        assert entity.preset_mode == "Quiet"

    def test_manual_speed_maps_to_percentage(self, deneb_payload):
        entity, _, payload = _make_fan(deneb_payload)
        payload["iduCoolFanSpeed"] = 5  # speed 3 of 5
        assert entity.preset_mode is None
        assert entity.percentage == 60

    def test_preset_priority_powerful_over_econo_over_fan(self, deneb_payload):
        entity, _, payload = _make_fan(deneb_payload)
        payload["iduEconoModeSetting"] = True
        assert entity.preset_mode == "Econo"
        payload["oduPowerfulOperationRequest"] = True
        assert entity.preset_mode == "Powerful"

    def test_follows_active_mode_fan_field(self, deneb_payload):
        entity, _, payload = _make_fan(deneb_payload)
        payload["iduOperatingMode"] = 1  # heat -> iduHeatFanSpeed (11 = quiet)
        assert entity.preset_mode == "Quiet"

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

    def test_turn_off_powers_head_down(self, deneb_payload):
        entity, client, _ = _make_fan(deneb_payload)
        entity.turn_off()
        client.set_deneb_power.assert_called_once_with(0, False)

    def test_plain_turn_on_clears_powerful_first(self, deneb_payload):
        entity, client, payload = _make_fan(deneb_payload)
        payload["oduPowerfulOperationRequest"] = True
        payload["iduEconoModeSetting"] = True
        entity.turn_on()
        client.set_deneb_flag.assert_called_once_with(
            0, "oduPowerfulOperationRequest", False
        )

    def test_plain_turn_on_clears_econo_when_no_powerful(self, deneb_payload):
        entity, client, payload = _make_fan(deneb_payload)
        payload["iduEconoModeSetting"] = True
        entity.turn_on()
        client.set_deneb_flag.assert_called_once_with(
            0, "iduEconoModeSetting", False
        )

    def test_plain_turn_on_quiet_falls_back_to_auto(self, deneb_payload):
        entity, client, payload = _make_fan(deneb_payload)
        payload["iduCoolFanSpeed"] = 11
        entity.turn_on()
        client.set_deneb_fan.assert_called_once_with(0, "iduCoolFanSpeed", 10)

    def test_set_percentage_writes_speed(self, deneb_payload):
        entity, client, _ = _make_fan(deneb_payload)
        entity.set_percentage(60)
        client.set_deneb_fan.assert_called_once_with(0, "iduCoolFanSpeed", 5)

    def test_set_percentage_zero_turns_off(self, deneb_payload):
        entity, client, _ = _make_fan(deneb_payload)
        entity.set_percentage(0)
        client.set_deneb_power.assert_called_once_with(0, False)
        client.set_deneb_fan.assert_not_called()

    def test_preset_auto_clears_boosts_and_sets_auto(self, deneb_payload):
        entity, client, payload = _make_fan(deneb_payload)
        payload["iduEconoModeSetting"] = True
        entity.set_preset_mode("Auto")
        assert client.set_deneb_flag.call_args_list == [
            call(0, "iduEconoModeSetting", False)
        ]
        client.set_deneb_fan.assert_called_once_with(0, "iduCoolFanSpeed", 10)

    def test_preset_quiet_sets_quiet(self, deneb_payload):
        entity, client, _ = _make_fan(deneb_payload)
        entity.set_preset_mode("Quiet")
        client.set_deneb_fan.assert_called_once_with(0, "iduCoolFanSpeed", 11)

    def test_preset_econo_turns_off_powerful_first(self, deneb_payload):
        entity, client, payload = _make_fan(deneb_payload)
        payload["oduPowerfulOperationRequest"] = True
        entity.set_preset_mode("Econo")
        assert client.set_deneb_flag.call_args_list == [
            call(0, "oduPowerfulOperationRequest", False),
            call(0, "iduEconoModeSetting", True),
        ]

    def test_preset_powerful_turns_off_econo_first(self, deneb_payload):
        entity, client, payload = _make_fan(deneb_payload)
        payload["iduEconoModeSetting"] = True
        entity.set_preset_mode("Powerful")
        assert client.set_deneb_flag.call_args_list == [
            call(0, "iduEconoModeSetting", False),
            call(0, "oduPowerfulOperationRequest", True),
        ]

    def test_unknown_preset_rejected(self, deneb_payload):
        from homeassistant.exceptions import ServiceValidationError

        entity, _, _ = _make_fan(deneb_payload)
        with pytest.raises(ServiceValidationError):
            entity.set_preset_mode("Turbo")

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
