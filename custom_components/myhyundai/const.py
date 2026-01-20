from __future__ import annotations

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "myhyundai"

PLATFORMS = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.LOCK,
    Platform.BUTTON,
]

CONF_VEHICLE_ID = "vehicle_id"
CONF_PIN = "pin"
CONF_REGION = "region"
CONF_BRAND = "brand"

DEFAULT_REGION = 6
DEFAULT_BRAND = 2
DEFAULT_UPDATE_INTERVAL = timedelta(minutes=5)

DEFAULT_REMOTE_START_TEMPERATURE = None
DEFAULT_REMOTE_START_DURATION = None
DEFAULT_REMOTE_START_DEFROST = None
DEFAULT_REMOTE_START_CLIMATE = None
DEFAULT_REMOTE_START_HEATING = None

ACTION_STATUS_TIMEOUT = 60

MANUFACTURER = "Hyundai"
