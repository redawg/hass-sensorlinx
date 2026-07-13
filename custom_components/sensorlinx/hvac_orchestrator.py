"""Forecast + actual-temp HVAC mode orchestration for whole-house and zone timing."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from homeassistant.components.number import NumberEntity, NumberMode, RestoreNumber
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import UnitOfTemperature

from .const import DOMAIN

if TYPE_CHECKING:
    from .outdoor_reset import OutdoorResetController

_LOGGER = logging.getLogger(__name__)

DEFAULT_ORCHESTRATOR_ENABLED = True
DEFAULT_ORCHESTRATOR_HEAT_SETPOINT = 72.0
DEFAULT_ORCHESTRATOR_COOL_SETPOINT = 74.0
DEFAULT_EVENING_COOL_CUTOFF_HOUR = 20
DEFAULT_PRECOOL_LEAD_MINUTES = 60
DEFAULT_COOL_OFF_OUTDOOR_MARGIN = 3.0  # °F below max_outdoor before leaving cool
DEFAULT_HEAT_ON_OUTDOOR_MARGIN = 2.0  # °F below shutdown before entering heat
DEFAULT_FORECAST_HIGH_MARGIN = 2.0  # °F below precool threshold to skip cool day
DEFAULT_FORECAST_LOW_HEAT_MARGIN = 5.0  # °F below shutdown triggers heat planning
DEFAULT_ZONE_THERMAL_LAG = 20.0  # min/°F fallback per zone


class HvacOrchestratorParams:
    """Runtime configuration for forecast-driven HVAC mode switching."""

    def __init__(self) -> None:
        self.enabled: bool = DEFAULT_ORCHESTRATOR_ENABLED
        self.heat_setpoint: float = DEFAULT_ORCHESTRATOR_HEAT_SETPOINT
        self.cool_setpoint: float = DEFAULT_ORCHESTRATOR_COOL_SETPOINT
        self.evening_cool_cutoff_hour: int = DEFAULT_EVENING_COOL_CUTOFF_HOUR
        self.precool_lead_minutes: int = DEFAULT_PRECOOL_LEAD_MINUTES
        self.cool_off_outdoor_margin: float = DEFAULT_COOL_OFF_OUTDOOR_MARGIN
        self.heat_on_outdoor_margin: float = DEFAULT_HEAT_ON_OUTDOOR_MARGIN
        self.forecast_high_margin: float = DEFAULT_FORECAST_HIGH_MARGIN
        self.forecast_low_heat_margin: float = DEFAULT_FORECAST_LOW_HEAT_MARGIN
        self.zone_thermal_lag: dict[str, float] = {}


class HvacOrchestratorMixin:
    """Mixin for OutdoorResetController — heat/cool/off from forecast + outdoor."""

    params: Any
    hass: Any
    coordinator: Any
    _orchestrator_active_mode: str | None
    _orchestrator_last_reason: str
    _orchestrator_last_decision: str
    _orchestrator_saved_hvac: dict[str, Any] | None

    def _orchestrator_params(self) -> HvacOrchestratorParams:
        return self.params.orchestrator

    def _init_orchestrator_state(self) -> None:
        self._orchestrator_active_mode = None
        self._orchestrator_last_reason = "startup"
        self._orchestrator_last_decision = "none"
        self._orchestrator_saved_hvac = None

    def zone_thermal_lag(self, zone_name: str) -> float:
        """Per-zone preheat lead multiplier (min per °F floor deficit)."""
        lag = self._orchestrator_params().zone_thermal_lag.get(zone_name)
        if lag is not None:
            return lag
        return self.params.thermal_lag

    async def _async_orchestrate_hvac_mode(self) -> None:
        """Choose heat/cool/off from forecast highs/lows and actual outdoor."""
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
        now = datetime.now()
        afternoon_high = await self._forecast_afternoon_high()
        overnight_low = await self._forecast_overnight_low()
        cool_limit = cc.max_outdoor_for_cooling
        cool_off_limit = cool_limit - oc.cool_off_outdoor_margin
        heat_on_limit = self.params.shutdown - oc.heat_on_outdoor_margin
        precool_needed_high = cc.precool_threshold - oc.forecast_high_margin
        heat_needed_low = self.params.shutdown - oc.forecast_low_heat_margin

        want_cool = False
        want_heat = False
        want_off = False
        reason = ""
        current_mode = state.state

        if outdoor is not None and afternoon_high is not None:
            hot_day_expected = afternoon_high >= precool_needed_high
            if (
                outdoor >= cool_limit
                and hot_day_expected
                and now.hour < oc.evening_cool_cutoff_hour
            ):
                want_cool = True
                reason = (
                    f"outdoor {outdoor:.0f}°F >= {cool_limit:.0f}°F, "
                    f"forecast high {afternoon_high:.0f}°F"
                )
            elif (
                current_mode == "cool"
                and self._orchestrator_active_mode == "cool"
                and outdoor >= cool_off_limit
                and hot_day_expected
                and now.hour < oc.evening_cool_cutoff_hour
            ):
                want_cool = True
                reason = f"holding cool (outdoor {outdoor:.0f}°F, high {afternoon_high:.0f}°F)"
            elif (
                current_mode == "cool"
                and outdoor is not None
                and outdoor < cool_off_limit
                and not hot_day_expected
            ):
                want_off = True
                reason = f"outdoor {outdoor:.0f}°F — no cooling needed today"
            elif (
                current_mode == "cool"
                and outdoor is not None
                and outdoor < cool_limit
                and hot_day_expected
                and now.hour < cc.precool_start_hour + 1
            ):
                want_off = True
                reason = (
                    f"waiting for afternoon heat (outdoor {outdoor:.0f}°F, "
                    f"high {afternoon_high:.0f}°F expected)"
                )

        if outdoor is not None and outdoor < heat_on_limit:
            want_heat = True
            want_off = False
            reason = f"outdoor {outdoor:.1f}°F < heat threshold {heat_on_limit:.1f}°F"
        elif (
            not want_cool
            and overnight_low is not None
            and overnight_low < heat_needed_low
            and now.hour >= oc.evening_cool_cutoff_hour - 2
            and outdoor is not None
            and outdoor < self.params.shutdown + 5
        ):
            want_heat = True
            want_off = False
            reason = (
                f"evening preheat: forecast low {overnight_low:.0f}°F "
                f"(outdoor {outdoor:.0f}°F)"
            )
        elif (
            self._orchestrator_active_mode == "heat"
            and outdoor is not None
            and outdoor < self.params.shutdown
        ):
            want_heat = True
            reason = f"holding heat until outdoor >= {self.params.shutdown:.0f}°F"

        if want_cool and want_heat:
            want_heat = False
        if want_cool:
            want_off = False

        if want_cool:
            await self._orchestrator_apply_cool(hvac_entity, oc, cc, reason)
        elif want_heat:
            await self._orchestrator_apply_heat(hvac_entity, oc, reason)
        elif want_off or self._orchestrator_active_mode in ("cool", "heat"):
            if want_off or (
                self._orchestrator_active_mode is not None
                and current_mode in ("cool", "heat")
                and outdoor is not None
                and cool_off_limit <= outdoor < cool_limit
                and now.hour >= oc.evening_cool_cutoff_hour
            ):
                await self._orchestrator_apply_off(
                    hvac_entity,
                    reason or "mild conditions — no heat/cool needed",
                )
            else:
                self._orchestrator_last_decision = "standby"
                self._orchestrator_last_reason = (
                    f"outdoor={outdoor}, high={afternoon_high}, low={overnight_low}"
                )
        else:
            self._orchestrator_last_decision = "standby"
            self._orchestrator_last_reason = (
                f"outdoor={outdoor}, high={afternoon_high}, low={overnight_low}"
            )

    async def _orchestrator_apply_cool(
        self, hvac_entity: str, oc: HvacOrchestratorParams, cc: Any, reason: str
    ) -> None:
        cc.precool_enabled = True
        cc.upstairs_bias_enabled = True
        state = self.hass.states.get(hvac_entity)
        current = state.state if state else None
        target = cc.precool_target if self.precool_active_today else oc.cool_setpoint

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
        _LOGGER.info("Orchestrator → COOL @ %.0f°F: %s", target, reason)

        now = datetime.now()
        cc_params = self._cooling_params()
        if (
            cc_params.precool_start_hour <= now.hour < cc_params.precool_end_hour
            and not self.precool_active_today
        ):
            afternoon_high = await self._forecast_afternoon_high()
            if afternoon_high and afternoon_high >= cc_params.precool_threshold:
                await self._async_set_hvac_cool(
                    cc_params.precool_target,
                    f"orchestrator pre-cool: forecast high {afternoon_high:.0f}°F",
                )
                self._precool_triggered_date = date.today()
                self._user_cool_setpoint = cc_params.precool_target

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
            round(
                oc.heat_setpoint
                if oc.heat_setpoint
                else self.calculated_target,
                0,
            ),
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

    async def _forecast_overnight_low(self) -> float | None:
        """Lowest forecast temp from now through 6 AM tomorrow."""
        forecast_entities: list[str] = []
        if self.params.forecast_entity_id:
            forecast_entities.append(self.params.forecast_entity_id)
        for st in self.hass.states.async_all("weather"):
            if st.entity_id not in forecast_entities:
                forecast_entities.append(st.entity_id)

        from datetime import timedelta

        now = datetime.now()
        end = now + timedelta(hours=18)
        best: float | None = None

        for entity_id in forecast_entities:
            forecast = await self._fetch_hourly_forecast_list(entity_id)
            if not forecast:
                continue
            for entry in forecast:
                fc_time_str = entry.get("datetime")
                fc_temp = entry.get("temperature")
                if fc_time_str is None or fc_temp is None:
                    continue
                try:
                    fc_temp_f = float(fc_temp)
                    fc_time = datetime.fromisoformat(
                        fc_time_str.replace("Z", "+00:00")
                    ).replace(tzinfo=None)
                except (ValueError, TypeError):
                    continue
                if fc_time < now or fc_time > end:
                    continue
                if best is None or fc_temp_f < best:
                    best = fc_temp_f
        return best

    def orchestrator_status(self) -> dict[str, Any]:
        oc = self._orchestrator_params()
        return {
            "enabled": oc.enabled,
            "active_mode": self._orchestrator_active_mode,
            "last_decision": self._orchestrator_last_decision,
            "last_reason": self._orchestrator_last_reason,
            "heat_setpoint": oc.heat_setpoint,
            "cool_setpoint": oc.cool_setpoint,
            "precool_lead_minutes": oc.precool_lead_minutes,
        }


# Import after mixin to avoid circular import at module level in outdoor_reset
MIN_VALID_HEAT_SETPOINT = 60.0


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
            "model": "Forecast Mode Control",
        }

    @property
    def is_on(self) -> bool:
        return self._controller.params.orchestrator.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._controller.params.orchestrator.enabled = True
        self.async_write_ha_state()
        await self._controller._async_orchestrate_hvac_mode()

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
            "model": "Forecast Mode Control",
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
            "model": "Forecast Mode Control",
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
            "model": "Forecast Mode Control",
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
    """Per-zone preheat lead time multiplier (minutes per °F floor deficit)."""

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
            "model": "Forecast Mode Control",
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
        attrs["outdoor_allows_cooling"] = self._controller._outdoor_allows_cooling()
        attrs["preheat_active"] = self._controller.preheat_active
        attrs["precool_triggered_today"] = self._controller.precool_active_today
        return attrs
