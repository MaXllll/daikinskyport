"""Device-type detection for Daikin Skyport devices.

The Skyport cloud serves more than One+ ducted thermostats: North American
Aurora ductless mini-splits (FTXV/CTXV heads behind a 4MXTH multi-zone
outdoor unit, on AZAI6WSCDKB-class WiFi adapters) live on the same account
and API, but report a different deviceData schema. Historically this
integration assumed every device was a One+ thermostat and crashed with
KeyError on anything else.

Two complementary checks:

* ``classify_model`` — routes on the ``model`` string from ``/devices``.
  Conservative: only models we have positively identified are classified;
  everything else is UNKNOWN (never a crash).
* ``is_oneplus_payload`` — routes on payload shape, for robustness against
  model strings we have not enumerated (e.g. One Lite variants). A device
  only gets One+ entities if its payload actually carries the fields those
  entities read.
"""
from __future__ import annotations

from enum import Enum

from .const import _LOGGER


class DeviceType(Enum):
    """Kinds of devices a Skyport account can contain."""

    ONEPLUS = "oneplus"
    DUCTLESS = "ductless"
    UNKNOWN = "unknown"


# Model strings positively identified as One+/One Lite ducted thermostats.
ONEPLUS_MODELS = {"ONEPLUS", "ONELITE"}

# Model strings positively identified as ductless units.
# "DENEB": North American Aurora mini-splits (FTXV/CTXV heads on
# AZAI6WSCDKB-class adapters), verified against live hardware.
DUCTLESS_MODELS: set[str] = {"DENEB"}

# Fields the One+ Thermostat entity reads unconditionally in __init__ /
# async_update. A payload missing any of these cannot back a One+ entity.
ONEPLUS_REQUIRED_FIELDS = frozenset(
    {
        "mode",
        "cspActive",
        "hspActive",
        "fanCirculate",
        "fanCirculateSpeed",
        "geofencingAway",
        "schedOverride",
        "schedEnabled",
        "ctSystemCapHeat",
        "tempIndoor",
        "equipmentStatus",
    }
)


def classify_model(model: str | None) -> DeviceType:
    """Classify a ``/devices`` model string. Unknown strings are UNKNOWN."""
    if not model:
        return DeviceType.UNKNOWN
    normalized = str(model).strip().upper()
    if normalized in ONEPLUS_MODELS:
        return DeviceType.ONEPLUS
    if normalized in DUCTLESS_MODELS:
        return DeviceType.DUCTLESS
    return DeviceType.UNKNOWN


# Fields the DENEB ductless climate entity reads unconditionally.
DENEB_REQUIRED_FIELDS = frozenset(
    {
        "iduOnOff",
        "iduOperatingMode",
        "iduRoomTemp",
        "iduHeatSetpoint",
        "iduCoolSetpoint",
    }
)


def is_oneplus_payload(thermostat: dict) -> bool:
    """Return True when a deviceData payload has the One+ thermostat shape."""
    return ONEPLUS_REQUIRED_FIELDS.issubset(thermostat.keys())


def is_deneb_payload(thermostat: dict) -> bool:
    """Return True when a deviceData payload has the DENEB ductless shape."""
    return DENEB_REQUIRED_FIELDS.issubset(thermostat.keys())


def log_skipped_device(thermostat: dict, platform: str) -> None:
    """Log that a non-One+ device was skipped by a One+-only platform.

    Known ductless (DENEB) devices are expected to be skipped by One+
    platforms (weather/switch) — that's routine, so log at debug to avoid
    warning spam on every reload. Truly unknown shapes stay at warning.
    """
    if is_deneb_payload(thermostat):
        _LOGGER.debug(
            "Device '%s' is a ductless head; the %s platform is One+-only.",
            thermostat.get("adptDeviceName") or thermostat.get("name", "<unnamed>"),
            platform,
        )
        return
    _LOGGER.warning(
        "Skipping device '%s' (model: %s) for %s: unrecognized device "
        "payload. Setup continues without it.",
        thermostat.get("name", "<unnamed>"),
        thermostat.get("model", "<unknown>"),
        platform,
    )
