"""Characterization tests for the DaikinSkyport API client.

These pin the CURRENT behavior of daikinskyport.py against the mocked
Skyport API so later changes (ductless support) can prove they did not
alter the existing One+ code paths.
"""
from __future__ import annotations

import json

import pytest

from custom_components.daikinskyport.daikinskyport import DaikinSkyport
from custom_components.daikinskyport.const import (
    DAIKIN_HVAC_MODE_HEAT,
    DAIKIN_HVAC_MODE_AUXHEAT,
)

from tests.conftest import API, LOGIN_URL, REFRESH_URL


class TestAuth:
    def test_request_tokens_stores_both_tokens(self, skyport_client, mock_skyport_api):
        result = skyport_client.request_tokens()

        assert result is not False
        assert skyport_client.access_token == "test-access-token"
        assert skyport_client.refresh_token == "test-refresh-token"
        login_calls = [c for c in mock_skyport_api.request_history if c.url == LOGIN_URL]
        assert len(login_calls) == 1
        assert login_calls[0].json() == {
            "email": "test@example.com",
            "password": "hunter2",
        }

    def test_refresh_tokens_updates_access_token(self, skyport_client, mock_skyport_api):
        skyport_client.refresh_token = "test-refresh-token"

        assert skyport_client.refresh_tokens() is True
        assert skyport_client.access_token == "refreshed-access-token"
        refresh_calls = [c for c in mock_skyport_api.request_history if c.url == REFRESH_URL]
        assert refresh_calls[0].json() == {
            "email": "test@example.com",
            "refreshToken": "test-refresh-token",
        }

    def test_failed_refresh_falls_back_to_full_login(self, skyport_client, mock_skyport_api):
        mock_skyport_api.post(REFRESH_URL, status_code=401, json={"error": "bad"})

        assert skyport_client.refresh_tokens() is True
        # Fallback path re-requests tokens with email+password.
        assert skyport_client.access_token == "test-access-token"


class TestDeviceFetch:
    def test_update_merges_identity_into_device_data(self, skyport_client, oneplus_device_data):
        skyport_client.request_tokens()
        skyport_client.update()

        assert len(skyport_client.thermostats) == 1
        merged = skyport_client.thermostats[0]
        # Identity fields from /devices are stamped onto the /deviceData payload.
        assert merged["id"] == "0000aaaa-1111-2222-3333-444455556666"
        assert merged["name"] == "Main Floor"
        assert merged["model"] == "ONEPLUS"
        # Telemetry preserved verbatim.
        assert merged["tempIndoor"] == oneplus_device_data["tempIndoor"]
        assert merged["mode"] == 1

    def test_get_sensors_oneplus_shape(self, skyport_client):
        skyport_client.request_tokens()
        skyport_client.update()

        sensors = skyport_client.get_sensors(0)
        by_key = {(s["name"], s["type"]): s["value"] for s in sensors}

        assert by_key[("Main Floor Outdoor", "temperature")] == -5.0
        assert by_key[("Main Floor Indoor", "temperature")] == 21.5
        assert by_key[("Main Floor Outdoor", "humidity")] == 70
        # DAIKIN_PERCENT_MULTIPLIER == 2 applied to demand values
        assert by_key[("Main Floor Outdoor heat pump", "demand")] == 60.0
        # power is x10
        assert by_key[("Main Floor Outdoor", "power")] == 2500
        # AQ available in fixture
        assert by_key[("Main Floor Outdoor", "particle")] == 8.2
        assert by_key[("Main Floor Indoor", "VOC")] == 120


class TestCommands:
    def test_set_hvac_mode_puts_mode_body(self, skyport_client, mock_skyport_api):
        skyport_client.request_tokens()
        skyport_client.update()

        skyport_client.set_hvac_mode(0, DAIKIN_HVAC_MODE_HEAT)

        put_calls = [c for c in mock_skyport_api.request_history if c.method == "PUT"]
        assert len(put_calls) == 1
        assert put_calls[0].url == f"{API}/deviceData/0000aaaa-1111-2222-3333-444455556666"
        assert put_calls[0].json() == {"mode": DAIKIN_HVAC_MODE_HEAT}
        # A successful write sets skip_next so the next poll doesn't clobber it.
        assert skyport_client.skip_next is True

    def test_set_temp_hold_puts_setpoints(self, skyport_client, mock_skyport_api):
        skyport_client.request_tokens()
        skyport_client.update()

        skyport_client.set_temp_hold(0, cool_temp=24.0, heat_temp=20.5, hold_duration=60)

        put_calls = [c for c in mock_skyport_api.request_history if c.method == "PUT"]
        body = put_calls[-1].json()
        assert body["hspHome"] == 20.5
        assert body["cspHome"] == 24.0
        assert body["schedOverride"] == 1

    def test_expired_token_on_put_triggers_refresh_and_retry(
        self, skyport_client, mock_skyport_api
    ):
        skyport_client.request_tokens()
        skyport_client.update()

        device_url = f"{API}/deviceData/0000aaaa-1111-2222-3333-444455556666"
        mock_skyport_api.put(
            device_url,
            [
                {
                    "status_code": 401,
                    "json": {"error": "authorization_expired"},
                },
                {"status_code": 200, "json": {"message": "Write sent"}},
            ],
        )

        result = skyport_client.make_request(0, {"mode": 1}, "test retry")

        assert result is not None
        put_calls = [c for c in mock_skyport_api.request_history if c.method == "PUT"]
        assert len(put_calls) == 2
        # Second attempt used the refreshed token.
        assert put_calls[-1].headers["Authorization"] == "Bearer refreshed-access-token"
