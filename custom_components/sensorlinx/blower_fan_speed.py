"""Bryant / Carrier variable-speed continuous fan speed programming via thermostat.

Method (furnace control board):
  1. HVAC mode OFF, fan ON — continuous fan runs
  2. Within ~3s: Auto → On ×3 (three toggles) to step to the next pre-programmed speed
  3. Typically ~6 discrete continuous-fan speeds (speed_1 lowest … speed_6 highest)

True fan watts on the shared Emporia breaker:
  furnace_tankless_water (total)
  − hot_water_heater_current_consumption
  − radiant_floor_contoller_current_consumption
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant, ServiceCall

from .const import DEFAULT_MAIN_HVAC_CLIMATE, DOMAIN

_LOGGER = logging.getLogger(__name__)

CONF_BLOWER_FAN_POWER_SENSOR = "blower_fan_power_sensor"
CONF_BLOWER_SPEED_CALIBRATION = "blower_fan_speed_calibration"
DEFAULT_BLOWER_FAN_POWER_SENSOR = "sensor.furnace_tankless_water_power_minute_average"
# Same Emporia breaker also feeds these two variable loads (smart-plug meters).
# True fan ≈ Emporia total − hot-water plug − radiant-controller plug.
# Do NOT use sensor.main_water_heater_power_draw (tankless element kW) or
# sensor.radiant_floor_heater_* (separate Emporia / plant CT).
DEFAULT_HOT_WATER_ON_CIRCUIT_SENSOR = "sensor.hot_water_heater_current_consumption"  # W
DEFAULT_RADIANT_CONTROLLER_POWER_SENSOR = (
    "sensor.radiant_floor_contoller_current_consumption"  # W
)
DEFAULT_TOGGLE_GAP_S = 0.28
DEFAULT_FAN_ENGAGE_S = 4.0
# Emporia minute-average needs a long settle; short settles caused false "done".
DEFAULT_SETTLE_S = 60.0
DEFAULT_HOLD_MINUTES = 10
# Retry Auto->On x3 bursts until residual watts move (from cycle reliability tests).
DEFAULT_MAX_BURST_RETRIES = 7
DEFAULT_MIN_DELTA_W = 25.0
DEFAULT_WRAP_DROP_W = 80.0
MAX_STEPS = 6
# speed_1 = lowest continuous fan … speed_6 = highest
SPEED_LABELS = tuple(f"speed_{i}" for i in range(1, MAX_STEPS + 1))
SPEED_ALIASES = {
    "low": "speed_1",
    "medium": "speed_3",
    "med": "speed_3",
    "high": "speed_6",
    "highest": "speed_6",
    "lowest": "speed_1",
}


def normalize_speed_label(raw: Any) -> str:
    """Map 1–6, speed_N, or low/med/high aliases to speed_1..speed_6."""
    s = str(raw).strip().lower()
    if s in SPEED_ALIASES:
        return SPEED_ALIASES[s]
    if s.isdigit() and 1 <= int(s) <= MAX_STEPS:
        return f"speed_{int(s)}"
    if s in SPEED_LABELS:
        return s
    raise ValueError(
        f"speed must be 1–{MAX_STEPS}, speed_1..speed_{MAX_STEPS}, "
        f"or low/medium/high (got {raw!r})"
    )


class BlowerFanSpeedProgrammer:
    """Drive Ecobee/Bryant continuous-fan speed steps and log power."""

    def __init__(self, hass: HomeAssistant, controller: Any) -> None:
        self.hass = hass
        self._controller = controller
        self._last_result: dict[str, Any] = {}
        self.hold_until: datetime | None = None

    @property
    def is_hold_active(self) -> bool:
        return self.hold_until is not None and datetime.now() < self.hold_until

    def _climate_entity(self, call: ServiceCall | None = None) -> str:
        if call and call.data.get("climate_entity_id"):
            return str(call.data["climate_entity_id"])
        eid = getattr(self._controller.params, "main_hvac_climate_entity_id", None)
        return eid or DEFAULT_MAIN_HVAC_CLIMATE

    def _power_sensor(self, call: ServiceCall | None = None) -> str | None:
        if call and call.data.get("power_sensor"):
            return str(call.data["power_sensor"])
        options = {}
        entry = getattr(self._controller, "_config_entry", None)
        if entry is not None:
            options = dict(entry.options or {})
        return (
            options.get(CONF_BLOWER_FAN_POWER_SENSOR)
            or DEFAULT_BLOWER_FAN_POWER_SENSOR
        )

    def _read_power(self, entity_id: str | None) -> float | None:
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unavailable", "unknown"):
            return None
        try:
            return float(state.state)
        except (TypeError, ValueError):
            return None

    def _read_fan_residual(self, total_sensor: str | None = None) -> float | None:
        """Blower watts = Emporia total − hot-water plug − radiant-controller plug.

        Those two devices share the Furnace/Tankless/Radiant Emporia breaker.
        Always subtract their live W readings (they are already in watts).
        """
        total = self._read_power(total_sensor or self._power_sensor())
        if total is None:
            return None
        hot_water_w = self._read_power(DEFAULT_HOT_WATER_ON_CIRCUIT_SENSOR) or 0.0
        radiant_w = self._read_power(DEFAULT_RADIANT_CONTROLLER_POWER_SENSOR) or 0.0
        return total - hot_water_w - radiant_w

    async def _set_hvac_mode(self, entity_id: str, mode: str) -> None:
        await self.hass.services.async_call(
            "climate",
            "set_hvac_mode",
            {"entity_id": entity_id, "hvac_mode": mode},
            blocking=True,
        )

    async def _set_fan_mode(self, entity_id: str, mode: str) -> None:
        await self.hass.services.async_call(
            "climate",
            "set_fan_mode",
            {"entity_id": entity_id, "fan_mode": mode},
            blocking=True,
        )

    async def _one_speed_step(
        self, entity_id: str, toggle_gap: float
    ) -> None:
        """Auto->On x3 inside a short window = one board speed step attempt."""
        for _ in range(3):
            await self._set_fan_mode(entity_id, "auto")
            await asyncio.sleep(toggle_gap)
            await self._set_fan_mode(entity_id, "on")
            await asyncio.sleep(toggle_gap)
        # Always leave continuous fan ON after a burst.
        await self._set_fan_mode(entity_id, "on")

    def _delta_kind(
        self, baseline: float | None, watts: float | None, min_up: float, wrap_drop: float
    ) -> str | None:
        if baseline is None or watts is None:
            return None
        delta = watts - baseline
        if delta >= min_up:
            return "up"
        if delta <= -wrap_drop:
            return "wrap"
        return None

    async def _step_until_power_moves(
        self,
        climate: str,
        power_sensor: str | None,
        baseline: float | None,
        toggle_gap: float,
        settle: float,
        max_retries: int,
        min_up: float,
        wrap_drop: float,
        step_index: int,
        readings: list[dict[str, Any]],
    ) -> float | None:
        """Burst Auto->On x3, settle, retry until residual moves or retries exhausted."""
        watts = baseline
        for burst in range(1, max_retries + 1):
            await self._one_speed_step(climate, toggle_gap)
            await asyncio.sleep(settle)
            watts = self._read_fan_residual(power_sensor)
            kind = self._delta_kind(baseline, watts, min_up, wrap_drop)
            readings.append(
                {
                    "label": f"after_step_{step_index}_burst_{burst}",
                    "watts": watts,
                    "kind": kind,
                }
            )
            _LOGGER.info(
                "Blower step %s burst %s/%s residual=%s W (baseline=%s) kind=%s",
                step_index,
                burst,
                max_retries,
                watts if watts is not None else "n/a",
                baseline if baseline is not None else "n/a",
                kind or "no_change",
            )
            if kind:
                return watts
            # Keep fan on between retries.
            await self._set_fan_mode(climate, "on")
        return watts

    def begin_hold(self, minutes: float = DEFAULT_HOLD_MINUTES) -> None:
        self.hold_until = datetime.now() + timedelta(minutes=minutes)
        _LOGGER.info(
            "Blower fan-speed program hold until %s (orchestrator will not fight)",
            self.hold_until.isoformat(timespec="seconds"),
        )

    def clear_hold(self) -> None:
        self.hold_until = None

    async def async_step_speed(self, call: ServiceCall) -> dict[str, Any]:
        """Run N continuous-fan speed steps; verify via residual watts + retry bursts."""
        climate = self._climate_entity(call)
        power_sensor = self._power_sensor(call)
        steps = int(call.data.get("steps", 1))
        steps = max(1, min(steps, MAX_STEPS))
        toggle_gap = float(call.data.get("toggle_gap_seconds", DEFAULT_TOGGLE_GAP_S))
        engage = float(call.data.get("fan_engage_seconds", DEFAULT_FAN_ENGAGE_S))
        settle = float(call.data.get("settle_seconds", DEFAULT_SETTLE_S))
        hold_min = float(call.data.get("hold_minutes", DEFAULT_HOLD_MINUTES))
        restore = bool(call.data.get("restore_previous", False))
        max_retries = int(
            call.data.get("max_burst_retries", DEFAULT_MAX_BURST_RETRIES)
        )
        max_retries = max(1, min(max_retries, 12))
        min_up = float(call.data.get("min_delta_w", DEFAULT_MIN_DELTA_W))
        wrap_drop = float(call.data.get("wrap_drop_w", DEFAULT_WRAP_DROP_W))

        state = self.hass.states.get(climate)
        if state is None or state.state in ("unavailable", "unknown"):
            raise ValueError(f"Climate entity unavailable: {climate}")

        prev_mode = state.state
        prev_fan = state.attributes.get("fan_mode")

        self.begin_hold(hold_min)

        readings: list[dict[str, Any]] = []
        baseline = self._read_fan_residual(power_sensor)
        readings.append({"label": "before", "watts": baseline})

        _LOGGER.info(
            "Blower speed program: %s OFF + fan ON, then %s step(s) "
            "(settle=%.0fs, max_bursts=%s); power=%s",
            climate,
            steps,
            settle,
            max_retries,
            power_sensor or "not configured",
        )

        await self._set_hvac_mode(climate, "off")
        await self._set_fan_mode(climate, "on")
        await asyncio.sleep(engage)
        baseline = self._read_fan_residual(power_sensor)
        readings.append({"label": "fan_engaged", "watts": baseline})

        for i in range(1, steps + 1):
            step_baseline = self._read_fan_residual(power_sensor)
            watts = await self._step_until_power_moves(
                climate,
                power_sensor,
                step_baseline,
                toggle_gap,
                settle,
                max_retries,
                min_up,
                wrap_drop,
                i,
                readings,
            )
            readings.append({"label": f"after_step_{i}", "watts": watts})
            _LOGGER.info(
                "Blower speed step %s/%s finished; fan residual=%s W",
                i,
                steps,
                watts if watts is not None else "n/a",
            )

        if restore and prev_mode not in (None, "unavailable", "unknown"):
            await self._set_hvac_mode(climate, prev_mode)
            if prev_fan in ("on", "auto"):
                await self._set_fan_mode(climate, prev_fan)
            self.clear_hold()
        else:
            # Leave OFF + fan ON so airflow/power can be checked by hand.
            await self._set_fan_mode(climate, "on")

        result = {
            "climate_entity_id": climate,
            "power_sensor": power_sensor,
            "steps": steps,
            "settle_seconds": settle,
            "max_burst_retries": max_retries,
            "readings": readings,
            "restored": restore,
            "hold_until": self.hold_until.isoformat() if self.hold_until else None,
            "finished_at": datetime.now().isoformat(),
        }
        self._last_result = result
        return result

    async def async_set_power_monitor(self, call: ServiceCall) -> None:
        """Persist which circuit/power sensor indicates blower speed."""
        entity_id = call.data.get("power_sensor")
        if not entity_id:
            raise ValueError("power_sensor is required")
        entry = getattr(self._controller, "_config_entry", None)
        if entry is None:
            raise ValueError("No config entry available to store power sensor")
        new_options = {**entry.options, CONF_BLOWER_FAN_POWER_SENSOR: entity_id}
        self.hass.data.setdefault(DOMAIN, {})[f"{entry.entry_id}_skip_reload"] = True
        self.hass.config_entries.async_update_entry(entry, options=new_options)
        _LOGGER.info("Blower fan power monitor set to %s", entity_id)

    def _calibration(self) -> dict[str, Any]:
        entry = getattr(self._controller, "_config_entry", None)
        if entry is None:
            return {}
        raw = (entry.options or {}).get(CONF_BLOWER_SPEED_CALIBRATION) or {}
        return dict(raw) if isinstance(raw, dict) else {}

    def _guess_speed(self, watts: float | None) -> str | None:
        """Nearest calibrated speed label from live watts."""
        cal = self._calibration()
        if watts is None or not cal:
            return None
        best = None
        best_dist = None
        for label in SPEED_LABELS:
            ref = cal.get(label)
            if not isinstance(ref, dict):
                continue
            ref_w = ref.get("watts")
            if ref_w is None:
                continue
            dist = abs(float(ref_w) - watts)
            if best_dist is None or dist < best_dist:
                best_dist = dist
                best = label
        return best

    async def async_record_speed_calibration(self, call: ServiceCall) -> dict[str, Any]:
        """Save current circuit watts as speed_1..speed_6 reference (6=highest)."""
        speed = normalize_speed_label(call.data.get("speed", ""))
        power_sensor = self._power_sensor(call)
        # Prefer explicit watts from the caller when provided (Emporia lag).
        if call.data.get("watts") is not None:
            watts = float(call.data["watts"])
        else:
            # Default to true fan residual, not raw circuit total.
            watts = self._read_fan_residual(power_sensor)
        if watts is None:
            raise ValueError("No power reading available to calibrate")

        entry = getattr(self._controller, "_config_entry", None)
        if entry is None:
            raise ValueError("No config entry available")
        cal = self._calibration()
        # Drop legacy low/medium/high keys if present
        for legacy in ("low", "medium", "high"):
            cal.pop(legacy, None)
        cal[speed] = {
            "watts": round(watts, 1),
            "step": int(speed.split("_")[1]),
            "recorded_at": datetime.now().isoformat(timespec="seconds"),
            "power_sensor": power_sensor,
        }
        new_options = {**entry.options, CONF_BLOWER_SPEED_CALIBRATION: cal}
        self.hass.data.setdefault(DOMAIN, {})[f"{entry.entry_id}_skip_reload"] = True
        self.hass.config_entries.async_update_entry(entry, options=new_options)
        _LOGGER.info("Blower speed calibration %s = %.0f W", speed, watts)
        return cal[speed]

    def status_dict(self) -> dict[str, Any]:
        entry = getattr(self._controller, "_config_entry", None)
        power = DEFAULT_BLOWER_FAN_POWER_SENSOR
        if entry is not None:
            power = (entry.options or {}).get(
                CONF_BLOWER_FAN_POWER_SENSOR, DEFAULT_BLOWER_FAN_POWER_SENSOR
            )
        total = self._read_power(power)
        live = self._read_fan_residual(power)
        hot_water_w = self._read_power(DEFAULT_HOT_WATER_ON_CIRCUIT_SENSOR) or 0.0
        radiant_w = self._read_power(DEFAULT_RADIANT_CONTROLLER_POWER_SENSOR) or 0.0
        return {
            "power_sensor": power,
            "hold_active": self.is_hold_active,
            "hold_until": self.hold_until.isoformat() if self.hold_until else None,
            "last_result": self._last_result or None,
            "circuit_total_w": total,
            "hot_water_on_circuit_w": hot_water_w,
            "radiant_controller_w": radiant_w,
            "live_watts": live,
            "estimated_speed": self._guess_speed(live),
            "calibration": self._calibration(),
        }


def async_register_blower_fan_speed_services(
    hass: HomeAssistant, controller: Any
) -> BlowerFanSpeedProgrammer:
    """Register services and attach programmer on the outdoor-reset controller."""
    programmer = BlowerFanSpeedProgrammer(hass, controller)
    controller.blower_fan_speed = programmer

    async def handle_step(call: ServiceCall) -> None:
        await programmer.async_step_speed(call)

    async def handle_set_monitor(call: ServiceCall) -> None:
        await programmer.async_set_power_monitor(call)

    async def handle_record_cal(call: ServiceCall) -> None:
        await programmer.async_record_speed_calibration(call)

    async def handle_clear_hold(_call: ServiceCall) -> None:
        programmer.clear_hold()
        _LOGGER.info("Blower fan-speed program hold cleared")

    hass.services.async_register(DOMAIN, "step_blower_fan_speed", handle_step)
    hass.services.async_register(
        DOMAIN, "set_blower_fan_power_monitor", handle_set_monitor
    )
    hass.services.async_register(
        DOMAIN, "record_blower_fan_speed_calibration", handle_record_cal
    )
    hass.services.async_register(
        DOMAIN, "clear_blower_fan_speed_hold", handle_clear_hold
    )
    return programmer
