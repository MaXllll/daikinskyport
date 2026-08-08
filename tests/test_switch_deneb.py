"""TDD spec for DENEB (Aurora ductless) feature switches.

Three per-head boolean features, all present in the live-captured
deviceData payload (tests/fixtures/deneb_device_data.json):

- Comfort airflow : iduWindNiceOperation      (remote "Comfort" button)
- Econo           : iduEconoModeSetting       (remote "Econo" button)
- Powerful        : oduPowerfulOperationRequest (remote "Powerful" button)

Same conventions as the DENEB climate entity: live payload reads through
a property, per-head DeviceInfo, availability from update_status, local
state mutated only after a confirmed PUT, HomeAssistantError on failure.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from homeassistant.exceptions import HomeAssistantError

from tests.conftest import API, DEVICES_URL, make_fake_coordinator


@pytest.fixture
def deneb_payload():
    return json.loads(
        (Path(__file__).parent / "fixtures" / "deneb_device_data.json").read_text()
    )


def _make_switch(deneb_payload, key):
    from custom_components.daikinskyport.switch_deneb import (
        DENEB_SWITCHES,
        DaikinDenebSwitch,
    )

    description = next(d for d in DENEB_SWITCHES if d.key == key)
    client = MagicMock()
    payload = dict(deneb_payload)
    client.thermostats = [payload]
    data = make_fake_coordinator(client)
    return DaikinDenebSwitch(data, 0, payload, description), client, payload


class TestDescriptions:
    def test_three_switches_with_verified_fields(self):
        from custom_components.daikinskyport.switch_deneb import DENEB_SWITCHES

        fields = {d.key: d.field for d in DENEB_SWITCHES}
        assert fields == {
            "comfort": "iduWindNiceOperation",
            "econo": "iduEconoModeSetting",
            "powerful": "oduPowerfulOperationRequest",
        }


class TestState:
    def test_identity_per_head_device(self, deneb_payload):
        entity, _, _ = _make_switch(deneb_payload, "comfort")
        assert entity.unique_id == (
            "dddddddd-0000-0000-0000-000000000004-iduWindNiceOperation"
        )
        assert entity._attr_has_entity_name is True
        assert entity.name == "Comfort airflow"
        assert entity.device_info["identifiers"] == {
            ("daikinskyport", "dddddddd-0000-0000-0000-000000000004")
        }
        assert entity.device_info["name"] == "Heatpump Bedroom2"

    @pytest.mark.parametrize("key", ["comfort", "econo", "powerful"])
    def test_is_on_reads_live_payload(self, deneb_payload, key):
        entity, _, payload = _make_switch(deneb_payload, key)
        assert entity.is_on is False  # all three False in the fixture
        payload[entity.entity_description.field] = True
        assert entity.is_on is True

    def test_live_read_survives_client_dict_replacement(self, deneb_payload):
        entity, client, _ = _make_switch(deneb_payload, "econo")
        fresh = dict(deneb_payload)
        fresh["iduEconoModeSetting"] = True
        client.thermostats = [fresh]
        assert entity.is_on is True

    def test_unavailable_when_device_offline(self, deneb_payload):
        entity, client, _ = _make_switch(deneb_payload, "powerful")
        client.is_device_available = MagicMock(return_value=False)
        assert entity.available is False


class TestCommands:
    @pytest.mark.parametrize("key,field", [
        ("comfort", "iduWindNiceOperation"),
        ("econo", "iduEconoModeSetting"),
        ("powerful", "oduPowerfulOperationRequest"),
    ])
    def test_turn_on_off_calls_flag_setter(self, deneb_payload, key, field):
        entity, client, _ = _make_switch(deneb_payload, key)
        entity.turn_on()
        client.set_deneb_flag.assert_called_with(0, field, True)
        entity.turn_off()
        client.set_deneb_flag.assert_called_with(0, field, False)

    def test_failed_command_raises(self, deneb_payload):
        entity, client, _ = _make_switch(deneb_payload, "comfort")
        client.set_deneb_flag.return_value = None
        with pytest.raises(HomeAssistantError):
            entity.turn_on()


class TestClientFlagWrites:
    """PUT bodies produced by the real client (mocked HTTP)."""

    @pytest.fixture
    def deneb_client(self, skyport_client, mock_skyport_api, deneb_payload):
        devices = json.loads(
            (Path(__file__).parent / "fixtures" / "devices_deneb.json").read_text()
        )
        mock_skyport_api.get(DEVICES_URL, json=devices)
        for dev in devices:
            payload = {
                k: v for k, v in deneb_payload.items()
                if k not in ("id", "name", "model")
            }
            mock_skyport_api.get(f"{API}/deviceData/{dev['id']}", json=payload)
            mock_skyport_api.put(
                f"{API}/deviceData/{dev['id']}", json={"message": "Write sent"}
            )
        skyport_client.request_tokens()
        skyport_client.update()
        return skyport_client

    def _last_put(self, mock_api):
        return [c for c in mock_api.request_history if c.method == "PUT"][-1]

    def test_set_deneb_flag_put_body_and_local_mutation(
        self, deneb_client, mock_skyport_api
    ):
        result = deneb_client.set_deneb_flag(0, "iduWindNiceOperation", True)
        assert result is not None
        assert self._last_put(mock_skyport_api).json() == {
            "iduWindNiceOperation": True
        }
        assert deneb_client.thermostats[0]["iduWindNiceOperation"] is True

    def test_set_deneb_flag_coerces_to_bool(self, deneb_client, mock_skyport_api):
        deneb_client.set_deneb_flag(0, "oduPowerfulOperationRequest", 1)
        assert self._last_put(mock_skyport_api).json() == {
            "oduPowerfulOperationRequest": True
        }

    def test_set_deneb_flag_no_mutation_on_failure(
        self, deneb_client, mock_skyport_api, deneb_payload
    ):
        device_id = deneb_client.thermostats[0]["id"]
        mock_skyport_api.put(
            f"{API}/deviceData/{device_id}",
            status_code=500,
            json={"error": "boom"},
        )
        result = deneb_client.set_deneb_flag(0, "iduEconoModeSetting", True)
        assert result is None
        assert deneb_client.thermostats[0]["iduEconoModeSetting"] is False


class TestPlatformRouting:
    def test_deneb_heads_get_three_switches_oneplus_gets_aux_heat(
        self, deneb_payload, oneplus_device_data
    ):
        import asyncio

        from custom_components.daikinskyport import switch as switch_platform

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
            switch_platform.async_setup_entry(hass, entry, _add)
        )

        from custom_components.daikinskyport.switch_deneb import DaikinDenebSwitch

        deneb_switches = [e for e in added if isinstance(e, DaikinDenebSwitch)]
        assert len(deneb_switches) == 3
        assert {e.entity_description.key for e in deneb_switches} == {
            "comfort", "econo", "powerful",
        }
        others = [e for e in added if not isinstance(e, DaikinDenebSwitch)]
        assert len(others) == 1  # the One+ aux-heat switch, unchanged
