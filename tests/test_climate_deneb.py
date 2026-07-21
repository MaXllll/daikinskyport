"""TDD spec for the DENEB (Aurora ductless) climate entity.

Fixture = real captured payload from a live 4MXTH36AVJU9 + FTXV/CTXV
system (state at capture: powered on,
cool mode, target 23, room 22.0C / 60% RH).

Verified facts encoded here (see project doc daikin-deneb-api-findings.md):
- mode enum: 0=fan_only, 1=heat, 2=cool, 3=auto, 5=dry (4, 6 invalid)
- power is iduOnOff (bool), separate from mode
- setpoint per mode: iduHeatSetpoint / iduCoolSetpoint / iduAutoSetpoint
- fan per mode: idu{Heat,Cool,Auto,Dry,FanMode}FanSpeed;
  values 3..7 = speeds 1..5, 10 = auto, 11 = quiet
- writes are direct field PUTs to /deviceData/{id}
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from homeassistant.components.climate import (
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)

from tests.conftest import make_fake_coordinator


@pytest.fixture
def deneb_payload(request):
    import json
    from pathlib import Path

    return json.loads(
        (Path(__file__).parent / "fixtures" / "deneb_device_data.json").read_text()
    )


@pytest.fixture
def deneb_entity(deneb_payload):
    from custom_components.daikinskyport.climate_deneb import DaikinDenebClimate

    client = MagicMock()
    payload = dict(deneb_payload)
    client.thermostats = [payload]
    data = make_fake_coordinator(client)
    entity = DaikinDenebClimate(data, 0, payload)
    return entity, client


class TestStateMapping:
    def test_identity_uses_cleaned_adapter_name_on_device(self, deneb_entity):
        entity, _ = deneb_entity
        assert entity.unique_id == "dddddddd-0000-0000-0000-000000000004-climate"
        # has_entity_name convention: entity name None, device carries the
        # cleaned (underscore-free) adapter name.
        assert entity._attr_has_entity_name is True
        assert entity.device_info["name"] == "Heatpump Bedroom2"

    def test_on_cool_maps_to_cool_mode(self, deneb_entity):
        entity, _ = deneb_entity
        # fixture: iduOnOff=True, iduOperatingMode=2
        assert entity.hvac_mode == HVACMode.COOL

    def test_target_follows_active_mode_setpoint(self, deneb_entity):
        entity, _ = deneb_entity
        # cool mode -> iduCoolSetpoint (23)
        assert entity.target_temperature == 23

    def test_current_temperature_and_humidity(self, deneb_entity):
        entity, _ = deneb_entity
        assert entity.current_temperature == 22
        assert entity.current_humidity == 60

    def test_fan_mode_auto(self, deneb_entity):
        entity, _ = deneb_entity
        # cool mode active, iduCoolFanSpeed=10 -> auto
        assert entity.fan_mode == "auto"
        assert set(entity.fan_modes) == {"auto", "quiet", "1", "2", "3", "4", "5"}

    def test_hvac_action_idle_when_not_conditioning(self, deneb_entity):
        entity, _ = deneb_entity
        # fixture: iduThermoState=False while on -> idle
        assert entity.hvac_action == HVACAction.IDLE

    def test_all_supported_modes_exposed(self, deneb_entity):
        entity, _ = deneb_entity
        assert set(entity.hvac_modes) == {
            HVACMode.OFF,
            HVACMode.HEAT,
            HVACMode.COOL,
            HVACMode.AUTO,
            HVACMode.DRY,
            HVACMode.FAN_ONLY,
        }

    def test_supported_features(self, deneb_entity):
        entity, _ = deneb_entity
        assert entity.supported_features & ClimateEntityFeature.TARGET_TEMPERATURE
        assert entity.supported_features & ClimateEntityFeature.FAN_MODE
        assert entity.supported_features & ClimateEntityFeature.TURN_OFF
        assert entity.supported_features & ClimateEntityFeature.TURN_ON

    def test_off_state(self, deneb_payload):
        from custom_components.daikinskyport.climate_deneb import DaikinDenebClimate

        payload = dict(deneb_payload)
        payload["iduOnOff"] = False
        client = MagicMock()
        client.thermostats = [payload]
        entity = DaikinDenebClimate(make_fake_coordinator(client), 0, payload)
        assert entity.hvac_mode == HVACMode.OFF
        assert entity.hvac_action == HVACAction.OFF
        # target still shown from last active mode so the card is useful
        assert entity.target_temperature == 23


class TestCommands:
    def test_set_hvac_mode_off_powers_down(self, deneb_entity):
        entity, client = deneb_entity
        entity.set_hvac_mode(HVACMode.OFF)
        client.set_deneb_power.assert_called_once_with(0, False)

    def test_set_hvac_mode_heat_powers_on_with_mode(self, deneb_entity):
        entity, client = deneb_entity
        entity.set_hvac_mode(HVACMode.HEAT)
        client.set_deneb_mode.assert_called_once_with(0, 1)

    def test_set_hvac_mode_dry_and_fan_only(self, deneb_entity):
        entity, client = deneb_entity
        entity.set_hvac_mode(HVACMode.DRY)
        client.set_deneb_mode.assert_called_with(0, 5)
        entity.set_hvac_mode(HVACMode.FAN_ONLY)
        client.set_deneb_mode.assert_called_with(0, 0)

    def test_set_temperature_targets_active_mode_field(self, deneb_entity):
        entity, client = deneb_entity
        entity.set_temperature(temperature=24.5)
        # active mode is cool -> write iduCoolSetpoint
        client.set_deneb_setpoint.assert_called_once_with(0, "iduCoolSetpoint", 24.5)

    def test_set_fan_mode_writes_active_mode_fan_field(self, deneb_entity):
        entity, client = deneb_entity
        entity.set_fan_mode("quiet")
        client.set_deneb_fan.assert_called_once_with(0, "iduCoolFanSpeed", 11)
        entity.set_fan_mode("3")
        client.set_deneb_fan.assert_called_with(0, "iduCoolFanSpeed", 5)

    def test_turn_on_off(self, deneb_entity):
        entity, client = deneb_entity
        entity.turn_off()
        client.set_deneb_power.assert_called_with(0, False)
        entity.turn_on()
        client.set_deneb_power.assert_called_with(0, True)


class TestClientDenebCommands:
    """PUT bodies produced by the real client (mocked HTTP)."""

    @pytest.fixture
    def deneb_client(self, skyport_client, mock_skyport_api, deneb_payload):
        from tests.conftest import API, DEVICES_URL
        import json
        from pathlib import Path

        devices = json.loads(
            (Path(__file__).parent / "fixtures" / "devices_deneb.json").read_text()
        )
        mock_skyport_api.get(DEVICES_URL, json=devices)
        for dev in devices:
            payload = {k: v for k, v in deneb_payload.items() if k not in ("id", "name", "model")}
            mock_skyport_api.get(f"{API}/deviceData/{dev['id']}", json=payload)
            mock_skyport_api.put(f"{API}/deviceData/{dev['id']}", json={"message": "Write sent"})
        skyport_client.request_tokens()
        skyport_client.update()
        return skyport_client

    def _last_put(self, mock_api):
        return [c for c in mock_api.request_history if c.method == "PUT"][-1]

    def test_set_deneb_power(self, deneb_client, mock_skyport_api):
        deneb_client.set_deneb_power(0, True)
        assert self._last_put(mock_skyport_api).json() == {"iduOnOff": True}

    def test_set_deneb_mode_powers_on_and_sets_mode(self, deneb_client, mock_skyport_api):
        deneb_client.set_deneb_mode(0, 1)
        assert self._last_put(mock_skyport_api).json() == {
            "iduOnOff": True,
            "iduOperatingMode": 1,
        }

    def test_set_deneb_setpoint_rounds_to_half_degree(self, deneb_client, mock_skyport_api):
        deneb_client.set_deneb_setpoint(0, "iduHeatSetpoint", 21.3)
        assert self._last_put(mock_skyport_api).json() == {"iduHeatSetpoint": 21.5}

    def test_set_deneb_fan(self, deneb_client, mock_skyport_api):
        deneb_client.set_deneb_fan(0, "iduCoolFanSpeed", 11)
        assert self._last_put(mock_skyport_api).json() == {"iduCoolFanSpeed": 11}

    def test_deneb_get_sensors(self, deneb_client):
        sensors = deneb_client.get_sensors(0)
        by_key = {(s["name"], s["type"]): s["value"] for s in sensors}
        assert by_key[("Heatpump_Bedroom2 Indoor", "temperature")] == 22
        assert by_key[("Heatpump_Bedroom2 Indoor", "humidity")] == 60
        assert by_key[("Heatpump_Bedroom2 Outdoor", "temperature")] == 21.5


class TestSafetyGuards:
    """Review-driven guards: multi-zone conflicts, clamping, honesty, availability."""

    def _make_two_heads(self, deneb_payload, own_mode=2, other_on=True, other_mode=2):
        from custom_components.daikinskyport.climate_deneb import DaikinDenebClimate

        other = dict(deneb_payload)
        other["id"] = "dddddddd-0000-0000-0000-000000000001"
        other["adptDeviceName"] = "Heatpump_LivingRoom"
        other["iduOnOff"] = other_on
        other["iduOperatingMode"] = other_mode
        own = dict(deneb_payload)
        own["iduOnOff"] = False
        own["iduOperatingMode"] = own_mode
        client = MagicMock()
        client.thermostats = [other, own]
        entity = DaikinDenebClimate(make_fake_coordinator(client), 1, own)
        return entity, client

    def test_heat_blocked_while_other_head_cools(self, deneb_payload):
        from homeassistant.exceptions import ServiceValidationError
        from homeassistant.components.climate import HVACMode

        entity, client = self._make_two_heads(deneb_payload, other_mode=2)  # other cooling
        with pytest.raises(ServiceValidationError):
            entity.set_hvac_mode(HVACMode.HEAT)
        client.set_deneb_mode.assert_not_called()

    def test_cool_blocked_while_other_head_heats(self, deneb_payload):
        from homeassistant.exceptions import ServiceValidationError
        from homeassistant.components.climate import HVACMode

        entity, client = self._make_two_heads(deneb_payload, other_mode=1)  # other heating
        with pytest.raises(ServiceValidationError):
            entity.set_hvac_mode(HVACMode.COOL)

    def test_no_conflict_when_other_head_off(self, deneb_payload):
        from homeassistant.components.climate import HVACMode

        entity, client = self._make_two_heads(deneb_payload, other_on=False, other_mode=2)
        entity.set_hvac_mode(HVACMode.HEAT)  # must not raise
        client.set_deneb_mode.assert_called_once_with(1, 1)

    def test_fan_only_never_conflicts(self, deneb_payload):
        from homeassistant.components.climate import HVACMode

        entity, client = self._make_two_heads(deneb_payload, other_mode=1)  # other heating
        entity.set_hvac_mode(HVACMode.FAN_ONLY)  # airflow only: allowed
        client.set_deneb_mode.assert_called_once_with(1, 0)

    def test_setpoint_out_of_bounds_rejected(self, deneb_entity):
        from homeassistant.exceptions import ServiceValidationError

        entity, client = deneb_entity
        with pytest.raises(ServiceValidationError):
            entity.set_temperature(temperature=40)
        with pytest.raises(ServiceValidationError):
            entity.set_temperature(temperature=5)
        client.set_deneb_setpoint.assert_not_called()

    def test_failed_command_raises_and_keeps_state(self, deneb_entity):
        from homeassistant.exceptions import HomeAssistantError

        entity, client = deneb_entity
        client.set_deneb_power.return_value = None  # make_request failure
        with pytest.raises(HomeAssistantError):
            entity.turn_off()
        # optimistic state NOT applied on failure
        assert entity.thermostat["iduOnOff"] is True

    def test_unavailable_when_device_offline(self, deneb_entity):
        entity, client = deneb_entity
        client.is_device_available.return_value = False
        assert entity.available is False
        client.is_device_available.return_value = True
        assert entity.available is True

    def test_diagnostic_attributes_exposed(self, deneb_entity):
        entity, _ = deneb_entity
        attrs = entity.extra_state_attributes
        assert attrs["mode_refusal"] is False
        assert attrs["defrosting"] is False


class TestClientRobustness:
    def test_deneb_write_mutates_only_after_success(
        self, skyport_client, mock_skyport_api, deneb_payload
    ):
        from tests.conftest import API, DEVICES_URL
        import json
        from pathlib import Path

        devices = json.loads(
            (Path(__file__).parent / "fixtures" / "devices_deneb.json").read_text()
        )[:1]
        payload = {k: v for k, v in deneb_payload.items() if k not in ("id", "name", "model")}
        mock_skyport_api.get(DEVICES_URL, json=devices)
        mock_skyport_api.get(f"{API}/deviceData/{devices[0]['id']}", json=payload)
        mock_skyport_api.put(
            f"{API}/deviceData/{devices[0]['id']}",
            status_code=500,
            json={"error": "boom"},
        )
        skyport_client.request_tokens()
        skyport_client.update()

        before = skyport_client.thermostats[0]["iduOnOff"]
        result = skyport_client.set_deneb_power(0, not before)

        assert result is None
        assert skyport_client.thermostats[0]["iduOnOff"] == before  # unchanged
        assert skyport_client.skip_next is False  # failed write must not skip polls

    def test_offline_device_marked_unavailable(
        self, skyport_client, mock_skyport_api, deneb_payload
    ):
        from tests.conftest import API, DEVICES_URL
        import json
        from pathlib import Path

        devices = json.loads(
            (Path(__file__).parent / "fixtures" / "devices_deneb.json").read_text()
        )[:1]
        payload = {k: v for k, v in deneb_payload.items() if k not in ("id", "name", "model")}
        mock_skyport_api.get(DEVICES_URL, json=devices)
        device_url = f"{API}/deviceData/{devices[0]['id']}"
        mock_skyport_api.get(device_url, json=payload)
        skyport_client.request_tokens()
        skyport_client.update()
        assert skyport_client.is_device_available(0) is True

        # Device goes offline: API answers 400 DeviceOfflineException
        mock_skyport_api.get(
            device_url, status_code=400, json={"message": "DeviceOfflineException"}
        )
        skyport_client.update()
        assert skyport_client.is_device_available(0) is False
        # stale data retained (so HA can show last-known values as unavailable)
        assert skyport_client.thermostats[0]["iduRoomTemp"] == 22


class TestRouting:
    def test_deneb_model_classified_ductless(self):
        from custom_components.daikinskyport.device_types import classify_model, DeviceType

        assert classify_model("DENEB") is DeviceType.DUCTLESS

    def test_deneb_payload_not_oneplus(self, deneb_payload):
        from custom_components.daikinskyport.device_types import (
            is_oneplus_payload,
            is_deneb_payload,
        )

        assert is_oneplus_payload(deneb_payload) is False
        assert is_deneb_payload(deneb_payload) is True
