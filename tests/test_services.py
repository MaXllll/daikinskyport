"""Spec for the raw diagnostic services."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from homeassistant.exceptions import HomeAssistantError


@pytest.fixture
def deneb_payload():
    return json.loads(
        (Path(__file__).parent / "fixtures" / "deneb_device_data.json").read_text()
    )


def _make_hass(client):
    from custom_components.daikinskyport.const import COORDINATOR

    coordinator = MagicMock()
    coordinator.daikinskyport = client
    hass = MagicMock()
    hass.data = {"daikinskyport": {"entry": {COORDINATOR: coordinator}}}

    async def _executor(func, *args):
        return func(*args)

    hass.async_add_executor_job = _executor
    registered = {}

    def _register(domain, name, handler, schema=None, supports_response=None):
        registered[name] = handler

    hass.services.async_register = _register
    hass.services.has_service = MagicMock(return_value=False)
    return hass, registered


def test_raw_write_calls_make_request_and_raises_on_refusal(deneb_payload):
    from custom_components.daikinskyport.services import async_register_services

    client = MagicMock()
    client.thermostats = [dict(deneb_payload)]
    hass, registered = _make_hass(client)
    async_register_services(hass)

    call = MagicMock()
    call.data = {"device_index": 0, "body": {"iduWindNiceOperation": True}}
    client.make_request.return_value = MagicMock()  # success
    asyncio.get_event_loop().run_until_complete(registered["raw_write"](call))
    client.make_request.assert_called_once_with(
        0, {"iduWindNiceOperation": True}, "raw service write"
    )

    client.make_request.return_value = None  # cloud refused
    with pytest.raises(HomeAssistantError):
        asyncio.get_event_loop().run_until_complete(registered["raw_write"](call))


def test_raw_read_returns_fresh_device_data(deneb_payload):
    from custom_components.daikinskyport.services import async_register_services

    client = MagicMock()
    client.thermostats = [dict(deneb_payload)]
    client.get_thermostat_info.return_value = {"iduWindNiceOperation": True}
    hass, registered = _make_hass(client)
    async_register_services(hass)

    call = MagicMock()
    call.data = {"device_index": 0}
    result = asyncio.get_event_loop().run_until_complete(
        registered["raw_read"](call)
    )
    assert result["data"] == {"iduWindNiceOperation": True}
    client.get_thermostat_info.assert_called_once_with(deneb_payload["id"])


def test_raw_write_rejects_bad_index(deneb_payload):
    from custom_components.daikinskyport.services import async_register_services

    client = MagicMock()
    client.thermostats = [dict(deneb_payload)]
    hass, registered = _make_hass(client)
    async_register_services(hass)

    call = MagicMock()
    call.data = {"device_index": 7, "body": {"x": 1}}
    with pytest.raises(HomeAssistantError):
        asyncio.get_event_loop().run_until_complete(registered["raw_write"](call))
    client.make_request.assert_not_called()
