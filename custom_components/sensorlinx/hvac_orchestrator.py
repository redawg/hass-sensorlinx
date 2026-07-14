"""Forecast daily plan with actual-temp execution and hourly adjustments."""

from __future__ import annotations

import logging
from dataclasses import dataclass
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
PLAN_ACTUAL_DIVERGENCE = 6.0  # °F — cancel hot-day plan if actual peak lags forecast

DEFAULT_ORCHESTRATOR_ENABLED = True
DEFAULT_ORCHESTRATOR_HEAT_SETPOINT = 72.0
DEFAULT_ORCHESTRATOR_COOL_SETPOINT = 74.0
DEFAULT_EVENING_COOL_CUTOFF_HOUR = 20
DEFAULT_PRECOOL_LEAD_MINUTES = 60
DEFAULT_COOL_OFF_OUTDOOR_MARGIN = 3.0
DEFAULT_HEAT_ON_OUTDOOR_MARGIN = 2.0
DEFAULT_INDOOR_COOL_MARGIN = 1.0
DEFAULT_FORECAST_HIGH_MARGIN = 2.0
DEFAULT_FORECAST_LOW_HEAT_MARGIN = 5.0
DEFAULT_ZONE_THERMAL_LAG = 20.0
MIN_VALID_HEAT_SETPOINT = 60.0


def _fmt_hour(dt: datetime) -> str:
    return dt.strftime("%I %p").lstrip("0")


def _fmt_hour_min(dt: datetime) -> str:
    return dt.strftime("%I:%M %p").lstrip("0")


@dataclass
class DailyHvacPlan:
    """Morning outlook from forecast; execution adjusted by actual temps."""

    plan_date: date
    forecast_high: float | None = None
    forecast_low: float | None = None
    forecast_high_at: datetime | None = None
    hot_day_planned: bool = False
    cold_night_planned: bool = False
    planned_precool_at: datetime | None = None
    planned_heat_at: datetime | None = None
    summary: str = ""
    plan_adjustment: str | None = None
    built_at: datetime | None = None


class HvacOrchestratorParams:
    """Runtime configuration for forecast plan + actual-temp execution."""

    def __init__(self) -> None:
        self.enabled: bool = DEFAULT_ORCHESTRATOR_ENABLED
        self.heat_setpoint: float = DEFAULT_ORCHESTRATOR_HEAT_SETPOINT
        self.cool_setpoint: float = DEFAULT_ORCHESTRATOR_COOL_SETPOINT
        self.evening_cool_cutoff_hour: int = DEFAULT_EVENING_COOL_CUTOFF_HOUR
        self.precool_lead_minutes: int = DEFAULT_PRECOOL_LEAD_MINUTES
        self.cool_off_outdoor_margin: float = DEFAULT_COOL_OFF_OUTDOOR_MARGIN
        self.heat_on_outdoor_margin: float = DEFAULT_HEAT_ON_OUTDOOR_MARGIN
        self.indoor_cool_margin: float = DEFAULT_INDOOR_COOL_MARGIN
        self.forecast_high_margin: float = DEFAULT_FORECAST_HIGH_MARGIN
        self.forecast_low_heat_margin: float = DEFAULT_FORECAST_LOW_HEAT_MARGIN
        self.zone_thermal_lag: dict[str, float] = {}


class HvacOrchestratorMixin:
    """Mixin — daily forecast plan with actual-temp overrides."""

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
    _daily_plan: DailyHvacPlan | None
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
        self._daily_plan = None
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
        await self._async_refresh_daily_plan(force=True)
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
        await self._async_refresh_daily_plan(force=True)
        await self._async_orchestrate_hvac_mode(force=True, trigger="hourly")

    async def _async_refresh_daily_plan(self, *, force: bool = False) -> None:
        """Build or refresh today's forecast plan (morning outlook + hourly update)."""
        today = date.today()
        now = datetime.now()
        if (
            not force
            and self._daily_plan is not None
            and self._daily_plan.plan_date == today
            and self._daily_plan.built_at is not None
            and now - self._daily_plan.built_at < ORCHESTRATOR_INTERVAL
        ):
            return

        plan = await self._async_build_daily_plan(today)
        if plan is not None:
            self._daily_plan = plan
            _LOGGER.info("Daily HVAC plan: %s", plan.summary)

    async def _async_build_daily_plan(self, plan_date: date) -> DailyHvacPlan | None:
        """Parse hourly forecast into a day plan for pre-cool / pre-heat timing."""
        hourly = await self._fetch_rest_of_day_hourly_forecast()
        if not hourly:
            return DailyHvacPlan(
                plan_date=plan_date,
                summary="Forecast unavailable — using actual temps only",
                built_at=datetime.now(),
            )

        cc = self._cooling_params()
        oc = self._orchestrator_params()
        cool_limit = cc.max_outdoor_for_cooling
        shutdown = self.params.shutdown
        hot_threshold = cc.precool_threshold - oc.forecast_high_margin
        cold_threshold = shutdown - oc.forecast_low_heat_margin

        forecast_high: float | None = None
        forecast_low: float | None = None
        high_at: datetime | None = None
        precool_at: datetime | None = None
        heat_at: datetime | None = None

        for fc_time, temp in hourly:
            if forecast_high is None or temp > forecast_high:
                forecast_high = temp
                high_at = fc_time
            if forecast_low is None or temp < forecast_low:
                forecast_low = temp
            if precool_at is None and temp >= cool_limit:
                lead = timedelta(minutes=oc.precool_lead_minutes)
                precool_at = max(datetime.now(), fc_time - lead)
            if (
                heat_at is None
                and fc_time.hour >= oc.evening_cool_cutoff_hour - 2
                and temp < shutdown
            ):
                heat_at = fc_time

        hot_day = forecast_high is not None and forecast_high >= hot_threshold
        cold_night = forecast_low is not None and forecast_low < cold_threshold

        summary_parts = []
        if hot_day and forecast_high is not None:
            peak = _fmt_hour(high_at) if high_at else "afternoon"
            precool_str = (
                _fmt_hour_min(precool_at) if precool_at else "when outdoor warms"
            )
            summary_parts.append(
                f"Warm day planned: peak {forecast_high:.0f}°F ~{peak}, "
                f"pre-cool armed ~{precool_str} (actual outdoor must reach {cool_limit:.0f}°F)"
            )
        else:
            summary_parts.append(
                f"Mild day planned: peak {forecast_high:.0f}°F"
                if forecast_high is not None
                else "Mild day planned"
            )

        if cold_night and forecast_low is not None:
            heat_str = _fmt_hour(heat_at) if heat_at else "evening"
            summary_parts.append(
                f"Cold night planned: low {forecast_low:.0f}°F, heat ~{heat_str}"
            )

        return DailyHvacPlan(
            plan_date=plan_date,
            forecast_high=forecast_high,
            forecast_low=forecast_low,
            forecast_high_at=high_at,
            hot_day_planned=hot_day,
            cold_night_planned=cold_night,
            planned_precool_at=precool_at,
            planned_heat_at=heat_at,
            summary="; ".join(summary_parts),
            built_at=datetime.now(),
        )

    async def _fetch_rest_of_day_hourly_forecast(self) -> list[tuple[datetime, float]]:
        """Return (local time, °F) pairs from now through end of today."""
        entities: list[str] = []
        if self.params.forecast_entity_id:
            entities.append(self.params.forecast_entity_id)
        for st in self.hass.states.async_all("weather"):
            if st.entity_id not in entities:
                entities.append(st.entity_id)

        now = datetime.now()
        end = now.replace(hour=23, minute=59, second=59)
        rows: list[tuple[datetime, float]] = []

        for entity_id in entities:
            forecast = await self._fetch_hourly_forecast_list(entity_id)
            if not forecast:
                continue
            for entry in forecast:
                fc_time_str = entry.get("datetime")
                fc_temp = entry.get("temperature")
                if fc_time_str is None or fc_temp is None:
                    continue
                try:
                    fc_time = datetime.fromisoformat(
                        fc_time_str.replace("Z", "+00:00")
                    ).astimezone().replace(tzinfo=None)
                    temp = float(fc_temp)
                except (ValueError, TypeError):
                    continue
                if now <= fc_time <= end:
                    rows.append((fc_time, temp))
            if rows:
                break

        rows.sort(key=lambda r: r[0])
        return rows

    def _adjust_plan_for_actuals(
        self, plan: DailyHvacPlan | None, outdoor: float | None, now: datetime
    ) -> None:
        """Revise the forecast plan when actual temps diverge."""
        if plan is None or outdoor is None:
            return

        cc = self._cooling_params()
        cool_limit = cc.max_outdoor_for_cooling

        if plan.hot_day_planned and now.hour >= 14:
            actual_peak = self._orchestrator_day_high or outdoor
            if (
                plan.forecast_high is not None
                and actual_peak < plan.forecast_high - PLAN_ACTUAL_DIVERGENCE
            ):
                plan.hot_day_planned = False
                plan.plan_adjustment = (
                    f"Actual peak {actual_peak:.0f}°F vs forecast "
                    f"{plan.forecast_high:.0f}°F — pre-cool cancelled"
                )
                _LOGGER.info("Plan adjusted: %s", plan.plan_adjustment)

        if (
            plan.hot_day_planned
            and outdoor >= cool_limit
            and plan.planned_precool_at
            and now < plan.planned_precool_at
        ):
            plan.plan_adjustment = (
                f"Ahead of plan: outdoor {outdoor:.0f}°F already at "
                f"{cool_limit:.0f}°F before {plan.planned_precool_at.strftime('%H:%M')}"
            )

        if plan.cold_night_planned and outdoor < self.params.shutdown:
            if not plan.plan_adjustment:
                plan.plan_adjustment = (
                    f"Actual outdoor {outdoor:.0f}°F confirms cold-night plan"
                )

    def _arm_plan_cooling_features(
        self, plan: DailyHvacPlan | None, now: datetime
    ) -> None:
        """Enable pre-cool / bias switches when the day plan expects warmth."""
        if plan is None or not plan.hot_day_planned:
            return
        cc = self._cooling_params()
        if cc.precool_start_hour <= now.hour < cc.precool_end_hour:
            cc.precool_enabled = True
            cc.upstairs_bias_enabled = True

    def _plan_standby_note(self, plan: DailyHvacPlan | None, outdoor: float | None) -> str:
        if plan is None:
            return "no forecast plan"
        note = plan.summary
        if plan.plan_adjustment:
            note += f" | {plan.plan_adjustment}"
        if (
            plan.hot_day_planned
            and plan.planned_precool_at
            and outdoor is not None
            and outdoor < self._cooling_params().max_outdoor_for_cooling
        ):
            note += (
                f" | waiting for actual outdoor "
                f"{self._cooling_params().max_outdoor_for_cooling:.0f}°F"
            )
        return note

    async def _async_orchestrate_hvac_mode(
        self, *, force: bool = False, trigger: str = "scheduled"
    ) -> None:
        """Execute from actual temps; day plan sets expectations and arms pre-cool."""
        if not self.enabled:
            return
        oc = self._orchestrator_params()
        if not oc.enabled:
            return
        if self.is_cooling_paused:
            return

        await self._async_refresh_daily_plan(force=(trigger == "hourly"))
        plan = self._daily_plan

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

        self._adjust_plan_for_actuals(plan, outdoor, now)
        self._arm_plan_cooling_features(plan, now)

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

        # --- Actual-temp execution (always wins over plan) ---
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

        # Plan-informed heat when forecast cold night and actual outdoor is dropping
        if (
            not want_cool
            and not want_heat
            and plan
            and plan.cold_night_planned
            and plan.planned_heat_at
            and now >= plan.planned_heat_at
            and outdoor is not None
            and outdoor < shutdown + 5
            and (trend is None or trend <= 0)
        ):
            want_heat = True
            want_off = False
            reason = (
                f"plan + actual: cold night (forecast low {plan.forecast_low:.0f}°F, "
                f"outdoor {outdoor:.0f}°F)"
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
            self._orchestrator_last_decision = "planned" if plan and plan.hot_day_planned else "standby"
            self._orchestrator_last_reason = (
                f"plan: {self._plan_standby_note(plan, outdoor)} | "
                f"actual outdoor={outdoor}, main={main}, "
                f"day_high={self._orchestrator_day_high}, trigger={trigger}"
            )

        self._orchestrator_last_run = now

    def daily_plan_dict(self) -> dict[str, Any]:
        """Serialize today's plan for status sensors."""
        plan = self._daily_plan
        if plan is None:
            return {"plan_date": str(date.today()), "summary": "Plan not built yet"}
        return {
            "plan_date": str(plan.plan_date),
            "forecast_high": plan.forecast_high,
            "forecast_low": plan.forecast_low,
            "forecast_high_at": (
                plan.forecast_high_at.isoformat() if plan.forecast_high_at else None
            ),
            "hot_day_planned": plan.hot_day_planned,
            "cold_night_planned": plan.cold_night_planned,
            "planned_precool_at": (
                plan.planned_precool_at.isoformat() if plan.planned_precool_at else None
            ),
            "planned_heat_at": (
                plan.planned_heat_at.isoformat() if plan.planned_heat_at else None
            ),
            "summary": plan.summary,
            "plan_adjustment": plan.plan_adjustment,
            "actual_day_high": self._orchestrator_day_high,
            "built_at": plan.built_at.isoformat() if plan.built_at else None,
        }

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
        status = {
            "enabled": oc.enabled,
            "active_mode": self._orchestrator_active_mode,
            "last_decision": self._orchestrator_last_decision,
            "last_reason": self._orchestrator_last_reason,
            "decision_source": "plan+actual",
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
        status.update(self.daily_plan_dict())
        return status


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
    for zone in controller.get_heating_zones():
        if zone.direct_floor_thermostat:
            default_lag = controller.zone_thermal_lag(zone.zone_key)
            entities.append(
                ZoneThermalLagNumberEntity(
                    coordinator, controller, zone.zone_key, zone.label, default_lag
                )
            )
    return entities


def get_orchestrator_sensor_entities(coordinator, controller: OutdoorResetController) -> list:
    return [
        HvacOrchestratorStatusSensor(coordinator, controller),
        DailyHvacPlanSensor(coordinator, controller),
    ]


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


class DailyHvacPlanSensor(SensorEntity):
    """Today's forecast-based HVAC plan (execution follows actual temps)."""

    _attr_has_entity_name = True
    _attr_name = "Daily HVAC Plan"
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator, controller: OutdoorResetController) -> None:
        self._coordinator = coordinator
        self._controller = controller
        self._attr_unique_id = "sensorlinx_daily_hvac_plan"

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, "hvac_orchestrator")},
            "name": "SensorLinx HVAC Orchestrator",
            "manufacturer": "HBX Controls",
            "model": "Plan + Actual Mode Control",
        }

    @property
    def native_value(self) -> str:
        plan = self._controller._daily_plan
        if plan is None:
            return "building"
        if plan.hot_day_planned and plan.cold_night_planned:
            return "warm_day_cold_night"
        if plan.hot_day_planned:
            return "warm_day"
        if plan.cold_night_planned:
            return "cold_night"
        return "mild"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._controller.daily_plan_dict()
