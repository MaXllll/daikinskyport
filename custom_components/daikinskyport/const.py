import logging

_LOGGER = logging.getLogger(__package__)

DOMAIN = "daikinskyport"
MANUFACTURER = "Daikin"

# Full list of HA conditions as of 10/2023
from homeassistant.components.weather import (
    ATTR_CONDITION_CLEAR_NIGHT,
    ATTR_CONDITION_CLOUDY,
    ATTR_CONDITION_EXCEPTIONAL,
    ATTR_CONDITION_FOG,
    ATTR_CONDITION_HAIL,
    ATTR_CONDITION_LIGHTNING,
    ATTR_CONDITION_LIGHTNING_RAINY,
    ATTR_CONDITION_PARTLYCLOUDY,
    ATTR_CONDITION_POURING,
    ATTR_CONDITION_RAINY,
    ATTR_CONDITION_SNOWY,
    ATTR_CONDITION_SNOWY_RAINY,
    ATTR_CONDITION_SUNNY,
    ATTR_CONDITION_WINDY,
    ATTR_CONDITION_WINDY_VARIANT,
)

# Map Daikin weather icons to HA conditions (weather icons are always the same, *Cond change with language)
# Unknown entries are unverifed.  Taken from Weather Underground icon names
DAIKIN_WEATHER_ICON_TO_HASS = {
    "sunny": ATTR_CONDITION_SUNNY, #Unknown
    "mostlysunny": ATTR_CONDITION_SUNNY, #Unknown
    "partlysunny": ATTR_CONDITION_PARTLYCLOUDY, #Unknown
    "partlycloudy": ATTR_CONDITION_PARTLYCLOUDY,
    "clear": ATTR_CONDITION_CLEAR_NIGHT, #Unknown
    "mostlycloudy": ATTR_CONDITION_CLOUDY,
    "cloudy": ATTR_CONDITION_CLOUDY, #Unknown
    "rain": ATTR_CONDITION_RAINY,
    "chancerain": ATTR_CONDITION_RAINY,
    "snow": ATTR_CONDITION_SNOWY, #Unknown
    "chancesnow": ATTR_CONDITION_SNOWY, #Unknown
    "chanceflurries": ATTR_CONDITION_SNOWY, #Unknown
    "flurries": ATTR_CONDITION_SNOWY, #Unknown
    "tstorms": ATTR_CONDITION_LIGHTNING,
    "chancetstorms": ATTR_CONDITION_LIGHTNING,
    "fog": ATTR_CONDITION_FOG, #Unknown
    "hazy": "hazy", #Unknown
    "sleet": "sleet", #Unknown
    "chancesleet": "sleet",  #Unknown
}

# The multiplier applied by the API to percentage values.
DAIKIN_PERCENT_MULTIPLIER = 2

# Possible hvac modes are auto (3), auxHeatOnly (4), cool (2), heat (1), off (0) '''
DAIKIN_HVAC_MODE_OFF = 0
DAIKIN_HVAC_MODE_HEAT = 1
DAIKIN_HVAC_MODE_COOL = 2
DAIKIN_HVAC_MODE_AUTO = 3
DAIKIN_HVAC_MODE_AUXHEAT = 4

# --- DENEB (North American Aurora ductless mini-split) constants ---
# Physically verified on a live Aurora multi-zone ductless system:
# values 4 and 6 are rejected by the unit. Power is a separate flag
# (iduOnOff); the mode field retains its last value while off.
DENEB_MODE_FAN_ONLY = 0
DENEB_MODE_HEAT = 1
DENEB_MODE_COOL = 2
DENEB_MODE_AUTO = 3
DENEB_MODE_DRY = 5

# Fan speed encoding: 3..7 = speeds 1..5, 10 = auto, 11 = quiet/night
DENEB_FAN_AUTO = 10
DENEB_FAN_QUIET = 11
DENEB_FAN_MIN = 3
DENEB_FAN_MAX = 7

# Per-mode field names in the deviceData payload
DENEB_MODE_SETPOINT_FIELD = {
    DENEB_MODE_HEAT: "iduHeatSetpoint",
    DENEB_MODE_COOL: "iduCoolSetpoint",
    DENEB_MODE_AUTO: "iduAutoSetpoint",
}
DENEB_MODE_FAN_FIELD = {
    DENEB_MODE_FAN_ONLY: "iduFanModeFanSpeed",
    DENEB_MODE_HEAT: "iduHeatFanSpeed",
    DENEB_MODE_COOL: "iduCoolFanSpeed",
    DENEB_MODE_AUTO: "iduAutoFanSpeed",
    DENEB_MODE_DRY: "iduDryFanSpeed",
}
DENEB_MODE_VANE_FIELD = {
    DENEB_MODE_FAN_ONLY: "iduFanAirDirectionUpDown",
    DENEB_MODE_HEAT: "iduHeatAirDirectionUpDown",
    DENEB_MODE_COOL: "iduCoolAirDirectionUpDown",
    DENEB_MODE_AUTO: "iduAutoAirDirectionUpDown",
    DENEB_MODE_DRY: "iduDryAirDirectionUpDown",
}

# Vane (air direction) values, physically verified: writing 23 to the
# active mode's vane field engages "Comfort airflow" (the remote's
# Comfort button) and iduWindNiceOperation flips true while running;
# writing 0 restores the default vane and clears it. Direct writes to
# iduWindNiceOperation itself are rejected by the cloud (read-only
# status flag).
DENEB_VANE_COMFORT = 23
DENEB_VANE_DEFAULT = 0

CONF_REFRESH_TOKEN = "refresh_token"
CONF_ACCESS_TOKEN = "access_token"

COORDINATOR = "coordinator"
