"""Upstairs-aware cooling control and predictive pre-cool for forced-air HVAC."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.components.number import NumberEntity, NumberMode, RestoreNumber
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import (
    async_call_later,
    async_track_point_in_time,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from .const import (
    CONF_COOLING_PAUSE_REASON,
    CONF_COOLING_PAUSED_UNTIL,
    CONF_HUNTER_FAN,
    CONF_MAIN_FLOOR_TEMP_SENSOR,
    CONF_SIDNEY_FAN,
    CONF_UPSTAIRS_TEMP_SENSOR,
    DEFAULT_HUNTER_FAN,
    DEFAULT_MAIN_FLOOR_TEMP_SENSOR,
    DEFAULT_SIDNEY_FAN,
    DEFAULT_UPSTAIRS_TEMP_SENSOR,
    DOMAIN,
)

if TYPE_CHECKING:
    from .outdoor_reset import OutdoorResetController

_LOGGER = logging.getLogger(__name__)

COOL_CHECK_INTERVAL = timedelta(minutes=15)
MANUAL_FAN_PAUSE_HOURS = 48

DEFAULT_PRECOOL_ENABLED = True
DEFAULT_UPSTAIRS_BIAS_ENABLED = True
DEFAULT_PRECOOL_THRESHOLD = 78.0  # forecast afternoon high (°F)
DEFAULT_PRECOOL_TARGET = 74.0
DEFAULT_PRECOOL_START_HOUR = 10
DEFAULT_PRECOOL_END_HOUR = 14
DEFAULT_STRATIFICATION_THRESHOLD = 2.0  # °F gap before setpoint trim
DEFAULT_MAX_COOL_ADJUSTMENT = 4.0
DEFAULT_MIN_COOL_SETPOINT = 70.0
DEFAULT_MAX_OUTDOOR_FOR_COOLING = 75.0  # no compressor cooling below this outdoor temp
DEFAULT_BEDROOM_FANS_ENABLED = True
DEFAULT_BEDROOM_FAN_SPEED = 66  # percentage (33/66/100 on these fans)
DEFAULT_SMART_FAN_CIRCULATION = True
DEFAULT_FURNACE_FAN_WATTS = 400.0
DEFAULT_BEDROOM_FAN_WATTS = 55.0
DEFAULT_COMPRESSOR_WATTS = 3500.0
DEFAULT_MIN_FAN_ROI = 1.0  # benefit $/hr must meet or exceed cost $/hr
DEFAULT_HEAT_DISTRIBUTION_GAIN = 0.05  # fraction of firing kW per °F stratification
DEFAULT_COMPRESSOR_DUTY_PER_DEG = 0.04  # duty reduction per °F above threshold
DEFAULT_TANKLESS_KW_WHEN_HEATING = 12.0


class CoolingControlParams:
    """Runtime configuration for upstairs-aware cooling."""

    def __init__(self) -> None:
        self.precool_enabled: bool = DEFAULT_PRECOOL_ENABLED
        self.upstairs_bias_enabled: bool = DEFAULT_UPSTAIRS_BIAS_ENABLED
        self.precool_threshold: float = DEFAULT_PRECOOL_THRESHOLD
        self.precool_target: float = DEFAULT_PRECOOL_TARGET
        self.precool_start_hour: int = DEFAULT_PRECOOL_START_HOUR
        self.precool_end_hour: int = DEFAULT_PRECOOL_END_HOUR
        self.stratification_threshold: float = DEFAULT_STRATIFICATION_THRESHOLD
        self.max_cool_adjustment: float = DEFAULT_MAX_COOL_ADJUSTMENT
        self.max_outdoor_for_cooling: float = DEFAULT_MAX_OUTDOOR_FOR_COOLING
        self.upstairs_sensor: str = DEFAULT_UPSTAIRS_TEMP_SENSOR
        self.main_floor_sensor: str = DEFAULT_MAIN_FLOOR_TEMP_SENSOR
        self.hunter_fan: str = DEFAULT_HUNTER_FAN
        self.sidney_fan: str = DEFAULT_SIDNEY_FAN
        self.bedroom_fans_enabled: bool = DEFAULT_BEDROOM_FANS_ENABLED
        self.bedroom_fan_speed: int = DEFAULT_BEDROOM_FAN_SPEED
        self.smart_fan_circulation_enabled: bool = DEFAULT_SMART_FAN_CIRCULATION
        self.furnace_fan_watts: float = DEFAULT_FURNACE_FAN_WATTS
        self.bedroom_fan_watts: float = DEFAULT_BEDROOM_FAN_WATTS
        self.compressor_watts: float = DEFAULT_COMPRESSOR_WATTS
        self.min_fan_roi: float = DEFAULT_MIN_FAN_ROI

    @property
    def upstairs_fan_entities(self) -> list[str]:
        """Configured upstairs bedroom fan entity IDs."""
        return [eid for eid in (self.hunter_fan, self.sidney_fan) if eid]


class CoolingControlMixin:
    """Mixin for OutdoorResetController — pre-cool and upstairs setpoint bias."""

    params: Any
    hass: HomeAssistant
    _unsub_cool_interval: Any
    _unsub_cool_sensors: Any
    _unsub_hvac_setpoint: Any
    _user_cool_setpoint: float | None
    _upstairs_bias_active: bool
    _precool_triggered_date: date | None
    _last_cool_adjustment: float
    _cooling_fans_active: set[str]
    _entry_id: str | None
    _cooling_paused_until: datetime | None
    _cooling_pause_reason: str | None
    _programmatic_fan_change: bool
    _unsub_cool_fans: Any
    _unsub_cooling_pause: Any
    _furnace_fan_circulation_active: bool
    _last_fan_roi: dict[str, Any]

    def _cooling_params(self) -> CoolingControlParams:
        return self.params.cooling_control

    async def _setup_cooling_control(self) -> None:
        """Register periodic checks and sensor listeners."""
        cc = self._cooling_params()
        self._user_cool_setpoint = None
        self._upstairs_bias_active = False
        self._precool_triggered_date = None
        self._last_cool_adjustment = 0.0
        self._cooling_fans_active = set()
        self._cooling_paused_until = None
        self._cooling_pause_reason = None
        self._programmatic_fan_change = False
        self._furnace_fan_circulation_active = False
        self._last_fan_roi = {}

        self._restore_cooling_pause_from_options()

        self._unsub_cool_interval = async_track_time_interval(
            self.hass, self._async_cooling_control_tick, COOL_CHECK_INTERVAL
        )

        sensor_entities = [
            eid for eid in (cc.upstairs_sensor, cc.main_floor_sensor) if eid
        ]
        if sensor_entities:
            self._unsub_cool_sensors = async_track_state_change_event(
                self.hass, sensor_entities, self._async_on_cool_sensor_change
            )

        hvac_entity = self.params.main_hvac_climate_entity_id
        if hvac_entity:
            self._unsub_hvac_setpoint = async_track_state_change_event(
                self.hass, [hvac_entity], self._async_on_hvac_state_change
            )
            self._capture_user_cool_setpoint()

        fan_entities = cc.upstairs_fan_entities
        if fan_entities:
            self._unsub_cool_fans = async_track_state_change_event(
                self.hass, fan_entities, self._async_on_bedroom_fan_change
            )

        await self._setup_hvac_orchestrator()
        await self._async_cooling_control_tick()

        @callback
        def _startup_resync(_now) -> None:
            self.hass.async_create_task(self._async_cooling_control_tick())
            self.hass.async_create_task(
                self._async_orchestrate_hvac_mode(force=True, trigger="startup_resync")
            )

        async_call_later(self.hass, 45, _startup_resync)

    def _unload_cooling_control(self) -> None:
        self._unload_hvac_orchestrator()
        for attr in (
            "_unsub_cool_interval",
            "_unsub_cool_sensors",
            "_unsub_hvac_setpoint",
            "_unsub_cool_fans",
            "_unsub_cooling_pause",
        ):
            unsub = getattr(self, attr, None)
            if unsub:
                unsub()
                setattr(self, attr, None)

    @property
    def is_cooling_paused(self) -> bool:
        """True while cooling automations are suspended after manual fan use."""
        if self._cooling_paused_until is None:
            return False
        if dt_util.now() >= self._cooling_paused_until:
            return False
        return True

    def _restore_cooling_pause_from_options(self) -> None:
        if not self._entry_id:
            return
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None:
            return
        paused_raw = entry.options.get(CONF_COOLING_PAUSED_UNTIL)
        if not paused_raw:
            return
        try:
            until = datetime.fromisoformat(str(paused_raw))
            if until.tzinfo is None:
                until = dt_util.as_local(until)
        except (ValueError, TypeError):
            return
        if dt_util.now() >= until:
            self.hass.async_create_task(self._clear_cooling_pause())
            return
        self._cooling_paused_until = until
        self._cooling_pause_reason = entry.options.get(CONF_COOLING_PAUSE_REASON)
        self._schedule_cooling_pause_end()

    async def _persist_cooling_pause(self) -> None:
        if not self._entry_id:
            return
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None:
            return
        new_options = dict(entry.options)
        if self._cooling_paused_until is None:
            new_options.pop(CONF_COOLING_PAUSED_UNTIL, None)
            new_options.pop(CONF_COOLING_PAUSE_REASON, None)
        else:
            new_options[CONF_COOLING_PAUSED_UNTIL] = self._cooling_paused_until.isoformat()
            if self._cooling_pause_reason:
                new_options[CONF_COOLING_PAUSE_REASON] = self._cooling_pause_reason
        self.hass.data.setdefault(DOMAIN, {})[f"{self._entry_id}_skip_reload"] = True
        self.hass.config_entries.async_update_entry(entry, options=new_options)

    def _schedule_cooling_pause_end(self) -> None:
        if self._unsub_cooling_pause:
            self._unsub_cooling_pause()
            self._unsub_cooling_pause = None
        if self._cooling_paused_until is None:
            return

        @callback
        def _on_pause_expired(_now: datetime) -> None:
            self.hass.async_create_task(self._clear_cooling_pause())

        self._unsub_cooling_pause = async_track_point_in_time(
            self.hass, _on_pause_expired, self._cooling_paused_until
        )

    async def _clear_cooling_pause(self) -> None:
        if self._cooling_paused_until is None and not self.is_cooling_paused:
            return
        _LOGGER.info("Cooling automations resumed after manual-fan pause")
        self._cooling_paused_until = None
        self._cooling_pause_reason = None
        if self._unsub_cooling_pause:
            self._unsub_cooling_pause()
            self._unsub_cooling_pause = None
        await self._persist_cooling_pause()

    async def _pause_cooling_for_manual_fan(self, entity_id: str) -> None:
        until = dt_util.now() + timedelta(hours=MANUAL_FAN_PAUSE_HOURS)
        self._cooling_paused_until = until
        friendly = self.hass.states.get(entity_id)
        name = (
            friendly.attributes.get("friendly_name", entity_id)
            if friendly
            else entity_id
        )
        self._cooling_pause_reason = f"Manual fan on: {name}"
        _LOGGER.info(
            "Cooling automations paused until %s (%s)",
            until.strftime("%Y-%m-%d %H:%M"),
            self._cooling_pause_reason,
        )
        await self._release_cooling_automation_state()
        await self._persist_cooling_pause()
        self._schedule_cooling_pause_end()

    async def _release_cooling_automation_state(self) -> None:
        """Stop active cooling assists without touching manually controlled fans."""
        if self._upstairs_bias_active and self._user_cool_setpoint is not None:
            await self._async_set_hvac_cool(
                self._user_cool_setpoint,
                "manual fan override — restoring setpoint",
            )
        self._upstairs_bias_active = False
        self._last_cool_adjustment = 0.0
        await self._async_turn_off_cooling_fans()

    @callback
    def _async_on_bedroom_fan_change(self, event) -> None:
        self.hass.async_create_task(self._async_handle_bedroom_fan_change(event))

    async def _async_handle_bedroom_fan_change(self, event) -> None:
        if self.is_cooling_paused:
            return
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        entity_id = event.data.get("entity_id")
        if new_state is None or not entity_id:
            return
        if new_state.state != "on":
            return
        if old_state is None or old_state.state in ("unavailable", "unknown", "on"):
            return
        if entity_id in self._cooling_fans_active or self._programmatic_fan_change:
            return
        await self._pause_cooling_for_manual_fan(entity_id)

    @callback
    def _async_on_cool_sensor_change(self, _event) -> None:
        self.hass.async_create_task(self._async_cooling_control_tick())

    @callback
    def _async_on_hvac_state_change(self, event) -> None:
        self.hass.async_create_task(self._async_handle_hvac_state_change(event))

    async def _async_handle_hvac_state_change(self, event) -> None:
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if new_state is None:
            return

        if new_state.state != "cool":
            self._user_cool_setpoint = None
            self._upstairs_bias_active = False
            self._last_cool_adjustment = 0.0
            await self._async_turn_off_cooling_fans()
            await self._async_stop_furnace_circulation_fan()
            return

        old_temp = None
        if old_state:
            old_temp = old_state.attributes.get("temperature")
        new_temp = new_state.attributes.get("temperature")
        if new_temp is not None and new_temp != old_temp:
            try:
                new_val = float(new_temp)
                if (
                    self._upstairs_bias_active
                    and self._user_cool_setpoint is not None
                    and new_val <= self._user_cool_setpoint
                ):
                    # Programmatic bias trim — keep user baseline
                    pass
                else:
                    self._user_cool_setpoint = new_val
                    self._upstairs_bias_active = False
                    self._last_cool_adjustment = 0.0
                    _LOGGER.debug(
                        "Captured user cool setpoint: %.1f°F", self._user_cool_setpoint
                    )
            except (TypeError, ValueError):
                pass
        elif self._user_cool_setpoint is None:
            self._capture_user_cool_setpoint()

        await self._apply_upstairs_cool_bias()
        await self._sync_smart_fan_circulation()

    async def _async_cooling_control_tick(self, _now=None) -> None:
        if not self.enabled:
            return
        if self._cooling_paused_until and not self.is_cooling_paused:
            await self._clear_cooling_pause()
        if self.is_cooling_paused:
            return
        await self._async_enforce_outdoor_cooling_limit()
        await self._check_precool()
        await self._apply_upstairs_cool_bias()
        await self._sync_smart_fan_circulation()

    async def _on_hvac_entered_cool_mode(self) -> None:
        """Called when HVAC switches to cool — start upstairs monitoring."""
        self._capture_user_cool_setpoint()
        await self._apply_upstairs_cool_bias()
        await self._sync_smart_fan_circulation()

    async def _on_hvac_left_cool_mode(self) -> None:
        """Reset upstairs bias state when leaving cool mode."""
        self._user_cool_setpoint = None
        self._upstairs_bias_active = False
        self._last_cool_adjustment = 0.0
        await self._async_turn_off_cooling_fans()
        await self._async_stop_furnace_circulation_fan()

    def _capture_user_cool_setpoint(self) -> None:
        entity_id = self.params.main_hvac_climate_entity_id
        if not entity_id:
            return
        state = self.hass.states.get(entity_id)
        if state is None or state.state != "cool":
            return
        temp = state.attributes.get("temperature")
        if temp is None:
            return
        try:
            self._user_cool_setpoint = float(temp)
        except (TypeError, ValueError):
            pass

    def _available_bedroom_fan_count(self) -> int:
        cc = self._cooling_params()
        count = 0
        for entity_id in cc.upstairs_fan_entities:
            state = self.hass.states.get(entity_id)
            if state and state.state not in ("unavailable", "unknown"):
                count += 1
        return count

    def _estimated_fan_watts(self, include_furnace: bool, include_bedrooms: bool) -> float:
        cc = self._cooling_params()
        watts = 0.0
        if include_furnace:
            watts += cc.furnace_fan_watts
        if include_bedrooms:
            watts += cc.bedroom_fan_watts * self._available_bedroom_fan_count()
        return watts

    def _fan_hourly_cost_usd(self, include_furnace: bool, include_bedrooms: bool) -> float:
        kwh = self._estimated_fan_watts(include_furnace, include_bedrooms) / 1000.0
        return kwh * self.params.electricity_cost_per_kwh

    def _tankless_is_heating(self) -> bool:
        state = self.hass.states.get("binary_sensor.main_water_heater_heating")
        return state is not None and state.state == "on"

    def _evaluate_fan_circulation_roi(
        self, gap: float | None
    ) -> tuple[bool, str, float, float]:
        """Return (worth_it, scenario, cost_usd_per_hr, benefit_usd_per_hr)."""
        cc = self._cooling_params()
        if gap is None:
            return False, "no_data", 0.0, 0.0

        threshold = cc.stratification_threshold
        excess = max(0.0, abs(gap) - threshold)
        if excess <= 0:
            return False, "balanced", 0.0, 0.0

        rate = self.params.electricity_cost_per_kwh

        # Floor heat: push warmth upstairs (gap negative = upstairs colder)
        if gap < -threshold and self._any_zone_heating() and self._tankless_is_heating():
            include_furnace = True
            include_bedrooms = cc.bedroom_fans_enabled
            cost = self._fan_hourly_cost_usd(include_furnace, include_bedrooms)
            benefit_kwh = (
                DEFAULT_TANKLESS_KW_WHEN_HEATING
                * DEFAULT_HEAT_DISTRIBUTION_GAIN
                * excess
            )
            benefit = benefit_kwh * rate
            worth = benefit >= cost * cc.min_fan_roi
            return worth, "heating_distribute", round(cost, 4), round(benefit, 4)

        hvac_entity = self.params.main_hvac_climate_entity_id
        hvac = self.hass.states.get(hvac_entity) if hvac_entity else None
        if hvac is None:
            return False, "no_hvac", 0.0, 0.0

        include_furnace = True
        include_bedrooms = cc.bedroom_fans_enabled
        cost = self._fan_hourly_cost_usd(include_furnace, include_bedrooms)

        # Compressor running: fans may reduce duty cycle
        if hvac.state == "cool" and hvac.attributes.get("hvac_action") == "cooling":
            duty = min(0.30, excess * DEFAULT_COMPRESSOR_DUTY_PER_DEG)
            benefit_kwh = (cc.compressor_watts / 1000.0) * duty
            benefit = benefit_kwh * rate
            worth = benefit >= cost * cc.min_fan_roi
            return worth, "cooling_compressor", round(cost, 4), round(benefit, 4)

        # Fan-only mixing (no compressor): low benefit unless very hot outside
        if gap > threshold:
            outdoor = self.outdoor_temp
            benefit_kwh = 0.0
            if outdoor is not None and outdoor >= 78 and excess >= 3:
                benefit_kwh = 0.25
            benefit = benefit_kwh * rate
            worth = benefit >= cost * cc.min_fan_roi
            return worth, "fan_only_mix", round(cost, 4), round(benefit, 4)

        return False, "no_scenario", round(cost, 4), 0.0

    def _read_temp_entity(self, entity_id: str) -> float | None:
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unavailable", "unknown"):
            return None
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return None

    @property
    def upstairs_temp(self) -> float | None:
        return self._read_temp_entity(self._cooling_params().upstairs_sensor)

    @property
    def main_floor_temp(self) -> float | None:
        return self._read_temp_entity(self._cooling_params().main_floor_sensor)

    @property
    def stratification_gap(self) -> float | None:
        upstairs = self.upstairs_temp
        main = self.main_floor_temp
        if upstairs is None or main is None:
            return None
        return round(upstairs - main, 1)

    @property
    def precool_active_today(self) -> bool:
        today = date.today()
        return self._precool_triggered_date == today

    def _outdoor_allows_cooling(self) -> bool:
        """False when outdoor air is below the cooling cutoff."""
        outdoor = self.outdoor_temp
        if outdoor is None:
            return True
        return outdoor >= self._cooling_params().max_outdoor_for_cooling

    async def _async_enforce_outdoor_cooling_limit(self) -> None:
        """Turn off integration-driven cooling when outdoor is too mild."""
        if self._outdoor_allows_cooling():
            return
        outdoor = self.outdoor_temp
        limit = self._cooling_params().max_outdoor_for_cooling
        hvac_entity = self.params.main_hvac_climate_entity_id
        if not hvac_entity:
            return
        state = self.hass.states.get(hvac_entity)
        if state is None or state.state != "cool":
            self._upstairs_bias_active = False
            self._last_cool_adjustment = 0.0
            return
        if not (self._upstairs_bias_active or self.precool_active_today):
            return
        _LOGGER.info(
            "Outdoor %.1f°F < %.1f°F limit — turning off integration cooling",
            outdoor or 0,
            limit,
        )
        await self.hass.services.async_call(
            "climate",
            "set_hvac_mode",
            {"entity_id": hvac_entity, "hvac_mode": "off"},
            blocking=True,
        )
        self._user_cool_setpoint = None
        self._upstairs_bias_active = False
        self._last_cool_adjustment = 0.0
        self._precool_triggered_date = None
        await self._async_turn_off_cooling_fans()
        await self._async_stop_furnace_circulation_fan()

    async def _check_precool(self) -> None:
        """Start cool mode when actual outdoor temp supports pre-cool."""
        if self.is_cooling_paused:
            return
        if not self._outdoor_allows_cooling():
            return
        cc = self._cooling_params()
        if not cc.precool_enabled:
            return

        hvac_entity = self.params.main_hvac_climate_entity_id
        if not hvac_entity or self.hass.states.get(hvac_entity) is None:
            return

        state = self.hass.states.get(hvac_entity)
        if state and state.state == "cool":
            return

        now = datetime.now()
        if not (cc.precool_start_hour <= now.hour < cc.precool_end_hour):
            return

        if self.precool_active_today:
            return

        outdoor = self.outdoor_temp
        if outdoor is None:
            return

        if self.params.orchestrator.enabled:
            if outdoor < cc.max_outdoor_for_cooling:
                return
            plan = getattr(self, "_daily_plan", None)
            plan_note = (
                f" (plan: peak {plan.forecast_high:.0f}°F)"
                if plan and plan.forecast_high
                else ""
            )
        else:
            plan_note = ""
            if outdoor < cc.precool_threshold - 8:
                return
            afternoon_high = await self._forecast_afternoon_high()
            if afternoon_high is None or afternoon_high < cc.precool_threshold:
                return

        target = cc.precool_target
        if not self.params.orchestrator.enabled:
            afternoon_high = await self._forecast_afternoon_high()
            if afternoon_high and afternoon_high >= cc.precool_threshold + 5:
                target = max(DEFAULT_MIN_COOL_SETPOINT, cc.precool_target - 1)

        await self._async_set_hvac_cool(
            target,
            f"pre-cool: actual outdoor {outdoor:.0f}°F{plan_note}",
        )
        self._precool_triggered_date = date.today()
        self._user_cool_setpoint = target

    async def _forecast_afternoon_high(self) -> float | None:
        """Return the highest forecast temp from now through end of today."""
        forecast_entities: list[str] = []
        if self.params.forecast_entity_id:
            forecast_entities.append(self.params.forecast_entity_id)
        for state in self.hass.states.async_all("weather"):
            if state.entity_id not in forecast_entities:
                forecast_entities.append(state.entity_id)

        now = datetime.now()
        end_of_day = now.replace(hour=23, minute=59, second=59)
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
                if fc_time < now or fc_time > end_of_day:
                    continue
                if best is None or fc_temp_f > best:
                    best = fc_temp_f

        return best

    async def _fetch_hourly_forecast_list(self, entity_id: str) -> list[dict]:
        try:
            response = await self.hass.services.async_call(
                "weather",
                "get_forecasts",
                {"entity_id": entity_id, "type": "hourly"},
                blocking=True,
                return_response=True,
            )
            payload = response
            if isinstance(response, dict) and "response" in response:
                payload = response["response"]
            if payload and entity_id in payload:
                return payload[entity_id].get("forecast", []) or []
        except Exception as exc:
            _LOGGER.debug("Forecast service failed for %s: %s", entity_id, exc)

        state = self.hass.states.get(entity_id)
        if state and state.attributes.get("forecast"):
            return state.attributes["forecast"]
        return []

    async def _apply_upstairs_cool_bias(self) -> None:
        """Lower cool setpoint when upstairs runs warmer than main floor."""
        if self.is_cooling_paused:
            return
        if not self._outdoor_allows_cooling():
            return
        cc = self._cooling_params()
        if not cc.upstairs_bias_enabled:
            return

        hvac_entity = self.params.main_hvac_climate_entity_id
        if not hvac_entity:
            return
        state = self.hass.states.get(hvac_entity)
        if state is None or state.state != "cool":
            return

        gap = self.stratification_gap
        if gap is None:
            return

        if self._user_cool_setpoint is None:
            self._capture_user_cool_setpoint()
        baseline = self._user_cool_setpoint
        if baseline is None:
            temp = state.attributes.get("temperature")
            if temp is not None:
                try:
                    baseline = float(temp)
                except (TypeError, ValueError):
                    return
            else:
                return

        if gap <= cc.stratification_threshold:
            if self._upstairs_bias_active:
                await self._async_set_hvac_cool(
                    baseline, "upstairs caught up — restoring setpoint"
                )
                self._upstairs_bias_active = False
                self._last_cool_adjustment = 0.0
            return

        excess = gap - cc.stratification_threshold
        adjustment = min(cc.max_cool_adjustment, max(1.0, excess))
        target = max(DEFAULT_MIN_COOL_SETPOINT, round(baseline - adjustment, 0))

        reason = (
            f"upstairs bias: gap {gap:.1f}°F "
            f"(upstairs {self.upstairs_temp:.1f}, main {self.main_floor_temp:.1f})"
        )
        self._upstairs_bias_active = True
        self._last_cool_adjustment = adjustment

        state = self.hass.states.get(hvac_entity)
        current_temp = state.attributes.get("temperature") if state else None
        needs_temp = True
        if current_temp is not None:
            try:
                needs_temp = abs(float(current_temp) - target) >= 0.5
            except (TypeError, ValueError):
                pass
        if needs_temp:
            await self._async_set_hvac_cool(target, reason)

        if gap > cc.stratification_threshold + 1 and state:
            worth, _, _, _ = self._evaluate_fan_circulation_roi(gap)
            if worth:
                fan_mode = state.attributes.get("fan_mode")
                if fan_mode != "on":
                    await self.hass.services.async_call(
                        "climate",
                        "set_fan_mode",
                        {"entity_id": hvac_entity, "fan_mode": "on"},
                        blocking=True,
                    )

    async def _async_set_hvac_cool(self, target: float, reason: str) -> None:
        hvac_entity = self.params.main_hvac_climate_entity_id
        if not hvac_entity:
            return
        state = self.hass.states.get(hvac_entity)
        current_mode = state.state if state else None
        current_temp = state.attributes.get("temperature") if state else None

        needs_mode = current_mode != "cool"
        needs_temp = True
        if current_temp is not None:
            try:
                needs_temp = abs(float(current_temp) - target) >= 0.5
            except (TypeError, ValueError):
                pass

        if not needs_mode and not needs_temp:
            return

        if needs_mode and not self._outdoor_allows_cooling():
            outdoor = self.outdoor_temp
            limit = self._cooling_params().max_outdoor_for_cooling
            _LOGGER.info(
                "Cooling blocked: outdoor %.1f°F < %.1f°F limit (%s)",
                outdoor or 0,
                limit,
                reason,
            )
            return

        _LOGGER.info(
            "Cooling control: %s → %.0f°F (%s)", hvac_entity, target, reason
        )
        await self.hass.services.async_call(
            "climate",
            "set_temperature",
            {
                "entity_id": hvac_entity,
                "hvac_mode": "cool",
                "temperature": target,
            },
            blocking=True,
        )

    async def _sync_smart_fan_circulation(self) -> None:
        """Run fans only when estimated energy benefit exceeds cost."""
        if self.is_cooling_paused:
            await self._async_turn_off_cooling_fans()
            await self._async_stop_furnace_circulation_fan()
            return

        cc = self._cooling_params()
        if not cc.smart_fan_circulation_enabled:
            return

        gap = self.stratification_gap
        worth, scenario, cost_hr, benefit_hr = self._evaluate_fan_circulation_roi(gap)
        self._last_fan_roi = {
            "worth_it": worth,
            "scenario": scenario,
            "cost_usd_per_hr": cost_hr,
            "benefit_usd_per_hr": benefit_hr,
            "gap": gap,
        }

        if not worth:
            await self._async_turn_off_cooling_fans()
            await self._async_stop_furnace_circulation_fan()
            _LOGGER.debug(
                "Fan circulation skipped (%s): cost $%.3f/hr benefit $%.3f/hr gap=%s",
                scenario,
                cost_hr,
                benefit_hr,
                gap,
            )
            return

        _LOGGER.info(
            "Fan circulation ON (%s): cost $%.3f/hr benefit $%.3f/hr gap=%.1f°F",
            scenario,
            cost_hr,
            benefit_hr,
            gap or 0,
        )

        if scenario == "heating_distribute":
            await self._async_start_furnace_circulation_fan(
                f"ROI heat distribute (gap {gap:.1f}°F)",
            )
            if cc.bedroom_fans_enabled:
                await self._async_turn_on_cooling_fans(
                    f"ROI heat distribute (gap {gap:.1f}°F)",
                )
            return

        if scenario in ("cooling_compressor", "fan_only_mix"):
            if scenario == "fan_only_mix":
                await self._async_start_furnace_circulation_fan(
                    f"ROI fan mix (gap {gap:.1f}°F)",
                )
            elif cc.bedroom_fans_enabled or scenario == "cooling_compressor":
                hvac_entity = self.params.main_hvac_climate_entity_id
                if hvac_entity:
                    state = self.hass.states.get(hvac_entity)
                    if state and state.attributes.get("fan_mode") != "on":
                        await self.hass.services.async_call(
                            "climate",
                            "set_fan_mode",
                            {"entity_id": hvac_entity, "fan_mode": "on"},
                            blocking=True,
                        )
            if cc.bedroom_fans_enabled:
                await self._async_turn_on_cooling_fans(
                    f"ROI {scenario} (gap {gap:.1f}°F)",
                )

    async def _async_start_furnace_circulation_fan(self, reason: str) -> None:
        """Blower only — no burner or compressor."""
        hvac_entity = self.params.main_hvac_climate_entity_id
        if not hvac_entity:
            return
        state = self.hass.states.get(hvac_entity)
        if state is None:
            return
        if state.state not in ("off",):
            await self.hass.services.async_call(
                "climate",
                "set_hvac_mode",
                {"entity_id": hvac_entity, "hvac_mode": "off"},
                blocking=True,
            )
        if state.attributes.get("fan_mode") != "on":
            _LOGGER.info("Furnace circulation fan ON (%s)", reason)
            await self.hass.services.async_call(
                "climate",
                "set_fan_mode",
                {"entity_id": hvac_entity, "fan_mode": "on"},
                blocking=True,
            )
        self._furnace_fan_circulation_active = True

    async def _async_stop_furnace_circulation_fan(self) -> None:
        if not self._furnace_fan_circulation_active:
            return
        hvac_entity = self.params.main_hvac_climate_entity_id
        if not hvac_entity:
            self._furnace_fan_circulation_active = False
            return
        state = self.hass.states.get(hvac_entity)
        if state and state.attributes.get("fan_mode") == "on" and state.state == "off":
            await self.hass.services.async_call(
                "climate",
                "set_fan_mode",
                {"entity_id": hvac_entity, "fan_mode": "auto"},
                blocking=True,
            )
            _LOGGER.info("Furnace circulation fan OFF (ROI no longer positive)")
        self._furnace_fan_circulation_active = False

    async def _sync_upstairs_bedroom_fans(self) -> None:
        """Deprecated path — use _sync_smart_fan_circulation."""
        await self._sync_smart_fan_circulation()

    async def _async_turn_on_cooling_fans(self, reason: str) -> None:
        cc = self._cooling_params()
        speed = cc.bedroom_fan_speed
        for entity_id in cc.upstairs_fan_entities:
            if entity_id in self._cooling_fans_active:
                continue
            state = self.hass.states.get(entity_id)
            if state is None or state.state in ("unavailable", "unknown"):
                _LOGGER.debug("Skipping unavailable fan %s", entity_id)
                continue
            if state.state == "on":
                continue
            _LOGGER.info("Cooling assist: %s → on @ %d%% (%s)", entity_id, speed, reason)
            self._programmatic_fan_change = True
            self._cooling_fans_active.add(entity_id)
            try:
                await self.hass.services.async_call(
                    "fan",
                    "turn_on",
                    {"entity_id": entity_id, "percentage": speed},
                    blocking=True,
                )
            except Exception:
                self._cooling_fans_active.discard(entity_id)
                raise
            finally:
                self._programmatic_fan_change = False

    async def _async_turn_off_cooling_fans(self) -> None:
        if not self._cooling_fans_active:
            return
        for entity_id in list(self._cooling_fans_active):
            state = self.hass.states.get(entity_id)
            if state is None or state.state in ("unavailable", "unknown"):
                continue
            _LOGGER.info("Cooling assist: %s → off (stratification resolved)", entity_id)
            await self.hass.services.async_call(
                "fan",
                "turn_off",
                {"entity_id": entity_id},
                blocking=True,
            )
        self._cooling_fans_active.clear()


def get_cooling_control_number_entities(
    coordinator, controller: OutdoorResetController
) -> list[NumberEntity]:
    """Number entities for cooling control tuning."""
    cc = controller.params.cooling_control
    return [
        PrecoolThresholdNumberEntity(coordinator, controller, cc.precool_threshold),
        PrecoolTargetNumberEntity(coordinator, controller, cc.precool_target),
        StratificationThresholdNumberEntity(
            coordinator, controller, cc.stratification_threshold
        ),
        MaxCoolAdjustmentNumberEntity(coordinator, controller, cc.max_cool_adjustment),
        MaxOutdoorForCoolingNumberEntity(
            coordinator, controller, cc.max_outdoor_for_cooling
        ),
        BedroomFanSpeedNumberEntity(coordinator, controller, cc.bedroom_fan_speed),
    ]


def get_cooling_control_switch_entities(
    coordinator, controller: OutdoorResetController
) -> list[SwitchEntity]:
    return [
        PrecoolEnableSwitch(coordinator, controller),
        UpstairsBiasEnableSwitch(coordinator, controller),
        BedroomFansEnableSwitch(coordinator, controller),
        SmartFanCirculationSwitch(coordinator, controller),
    ]


def get_cooling_control_sensor_entities(
    coordinator, controller: OutdoorResetController
) -> list[SensorEntity]:
    return [CoolingControlStatusSensor(coordinator, controller)]


class PrecoolEnableSwitch(SwitchEntity):
    _attr_has_entity_name = True
    _attr_name = "Pre-Cool Enabled"
    _attr_icon = "mdi:snowflake-thermometer"

    def __init__(self, coordinator, controller: OutdoorResetController) -> None:
        self._coordinator = coordinator
        self._controller = controller
        self._attr_unique_id = "sensorlinx_precool_enabled"

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, "cooling_control")},
            "name": "SensorLinx Cooling Control",
            "manufacturer": "HBX Controls",
            "model": "Upstairs-Aware Cooling",
        }

    @property
    def is_on(self) -> bool:
        return self._controller.params.cooling_control.precool_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._controller.params.cooling_control.precool_enabled = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._controller.params.cooling_control.precool_enabled = False
        self.async_write_ha_state()


class UpstairsBiasEnableSwitch(SwitchEntity):
    _attr_has_entity_name = True
    _attr_name = "Upstairs Bias Enabled"
    _attr_icon = "mdi:home-floor-2"

    def __init__(self, coordinator, controller: OutdoorResetController) -> None:
        self._coordinator = coordinator
        self._controller = controller
        self._attr_unique_id = "sensorlinx_upstairs_bias_enabled"

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, "cooling_control")},
            "name": "SensorLinx Cooling Control",
            "manufacturer": "HBX Controls",
            "model": "Upstairs-Aware Cooling",
        }

    @property
    def is_on(self) -> bool:
        return self._controller.params.cooling_control.upstairs_bias_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._controller.params.cooling_control.upstairs_bias_enabled = True
        self.async_write_ha_state()
        await self._controller._apply_upstairs_cool_bias()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._controller.params.cooling_control.upstairs_bias_enabled = False
        self.async_write_ha_state()
        if self._controller._upstairs_bias_active:
            baseline = self._controller._user_cool_setpoint
            if baseline is not None:
                await self._controller._async_set_hvac_cool(
                    baseline, "upstairs bias disabled"
                )
            self._controller._upstairs_bias_active = False
            self._controller._last_cool_adjustment = 0.0


class _CoolingNumberBase(RestoreNumber):
    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self,
        coordinator,
        controller: OutdoorResetController,
        name: str,
        unique_id: str,
        default: float,
        min_val: float,
        max_val: float,
        step: float,
        icon: str,
    ) -> None:
        self._coordinator = coordinator
        self._controller = controller
        self._attr_name = name
        self._attr_unique_id = unique_id
        self._default = default
        self._attr_native_min_value = min_val
        self._attr_native_max_value = max_val
        self._attr_native_step = step
        self._attr_icon = icon
        self._attr_native_value = default

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, "cooling_control")},
            "name": "SensorLinx Cooling Control",
            "manufacturer": "HBX Controls",
            "model": "Upstairs-Aware Cooling",
        }


class PrecoolThresholdNumberEntity(_CoolingNumberBase):
    def __init__(self, coordinator, controller: OutdoorResetController, default: float) -> None:
        super().__init__(
            coordinator,
            controller,
            "Pre-Cool: Forecast High Threshold",
            "sensorlinx_precool_threshold",
            default,
            72,
            95,
            1,
            "mdi:weather-sunny-alert",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last and last.native_value is not None:
            self._attr_native_value = last.native_value
        self._controller.params.cooling_control.precool_threshold = float(
            self._attr_native_value
        )

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self._controller.params.cooling_control.precool_threshold = float(value)
        self.async_write_ha_state()


class PrecoolTargetNumberEntity(_CoolingNumberBase):
    def __init__(self, coordinator, controller: OutdoorResetController, default: float) -> None:
        super().__init__(
            coordinator,
            controller,
            "Pre-Cool: Target Setpoint",
            "sensorlinx_precool_target",
            default,
            68,
            78,
            1,
            "mdi:thermometer-low",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last and last.native_value is not None:
            self._attr_native_value = last.native_value
        self._controller.params.cooling_control.precool_target = float(
            self._attr_native_value
        )

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self._controller.params.cooling_control.precool_target = float(value)
        self.async_write_ha_state()


class StratificationThresholdNumberEntity(_CoolingNumberBase):
    def __init__(self, coordinator, controller: OutdoorResetController, default: float) -> None:
        super().__init__(
            coordinator,
            controller,
            "Upstairs Bias: Gap Threshold",
            "sensorlinx_stratification_threshold",
            default,
            0.5,
            6,
            0.5,
            "mdi:arrow-expand-vertical",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last and last.native_value is not None:
            self._attr_native_value = last.native_value
        self._controller.params.cooling_control.stratification_threshold = float(
            self._attr_native_value
        )

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self._controller.params.cooling_control.stratification_threshold = float(value)
        self.async_write_ha_state()
        await self._controller._apply_upstairs_cool_bias()


class MaxCoolAdjustmentNumberEntity(_CoolingNumberBase):
    def __init__(self, coordinator, controller: OutdoorResetController, default: float) -> None:
        super().__init__(
            coordinator,
            controller,
            "Upstairs Bias: Max Setpoint Trim",
            "sensorlinx_max_cool_adjustment",
            default,
            1,
            8,
            0.5,
            "mdi:thermometer-minus",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last and last.native_value is not None:
            self._attr_native_value = last.native_value
        self._controller.params.cooling_control.max_cool_adjustment = float(
            self._attr_native_value
        )

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self._controller.params.cooling_control.max_cool_adjustment = float(value)
        self.async_write_ha_state()
        await self._controller._apply_upstairs_cool_bias()


class MaxOutdoorForCoolingNumberEntity(_CoolingNumberBase):
    def __init__(self, coordinator, controller: OutdoorResetController, default: float) -> None:
        super().__init__(
            coordinator,
            controller,
            "Cooling: Max Outdoor Temp",
            "sensorlinx_max_outdoor_for_cooling",
            default,
            65,
            90,
            1,
            "mdi:thermometer-chevron-down",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last and last.native_value is not None:
            self._attr_native_value = last.native_value
        self._controller.params.cooling_control.max_outdoor_for_cooling = float(
            self._attr_native_value
        )

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self._controller.params.cooling_control.max_outdoor_for_cooling = float(value)
        self.async_write_ha_state()
        await self._controller._async_enforce_outdoor_cooling_limit()


class BedroomFanSpeedNumberEntity(RestoreNumber):
    _attr_has_entity_name = True
    _attr_name = "Bedroom Fans: Speed"
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:fan-speed-2"
    _attr_native_min_value = 33
    _attr_native_max_value = 100
    _attr_native_step = 33
    _attr_native_unit_of_measurement = "%"

    def __init__(self, coordinator, controller: OutdoorResetController, default: float) -> None:
        self._coordinator = coordinator
        self._controller = controller
        self._attr_unique_id = "sensorlinx_bedroom_fan_speed"
        self._attr_native_value = default

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, "cooling_control")},
            "name": "SensorLinx Cooling Control",
            "manufacturer": "HBX Controls",
            "model": "Upstairs-Aware Cooling",
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last and last.native_value is not None:
            self._attr_native_value = int(last.native_value)
        self._controller.params.cooling_control.bedroom_fan_speed = int(
            self._attr_native_value
        )

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = int(value)
        self._controller.params.cooling_control.bedroom_fan_speed = int(value)
        self.async_write_ha_state()


class BedroomFansEnableSwitch(SwitchEntity):
    _attr_has_entity_name = True
    _attr_name = "Bedroom Fans Assist"
    _attr_icon = "mdi:fan"

    def __init__(self, coordinator, controller: OutdoorResetController) -> None:
        self._coordinator = coordinator
        self._controller = controller
        self._attr_unique_id = "sensorlinx_bedroom_fans_enabled"

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, "cooling_control")},
            "name": "SensorLinx Cooling Control",
            "manufacturer": "HBX Controls",
            "model": "Upstairs-Aware Cooling",
        }

    @property
    def is_on(self) -> bool:
        return self._controller.params.cooling_control.bedroom_fans_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._controller.params.cooling_control.bedroom_fans_enabled = True
        self.async_write_ha_state()
        await self._controller._sync_smart_fan_circulation()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._controller.params.cooling_control.bedroom_fans_enabled = False
        self.async_write_ha_state()
        await self._controller._async_turn_off_cooling_fans()


class SmartFanCirculationSwitch(SwitchEntity):
    _attr_has_entity_name = True
    _attr_name = "Smart Fan Circulation (Energy ROI)"
    _attr_icon = "mdi:fan-auto"

    def __init__(self, coordinator, controller: OutdoorResetController) -> None:
        self._coordinator = coordinator
        self._controller = controller
        self._attr_unique_id = "sensorlinx_smart_fan_circulation"

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, "cooling_control")},
            "name": "SensorLinx Cooling Control",
            "manufacturer": "HBX Controls",
            "model": "Upstairs-Aware Cooling",
        }

    @property
    def is_on(self) -> bool:
        return self._controller.params.cooling_control.smart_fan_circulation_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._controller.params.cooling_control.smart_fan_circulation_enabled = True
        self.async_write_ha_state()
        await self._controller._sync_smart_fan_circulation()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._controller.params.cooling_control.smart_fan_circulation_enabled = False
        self.async_write_ha_state()
        await self._controller._async_turn_off_cooling_fans()
        await self._controller._async_stop_furnace_circulation_fan()


class CoolingControlStatusSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Cooling Control Status"
    _attr_icon = "mdi:home-thermometer"

    def __init__(self, coordinator, controller: OutdoorResetController) -> None:
        self._coordinator = coordinator
        self._controller = controller
        self._attr_unique_id = "sensorlinx_cooling_control_status"

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, "cooling_control")},
            "name": "SensorLinx Cooling Control",
            "manufacturer": "HBX Controls",
            "model": "Upstairs-Aware Cooling",
        }

    @property
    def native_value(self) -> str:
        if self._controller.is_cooling_paused:
            return "paused"
        cc = self._controller.params.cooling_control
        if (
            not cc.precool_enabled
            and not cc.upstairs_bias_enabled
            and not cc.bedroom_fans_enabled
        ):
            return "disabled"
        if self._controller._cooling_fans_active:
            return "bedroom_fans"
        if self._controller._furnace_fan_circulation_active:
            return "furnace_fan"
        roi = self._controller._last_fan_roi
        if roi.get("worth_it") is False and roi.get("scenario") not in (
            None,
            "balanced",
            "no_data",
        ):
            return "fan_skipped_roi"
        if self._controller._upstairs_bias_active:
            return "upstairs_bias"
        if self._controller.precool_active_today:
            return "precool_today"
        hvac = self._controller.params.main_hvac_climate_entity_id
        state = self._controller.hass.states.get(hvac) if hvac else None
        if state and state.state == "cool":
            return "cooling"
        return "standby"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        cc = self._controller.params.cooling_control
        return {
            "cooling_paused": self._controller.is_cooling_paused,
            "cooling_paused_until": (
                self._controller._cooling_paused_until.isoformat()
                if self._controller._cooling_paused_until
                else None
            ),
            "cooling_pause_reason": self._controller._cooling_pause_reason,
            "precool_enabled": cc.precool_enabled,
            "upstairs_bias_enabled": cc.upstairs_bias_enabled,
            "bedroom_fans_enabled": cc.bedroom_fans_enabled,
            "bedroom_fan_speed": cc.bedroom_fan_speed,
            "bedroom_fans_active": sorted(self._controller._cooling_fans_active),
            "hunter_fan": cc.hunter_fan,
            "sidney_fan": cc.sidney_fan,
            "upstairs_temp": self._controller.upstairs_temp,
            "main_floor_temp": self._controller.main_floor_temp,
            "stratification_gap": self._controller.stratification_gap,
            "user_cool_setpoint": self._controller._user_cool_setpoint,
            "upstairs_bias_active": self._controller._upstairs_bias_active,
            "last_cool_adjustment": self._controller._last_cool_adjustment,
            "precool_triggered_today": self._controller.precool_active_today,
            "outdoor_temp": self._controller.outdoor_temp,
            "max_outdoor_for_cooling": cc.max_outdoor_for_cooling,
            "outdoor_allows_cooling": self._controller._outdoor_allows_cooling(),
            "upstairs_sensor": cc.upstairs_sensor,
            "main_floor_sensor": cc.main_floor_sensor,
            "smart_fan_circulation_enabled": cc.smart_fan_circulation_enabled,
            "fan_circulation_worth_it": self._controller._last_fan_roi.get("worth_it"),
            "fan_circulation_scenario": self._controller._last_fan_roi.get("scenario"),
            "fan_cost_usd_per_hr": self._controller._last_fan_roi.get("cost_usd_per_hr"),
            "fan_benefit_usd_per_hr": self._controller._last_fan_roi.get(
                "benefit_usd_per_hr"
            ),
            "furnace_fan_circulation_active": (
                self._controller._furnace_fan_circulation_active
            ),
        }
