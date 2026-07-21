"""Shared test scaffolding for the daikinskyport integration.

Design notes
------------
* Tests run against fixtures captured from the real Skyport API
  (``tests/fixtures/``).  The ONEPLUS fixture is reconstructed from the
  repo's own ``API_info.md``; the ductless fixtures are captured from a
  live Daikin Aurora multi-zone system.
* ``mock_skyport_api`` intercepts HTTP at the ``requests`` layer, so the
  real client code (auth, retries, parsing) is exercised end to end
  without network access.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests_mock as requests_mock_lib

# Make `custom_components.daikinskyport` importable when pytest runs from
# the repository root.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

FIXTURES = Path(__file__).parent / "fixtures"

API = "https://api.daikinskyport.com"
LOGIN_URL = f"{API}/users/auth/login"
REFRESH_URL = f"{API}/users/auth/token"
DEVICES_URL = f"{API}/devices"


def load_fixture(name: str):
    """Load a JSON fixture by file name."""
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def oneplus_device_data():
    """Full /deviceData payload for a Daikin One+ ducted thermostat."""
    return load_fixture("oneplus_device_data.json")


@pytest.fixture
def oneplus_devices_list():
    """/devices payload for an account with one One+ thermostat."""
    return load_fixture("devices_oneplus.json")


@pytest.fixture
def client_config():
    """Minimal in-memory config for the DaikinSkyport client."""
    return {
        "EMAIL": "test@example.com",
        "PASSWORD": "hunter2",
        "ACCESS_TOKEN": "",
        "REFRESH_TOKEN": "",
    }


@pytest.fixture
def mock_skyport_api(oneplus_devices_list, oneplus_device_data):
    """Mock the Skyport HTTP API at the requests layer.

    Default behavior: successful login, one ONEPLUS device, full payload.
    Individual tests can re-register URLs to model other accounts.
    """
    with requests_mock_lib.Mocker() as m:
        m.post(
            LOGIN_URL,
            json={
                "accessToken": "test-access-token",
                "accessTokenExpiresIn": 3600,
                "refreshToken": "test-refresh-token",
                "tokenType": "Bearer",
            },
        )
        m.post(
            REFRESH_URL,
            json={
                "accessToken": "refreshed-access-token",
                "accessTokenExpiresIn": 3600,
                "tokenType": "Bearer",
            },
        )
        m.get(DEVICES_URL, json=oneplus_devices_list)
        for device in oneplus_devices_list:
            m.get(f"{API}/deviceData/{device['id']}", json=oneplus_device_data)
            m.put(f"{API}/deviceData/{device['id']}", json={"message": "Write sent"})
        yield m


@pytest.fixture
def skyport_client(client_config, mock_skyport_api):
    """A real DaikinSkyport client wired to the mocked API, tokens ready."""
    from custom_components.daikinskyport.daikinskyport import DaikinSkyport

    client = DaikinSkyport(config=client_config)
    return client


def make_fake_coordinator(client):
    """Build the minimal `data` object climate/sensor/switch entities expect.

    Mirrors DaikinSkyportData's public surface without needing a running
    Home Assistant instance.
    """
    return SimpleNamespace(
        daikinskyport=client,
        device_info=None,
        _async_update_data=MagicMock(),
    )
