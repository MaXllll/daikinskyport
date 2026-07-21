"""Climate entity for Daikin DENEB devices (North American Aurora ductless).

Covers FTXV/CTXV high-wall heads behind a 4MXTH multi-zone outdoor unit,
connected via AZAI6WSCDKB-class WiFi adapters to the Skyport cloud.

All field semantics were verified against live hardware; see the
repo docs. Key model notes:
- Power (iduOnOff) is separate from mode; mode persists while off.
- One setpoint per mode (heat/cool/auto); dry and fan-only have none.
- One fan-speed field per mode.
- iduThermoState indicates active conditioning (compressor engaged).
- All running heads share ONE outdoor unit: simultaneous heating and
  cooling across zones is physically impossible (the unit refuses and
  raises iduControlModeRefusalState). set_hvac_mode guards against
  creating such conflicts instead of silently pretending they work.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_HALVES, UnitOfTemperature
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo

from .const import (
    _LOGGER,
    DOMAIN,
    MANUFACTURER,
    DENEB_MODE_FAN_ONLY,
    DENEB_MODE_HEAT,
    DENEB_MODE_COOL,
    DENEB_MODE_AUTO,
    DENEB_MODE_DRY,
    DENEB_MODE_SETPOINT_FIELD,
    DENEB_MODE_FAN_FIELD,
)
from .device_types import is_deneb_payload

DENEB_MODE_TO_HASS = {
    DENEB_MODE_FAN_ONLY: HVACMode.FAN_ONLY,
    DENEB_MODE_HEAT: HVACMode.HEAT,
    DENEB_MODE_COOL: HVACMode.COOL,
    DENEB_MODE_AUTO: HVACMode.AUTO,
    DENEB_MODE_DRY: HVACMode.DRY,
}
HASS_TO_DENEB_MODE = {v: k for k, v in DENEB_MODE_TO_HASS.items()}

DENEB_MODE_TO_ACTION = {
    DENEB_MODE_FAN_ONLY: HVACAction.FAN,
    DENEB_MODE_HEAT: HVACAction.HEATING,
    DENEB_MODE_COOL: HVACAction.COOLING,
    DENEB_MODE_DRY: HVACAction.DRYING,
}

# Thermal direction of each mode for multi-zone conflict detection.
# Dry runs the cooling cycle. Auto and fan-only are flexible/neutral.
_HEATING_MODES = {DENEB_MODE_HEAT}
_COOLING_MODES = {DENEB_MODE_COOL, DENEB_MODE_DRY}

DENEB_FAN_TO_HASS = {3: "1", 4: "2", 5: "3", 6: "4", 7: "5", 10: "auto", 11: "quiet"}
HASS_TO_DENEB_FAN = {v: k for k, v in DENEB_FAN_TO_HASS.items()}

FAN_MODES = ["auto", "quiet", "1", "2", "3", "4", "5"]


def _clean_name(raw: str | None) -> str | None:
    """Turn adapter labels like 'Heatpump_LivingRoom' into 'Heatpump LivingRoom'."""
    if not raw:
        return raw
    return " ".join(str(raw).replace("_", " ").split())


class DaikinDenebClimate(ClimateEntity):
    """A single ductless indoor head (zone) on the Skyport cloud."""

    _attr_precision = PRECISION_HALVES
    _attr_target_temperature_step = 0.5
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = 10.0
    _attr_max_temp = 32.0
    _attr_fan_modes = FAN_MODES
    _attr_hvac_modes = [
        HVACMode.OFF,
        HVACMode.HEAT,
        HVACMode.COOL,
        HVACMode.AUTO,
        HVACMode.DRY,
        HVACMode.FAN_ONLY,
    ]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_has_entity_name = True
    _attr_name = None
    _enable_turn_on_off_backwards_compatibility = False

    def __init__(self, data, thermostat_index: int, thermostat: dict) -> None:
        self.data = data
        self.thermostat_index = thermostat_index
        # Fallback snapshot only; live reads go through the `thermostat`
        # property so a client-side dict replacement never orphans us.
        self._initial_thermostat = thermostat
        self._device_id = thermostat["id"]
        self._device_name = _clean_name(
            thermostat.get("adptDeviceName") or thermostat.get("name")
        )
        self._attr_unique_id = f"{thermostat['id']}-climate"
        self.update_without_throttle = False

    # ------------------------------------------------------------------ state

    @property
    def thermostat(self) -> dict:
        """Always read the client's CURRENT dict for this device."""
        try:
            return self.data.daikinskyport.thermostats[self.thermostat_index]
        except (IndexError, TypeError, AttributeError):
            return self._initial_thermostat

    def _mode_raw(self):
        return self.thermostat.get("iduOperatingMode")

    def _is_on(self) -> bool:
        return bool(self.thermostat.get("iduOnOff"))

    @property
    def available(self) -> bool:
        checker = getattr(self.data.daikinskyport, "is_device_available", None)
        if callable(checker):
            return bool(checker(self.thermostat_index))
        return True

    @property
    def hvac_mode(self) -> HVACMode | None:
        if not self._is_on():
            return HVACMode.OFF
        return DENEB_MODE_TO_HASS.get(self._mode_raw())

    @property
    def hvac_action(self) -> HVACAction | None:
        if not self._is_on():
            return HVACAction.OFF
        if not self.thermostat.get("iduThermoState"):
            return HVACAction.IDLE
        # Active auto resolves to heating or cooling but the payload does not
        # say which; report None rather than guess.
        return DENEB_MODE_TO_ACTION.get(self._mode_raw())

    @property
    def current_temperature(self) -> float | None:
        return self.thermostat.get("iduRoomTemp")

    @property
    def current_humidity(self) -> int | None:
        return self.thermostat.get("iduRoomHum")

    @property
    def target_temperature(self) -> float | None:
        # Follow the active mode's setpoint; while off, show the last mode's
        # setpoint so the card remains informative.
        field = DENEB_MODE_SETPOINT_FIELD.get(self._mode_raw())
        if field is None:  # dry / fan-only have no setpoint
            return None
        return self.thermostat.get(field)

    @property
    def fan_mode(self) -> str | None:
        field = DENEB_MODE_FAN_FIELD.get(self._mode_raw())
        if field is None:
            return None
        return DENEB_FAN_TO_HASS.get(self.thermostat.get(field))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        t = self.thermostat
        return {
            # True when the shared outdoor unit refused this head's mode
            # (heat/cool conflict across zones).
            "mode_refusal": bool(t.get("iduControlModeRefusalState")),
            # True during a defrost cycle: the head blows lukewarm/no air for
            # a few minutes in heating. Normal behavior, worth surfacing.
            "defrosting": bool(t.get("oduDefrost")),
        }

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            manufacturer=MANUFACTURER,
            model="Aurora ductless (DENEB)",
            name=self._device_name,
        )

    # -------------------------------------------------------------- commands

    def _check_multizone_conflict(self, requested_mode: int) -> None:
        """Refuse mode changes the shared outdoor unit cannot satisfy."""
        if requested_mode in _HEATING_MODES:
            conflicting = _COOLING_MODES
        elif requested_mode in _COOLING_MODES:
            conflicting = _HEATING_MODES
        else:  # auto / fan-only: flexible, never blocked
            return
        client = self.data.daikinskyport
        for idx, other in enumerate(getattr(client, "thermostats", []) or []):
            if idx == self.thermostat_index or not isinstance(other, dict):
                continue
            if not is_deneb_payload(other):
                continue
            if other.get("iduOnOff") and other.get("iduOperatingMode") in conflicting:
                other_name = _clean_name(
                    other.get("adptDeviceName") or other.get("name")
                )
                raise ServiceValidationError(
                    f"Cannot set {self._device_name} to "
                    f"{DENEB_MODE_TO_HASS.get(requested_mode)}: {other_name} is "
                    "running the opposite cycle and all heads share one outdoor "
                    "unit. Turn it off or align modes first."
                )

    def _require_success(self, result, action: str) -> None:
        if result is None:
            raise HomeAssistantError(
                f"Daikin command failed ({action} on {self._device_name}); "
                "the unit state was NOT changed. Check connectivity and retry."
            )

    def set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            result = self.data.daikinskyport.set_deneb_power(
                self.thermostat_index, False
            )
            self._require_success(result, "power off")
        else:
            mode = HASS_TO_DENEB_MODE.get(hvac_mode)
            if mode is None:
                raise ServiceValidationError(
                    f"Unsupported HVAC mode for this Daikin head: {hvac_mode}"
                )
            self._check_multizone_conflict(mode)
            result = self.data.daikinskyport.set_deneb_mode(
                self.thermostat_index, mode
            )
            self._require_success(result, f"set mode {hvac_mode}")
        self.update_without_throttle = True

    def turn_on(self) -> None:
        result = self.data.daikinskyport.set_deneb_power(self.thermostat_index, True)
        self._require_success(result, "power on")
        self.update_without_throttle = True

    def turn_off(self) -> None:
        result = self.data.daikinskyport.set_deneb_power(self.thermostat_index, False)
        self._require_success(result, "power off")
        self.update_without_throttle = True

    def set_temperature(self, **kwargs: Any) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        if not self._attr_min_temp <= temperature <= self._attr_max_temp:
            raise ServiceValidationError(
                f"Setpoint {temperature} out of range "
                f"({self._attr_min_temp}-{self._attr_max_temp} C) for "
                f"{self._device_name}"
            )
        field = DENEB_MODE_SETPOINT_FIELD.get(self._mode_raw())
        if field is None:
            raise ServiceValidationError(
                f"{self._device_name} has no temperature setpoint in "
                f"{self.hvac_mode} mode"
            )
        result = self.data.daikinskyport.set_deneb_setpoint(
            self.thermostat_index, field, temperature
        )
        self._require_success(result, f"set temperature {temperature}")
        self.update_without_throttle = True

    def set_fan_mode(self, fan_mode: str) -> None:
        value = HASS_TO_DENEB_FAN.get(fan_mode)
        field = DENEB_MODE_FAN_FIELD.get(self._mode_raw())
        if value is None or field is None:
            raise ServiceValidationError(
                f"Unsupported fan mode for this Daikin head: {fan_mode}"
            )
        result = self.data.daikinskyport.set_deneb_fan(
            self.thermostat_index, field, value
        )
        self._require_success(result, f"set fan {fan_mode}")
        self.update_without_throttle = True

    # ---------------------------------------------------------------- update

    async def async_update(self) -> None:
        if self.update_without_throttle:
            await self.data._async_update_data(no_throttle=True)
            self.update_without_throttle = False
        else:
            await self.data._async_update_data()
        # No rebinding needed: `thermostat` property reads the live dict.
