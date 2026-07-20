"""Bryant / Carrier variable-speed continuous fan speed programming via thermostat.

Method (furnace control board):
  1. HVAC mode OFF, fan ON — continuous fan runs
  2. Within ~3s: Auto → On → Auto → On to step to the next pre-programmed speed
  3. Repeat to cycle Low / Medium / High (exact labels vary by board)

Power draw on a dedicated CT/circuit is the practical speed indicator.
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
DEFAULT_TOGGLE_GAP_S = 0.45
DEFAULT_FAN_ENGAGE_S = 4.0
DEFAULT_SETTLE_S = 8.0
DEFAULT_HOLD_MINUTES = 10
MAX_STEPS = 6
SPEED_LABELS = ("low", "medium", "high")


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
        """Auto→On→Auto→On inside a short window = one board speed step."""
        await self._set_fan_mode(entity_id, "auto")
        await asyncio.sleep(toggle_gap)
        await self._set_fan_mode(entity_id, "on")
        await asyncio.sleep(toggle_gap)
        await self._set_fan_mode(entity_id, "auto")
        await asyncio.sleep(toggle_gap)
        await self._set_fan_mode(entity_id, "on")

    def begin_hold(self, minutes: float = DEFAULT_HOLD_MINUTES) -> None:
        self.hold_until = datetime.now() + timedelta(minutes=minutes)
        _LOGGER.info(
            "Blower fan-speed program hold until %s (orchestrator will not fight)",
            self.hold_until.isoformat(timespec="seconds"),
        )

    def clear_hold(self) -> None:
        self.hold_until = None

    async def async_step_speed(self, call: ServiceCall) -> dict[str, Any]:
        """Run N continuous-fan speed steps; optionally sample power each step."""
        climate = self._climate_entity(call)
        power_sensor = self._power_sensor(call)
        steps = int(call.data.get("steps", 1))
        steps = max(1, min(steps, MAX_STEPS))
        toggle_gap = float(call.data.get("toggle_gap_seconds", DEFAULT_TOGGLE_GAP_S))
        engage = float(call.data.get("fan_engage_seconds", DEFAULT_FAN_ENGAGE_S))
        settle = float(call.data.get("settle_seconds", DEFAULT_SETTLE_S))
        hold_min = float(call.data.get("hold_minutes", DEFAULT_HOLD_MINUTES))
        restore = bool(call.data.get("restore_previous", False))

        state = self.hass.states.get(climate)
        if state is None or state.state in ("unavailable", "unknown"):
            raise ValueError(f"Climate entity unavailable: {climate}")

        prev_mode = state.state
        prev_fan = state.attributes.get("fan_mode")

        self.begin_hold(hold_min)

        readings: list[dict[str, Any]] = []
        baseline = self._read_power(power_sensor)
        readings.append({"label": "before", "watts": baseline})

        _LOGGER.info(
            "Blower speed program: %s OFF + fan ON, then %s step(s); power=%s",
            climate,
            steps,
            power_sensor or "not configured",
        )

        await self._set_hvac_mode(climate, "off")
        await self._set_fan_mode(climate, "on")
        await asyncio.sleep(engage)
        readings.append(
            {"label": "fan_engaged", "watts": self._read_power(power_sensor)}
        )

        for i in range(1, steps + 1):
            await self._one_speed_step(climate, toggle_gap)
            await asyncio.sleep(settle)
            watts = self._read_power(power_sensor)
            readings.append({"label": f"after_step_{i}", "watts": watts})
            _LOGGER.info(
                "Blower speed step %s/%s complete; power=%s W",
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
        """Save current circuit watts as low/medium/high reference."""
        speed = str(call.data.get("speed", "")).strip().lower()
        if speed not in SPEED_LABELS:
            raise ValueError(f"speed must be one of {SPEED_LABELS}")
        power_sensor = self._power_sensor(call)
        watts = self._read_power(power_sensor)
        if watts is None and call.data.get("watts") is not None:
            watts = float(call.data["watts"])
        if watts is None:
            raise ValueError("No power reading available to calibrate")

        entry = getattr(self._controller, "_config_entry", None)
        if entry is None:
            raise ValueError("No config entry available")
        cal = self._calibration()
        cal[speed] = {
            "watts": round(watts, 1),
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
        live = self._read_power(power)
        return {
            "power_sensor": power,
            "hold_active": self.is_hold_active,
            "hold_until": self.hold_until.isoformat() if self.hold_until else None,
            "last_result": self._last_result or None,
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
