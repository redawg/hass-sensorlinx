"""Night air mix — continuous blower at a mid speed, 30 minutes each hour.

Moves radiant heat through the house overnight without short-cycling:
  - Target continuous-fan speed_3 (~medium airflow)
  - Minutes 0–29 of each hour: fan ON
  - Minutes 30–59: fan AUTO (off)
  - Active 10:00 PM – 6:00 AM local
  - At 6:00 AM: stop mix, restore HVAC orchestrator
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN
from homeassistant.helpers.event import async_track_time_interval

_LOGGER = logging.getLogger(__name__)

NIGHT_AIR_MIX_TICK = timedelta(minutes=1)
NIGHT_START_HOUR = 22  # 10 PM
NIGHT_END_HOUR = 6  # 6 AM
ON_MINUTES_PER_HOUR = 30
DEFAULT_TARGET_SPEED = "speed_3"
DEFAULT_ENABLED = True
ORCHESTRATOR_SWITCH = "switch.sensorlinx_hvac_orchestrator_hvac_mode_orchestration"


class NightAirMixParams:
    def __init__(self) -> None:
        self.enabled: bool = DEFAULT_ENABLED
        self.target_speed: str = DEFAULT_TARGET_SPEED
        self.start_hour: int = NIGHT_START_HOUR
        self.end_hour: int = NIGHT_END_HOUR
        self.on_minutes: int = ON_MINUTES_PER_HOUR


class NightAirMixMixin:
    """Mixin — timed night blower for radiant heat distribution."""

    hass: Any
    params: Any
    blower_fan_speed: Any

    def _night_air_mix_params(self) -> NightAirMixParams:
        if not hasattr(self.params, "night_air_mix"):
            self.params.night_air_mix = NightAirMixParams()
        return self.params.night_air_mix

    def _init_night_air_mix_state(self) -> None:
        self._night_air_mix_active = False
        self._night_air_mix_speed_ready = False
        self._night_air_mix_programming = False
        self._night_air_mix_force_on_until: datetime | None = None
        self._night_air_mix_last_fan: str | None = None
        self._night_air_mix_paused_orchestrator = False
        self._unsub_night_air_mix = None

    def night_air_mix_blocks_orchestrator(self) -> bool:
        return bool(getattr(self, "_night_air_mix_active", False))

    def _in_night_air_mix_window(self, now: datetime | None = None) -> bool:
        now = now or datetime.now()
        p = self._night_air_mix_params()
        hour = now.hour
        start, end = p.start_hour, p.end_hour
        if start == end:
            return False
        if start < end:
            return start <= hour < end
        # Overnight window (e.g. 22 → 6)
        return hour >= start or hour < end

    def _night_air_mix_should_fan_on(self, now: datetime | None = None) -> bool:
        now = now or datetime.now()
        force_until = getattr(self, "_night_air_mix_force_on_until", None)
        if force_until is not None and now < force_until:
            return True
        p = self._night_air_mix_params()
        return now.minute < p.on_minutes

    async def _setup_night_air_mix(self) -> None:
        self._unsub_night_air_mix = async_track_time_interval(
            self.hass, self._async_night_air_mix_tick, NIGHT_AIR_MIX_TICK
        )
        # Immediate evaluate so bedtime activation does not wait a full minute.
        self.hass.async_create_task(self._async_night_air_mix_tick())

    def _unload_night_air_mix(self) -> None:
        if self._unsub_night_air_mix:
            self._unsub_night_air_mix()
            self._unsub_night_air_mix = None

    async def _async_night_air_mix_tick(self, _now=None) -> None:
        p = self._night_air_mix_params()
        if not p.enabled:
            if self._night_air_mix_active:
                await self._async_night_air_mix_stop("disabled")
            return

        now = datetime.now()
        in_window = self._in_night_air_mix_window(now)

        if in_window and not self._night_air_mix_active:
            await self._async_night_air_mix_start(now)
            return

        if not in_window and self._night_air_mix_active:
            await self._async_night_air_mix_stop("morning resume")
            return

        if not self._night_air_mix_active:
            return

        await self._async_night_air_mix_apply_fan(now)

    async def _async_night_air_mix_start(self, now: datetime) -> None:
        """Enter night mix: pause orchestrator, program speed_3, start 30-min duty."""
        self._night_air_mix_active = True
        self._night_air_mix_speed_ready = False
        # First activation mid-hour: run fan for 30 minutes immediately.
        self._night_air_mix_force_on_until = now + timedelta(minutes=30)
        _LOGGER.info(
            "Night air mix START (speed %s, %s min/hour, until %02d:00)",
            self._night_air_mix_params().target_speed,
            self._night_air_mix_params().on_minutes,
            self._night_air_mix_params().end_hour,
        )

        orch = self.hass.states.get(ORCHESTRATOR_SWITCH)
        if orch is not None and orch.state == "on":
            await self.hass.services.async_call(
                "switch",
                "turn_off",
                {"entity_id": ORCHESTRATOR_SWITCH},
                blocking=True,
            )
            self._night_air_mix_paused_orchestrator = True
        else:
            # Already off (e.g. bedtime quiet) — still restore at 6 AM.
            self._night_air_mix_paused_orchestrator = True

        blower = getattr(self, "blower_fan_speed", None)
        if blower is not None:
            # Hold orchestrator path through morning even if switch re-enabled early.
            morning = now.replace(
                hour=self._night_air_mix_params().end_hour,
                minute=0,
                second=0,
                microsecond=0,
            )
            if morning <= now:
                morning += timedelta(days=1)
            blower.begin_hold(max(1.0, (morning - now).total_seconds() / 60.0))

        self.hass.async_create_task(self._async_night_air_mix_program_speed())
        await self._async_night_air_mix_apply_fan(now)

    async def _async_night_air_mix_program_speed(self) -> None:
        if self._night_air_mix_programming:
            return
        blower = getattr(self, "blower_fan_speed", None)
        if blower is None:
            self._night_air_mix_speed_ready = True
            return
        self._night_air_mix_programming = True
        try:
            target = self._night_air_mix_params().target_speed
            result = await blower.async_goto_speed(target, hold_minutes=60)
            _LOGGER.info("Night air mix speed program result: %s", result.get("status"))
            self._night_air_mix_speed_ready = True
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Night air mix failed to reach target speed")
            # Still circulate at whatever continuous speed is active.
            self._night_air_mix_speed_ready = True
        finally:
            self._night_air_mix_programming = False
            if self._night_air_mix_active:
                await self._async_night_air_mix_apply_fan(datetime.now())

    async def _async_night_air_mix_apply_fan(self, now: datetime) -> None:
        hvac = getattr(self.params, "main_hvac_climate_entity_id", None)
        if not hvac:
            return
        # Avoid fighting Auto↔On toggles while programming speed_3.
        if self._night_air_mix_programming:
            return

        want_on = self._night_air_mix_should_fan_on(now)
        fan_mode = "on" if want_on else "auto"
        state = self.hass.states.get(hvac)
        current_fan = state.attributes.get("fan_mode") if state else None
        if fan_mode == self._night_air_mix_last_fan and current_fan == fan_mode:
            return

        if state and state.state not in ("off", "unavailable", "unknown"):
            await self.hass.services.async_call(
                "climate",
                "set_hvac_mode",
                {"entity_id": hvac, "hvac_mode": "off"},
                blocking=True,
            )
        await self.hass.services.async_call(
            "climate",
            "set_fan_mode",
            {"entity_id": hvac, "fan_mode": fan_mode},
            blocking=True,
        )
        self._night_air_mix_last_fan = fan_mode
        _LOGGER.info(
            "Night air mix fan=%s (minute=%s, force_until=%s)",
            fan_mode,
            now.minute,
            self._night_air_mix_force_on_until,
        )

    async def _async_night_air_mix_stop(self, reason: str) -> None:
        _LOGGER.info("Night air mix STOP (%s)", reason)
        self._night_air_mix_active = False
        self._night_air_mix_force_on_until = None
        self._night_air_mix_last_fan = None
        self._night_air_mix_speed_ready = False

        hvac = getattr(self.params, "main_hvac_climate_entity_id", None)
        if hvac:
            await self.hass.services.async_call(
                "climate",
                "set_fan_mode",
                {"entity_id": hvac, "fan_mode": "auto"},
                blocking=True,
            )

        blower = getattr(self, "blower_fan_speed", None)
        if blower is not None:
            blower.clear_hold()

        if self._night_air_mix_paused_orchestrator:
            await self.hass.services.async_call(
                "switch",
                "turn_on",
                {"entity_id": ORCHESTRATOR_SWITCH},
                blocking=True,
            )
            self._night_air_mix_paused_orchestrator = False
            if hasattr(self, "_async_orchestrate_hvac_mode"):
                await self._async_orchestrate_hvac_mode(
                    force=True, trigger="night_air_mix_morning"
                )

    def night_air_mix_status(self) -> dict[str, Any]:
        p = self._night_air_mix_params()
        now = datetime.now()
        return {
            "enabled": p.enabled,
            "active": self._night_air_mix_active,
            "in_window": self._in_night_air_mix_window(now),
            "fan_should_be_on": self._night_air_mix_should_fan_on(now),
            "last_fan": self._night_air_mix_last_fan,
            "speed_ready": self._night_air_mix_speed_ready,
            "programming": self._night_air_mix_programming,
            "target_speed": p.target_speed,
            "on_minutes_per_hour": p.on_minutes,
            "start_hour": p.start_hour,
            "end_hour": p.end_hour,
            "force_on_until": (
                self._night_air_mix_force_on_until.isoformat()
                if self._night_air_mix_force_on_until
                else None
            ),
        }


class NightAirMixEnableSwitch(SwitchEntity):
    _attr_has_entity_name = True
    _attr_name = "Night Air Mix (30 min/hr @ speed 3)"
    _attr_icon = "mdi:fan-clock"

    def __init__(self, coordinator, controller) -> None:
        self._coordinator = coordinator
        self._controller = controller
        self._attr_unique_id = "sensorlinx_night_air_mix_enabled"

    @property
    def is_on(self) -> bool:
        return self._controller._night_air_mix_params().enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._controller._night_air_mix_params().enabled = True
        self.async_write_ha_state()
        await self._controller._async_night_air_mix_tick()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._controller._night_air_mix_params().enabled = False
        self.async_write_ha_state()
        await self._controller._async_night_air_mix_tick()
