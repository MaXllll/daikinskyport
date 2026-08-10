"""Feature switches for Daikin DENEB devices (North American Aurora ductless).

Three boolean features per head, mirroring the IR remote's dedicated
buttons. All three fields are present in live-captured deviceData:

- Comfort airflow: vane aims airflow away from occupants (up in
  cooling, down in heating). Commanded via the ACTIVE mode's vane field
  (idu{Mode}AirDirectionUpDown = 23 on / 0 off, verified live);
  ``iduWindNiceOperation`` is only the read-only running-status flag and
  rejects direct writes.
- Econo (``iduEconoModeSetting``): caps compressor draw for lower
  consumption / breaker relief.
- Powerful (``oduPowerfulOperationRequest``): 20-minute full-tilt boost.

Note: on the remote these are partly mutually exclusive (Powerful
overrides Econo/Comfort). We don't re-implement that arbitration; the
unit resolves it and the next poll trues up our state.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo

from .const import (
    DOMAIN,
    MANUFACTURER,
    DENEB_MODE_VANE_FIELD,
    DENEB_VANE_COMFORT,
)
from .climate_deneb import _clean_name


@dataclass(frozen=True)
class DenebSwitchDescription(SwitchEntityDescription):
    """Describes a DENEB boolean feature switch."""

    field: str = ""


DENEB_SWITCHES: tuple[DenebSwitchDescription, ...] = (
    DenebSwitchDescription(
        key="comfort",
        name="Comfort airflow",
        # State/commands go through the active mode's vane field; this
        # read-only status field is kept for the unique_id (stable) and
        # surfaced as an attribute.
        field="iduWindNiceOperation",
        icon="mdi:weather-windy",
    ),
    DenebSwitchDescription(
        key="econo",
        name="Econo",
        field="iduEconoModeSetting",
        icon="mdi:leaf",
    ),
    DenebSwitchDescription(
        key="powerful",
        name="Powerful",
        field="oduPowerfulOperationRequest",
        icon="mdi:rocket-launch",
    ),
)


class DaikinDenebSwitch(SwitchEntity):
    """One boolean feature on one ductless head."""

    _attr_has_entity_name = True

    def __init__(
        self,
        data,
        thermostat_index: int,
        thermostat: dict,
        description: DenebSwitchDescription,
    ) -> None:
        self.data = data
        self.entity_description = description
        self.thermostat_index = thermostat_index
        # Fallback snapshot only; live reads go through the `thermostat`
        # property so a client-side dict replacement never orphans us.
        self._initial_thermostat = thermostat
        self._device_id = thermostat["id"]
        self._device_name = _clean_name(
            thermostat.get("adptDeviceName") or thermostat.get("name")
        )
        self._attr_unique_id = f"{thermostat['id']}-{description.field}"
        self.update_without_throttle = False

    # ------------------------------------------------------------------ state

    @property
    def thermostat(self) -> dict:
        """Always read the client's CURRENT dict for this device."""
        try:
            return self.data.daikinskyport.thermostats[self.thermostat_index]
        except (IndexError, TypeError, AttributeError):
            return self._initial_thermostat

    def _vane_field(self) -> str | None:
        return DENEB_MODE_VANE_FIELD.get(self.thermostat.get("iduOperatingMode"))

    @property
    def is_on(self) -> bool:
        if self.entity_description.key == "comfort":
            field = self._vane_field()
            return (
                field is not None
                and self.thermostat.get(field) == DENEB_VANE_COMFORT
            )
        return bool(self.thermostat.get(self.entity_description.field))

    @property
    def extra_state_attributes(self):
        if self.entity_description.key != "comfort":
            return None
        # Live status: true only while the head is running with the
        # comfort vane engaged in the active mode.
        return {
            "comfort_active": bool(self.thermostat.get("iduWindNiceOperation"))
        }

    @property
    def available(self) -> bool:
        checker = getattr(self.data.daikinskyport, "is_device_available", None)
        if callable(checker):
            return bool(checker(self.thermostat_index))
        return True

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            manufacturer=MANUFACTURER,
            model="Aurora ductless (DENEB)",
            name=self._device_name,
        )

    # -------------------------------------------------------------- commands

    def _set(self, state: bool) -> None:
        if self.entity_description.key == "comfort":
            result = self.data.daikinskyport.set_deneb_comfort(
                self.thermostat_index, state
            )
        else:
            result = self.data.daikinskyport.set_deneb_flag(
                self.thermostat_index, self.entity_description.field, state
            )
        if result is None:
            raise HomeAssistantError(
                f"Daikin command failed ({self.entity_description.name} "
                f"{'on' if state else 'off'} on {self._device_name}); the unit "
                "state was NOT changed. Check connectivity and retry."
            )
        # is_on reads the client's live dict, which set_deneb_flag mutated
        # after the confirmed write — HA's post-service state write picks the
        # new value up immediately; no manual scheduling needed.
        self.update_without_throttle = True

    def turn_on(self, **kwargs: Any) -> None:
        self._set(True)

    def turn_off(self, **kwargs: Any) -> None:
        self._set(False)

    # ---------------------------------------------------------------- update

    async def async_update(self) -> None:
        if self.update_without_throttle:
            await self.data._async_update_data(no_throttle=True)
            self.update_without_throttle = False
        else:
            await self.data._async_update_data()
        # No rebinding needed: `thermostat` property reads the live dict.
