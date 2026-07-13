"""Actual-temp HVAC mode orchestration with hourly and realtime threshold checks."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.components.number import NumberEntity, NumberMode, RestoreNumber
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import UnitOfTemperature
from homeassistant.core import callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)

from .const import DOMAIN

OUTDOOR_TEMP_ENTITY = "sensor.quail_creek_ames_lake_279th_ct_ne_temperature"

if TYPE_CHECKING:
    from .outdoor_reset import OutdoorResetController

_LOGGER = logging.getLogger(__name__)

ORCHESTRATOR_INTERVAL = timedelta(hours=1)
OUTDOOR_REALTIME_DEBOUNCE = timedelta(minutes=5)

DEFAULT_ORCHESTRATOR_ENABLED = True
DEFAULT_ORCHESTRATOR_HEAT_SETPOINT = 72.0
DEFAULT_ORCHESTRATOR_COOL_SETPOINT = 74.0
DEFAULT_EVENING_COOL_CUTOFF_HOUR = 20
DEFAULT_PRECOOL_LEAD_MINUTES = 60
DEFAULT_COOL_OFF_OUTDOOR_MARGIN = 3.0
DEFAULT_HEAT_ON_OUTDOOR_MARGIN = 2.0
DEFAULT_INDOOR_COOL_MARGIN = 1.0
DEFAULT_ZONE_THERMAL_LAG = 20.0
MIN_VALID_HEAT_SETPOINT = 60.0


class HvacOrchestratorParams:
    """Runtime configuration for actual-temp HVAC mode switching."""

    def __init__(self) -> None:
        self.enabled: bool = DEFAULT_ORCHESTRATOR_ENABLED
        self.heat_setpoint: float = DEFAULT_ORCHESTRATOR_HEAT_SETPOINT
        self.cool_setpoint: float = DEFAULT_ORCHESTRATOR_COOL_SETPOINT
        self.evening_cool_cutoff_hour: int = DEFAULT_EVENING_COOL_CUTOFF_HOUR
        self.precool_lead_minutes: int = DEFAULT_PRECOOL_LEAD_MINUTES
        self.cool_off_outdoor_margin: float = DEFAULT_COOL_OFF_OUTDOOR_MARGIN
        self.heat_on_outdoor_margin: float = DEFAULT_HEAT_ON_OUTDOOR_MARGIN
        self.indoor_cool_margin: float = DEFAULT_INDOOR_COOL_MARGIN
        self.zone_thermal_lag: dict[str, float] = {}


class HvacOrchestratorMixin:
    """Mixin for OutdoorResetController — heat/cool/off from actual temps."""

    params: Any
    hass: Any
    coordinator: Any
    _orchestrator_active_mode: str | None
    _orchestrator_last_reason: str
    _orchestrator_last_decision: str
    _orchestrator_saved_hvac: dict[str, Any] | None
    _orchestrator_day_high: float | None
    _orchestrator_day_date: date | None
    _orchestrator_last_run: datetime | None
    _orchestrator_last_outdoor_trigger: datetime | None
    _unsub_orch_interval: Any
    _unsub_orch_outdoor: Any

    def _orchestrator_params(self) -> HvacOrchestratorParams:
        return self.params.orchestrator

    def _init_orchestrator_state(self) -> None:
        self._orchestrator_active_mode = None
        self._orchestrator_last_reason = "startup"
        self._orchestrator_last_decision = "none"
        self._orchestrator_saved_hvac = None
        self._orchestrator_day_high = None
        self._orchestrator_day_date = None
        self._orchestrator_last_run = None
        self._orchestrator_last_outdoor_trigger = None
        self._unsub_orch_interval = None
        self._unsub_orch_outdoor = None

    async def _setup_hvac_orchestrator(self) -> None:
        """Hourly mode review plus realtime outdoor threshold crossings."""
        self._unsub_orch_interval = async_track_time_interval(
            self.hass, self._async_orchestrator_hourly_tick, ORCHESTRATOR_INTERVAL
        )
        self._unsub_orch_outdoor = async_track_state_change_event(
            self.hass,
            [OUTDOOR_TEMP_ENTITY],
            self._async_on_orchestrator_outdoor_change,
        )
        outdoor = self.outdoor_temp
        if outdoor is not None:
            self._orchestrator_update_day_high(outdoor)
            self._record_outdoor_temp(outdoor)
        await self._async_orchestrate_hvac_mode(force=True)

    def _unload_hvac_orchestrator(self) -> None:
        for attr in ("_unsub_orch_interval", "_unsub_orch_outdoor"):
            unsub = getattr(self, attr, None)
            if unsub:
                unsub()
                setattr(self, attr, None)

    def zone_thermal_lag(self, zone_name: str) -> float:
        lag = self._orchestrator_params().zone_thermal_lag.get(zone_name)
        if lag is not None:
            return lag
        return self.params.thermal_lag

    def _orchestrator_update_day_high(self, outdoor: float | None) -> None:
        if outdoor is None:
            return
        today = date.today()
        if self._orchestrator_day_date != today:
            self._orchestrator_day_date = today
            self._orchestrator_day_high = outdoor
        elif (
            self._orchestrator_day_high is None
            or outdoor > self._orchestrator_day_high
        ):
            self._orchestrator_day_high = outdoor

    def _orchestrator_outdoor_trend(self) -> float | None:
        """Return outdoor change rate in °F per minute (negative = cooling)."""
        if len(self._temp_history) < 3:
            return None
        oldest_time, oldest_temp = self._temp_history[0]
        newest_time, newest_temp = self._temp_history[-1]
        minutes = (newest_time - oldest_time).total_seconds() / 60.0
        if minutes < 10:
            return None
        return (newest_temp - oldest_temp) / minutes

    def _outdoor_crossed_orchestrator_threshold(
        self, old_temp: float | None, new_temp: float | None
    ) -> bool:
        if old_temp is None or new_temp is None:
            return new_temp is not None
        cc = self._cooling_params()
        cool_limit = cc.max_outdoor_for_cooling
        cool_off = cool_limit - self._orchestrator_params().cool_off_outdoor_margin
        heat_on = self.params.shutdown - self._orchestrator_params().heat_on_outdoor_margin
        shutdown = self.params.shutdown
        thresholds = (cool_limit, cool_off, heat_on, shutdown)
        for threshold in thresholds:
            if (old_temp < threshold <= new_temp) or (old_temp >= threshold > new_temp):
                return True
        return abs(new_temp - old_temp) >= 2.0

    @callback
    def _async_on_orchestrator_outdoor_change(self, event) -> None:
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if new_state is None or new_state.state in ("unavailable", "unknown"):
            return
        try:
            new_temp = float(new_state.state)
        except (TypeError, ValueError):
            return
        old_temp = None
        if old_state and old_state.state not in ("unavailable", "unknown"):
            try:
                old_temp = float(old_state.state)
            except (TypeError, ValueError):
                pass

        self._orchestrator_update_day_high(new_temp)
        self._record_outdoor_temp(new_temp)

        now = datetime.now()
        if self._orchestrator_last_outdoor_trigger is not None:
            if now - self._orchestrator_last_outdoor_trigger < OUTDOOR_REALTIME_DEBOUNCE:
                return
        if not self._outdoor_crossed_orchestrator_threshold(old_temp, new_temp):
            return

        self._orchestrator_last_outdoor_trigger = now
        self.hass.async_create_task(
            self._async_orchestrate_hvac_mode(force=True, trigger="outdoor")
        )

    async def _async_orchestrator_hourly_tick(self, _now=None) -> None:
        await self._async_orchestrate_hvac_mode(force=True, trigger="hourly")

    async def _async_orchestrate_hvac_mode(
        self, *, force: bool = False, trigger: str = "scheduled"
    ) -> None:
        """Choose heat/cool/off from actual outdoor and indoor temps."""
        if not self.enabled:
            return
        oc = self._orchestrator_params()
        if not oc.enabled:
            return
        if self.is_cooling_paused:
            return

        hvac_entity = self.params.main_hvac_climate_entity_id
        if not hvac_entity:
            return
        state = self.hass.states.get(hvac_entity)
        if state is None or state.state in ("unavailable", "unknown"):
            return

        cc = self._cooling_params()
        outdoor = self.outdoor_temp
        main = self.main_floor_temp
        now = datetime.now()
        self._orchestrator_update_day_high(outdoor)
        if outdoor is not None:
            self._record_outdoor_temp(outdoor)

        cool_limit = cc.max_outdoor_for_cooling
        cool_off_limit = cool_limit - oc.cool_off_outdoor_margin
        heat_on_limit = self.params.shutdown - oc.heat_on_outdoor_margin
        shutdown = self.params.shutdown
        trend = self._orchestrator_outdoor_trend()
        current_mode = state.state

        want_cool = False
        want_heat = False
        want_off = False
        reason = ""

        if outdoor is not None and outdoor >= cool_limit and now.hour < oc.evening_cool_cutoff_hour:
            want_cool = True
            reason = f"actual outdoor {outdoor:.0f}°F >= {cool_limit:.0f}°F"
        elif (
            self._orchestrator_active_mode == "cool"
            and outdoor is not None
            and outdoor >= cool_off_limit
            and now.hour < oc.evening_cool_cutoff_hour
        ):
            want_cool = True
            reason = f"holding cool (actual outdoor {outdoor:.0f}°F)"

        if (
            not want_heat
            and outdoor is not None
            and outdoor < heat_on_limit
        ):
            want_heat = True
            want_off = False
            reason = f"actual outdoor {outdoor:.1f}°F < {heat_on_limit:.1f}°F"
        elif (
            self._orchestrator_active_mode == "heat"
            and outdoor is not None
            and outdoor < shutdown
        ):
            want_heat = True
            reason = f"holding heat (actual outdoor {outdoor:.0f}°F < {shutdown:.0f}°F)"
        elif (
            not want_cool
            and outdoor is not None
            and outdoor < shutdown + 3
            and now.hour >= oc.evening_cool_cutoff_hour - 2
            and trend is not None
            and trend < -0.02
        ):
            want_heat = True
            want_off = False
            reason = (
                f"actual evening cool-down: outdoor {outdoor:.0f}°F "
                f"(trend {trend:.2f}°F/min)"
            )

        if (
            not want_heat
            and not want_cool
            and outdoor is not None
            and outdoor >= cool_limit
            and main is not None
            and main > oc.cool_setpoint + oc.indoor_cool_margin
        ):
            want_cool = True
            reason = (
                f"actual indoor {main:.0f}°F warm with outdoor {outdoor:.0f}°F"
            )

        if want_cool and want_heat:
            if outdoor is not None and outdoor >= cool_limit:
                want_heat = False
            else:
                want_cool = False

        if not want_cool and not want_heat:
            if (
                current_mode == "cool"
                and outdoor is not None
                and outdoor < cool_off_limit
            ):
                want_off = True
                reason = (
                    f"actual outdoor {outdoor:.0f}°F below cool release "
                    f"{cool_off_limit:.0f}°F"
                )
            elif (
                outdoor is not None
                and shutdown <= outdoor < cool_off_limit
                and (
                    current_mode in ("cool", "heat")
                    or self._orchestrator_active_mode in ("cool", "heat")
                )
            ):
                want_off = True
                reason = (
                    f"mild actual outdoor {outdoor:.0f}°F "
                    f"({shutdown:.0f}–{cool_off_limit:.0f}°F band)"
                )
            elif (
                now.hour >= oc.evening_cool_cutoff_hour
                and self._orchestrator_active_mode == "cool"
                and outdoor is not None
                and outdoor < cool_limit
            ):
                want_off = True
                reason = f"evening: actual outdoor {outdoor:.0f}°F below {cool_limit:.0f}°F"

        if want_cool:
            await self._orchestrator_apply_cool(hvac_entity, oc, cc, reason)
        elif want_heat:
            await self._orchestrator_apply_heat(hvac_entity, oc, reason)
        elif want_off:
            await self._orchestrator_apply_off(hvac_entity, reason)
        else:
            self._orchestrator_last_decision = "standby"
            self._orchestrator_last_reason = (
                f"actual outdoor={outdoor}, main={main}, "
                f"day_high={self._orchestrator_day_high}, trend={trend}, "
                f"trigger={trigger}"
            )

        self._orchestrator_last_run = now

    async def _orchestrator_apply_cool(
        self, hvac_entity: str, oc: HvacOrchestratorParams, cc: Any, reason: str
    ) -> None:
        cc.precool_enabled = True
        cc.upstairs_bias_enabled = True
        state = self.hass.states.get(hvac_entity)
        current = state.state if state else None
        outdoor = self.outdoor_temp
        target = oc.cool_setpoint

        now = datetime.now()
        if (
            cc.precool_start_hour <= now.hour < cc.precool_end_hour
            and outdoor is not None
            and outdoor >= cc.max_outdoor_for_cooling
        ):
            target = cc.precool_target

        if self._orchestrator_active_mode != "cool":
            self._orchestrator_save_hvac_state(state)

        if current != "cool":
            await self.hass.services.async_call(
                "climate",
                "set_hvac_mode",
                {"entity_id": hvac_entity, "hvac_mode": "cool"},
                blocking=True,
            )
        await self.hass.services.async_call(
            "climate",
            "set_temperature",
            {"entity_id": hvac_entity, "temperature": target, "hvac_mode": "cool"},
            blocking=True,
        )
        self._user_cool_setpoint = target
        self._orchestrator_active_mode = "cool"
        self._orchestrator_last_decision = "cool"
        self._orchestrator_last_reason = reason
        if target == cc.precool_target:
            self._precool_triggered_date = date.today()
        _LOGGER.info("Orchestrator → COOL @ %.0f°F: %s", target, reason)

    async def _orchestrator_apply_heat(
        self, hvac_entity: str, oc: HvacOrchestratorParams, reason: str
    ) -> None:
        cc = self._cooling_params()
        cc.precool_enabled = False
        cc.upstairs_bias_enabled = False
        self._upstairs_bias_active = False
        self._precool_triggered_date = None
        self._last_cool_adjustment = 0.0
        await self._async_turn_off_cooling_fans()
        await self._async_stop_furnace_circulation_fan()

        state = self.hass.states.get(hvac_entity)
        current = state.state if state else None
        target = max(
            MIN_VALID_HEAT_SETPOINT,
            round(oc.heat_setpoint or self.calculated_target, 0),
        )

        if self._orchestrator_active_mode != "heat":
            self._orchestrator_save_hvac_state(state)

        if current != "heat":
            await self.hass.services.async_call(
                "climate",
                "set_hvac_mode",
                {"entity_id": hvac_entity, "hvac_mode": "heat"},
                blocking=True,
            )
        await self.hass.services.async_call(
            "climate",
            "set_temperature",
            {"entity_id": hvac_entity, "temperature": target, "hvac_mode": "heat"},
            blocking=True,
        )
        self._orchestrator_active_mode = "heat"
        self._orchestrator_last_decision = "heat"
        self._orchestrator_last_reason = reason
        _LOGGER.info("Orchestrator → HEAT @ %.0f°F: %s", target, reason)
        await self._apply_setpoints()

    async def _orchestrator_apply_off(self, hvac_entity: str, reason: str) -> None:
        cc = self._cooling_params()
        cc.precool_enabled = False
        cc.upstairs_bias_enabled = False
        self._upstairs_bias_active = False
        self._precool_triggered_date = None
        await self._async_turn_off_cooling_fans()
        await self._async_stop_furnace_circulation_fan()

        state = self.hass.states.get(hvac_entity)
        if state and state.state not in ("off", "unavailable", "unknown"):
            await self.hass.services.async_call(
                "climate",
                "set_hvac_mode",
                {"entity_id": hvac_entity, "hvac_mode": "off"},
                blocking=True,
            )
        self._user_cool_setpoint = None
        self._orchestrator_active_mode = "off"
        self._orchestrator_last_decision = "off"
        self._orchestrator_last_reason = reason
        _LOGGER.info("Orchestrator → OFF: %s", reason)
        await self._apply_setpoints()

    def _orchestrator_save_hvac_state(self, state: Any) -> None:
        if state is None:
            return
        temp = state.attributes.get("temperature")
        self._orchestrator_saved_hvac = {
            "mode": state.state,
            "temperature": float(temp) if temp is not None else None,
        }

    def orchestrator_status(self) -> dict[str, Any]:
        oc = self._orchestrator_params()
        return {
            "enabled": oc.enabled,
            "active_mode": self._orchestrator_active_mode,
            "last_decision": self._orchestrator_last_decision,
            "last_reason": self._orchestrator_last_reason,
            "decision_source": "actual",
            "heat_setpoint": oc.heat_setpoint,
            "cool_setpoint": oc.cool_setpoint,
            "actual_day_high": self._orchestrator_day_high,
            "outdoor_trend_f_per_min": self._orchestrator_outdoor_trend(),
            "last_run": (
                self._orchestrator_last_run.isoformat()
                if self._orchestrator_last_run
                else None
            ),
            "next_hourly_check": (
                (self._orchestrator_last_run + ORCHESTRATOR_INTERVAL).isoformat()
                if self._orchestrator_last_run
                else None
            ),
        }


def get_orchestrator_switch_entities(coordinator, controller: OutdoorResetController) -> list:
    return [HvacOrchestratorEnableSwitch(coordinator, controller)]


def get_orchestrator_number_entities(coordinator, controller: OutdoorResetController) -> list:
    oc = controller.params.orchestrator
    entities: list[NumberEntity] = [
        OrchestratorHeatSetpointNumber(coordinator, controller, oc.heat_setpoint),
        OrchestratorCoolSetpointNumber(coordinator, controller, oc.cool_setpoint),
        OrchestratorPrecoolLeadNumber(coordinator, controller, oc.precool_lead_minutes),
    ]
    for thm in coordinator.get_thm_devices():
        zone_name = thm.name.lower().replace(" ", "_")
        default_lag = controller.zone_thermal_lag(zone_name)
        entities.append(
            ZoneThermalLagNumberEntity(
                coordinator, controller, zone_name, thm.name, default_lag
            )
        )
    return entities


def get_orchestrator_sensor_entities(coordinator, controller: OutdoorResetController) -> list:
    return [HvacOrchestratorStatusSensor(coordinator, controller)]


class HvacOrchestratorEnableSwitch(SwitchEntity):
    _attr_has_entity_name = True
    _attr_name = "HVAC Mode Orchestration"
    _attr_icon = "mdi:home-thermometer-outline"

    def __init__(self, coordinator, controller: OutdoorResetController) -> None:
        self._coordinator = coordinator
        self._controller = controller
        self._attr_unique_id = "sensorlinx_hvac_orchestrator_enabled"

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, "hvac_orchestrator")},
            "name": "SensorLinx HVAC Orchestrator",
            "manufacturer": "HBX Controls",
            "model": "Actual-Temp Mode Control",
        }

    @property
    def is_on(self) -> bool:
        return self._controller.params.orchestrator.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._controller.params.orchestrator.enabled = True
        self.async_write_ha_state()
        await self._controller._async_orchestrate_hvac_mode(force=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._controller.params.orchestrator.enabled = False
        self._controller._orchestrator_active_mode = None
        self.async_write_ha_state()


class OrchestratorHeatSetpointNumber(RestoreNumber):
    _attr_has_entity_name = True
    _attr_name = "Orchestrator Heat Setpoint"
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator, controller, initial: float) -> None:
        self._coordinator = coordinator
        self._controller = controller
        self._attr_unique_id = "sensorlinx_orchestrator_heat_setpoint"
        self._attr_native_min_value = 60
        self._attr_native_max_value = 78
        self._attr_native_step = 1
        self._attr_native_value = initial

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, "hvac_orchestrator")},
            "name": "SensorLinx HVAC Orchestrator",
            "manufacturer": "HBX Controls",
            "model": "Actual-Temp Mode Control",
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) and last.state not in (
            "unknown",
            "unavailable",
        ):
            try:
                self._attr_native_value = float(last.state)
                self._controller.params.orchestrator.heat_setpoint = float(last.state)
            except (TypeError, ValueError):
                pass

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self._controller.params.orchestrator.heat_setpoint = float(value)
        self.async_write_ha_state()


class OrchestratorCoolSetpointNumber(RestoreNumber):
    _attr_has_entity_name = True
    _attr_name = "Orchestrator Cool Setpoint"
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator, controller, initial: float) -> None:
        self._coordinator = coordinator
        self._controller = controller
        self._attr_unique_id = "sensorlinx_orchestrator_cool_setpoint"
        self._attr_native_min_value = 68
        self._attr_native_max_value = 80
        self._attr_native_step = 1
        self._attr_native_value = initial

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, "hvac_orchestrator")},
            "name": "SensorLinx HVAC Orchestrator",
            "manufacturer": "HBX Controls",
            "model": "Actual-Temp Mode Control",
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) and last.state not in (
            "unknown",
            "unavailable",
        ):
            try:
                self._attr_native_value = float(last.state)
                self._controller.params.orchestrator.cool_setpoint = float(last.state)
            except (TypeError, ValueError):
                pass

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self._controller.params.orchestrator.cool_setpoint = float(value)
        self.async_write_ha_state()


class OrchestratorPrecoolLeadNumber(RestoreNumber):
    _attr_has_entity_name = True
    _attr_name = "Pre-Cool Lead Time (min)"
    _attr_native_unit_of_measurement = "min"
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator, controller, initial: float) -> None:
        self._coordinator = coordinator
        self._controller = controller
        self._attr_unique_id = "sensorlinx_orchestrator_precool_lead"
        self._attr_native_min_value = 15
        self._attr_native_max_value = 180
        self._attr_native_step = 15
        self._attr_native_value = initial

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, "hvac_orchestrator")},
            "name": "SensorLinx HVAC Orchestrator",
            "manufacturer": "HBX Controls",
            "model": "Actual-Temp Mode Control",
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) and last.state not in (
            "unknown",
            "unavailable",
        ):
            try:
                self._attr_native_value = float(last.state)
                self._controller.params.orchestrator.precool_lead_minutes = int(
                    float(last.state)
                )
            except (TypeError, ValueError):
                pass

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self._controller.params.orchestrator.precool_lead_minutes = int(value)
        self.async_write_ha_state()


class ZoneThermalLagNumberEntity(RestoreNumber):
    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = "min/°F"
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        coordinator,
        controller: OutdoorResetController,
        zone_name: str,
        zone_label: str,
        initial: float,
    ) -> None:
        self._coordinator = coordinator
        self._controller = controller
        self._zone_name = zone_name
        self._attr_name = f"Preheat Lag: {zone_label}"
        self._attr_unique_id = f"sensorlinx_zone_thermal_lag_{zone_name}"
        self._attr_icon = "mdi:clock-fast"
        self._attr_native_min_value = 5
        self._attr_native_max_value = 60
        self._attr_native_step = 5
        self._attr_native_value = initial

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
        if (last := await self.async_get_last_state()) and last.state not in (
            "unknown",
            "unavailable",
        ):
            try:
                val = float(last.state)
                self._attr_native_value = val
                self._controller.params.orchestrator.zone_thermal_lag[
                    self._zone_name
                ] = val
            except (TypeError, ValueError):
                pass

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self._controller.params.orchestrator.zone_thermal_lag[self._zone_name] = float(
            value
        )
        self.async_write_ha_state()


class HvacOrchestratorStatusSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "HVAC Orchestrator Status"
    _attr_icon = "mdi:home-thermometer-outline"

    def __init__(self, coordinator, controller: OutdoorResetController) -> None:
        self._coordinator = coordinator
        self._controller = controller
        self._attr_unique_id = "sensorlinx_hvac_orchestrator_status"

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, "hvac_orchestrator")},
            "name": "SensorLinx HVAC Orchestrator",
            "manufacturer": "HBX Controls",
            "model": "Actual-Temp Mode Control",
        }

    @property
    def native_value(self) -> str:
        st = self._controller.orchestrator_status()
        if not st["enabled"]:
            return "disabled"
        return st.get("last_decision") or "standby"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = dict(self._controller.orchestrator_status())
        attrs["outdoor_temp"] = self._controller.outdoor_temp
        attrs["main_floor_temp"] = self._controller.main_floor_temp
        attrs["outdoor_allows_cooling"] = self._controller._outdoor_allows_cooling()
        attrs["preheat_active"] = self._controller.preheat_active
        attrs["precool_triggered_today"] = self._controller.precool_active_today
        return attrs
