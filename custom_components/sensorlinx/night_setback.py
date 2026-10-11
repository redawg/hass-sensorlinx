"""Stepped night setback and morning slab recovery for radiant floors.

Schedule:
  11:00 PM  → evening 70°F
  midnight  → deep night 66°F
  (dynamic) → morning recovery to 72°F early enough for ~8:00 AM wake
  8:00 AM   → day outdoor-reset / floor targets

Morning lead time uses slab deficit × thermal lag, scaled by outdoor coldness
and tankless supply / return (ΔT) conditions.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.components.number import NumberEntity, NumberMode, RestoreNumber
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.event import async_track_time_change, async_track_time_interval

from .const import DOMAIN

SIGNAL_NIGHT_MODE_CHANGED = f"{DOMAIN}_night_mode_changed"

if TYPE_CHECKING:
    from .outdoor_reset import OutdoorResetController

_LOGGER = logging.getLogger(__name__)

# 11:00 PM — step down toward sleep
EVENING_TARGET = 70.0
EVENING_SETBACK_HOUR = 23
EVENING_SETBACK_MINUTE = 0

# Midnight — deep night (tunable via HA number entities)
DEFAULT_NIGHT_ROOM_TARGETS = {
    "main_area": 66.0,
    "living_room": 66.0,
    "main_office": 66.0,
}
DEFAULT_NIGHT_FLOOR_TARGETS = {
    "laundry": 66.0,
}
NIGHT_SETBACK_HOUR = 0
NIGHT_SETBACK_MINUTE = 0

# Morning wake — floors at comfort by this clock time
MORNING_WAKE_TARGET = 72.0
MORNING_WAKE_HOUR = 8
MORNING_WAKE_MINUTE = 0
MORNING_CHECK_INTERVAL = timedelta(minutes=5)
MORNING_LEAD_MIN = 20.0  # never start recovery with less than this if deficit exists
MORNING_LEAD_MAX = 240.0  # 4 h cap

PHASE_OFF = "off"
PHASE_EVENING = "evening"
PHASE_NIGHT = "night"
PHASE_MORNING = "morning"


class NightSetbackParams:
    """Night setback configuration and runtime state."""

    def __init__(self) -> None:
        self.enabled: bool = True
        self.active: bool = False
        self.phase: str = PHASE_OFF  # off | evening | night | morning
        self.night_room_targets: dict[str, float] = dict(DEFAULT_NIGHT_ROOM_TARGETS)
        self.night_floor_targets: dict[str, float] = dict(DEFAULT_NIGHT_FLOOR_TARGETS)
        self.evening_target: float = EVENING_TARGET
        self.morning_wake_target: float = MORNING_WAKE_TARGET
        self.morning_wake_hour: int = MORNING_WAKE_HOUR
        self.morning_wake_minute: int = MORNING_WAKE_MINUTE
        self.last_morning_lead_minutes: float | None = None


class NightSetbackMixin:
    """Mixin methods for OutdoorResetController night setback + morning recovery."""

    params: Any
    hass: HomeAssistant
    _unsub_evening_schedule: Any
    _unsub_night_schedule: Any
    _unsub_day_restore: Any
    _unsub_morning_check: Any

    def _night_params(self) -> NightSetbackParams:
        return self.params.night_setback

    async def _setup_night_setback(self) -> None:
        """Register evening, midnight, morning-check, and wake-time restore."""

        @callback
        def _on_evening_setback(now: datetime) -> None:
            self.hass.async_create_task(
                self._async_apply_evening_setback("11:00 PM schedule")
            )

        self._unsub_evening_schedule = async_track_time_change(
            self.hass,
            _on_evening_setback,
            hour=EVENING_SETBACK_HOUR,
            minute=EVENING_SETBACK_MINUTE,
            second=0,
        )

        @callback
        def _on_night_setback(now: datetime) -> None:
            self.hass.async_create_task(
                self._async_apply_night_setback("midnight schedule")
            )

        self._unsub_night_schedule = async_track_time_change(
            self.hass,
            _on_night_setback,
            hour=NIGHT_SETBACK_HOUR,
            minute=NIGHT_SETBACK_MINUTE,
            second=0,
        )

        @callback
        def _on_wake_restore(now: datetime) -> None:
            self.hass.async_create_task(
                self._async_restore_day_setback("8:00 AM wake")
            )

        self._unsub_day_restore = async_track_time_change(
            self.hass,
            _on_wake_restore,
            hour=MORNING_WAKE_HOUR,
            minute=MORNING_WAKE_MINUTE,
            second=0,
        )

        @callback
        def _on_morning_check(now: datetime) -> None:
            self.hass.async_create_task(self._async_check_morning_recovery())

        self._unsub_morning_check = async_track_time_interval(
            self.hass, _on_morning_check, MORNING_CHECK_INTERVAL
        )

        await self._async_night_setback_catch_up()
        await self._async_check_morning_recovery()

    async def _async_night_setback_catch_up(self) -> None:
        """Apply the phase that should already be active for the current clock."""
        ns = self._night_params()
        if not ns.enabled:
            return
        now = datetime.now()
        wake_minutes = ns.morning_wake_hour * 60 + ns.morning_wake_minute
        now_minutes = now.hour * 60 + now.minute
        if now_minutes < wake_minutes:
            # Overnight — prefer night; morning check may promote to recovery
            if ns.phase not in (PHASE_NIGHT, PHASE_MORNING):
                await self._async_apply_night_setback("startup catch-up")
        elif now.hour >= EVENING_SETBACK_HOUR:
            await self._async_apply_evening_setback("startup catch-up")

    def _unload_night_setback(self) -> None:
        for attr in (
            "_unsub_evening_schedule",
            "_unsub_night_schedule",
            "_unsub_day_restore",
            "_unsub_morning_check",
        ):
            unsub = getattr(self, attr, None)
            if unsub:
                unsub()
                setattr(self, attr, None)

    def _minutes_until_morning_wake(self, now: datetime | None = None) -> float:
        """Minutes until today's (or tomorrow's) morning wake clock."""
        ns = self._night_params()
        now = now or datetime.now()
        wake = now.replace(
            hour=ns.morning_wake_hour,
            minute=ns.morning_wake_minute,
            second=0,
            microsecond=0,
        )
        if now >= wake:
            wake = wake + timedelta(days=1)
        return (wake - now).total_seconds() / 60.0

    def _compute_morning_lead_minutes(self) -> float:
        """Worst-case minutes to bring slabs to morning wake target.

        Factors:
          - floor deficit × per-zone thermal lag (min/°F)
          - outdoor coldness (slower when near design outdoor)
          - supply water vs curve target (cooler supply → more lead)
          - loop ΔT (low extraction → more lead)
        """
        ns = self._night_params()
        wake_target = ns.morning_wake_target
        outdoor = getattr(self, "outdoor_temp", None)
        supply = getattr(self, "supply_water_actual", None)
        dT = getattr(self, "delta_t", None)

        outdoor_mult = 1.0
        if outdoor is not None and hasattr(self, "_outdoor_boost_fraction"):
            # 1.0 mild → 1.5 at design cold
            outdoor_mult = 1.0 + 0.5 * float(self._outdoor_boost_fraction(outdoor))

        supply_mult = 1.0
        if outdoor is not None and supply is not None and hasattr(
            self, "_compute_supply_water_temp"
        ):
            try:
                curve = float(self._compute_supply_water_temp(outdoor))
                if curve > supply + 5:
                    supply_mult = 1.0 + min(0.5, (curve - supply) / 40.0)
            except (TypeError, ValueError):
                pass

        dt_mult = 1.0
        if dT is not None and dT < 6.0:
            # Weak extraction across the closed loop
            dt_mult = 1.0 + min(0.5, (6.0 - dT) / 12.0)

        worst = 0.0
        for zone in self.get_heating_zones():
            zone_name = zone.zone_key
            if self.is_schedule_managed_zone(zone_name):
                continue
            raw = self._read_zone_floor_temp(zone_name)
            floor = self.effective_floor_temp(zone_name, raw)
            if floor is None:
                deficit = 4.0  # unknown slab — assume moderate recovery need
            else:
                deficit = max(0.0, wake_target - float(floor))
            if deficit <= 0:
                continue
            lag = float(self.zone_thermal_lag(zone_name))
            minutes = deficit * lag * outdoor_mult * supply_mult * dt_mult
            worst = max(worst, minutes)

        if worst <= 0:
            ns.last_morning_lead_minutes = 0.0
            return 0.0

        lead = min(MORNING_LEAD_MAX, max(MORNING_LEAD_MIN, worst))
        ns.last_morning_lead_minutes = round(lead, 1)
        return lead

    async def _async_check_morning_recovery(self) -> None:
        """Start morning recovery when lead time says slabs won't hit 72 by wake."""
        ns = self._night_params()
        if not ns.enabled:
            return
        if ns.phase not in (PHASE_EVENING, PHASE_NIGHT):
            return

        now = datetime.now()
        # Only recover for today's wake (overnight window)
        wake_today = now.replace(
            hour=ns.morning_wake_hour,
            minute=ns.morning_wake_minute,
            second=0,
            microsecond=0,
        )
        if now >= wake_today:
            return
        # Don't start morning recovery in the evening setback window
        if now.hour >= EVENING_SETBACK_HOUR:
            return

        minutes_to_wake = (wake_today - now).total_seconds() / 60.0
        lead = self._compute_morning_lead_minutes()
        if lead <= 0:
            return
        if minutes_to_wake > lead:
            _LOGGER.debug(
                "Morning recovery: %.0f min to wake, need %.0f min lead — waiting",
                minutes_to_wake, lead,
            )
            return

        outdoor = getattr(self, "outdoor_temp", None)
        supply = getattr(self, "supply_water_actual", None)
        ret = getattr(self, "return_water_actual", None)
        dT = getattr(self, "delta_t", None)
        _LOGGER.info(
            "Morning recovery START: %.0f min to wake, lead=%.0f min, "
            "outdoor=%s supply=%s return=%s dT=%s → zones to %.0fF",
            minutes_to_wake,
            lead,
            f"{outdoor:.1f}F" if outdoor is not None else "?",
            f"{supply:.0f}F" if supply is not None else "?",
            f"{ret:.0f}F" if ret is not None else "?",
            f"{dT:.1f}F" if dT is not None else "?",
            ns.morning_wake_target,
        )
        await self._async_apply_morning_wake(
            f"lead {lead:.0f} min (wake {ns.morning_wake_hour:02d}:{ns.morning_wake_minute:02d})"
        )

    def _zone_night_target(
        self,
        zone_name: str,
        floor_mode: bool,
        floor_temp: float | None,
        outdoor: float,
        zone: Any = None,
    ) -> float:
        """Setpoint while evening, deep-night, or morning-recovery is active."""
        ns = self._night_params()
        if ns.phase == PHASE_MORNING:
            target = ns.morning_wake_target
        elif ns.phase == PHASE_EVENING:
            target = ns.evening_target
        elif floor_mode:
            target = ns.night_floor_targets.get(
                zone_name, DEFAULT_NIGHT_FLOOR_TARGETS.get(zone_name, 66.0)
            )
        else:
            target = ns.night_room_targets.get(
                zone_name, DEFAULT_NIGHT_ROOM_TARGETS.get(zone_name, 66.0)
            )

        if floor_mode:
            if zone is not None and getattr(zone, "thm_floor_mode", False):
                return self._device_floor_setpoint(zone_name, target)
            if zone is not None and zone.direct_floor_thermostat:
                return self._compute_direct_floor_setpoint(
                    floor_temp, target, zone_name
                )
            return self._compute_floor_mode_setpoint(floor_temp, target, zone_name)
        return target

    async def _async_apply_evening_setback(self, reason: str) -> None:
        """11 PM step: all zones to evening target (70°F)."""
        ns = self._night_params()
        if not ns.enabled:
            return
        if ns.phase == PHASE_EVENING:
            return
        if ns.phase in (PHASE_NIGHT, PHASE_MORNING) and "catch-up" in reason:
            return
        if ns.phase == PHASE_NIGHT and "catch-up" not in reason:
            # Don't regress from deep night to evening except fresh 11 PM edge
            pass
        ns.phase = PHASE_EVENING
        ns.active = True
        _LOGGER.info(
            "Evening setback activated (%s) — zones -> %.0fF",
            reason, ns.evening_target,
        )
        await self._apply_setpoints()
        async_dispatcher_send(self.hass, SIGNAL_NIGHT_MODE_CHANGED)

    async def _async_apply_night_setback(self, reason: str) -> None:
        """Midnight step: deep night targets (default 66°F)."""
        ns = self._night_params()
        if not ns.enabled:
            return
        if ns.phase == PHASE_NIGHT:
            return
        if ns.phase == PHASE_MORNING and "catch-up" in reason:
            return
        ns.phase = PHASE_NIGHT
        ns.active = True
        _LOGGER.info("Night setback activated (%s) — deep night targets", reason)
        await self._apply_setpoints()
        async_dispatcher_send(self.hass, SIGNAL_NIGHT_MODE_CHANGED)

    async def _async_apply_morning_wake(self, reason: str) -> None:
        """Raise zones to morning wake target (72°F) ahead of the wake clock."""
        ns = self._night_params()
        if not ns.enabled:
            return
        if ns.phase == PHASE_MORNING:
            return
        ns.phase = PHASE_MORNING
        ns.active = True
        _LOGGER.info(
            "Morning wake recovery (%s) — zones -> %.0fF",
            reason, ns.morning_wake_target,
        )
        await self._apply_setpoints()
        async_dispatcher_send(self.hass, SIGNAL_NIGHT_MODE_CHANGED)

    async def _async_restore_day_setback(self, reason: str) -> None:
        ns = self._night_params()
        if ns.phase == PHASE_OFF and not ns.active:
            return
        ns.phase = PHASE_OFF
        ns.active = False
        _LOGGER.info("Day setback restored (%s)", reason)
        await self._apply_setpoints()
        async_dispatcher_send(self.hass, SIGNAL_NIGHT_MODE_CHANGED)


def get_night_setback_number_entities(
    coordinator,
    controller: OutdoorResetController,
) -> list[NumberEntity]:
    """Night / morning target number entities."""
    return [
        NightRoomTargetNumberEntity(
            coordinator, controller, "main_area", "Night Target: Main Area", 66.0,
        ),
        NightRoomTargetNumberEntity(
            coordinator, controller, "living_room", "Night Target: Living Room", 66.0,
        ),
        NightRoomTargetNumberEntity(
            coordinator, controller, "main_office", "Night Target: Main Office", 66.0,
        ),
        NightFloorTargetNumberEntity(
            coordinator, controller, "laundry", "Night Floor Target: Laundry", 66.0,
        ),
        MorningWakeTargetNumberEntity(coordinator, controller),
    ]


def get_night_setback_switch_entities(
    coordinator,
    controller: OutdoorResetController,
) -> list[SwitchEntity]:
    return [
        NightSetbackEnableSwitch(coordinator, controller),
        NightModeActiveSwitch(coordinator, controller),
    ]


class NightSetbackEnableSwitch(SwitchEntity):
    """Enable evening / night setback and morning slab recovery."""

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
    """Indicates evening, deep-night, or morning-recovery is active."""

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

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        ns = self._controller.params.night_setback
        return {
            "phase": ns.phase,
            "evening_target": ns.evening_target,
            "morning_wake_target": ns.morning_wake_target,
            "morning_wake_time": (
                f"{ns.morning_wake_hour:02d}:{ns.morning_wake_minute:02d}"
            ),
            "morning_lead_minutes": ns.last_morning_lead_minutes,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._controller._async_apply_night_setback("manual")

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._controller._async_restore_day_setback("manual")


class MorningWakeTargetNumberEntity(RestoreNumber):
    """Comfort floor/room target that morning recovery aims to hit by wake time."""

    _attr_has_entity_name = True
    _attr_name = "Morning Wake Target"
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:weather-sunset-up"
    _attr_unique_id = "sensorlinx_outdoor_reset_morning_wake_target"

    def __init__(self, coordinator, controller: OutdoorResetController) -> None:
        self._coordinator = coordinator
        self._controller = controller
        self._attr_native_min_value = 65
        self._attr_native_max_value = 78
        self._attr_native_step = 1
        self._attr_native_value = MORNING_WAKE_TARGET

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
        self._controller.params.night_setback.morning_wake_target = float(
            self._attr_native_value
        )

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self._controller.params.night_setback.morning_wake_target = float(value)
        self.async_write_ha_state()
        if self._controller.params.night_setback.phase == PHASE_MORNING:
            await self._controller._apply_setpoints()


class NightRoomTargetNumberEntity(RestoreNumber):
    """Per-zone target during deep-night (midnight) setback."""

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
        if self._controller.params.night_setback.phase == PHASE_NIGHT:
            await self._controller._apply_setpoints()


class NightFloorTargetNumberEntity(RestoreNumber):
    """Floor target during deep-night setback (floor-control zones)."""

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
        self._attr_native_min_value = 60
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
        if self._controller.params.night_setback.phase == PHASE_NIGHT:
            await self._controller._apply_setpoints()
