"""Constants for the HBX SensorLinx integration."""

DOMAIN = "sensorlinx"

CONF_BUILDING_ID = "building_id"

CONF_HOT_WATER_SWITCH = "hot_water_switch_entity_id"
CONF_RADIANT_FLOOR_SWITCH = "radiant_floor_switch_entity_id"
CONF_HEATED_FLOOR_CONTROLLER = "heated_floor_controller_entity_id"
CONF_MAIN_HVAC_CLIMATE = "main_hvac_climate_entity_id"
CONF_UPSTAIRS_TEMP_SENSOR = "upstairs_temp_sensor_entity_id"
CONF_MAIN_FLOOR_TEMP_SENSOR = "main_floor_temp_sensor_entity_id"
# Legacy option keys (no longer wired into automations)
CONF_HUNTER_FAN = "hunter_fan_entity_id"
CONF_SIDNEY_FAN = "sidney_fan_entity_id"

DEFAULT_MAIN_HVAC_CLIMATE = "climate.main_floor"
DEFAULT_UPSTAIRS_TEMP_SENSOR = "sensor.upstairs_temperature"
DEFAULT_MAIN_FLOOR_TEMP_SENSOR = "sensor.main_floor_current_temperature"
DEFAULT_HUNTER_FAN = ""
DEFAULT_SIDNEY_FAN = ""

# Watts Home / external floor thermostats (not on SensorLinx THM API)
DEFAULT_PRIMARY_BATH_CLIMATE = "climate.primary_bathroom_primary_bath_floor"
DEFAULT_PRIMARY_BATH_ROOM_SENSOR = "sensor.ba_temperature"

# Forest house occupancy — Primary Bath floor off when both are away
DEFAULT_ANDREW_PRESENCE_ENTITIES = (
    "device_tracker.life360_andrew_schoenfeld",
    "person.andrew_schoenfeld",
)
DEFAULT_BECKY_PRESENCE_ENTITIES = (
    "device_tracker.beckysi15promax",
    "device_tracker.becky_laptop",
    "device_tracker.beckys_asus_laptop",
)

CONF_COOLING_PAUSED_UNTIL = "cooling_paused_until"
CONF_COOLING_PAUSE_REASON = "cooling_pause_reason"
DEFAULT_SCAN_INTERVAL = 60

DEVICE_TYPE_THM = "THM"
DEVICE_TYPE_ZON = "ZON"
DEVICE_TYPE_ECO = "ECO"
