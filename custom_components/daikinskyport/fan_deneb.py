"""Fan entity for Daikin DENEB devices (North American Aurora ductless).

Designed for clean HomeKit bridging: one accessory per head that carries
the whole "airflow and boost" surface, so no separate switch accessories
are needed in the Home app.

- The percentage slider covers MANUAL speeds 1..5 only (device fan
  values 3..7). speed_count=5 gives HomeKit 20% steps.
- preset_modes: Auto, Quiet, Econo, Powerful (Comfort will join once its
  write encoding is decoded — the cloud rejects the naked field write).
  The HomeKit bridge renders each preset as a labeled toggle inside the
  fan tile.
- is_on mirrors head power (iduOnOff): this fan IS the head's fan, and
  sliding to 0 / toggling the tile powers the head off, consistent with
  the thermostat tile.
- Presets are exclusive, matching the IR remote (Powerful cancels Econo
  and vice versa). Fan speed fields are per-hvac-mode, so reads/writes
  follow the currently selected mode's field.
- HomeKit deactivates a preset toggle by calling plain fan.turn_on: on an
  already-running head we interpret that as "back to normal" — clear
  Powerful, else Econo, else Quiet->Auto, else no-op.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN, MANUFACTURER, DENEB_MODE_FAN_FIELD
from .climate_deneb import _clean_name

PRESET_AUTO = "Auto"
PRESET_QUIET = "Quiet"
PRESET_ECONO = "Econo"
PRESET_POWERFUL = "Powerful"

FAN_VALUE_AUTO = 10
FAN_VALUE_QUIET = 11
# Device fan-speed values 3..7 = manual speeds 1..5.
FAN_VALUE_FIRST_MANUAL = 3
MANUAL_SPEEDS = 5

ECONO_FIELD = "iduEconoModeSetting"
POWERFUL_FIELD = "oduPowerfulOperationRequest"


class DaikinDenebFan(FanEntity):
    """Airflow + boost control for a single ductless head."""

    _attr_has_entity_name = True
    _attr_name = "Fan"
    _attr_speed_count = MANUAL_SPEEDS
    _attr_preset_modes = [PRESET_AUTO, PRESET_QUIET, PRESET_ECONO, PRESET_POWERFUL]
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
        if isinstance(value, int) and (
            FAN_VALUE_FIRST_MANUAL
            <= value
            < FAN_VALUE_FIRST_MANUAL + MANUAL_SPEEDS
        ):
            return (value - FAN_VALUE_FIRST_MANUAL + 1) * 100 // MANUAL_SPEEDS
        return 0

    @property
    def preset_mode(self) -> str | None:
        t = self.thermostat
        if t.get(POWERFUL_FIELD):
            return PRESET_POWERFUL
        if t.get(ECONO_FIELD):
            return PRESET_ECONO
        value = self._fan_value()
        if value == FAN_VALUE_QUIET:
            return PRESET_QUIET
        if value == FAN_VALUE_AUTO:
            return PRESET_AUTO
        return None

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

    def _set_flag(self, field: str, state: bool, action: str) -> None:
        result = self.data.daikinskyport.set_deneb_flag(
            self.thermostat_index, field, state
        )
        self._require_success(result, action)

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
        t = self.thermostat
        if not t.get("iduOnOff"):
            result = self.data.daikinskyport.set_deneb_power(
                self.thermostat_index, True
            )
            self._require_success(result, "power on")
        elif t.get(POWERFUL_FIELD):
            # HomeKit preset-toggle-off arrives as plain turn_on: step the
            # head back toward normal, one layer at a time.
            self._set_flag(POWERFUL_FIELD, False, "Powerful off")
        elif t.get(ECONO_FIELD):
            self._set_flag(ECONO_FIELD, False, "Econo off")
        elif self._fan_value() == FAN_VALUE_QUIET:
            field = self._require_fan_field()
            result = self.data.daikinskyport.set_deneb_fan(
                self.thermostat_index, field, FAN_VALUE_AUTO
            )
            self._require_success(result, "fan auto")
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
        field = self._require_fan_field()
        step = max(1, min(MANUAL_SPEEDS,
                          round(percentage * MANUAL_SPEEDS / 100)))
        value = FAN_VALUE_FIRST_MANUAL + step - 1
        result = self.data.daikinskyport.set_deneb_fan(
            self.thermostat_index, field, value
        )
        self._require_success(result, f"fan speed {step}")
        self.update_without_throttle = True

    def set_preset_mode(self, preset_mode: str) -> None:
        t = self.thermostat
        if preset_mode == PRESET_POWERFUL:
            if t.get(ECONO_FIELD):
                self._set_flag(ECONO_FIELD, False, "Econo off")
            self._set_flag(POWERFUL_FIELD, True, "Powerful on")
        elif preset_mode == PRESET_ECONO:
            if t.get(POWERFUL_FIELD):
                self._set_flag(POWERFUL_FIELD, False, "Powerful off")
            self._set_flag(ECONO_FIELD, True, "Econo on")
        elif preset_mode in (PRESET_AUTO, PRESET_QUIET):
            if t.get(POWERFUL_FIELD):
                self._set_flag(POWERFUL_FIELD, False, "Powerful off")
            if t.get(ECONO_FIELD):
                self._set_flag(ECONO_FIELD, False, "Econo off")
            field = self._require_fan_field()
            value = (
                FAN_VALUE_AUTO if preset_mode == PRESET_AUTO else FAN_VALUE_QUIET
            )
            result = self.data.daikinskyport.set_deneb_fan(
                self.thermostat_index, field, value
            )
            self._require_success(result, f"fan {preset_mode.lower()}")
        else:
            raise ServiceValidationError(
                f"Unsupported fan preset for this Daikin head: {preset_mode}"
            )
        self.update_without_throttle = True

    # ---------------------------------------------------------------- update

    async def async_update(self) -> None:
        if self.update_without_throttle:
            await self.data._async_update_data(no_throttle=True)
            self.update_without_throttle = False
        else:
            await self.data._async_update_data()
        # No rebinding needed: `thermostat` property reads the live dict.
