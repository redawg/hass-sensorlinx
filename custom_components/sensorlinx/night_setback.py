"""Night setback for radiant floor zones based on first-floor / basement motion."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.components.number import NumberEntity, NumberMode, RestoreNumber
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_change
from homeassistant.util import dt as dt_util

from .const import DOMAIN

SIGNAL_NIGHT_MODE_CHANGED = f"{DOMAIN}_night_mode_changed"

if TYPE_CHECKING:
    from .outdoor_reset import OutdoorResetController

_LOGGER = logging.getLogger(__name__)

# First floor + downstairs motion (binary_sensor, device_class motion)
DEFAULT_MOTION_SENSORS = [
    "binary_sensor.family_room_motion",
    "binary_sensor.living_room_motion",
    "binary_sensor.main_floor_motion",
    "binary_sensor.office_motion",
    "binary_sensor.invisoutlet_7d18_motion",
    "binary_sensor.invisoutlet_7f6c_motion",
    "binary_sensor.invisoutlet_4348_motion",
    "binary_sensor.front_door_motion_sensor",
    "binary_sensor.basement_door_motion",
    "binary_sensor.tp_link_tapo_c230_motion",
]

# Efficient starting points — tune per zone over time via HA number entities
DEFAULT_NIGHT_ROOM_TARGETS = {
    "main_area": 69.0,
    "living_room": 68.0,
    "main_office": 66.0,
}
DEFAULT_NIGHT_FLOOR_TARGETS = {
    "laundry": 70.0,
}

DEFAULT_MOTION_IDLE_MINUTES = 45
NIGHT_CHECK_START_HOUR = 0  # midnight
DAY_RESTORE_HOUR = 6


class NightSetbackParams:
    """Night setback configuration and runtime state."""

    def __init__(self) -> None:
        self.enabled: bool = True
        self.active: bool = False
        self.motion_idle_minutes: int = DEFAULT_MOTION_IDLE_MINUTES
        self.motion_sensor_ids: list[str] = list(DEFAULT_MOTION_SENSORS)
        self.night_room_targets: dict[str, float] = dict(DEFAULT_NIGHT_ROOM_TARGETS)
        self.night_floor_targets: dict[str, float] = dict(DEFAULT_NIGHT_FLOOR_TARGETS)


class NightSetbackMixin:
    """Mixin methods for OutdoorResetController night setback."""

    params: Any
    hass: HomeAssistant
    _unsub_night_schedule: Any
    _unsub_night_motion: Any
    _unsub_day_restore: Any

    def _night_params(self) -> NightSetbackParams:
        return self.params.night_setback

    async def _setup_night_setback(self) -> None:
        """Register schedules and motion listeners for night setback."""
        ns = self._night_params()
        if not ns.motion_sensor_ids:
            ns.motion_sensor_ids = list(DEFAULT_MOTION_SENSORS)

        @callback
        def _on_schedule(now: datetime) -> None:
            self.hass.async_create_task(self._async_night_schedule_tick(now))

        self._unsub_night_schedule = async_track_time_change(
            self.hass,
            _on_schedule,
            minute=(0, 15, 30, 45),
            second=0,
        )

        @callback
        def _on_day_restore(now: datetime) -> None:
            self.hass.async_create_task(self._async_restore_day_setback("morning schedule"))

        self._unsub_day_restore = async_track_time_change(
            self.hass,
            _on_day_restore,
            hour=DAY_RESTORE_HOUR,
            minute=0,
            second=0,
        )

        @callback
        def _on_motion(event) -> None:
            if not self._night_params().active:
                return
            new_state = event.data.get("new_state")
            if new_state is None or new_state.state != "on":
                return
            self.hass.async_create_task(
                self._async_restore_day_setback(f"motion on {event.data.get('entity_id')}")
            )

        self._unsub_night_motion = async_track_state_change_event(
            self.hass, ns.motion_sensor_ids, _on_motion
        )

    def _unload_night_setback(self) -> None:
        for attr in ("_unsub_night_schedule", "_unsub_night_motion", "_unsub_day_restore"):
            unsub = getattr(self, attr, None)
            if unsub:
                unsub()
                setattr(self, attr, None)

    def _has_recent_first_floor_activity(self) -> bool:
        """True if any monitored motion sensor is on or changed recently."""
        ns = self._night_params()
        idle = timedelta(minutes=ns.motion_idle_minutes)
        now = dt_util.now()
        for entity_id in ns.motion_sensor_ids:
            state = self.hass.states.get(entity_id)
            if state is None or state.state in ("unavailable", "unknown"):
                continue
            if state.state == "on":
                return True
            if state.last_changed and (now - state.last_changed) < idle:
                return True
        return False

    def _zone_night_target(
        self,
        zone_name: str,
        floor_mode: bool,
        floor_temp: float | None,
        outdoor: float,
        zone: Any = None,
    ) -> float:
        """Compute setpoint for a zone while night setback is active."""
        ns = self._night_params()
        if floor_mode:
            floor_target = ns.night_floor_targets.get(
                zone_name, DEFAULT_NIGHT_FLOOR_TARGETS.get(zone_name, 70.0)
            )
            if zone is not None and zone.direct_floor_thermostat:
                return self._compute_direct_floor_setpoint(
                    floor_temp, floor_target, zone_name
                )
            return self._compute_floor_mode_setpoint(floor_temp, floor_target, zone_name)
        return ns.night_room_targets.get(
            zone_name, DEFAULT_NIGHT_ROOM_TARGETS.get(zone_name, 68.0)
        )

    async def _async_night_schedule_tick(self, now: datetime) -> None:
        """After midnight, apply night setback when first floor has been still."""
        ns = self._night_params()
        if not ns.enabled or ns.active:
            return
        if not (NIGHT_CHECK_START_HOUR <= now.hour < DAY_RESTORE_HOUR):
            return
        if self._has_recent_first_floor_activity():
            _LOGGER.debug("Night setback skipped — recent first-floor activity")
            return
        await self._async_apply_night_setback("no motion after midnight")

    async def _async_apply_night_setback(self, reason: str) -> None:
        ns = self._night_params()
        if ns.active:
            return
        ns.active = True
        _LOGGER.info("Night setback activated (%s)", reason)
        await self._apply_setpoints()
        async_dispatcher_send(self.hass, SIGNAL_NIGHT_MODE_CHANGED)

    async def _async_restore_day_setback(self, reason: str) -> None:
        ns = self._night_params()
        if not ns.active:
            return
        ns.active = False
        _LOGGER.info("Day setback restored (%s)", reason)
        await self._apply_setpoints()
        async_dispatcher_send(self.hass, SIGNAL_NIGHT_MODE_CHANGED)


def get_night_setback_number_entities(
    coordinator,
    controller: OutdoorResetController,
) -> list[NumberEntity]:
    """Night target and motion-idle number entities."""
    entities: list[NumberEntity] = [
        NightMotionIdleNumberEntity(coordinator, controller),
        NightRoomTargetNumberEntity(
            coordinator, controller, "main_area", "Night Target: Main Area", 69.0,
        ),
        NightRoomTargetNumberEntity(
            coordinator, controller, "living_room", "Night Target: Living Room", 68.0,
        ),
        NightRoomTargetNumberEntity(
            coordinator, controller, "main_office", "Night Target: Main Office", 66.0,
        ),
        NightFloorTargetNumberEntity(
            coordinator, controller, "laundry", "Night Floor Target: Laundry", 70.0,
        ),
    ]
    return entities


def get_night_setback_switch_entities(
    coordinator,
    controller: OutdoorResetController,
) -> list[SwitchEntity]:
    return [
        NightSetbackEnableSwitch(coordinator, controller),
        NightModeActiveSwitch(coordinator, controller),
    ]


class NightSetbackEnableSwitch(SwitchEntity):
    """Enable automatic night setback after midnight when no motion."""

    _attr_has_entity_name = True
    _attr_name = "Night Setback Enabled"
    _attr_icon = "mdi:weather-night"
    _attr_unique_id = "sensorlinx_outdoor_reset_night_setback_enabled"

    def __init__(self, coordinator, controller: OutdoorResetController) -> None:
        self._coordinator = coordinator
        self._controller = controller

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, "outdoor_reset")},
            "name": "SensorLinx Outdoor Reset",
            "manufacturer": "HBX Controls",
            "model": "Heating Curve Controller",
        }

    @property
    def is_on(self) -> bool:
        return self._controller.params.night_setback.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._controller.params.night_setback.enabled = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._controller.params.night_setback.enabled = False
        self.async_write_ha_state()


class NightModeActiveSwitch(SwitchEntity):
    """Indicates night setback is currently active (read-only indicator)."""

    _attr_has_entity_name = True
    _attr_name = "Night Mode Active"
    _attr_icon = "mdi:sleep"
    _attr_unique_id = "sensorlinx_outdoor_reset_night_mode_active"

    def __init__(self, coordinator, controller: OutdoorResetController) -> None:
        self._coordinator = coordinator
        self._controller = controller

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, "outdoor_reset")},
            "name": "SensorLinx Outdoor Reset",
            "manufacturer": "HBX Controls",
            "model": "Heating Curve Controller",
        }

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_NIGHT_MODE_CHANGED, self._handle_night_mode_changed
            )
        )

    @callback
    def _handle_night_mode_changed(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self._controller.params.night_setback.active

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._controller._async_apply_night_setback("manual")

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._controller._async_restore_day_setback("manual")


class NightMotionIdleNumberEntity(RestoreNumber):
    """Minutes without first-floor motion before night setback applies."""

    _attr_has_entity_name = True
    _attr_name = "Night Setback: Motion Idle (min)"
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:motion-sensor"
    _attr_native_min_value = 15
    _attr_native_max_value = 120
    _attr_native_step = 5
    _attr_unique_id = "sensorlinx_outdoor_reset_night_motion_idle_minutes"

    def __init__(self, coordinator, controller: OutdoorResetController) -> None:
        self._coordinator = coordinator
        self._controller = controller
        self._attr_native_value = controller.params.night_setback.motion_idle_minutes

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, "outdoor_reset")},
            "name": "SensorLinx Outdoor Reset",
            "manufacturer": "HBX Controls",
            "model": "Heating Curve Controller",
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last and last.native_value is not None:
            self._attr_native_value = int(last.native_value)
        self._controller.params.night_setback.motion_idle_minutes = int(self._attr_native_value)

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = int(value)
        self._controller.params.night_setback.motion_idle_minutes = int(value)
        self.async_write_ha_state()


class NightRoomTargetNumberEntity(RestoreNumber):
    """Per-zone room temperature target during night setback."""

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:thermometer-low"

    def __init__(
        self,
        coordinator,
        controller: OutdoorResetController,
        zone_key: str,
        name: str,
        default: float,
    ) -> None:
        self._coordinator = coordinator
        self._controller = controller
        self._zone_key = zone_key
        self._default = default
        self._attr_name = name
        self._attr_native_min_value = 60
        self._attr_native_max_value = 75
        self._attr_native_step = 1
        self._attr_unique_id = f"sensorlinx_outdoor_reset_night_target_{zone_key}"
        self._attr_native_value = default

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, "outdoor_reset")},
            "name": "SensorLinx Outdoor Reset",
            "manufacturer": "HBX Controls",
            "model": "Heating Curve Controller",
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last and last.native_value is not None:
            self._attr_native_value = last.native_value
        self._controller.params.night_setback.night_room_targets[self._zone_key] = (
            self._attr_native_value
        )

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self._controller.params.night_setback.night_room_targets[self._zone_key] = value
        self.async_write_ha_state()
        if self._controller.params.night_setback.active:
            await self._controller._apply_setpoints()


class NightFloorTargetNumberEntity(RestoreNumber):
    """Floor temperature target during night setback (floor-control zones)."""

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:heat-wave"

    def __init__(
        self,
        coordinator,
        controller: OutdoorResetController,
        zone_key: str,
        name: str,
        default: float,
    ) -> None:
        self._coordinator = coordinator
        self._controller = controller
        self._zone_key = zone_key
        self._default = default
        self._attr_name = name
        self._attr_native_min_value = 65
        self._attr_native_max_value = 78
        self._attr_native_step = 1
        self._attr_unique_id = f"sensorlinx_outdoor_reset_night_floor_target_{zone_key}"
        self._attr_native_value = default

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, "outdoor_reset")},
            "name": "SensorLinx Outdoor Reset",
            "manufacturer": "HBX Controls",
            "model": "Heating Curve Controller",
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last and last.native_value is not None:
            self._attr_native_value = last.native_value
        self._controller.params.night_setback.night_floor_targets[self._zone_key] = (
            self._attr_native_value
        )

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self._controller.params.night_setback.night_floor_targets[self._zone_key] = value
        self.async_write_ha_state()
        if self._controller.params.night_setback.active:
            await self._controller._apply_setpoints()
