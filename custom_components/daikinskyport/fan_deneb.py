"""Fan entity for Daikin DENEB devices (North American Aurora ductless).

Designed for clean HomeKit bridging (hybrid layout):

- Exactly ONE preset mode ("Auto"): the HomeKit bridge maps a single
  preset to the native TargetFanState Auto/Manual selector, which the
  Home app renders cleanly (no grouped anonymous switch toggles).
- The percentage slider covers quiet + manual speeds 1..5 (speed_count
  6): the bottom step is Quiet (quieter = lower, intuitive), then 1-5.
  Device fan values: 11 = quiet, 3..7 = speeds 1..5, 10 = auto.
- Econo/Powerful/Comfort are NOT fan presets: they are standalone switch
  entities (switch platform) so they appear as named accessories.
- is_on mirrors head power (iduOnOff): sliding to 0 / toggling the tile
  powers the head off, consistent with the thermostat tile.
- Fan-speed fields are per-hvac-mode, so reads/writes follow the
  currently selected mode's field.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN, MANUFACTURER, DENEB_MODE_FAN_FIELD
from .climate_deneb import _clean_name

PRESET_AUTO = "Auto"

FAN_VALUE_AUTO = 10
FAN_VALUE_QUIET = 11
# Device fan-speed values 3..7 = manual speeds 1..5.
FAN_VALUE_FIRST_MANUAL = 3
# Slider steps: 1 = quiet, 2..6 = manual speeds 1..5.
SLIDER_STEPS = 6


class DaikinDenebFan(FanEntity):
    """Airflow + boost control for a single ductless head."""

    _attr_has_entity_name = True
    _attr_name = "Fan"
    _attr_speed_count = SLIDER_STEPS
    _attr_preset_modes = [PRESET_AUTO]
    # TURN_ON/TURN_OFF fan features only exist on HA >= 2024.8; fall back
    # gracefully so the test harness (2024.3) still loads the module.
    _attr_supported_features = (
        FanEntityFeature.SET_SPEED
        | FanEntityFeature.PRESET_MODE
        | getattr(FanEntityFeature, "TURN_ON", 0)
        | getattr(FanEntityFeature, "TURN_OFF", 0)
    )
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
        self._attr_unique_id = f"{thermostat['id']}-fan"
        self.update_without_throttle = False

    # ------------------------------------------------------------------ state

    @property
    def thermostat(self) -> dict:
        """Always read the client's CURRENT dict for this device."""
        try:
            return self.data.daikinskyport.thermostats[self.thermostat_index]
        except (IndexError, TypeError, AttributeError):
            return self._initial_thermostat

    def _fan_field(self) -> str | None:
        return DENEB_MODE_FAN_FIELD.get(self.thermostat.get("iduOperatingMode"))

    def _fan_value(self):
        field = self._fan_field()
        return self.thermostat.get(field) if field else None

    @property
    def is_on(self) -> bool:
        return bool(self.thermostat.get("iduOnOff"))

    @property
    def available(self) -> bool:
        checker = getattr(self.data.daikinskyport, "is_device_available", None)
        if callable(checker):
            return bool(checker(self.thermostat_index))
        return True

    @property
    def percentage(self) -> int | None:
        value = self._fan_value()
        if value == FAN_VALUE_QUIET:
            return 100 // SLIDER_STEPS  # bottom step = quiet
        if isinstance(value, int) and (
            FAN_VALUE_FIRST_MANUAL <= value < FAN_VALUE_FIRST_MANUAL + 5
        ):
            step = value - FAN_VALUE_FIRST_MANUAL + 2
            return step * 100 // SLIDER_STEPS
        return 0

    @property
    def preset_mode(self) -> str | None:
        return PRESET_AUTO if self._fan_value() == FAN_VALUE_AUTO else None

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            manufacturer=MANUFACTURER,
            model="Aurora ductless (DENEB)",
            name=self._device_name,
        )

    # -------------------------------------------------------------- commands

    def _require_success(self, result, action: str) -> None:
        if result is None:
            raise HomeAssistantError(
                f"Daikin command failed ({action} on {self._device_name}); "
                "the unit state was NOT changed. Check connectivity and retry."
            )

    def _require_fan_field(self) -> str:
        field = self._fan_field()
        if field is None:
            raise ServiceValidationError(
                f"{self._device_name} has no fan-speed control in its "
                "current mode."
            )
        return field

    def turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        if preset_mode is not None:
            self.set_preset_mode(preset_mode)
            return
        if percentage is not None:
            self.set_percentage(percentage)
            return
        if not self.thermostat.get("iduOnOff"):
            result = self.data.daikinskyport.set_deneb_power(
                self.thermostat_index, True
            )
            self._require_success(result, "power on")
        self.update_without_throttle = True

    def turn_off(self, **kwargs: Any) -> None:
        result = self.data.daikinskyport.set_deneb_power(
            self.thermostat_index, False
        )
        self._require_success(result, "power off")
        self.update_without_throttle = True

    def set_percentage(self, percentage: int) -> None:
        if percentage <= 0:
            self.turn_off()
            return
        step = max(1, min(SLIDER_STEPS,
                          round(percentage * SLIDER_STEPS / 100)))
        field = self._require_fan_field()
        if step == 1:
            value = FAN_VALUE_QUIET
            label = "fan quiet"
        else:
            value = FAN_VALUE_FIRST_MANUAL + step - 2
            label = f"fan speed {step - 1}"
        result = self.data.daikinskyport.set_deneb_fan(
            self.thermostat_index, field, value
        )
        self._require_success(result, label)
        self.update_without_throttle = True

    def set_preset_mode(self, preset_mode: str) -> None:
        if preset_mode != PRESET_AUTO:
            raise ServiceValidationError(
                f"Unsupported fan preset for this Daikin head: {preset_mode}"
            )
        field = self._require_fan_field()
        result = self.data.daikinskyport.set_deneb_fan(
            self.thermostat_index, field, FAN_VALUE_AUTO
        )
        self._require_success(result, "fan auto")
        self.update_without_throttle = True

    # ---------------------------------------------------------------- update

    async def async_update(self) -> None:
        if self.update_without_throttle:
            await self.data._async_update_data(no_throttle=True)
            self.update_without_throttle = False
        else:
            await self.data._async_update_data()
        # No rebinding needed: `thermostat` property reads the live dict.
