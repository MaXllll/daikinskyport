"""TDD gap tests: desired behavior for ductless (Aurora FTXV/CTXV) devices.

Originally written as strict-xfail specs documenting the KeyError crashes
ductless owners hit before device-type routing existed; now they are
permanent regression tests for that routing.

`DUCTLESS_STUB` is a deliberately minimal synthetic payload (an unknown
device shape) used to prove setup never crashes on devices we don't
recognize; the real DENEB coverage lives in tests/test_climate_deneb.py.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tests.conftest import make_fake_coordinator

# Placeholder until the Phase 0 schema capture replaces it.
DUCTLESS_STUB = {
    "id": "7777bbbb-8888-9999-0000-111122223333",
    "name": "Salon",
    "model": "AURORA_DUCTLESS_STUB",  # real model string TBD from capture
    # Deliberately missing: mode, cspActive, hspActive, tempOutdoor,
    # humOutdoor, timeZone, fanCirculate, equipmentStatus, schedEnabled...
    "tempIndoor": 23.0,
}


@pytest.fixture
def client_with_ductless(skyport_client, mock_skyport_api, oneplus_devices_list):
    """Account with one ductless head (and the API mocked accordingly)."""
    from tests.conftest import API, DEVICES_URL

    devices = [
        {
            "id": DUCTLESS_STUB["id"],
            "locationId": "9999ffff-8888-7777-6666-555544443333",
            "name": DUCTLESS_STUB["name"],
            "model": DUCTLESS_STUB["model"],
            "firmwareVersion": "1.0.0",
            "hasOwner": True,
            "hasWrite": True,
        }
    ]
    mock_skyport_api.get(DEVICES_URL, json=devices)
    mock_skyport_api.get(
        f"{API}/deviceData/{DUCTLESS_STUB['id']}",
        json={k: v for k, v in DUCTLESS_STUB.items() if k not in ("id", "name", "model")},
    )
    skyport_client.request_tokens()
    skyport_client.update()
    return skyport_client


# (The former xfail "ductless climate entity" test is superseded by the
# full spec in tests/test_climate_deneb.py, driven by the real captured
# DENEB fixture.)


def test_get_sensors_survives_ductless_payload(client_with_ductless):
    """get_sensors must not assume One+-only fields exist."""
    sensors = client_with_ductless.get_sensors(0)
    # Whatever it returns, it must not raise; indoor temp is present.
    assert any(
        s["type"] == "temperature" and s["value"] == 23.0 for s in sensors
    )


def test_get_sensors_oneplus_unchanged(skyport_client):
    """Regression guard: ductless-proofing must not change One+ sensors."""
    skyport_client.request_tokens()
    skyport_client.update()
    sensors = skyport_client.get_sensors(0)
    by_key = {(s["name"], s["type"]) for s in sensors}
    assert ("Main Floor Outdoor", "temperature") in by_key
    assert ("Main Floor Indoor", "power") in by_key


class TestDeviceTypeRouting:
    def test_classify_model(self):
        """A model-string router classifies ONEPLUS vs ductless vs unknown."""
        from custom_components.daikinskyport.device_types import classify_model

        assert classify_model("ONEPLUS").name == "ONEPLUS"
        assert classify_model("SOMETHING_NEVER_SEEN").name == "UNKNOWN"
        assert classify_model(None).name == "UNKNOWN"

    def test_oneplus_payload_shape_detection(self, oneplus_device_data):
        """Payload-shape check: safer than model strings for old thermostats
        whose model value we can't enumerate (e.g. One Lite)."""
        from custom_components.daikinskyport.device_types import is_oneplus_payload

        assert is_oneplus_payload(oneplus_device_data) is True
        assert is_oneplus_payload(DUCTLESS_STUB) is False

    def test_climate_setup_skips_unsupported_devices(
        self, skyport_client, mock_skyport_api, oneplus_devices_list, oneplus_device_data
    ):
        """Mixed account: One+ gets its entity, ductless is skipped, no crash."""
        from tests.conftest import API, DEVICES_URL
        from custom_components.daikinskyport.climate import iter_oneplus_thermostats

        devices = list(oneplus_devices_list) + [
            {
                "id": DUCTLESS_STUB["id"],
                "locationId": "9999ffff-8888-7777-6666-555544443333",
                "name": DUCTLESS_STUB["name"],
                "model": DUCTLESS_STUB["model"],
                "firmwareVersion": "1.0.0",
                "hasOwner": True,
                "hasWrite": True,
            }
        ]
        mock_skyport_api.get(DEVICES_URL, json=devices)
        mock_skyport_api.get(
            f"{API}/deviceData/{DUCTLESS_STUB['id']}",
            json={
                k: v
                for k, v in DUCTLESS_STUB.items()
                if k not in ("id", "name", "model")
            },
        )
        skyport_client.request_tokens()
        skyport_client.update()
        assert len(skyport_client.thermostats) == 2

        supported = list(iter_oneplus_thermostats(skyport_client))
        assert [(i, t["name"]) for i, t in supported] == [(0, "Main Floor")]
