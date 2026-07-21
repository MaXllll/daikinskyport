"""Climate entity for Daikin DENEB devices (North American Aurora ductless).

Covers FTXV/CTXV high-wall heads behind a 4MXTH multi-zone outdoor unit,
connected via AZAI6WSCDKB-class WiFi adapters to the Skyport cloud.

All field semantics were verified against live hardware; see the
repo docs. Key model notes:
- Power (iduOnOff) is separate from mode; mode persists while off.
- One setpoint per mode (heat/cool/auto); dry and fan-only have none.
- One fan-speed field per mode.
- iduThermoState indicates active conditioning (compressor engaged).
"""
from __future__ import annotations

from typing import Any, Optional

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_HALVES, UnitOfTemperature
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

DENEB_FAN_TO_HASS = {3: "1", 4: "2", 5: "3", 6: "4", 7: "5", 10: "auto", 11: "quiet"}
HASS_TO_DENEB_FAN = {v: k for k, v in DENEB_FAN_TO_HASS.items()}

FAN_MODES = ["auto", "quiet", "1", "2", "3", "4", "5"]


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
    _enable_turn_on_off_backwards_compatibility = False

    def __init__(self, data, thermostat_index: int, thermostat: dict) -> None:
        self.data = data
        self.thermostat_index = thermostat_index
        self.thermostat = thermostat
        self._attr_unique_id = f"{thermostat['id']}-climate"
        self._attr_name = thermostat.get("adptDeviceName") or thermostat.get("name")
        self.update_without_throttle = False

    # ------------------------------------------------------------------ state

    def _mode_raw(self) -> int:
        return self.thermostat.get("iduOperatingMode")

    def _is_on(self) -> bool:
        return bool(self.thermostat.get("iduOnOff"))

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
    def current_temperature(self) -> Optional[float]:
        return self.thermostat.get("iduRoomTemp")

    @property
    def current_humidity(self) -> Optional[int]:
        return self.thermostat.get("iduRoomHum")

    @property
    def target_temperature(self) -> Optional[float]:
        # Follow the active mode's setpoint; while off, show the last mode's
        # setpoint so the card remains informative.
        field = DENEB_MODE_SETPOINT_FIELD.get(self._mode_raw())
        if field is None:  # dry / fan-only have no setpoint
            return None
        return self.thermostat.get(field)

    @property
    def fan_mode(self) -> Optional[str]:
        field = DENEB_MODE_FAN_FIELD.get(self._mode_raw())
        if field is None:
            return None
        return DENEB_FAN_TO_HASS.get(self.thermostat.get(field))

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.thermostat["id"])},
            manufacturer=MANUFACTURER,
            model="Aurora ductless (DENEB)",
            name=self._attr_name,
        )

    # -------------------------------------------------------------- commands

    def set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            self.data.daikinskyport.set_deneb_power(self.thermostat_index, False)
            self.thermostat["iduOnOff"] = False
        else:
            mode = HASS_TO_DENEB_MODE.get(hvac_mode)
            if mode is None:
                _LOGGER.warning("Unsupported HVAC mode for DENEB: %s", hvac_mode)
                return
            self.data.daikinskyport.set_deneb_mode(self.thermostat_index, mode)
            self.thermostat["iduOnOff"] = True
            self.thermostat["iduOperatingMode"] = mode
        self.update_without_throttle = True

    def turn_on(self) -> None:
        self.data.daikinskyport.set_deneb_power(self.thermostat_index, True)
        self.thermostat["iduOnOff"] = True
        self.update_without_throttle = True

    def turn_off(self) -> None:
        self.data.daikinskyport.set_deneb_power(self.thermostat_index, False)
        self.thermostat["iduOnOff"] = False
        self.update_without_throttle = True

    def set_temperature(self, **kwargs: Any) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        field = DENEB_MODE_SETPOINT_FIELD.get(self._mode_raw())
        if field is None:
            _LOGGER.warning(
                "Cannot set temperature in %s mode on %s",
                self.hvac_mode,
                self._attr_name,
            )
            return
        self.data.daikinskyport.set_deneb_setpoint(
            self.thermostat_index, field, temperature
        )
        self.thermostat[field] = temperature
        self.update_without_throttle = True

    def set_fan_mode(self, fan_mode: str) -> None:
        value = HASS_TO_DENEB_FAN.get(fan_mode)
        field = DENEB_MODE_FAN_FIELD.get(self._mode_raw())
        if value is None or field is None:
            _LOGGER.warning("Unsupported fan mode for DENEB: %s", fan_mode)
            return
        self.data.daikinskyport.set_deneb_fan(self.thermostat_index, field, value)
        self.thermostat[field] = value
        self.update_without_throttle = True

    # ---------------------------------------------------------------- update

    async def async_update(self) -> None:
        if self.update_without_throttle:
            await self.data._async_update_data(no_throttle=True)
            self.update_without_throttle = False
        else:
            await self.data._async_update_data()
        self.thermostat = self.data.daikinskyport.get_thermostat(self.thermostat_index)
