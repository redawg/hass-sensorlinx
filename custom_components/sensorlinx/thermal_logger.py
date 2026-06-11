"""Thermal data logger for AI-driven heating curve optimization.

Collects periodic snapshots of all thermal system data. Stores as JSONL
files for feeding into ML models that learn each room's thermal dynamics.

Data captured per sample:
  - timestamp (ISO 8601)
  - outdoor_temp (°F)
  - zone_name, room_temp, floor_temp
  - target_setpoint, commanded_setpoint
  - hvac_mode, hvac_action
  - heating_curve_params (base, overshoot, shutdown, design_outdoor)
  - zone_offset, outdoor_reset_enabled
  - ecobee_mode, ecobee_action, ecobee_temp, ecobee_setpoint
  - ecobee_humidity, ecobee_fan, ecobee_sensors, occupancy
  - wh_supply_temp, wh_return_temp, wh_delta_t (Optimal Tankless)
  - wh_flow_rate_gpm, wh_power_kw, wh_btu_hr (computed)
  - wh_heating, wh_state, wh_target_temp, wh_current_temp
  - wh_available_flow_gpm, wh_heater_capacity, wh_input_voltage
  - wh_error_code, wh_online

Stored at: /config/www/sensorlinx_thermal_log/thermal_YYYY-MM.jsonl
One file per month, one JSON line per sample interval.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval

from .coordinator import SensorlinxCoordinator
from .outdoor_reset import OutdoorResetController, OUTDOOR_TEMP_ENTITY

_LOGGER = logging.getLogger(__name__)

LOG_DIR = "/config/www/sensorlinx_thermal_log"
SAMPLE_INTERVAL = timedelta(minutes=5)

# Ecobee / main HVAC entities
ECOBEE_CLIMATE_ENTITY = "climate.main_floor"
ECOBEE_REMOTE_SENSORS = {
    "main_floor": "sensor.main_floor_current_temperature",
    "upstairs": "sensor.upstairs_temperature",
    "office": "sensor.office_temperature",
    "family_room": "sensor.family_room_temperature",
}
ECOBEE_HUMIDITY_ENTITY = "sensor.main_floor_current_humidity"
OCCUPANCY_SENSORS = {
    "main_floor": "binary_sensor.main_floor_occupancy",
    "upstairs": "binary_sensor.upstairs_occupancy",
    "family_room": "binary_sensor.family_room_occupancy",
}

# Optimal Tankless water heater sensors
TANKLESS_SENSORS = {
    "supply_temp": "sensor.main_water_heater_outlet_temperature",
    "return_temp": "sensor.main_water_heater_inlet_temperature",
    "flow_rate_gpm": "sensor.main_water_heater_flow_rate",
    "power_kw": "sensor.main_water_heater_power_draw",
    "available_flow_gpm": "sensor.main_water_heater_available_flow_rate",
    "heater_capacity": "sensor.main_water_heater_heater_capacity",
    "input_voltage": "sensor.main_water_heater_input_voltage",
    "error_code": "sensor.main_water_heater_error_code",
}
TANKLESS_BINARY = {
    "heating": "binary_sensor.main_water_heater_heating",
    "online": "binary_sensor.main_water_heater_online",
}
TANKLESS_WATER_HEATER = "water_heater.main_water_heater"


class ThermalDataLogger:
    """Periodically logs thermal state for ML training."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: SensorlinxCoordinator,
        controller: OutdoorResetController,
    ) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self.controller = controller
        self._unsub = None
        self._ensure_log_dir()

    def _ensure_log_dir(self) -> None:
        """Create the log directory if it doesn't exist."""
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
        except OSError:
            _LOGGER.warning("Cannot create thermal log dir %s", LOG_DIR)

    @property
    def _log_path(self) -> str:
        """Return path for current month's log file."""
        now = datetime.now()
        return os.path.join(LOG_DIR, f"thermal_{now.strftime('%Y-%m')}.jsonl")

    async def async_setup(self) -> None:
        """Start periodic data collection."""
        self._unsub = async_track_time_interval(
            self.hass, self._async_collect, SAMPLE_INTERVAL
        )
        _LOGGER.info("Thermal data logger started (interval=%s)", SAMPLE_INTERVAL)

    @callback
    def async_unload(self) -> None:
        """Stop collection."""
        if self._unsub:
            self._unsub()

    async def _async_collect(self, _now=None) -> None:
        """Collect and write one sample for each zone."""
        outdoor_temp = self._get_outdoor_temp()
        timestamp = datetime.now().isoformat()
        params = self.controller.params

        # Collect shared state (same for all zone samples in this interval)
        ecobee_data = self._get_ecobee_state()
        tankless_data = self._get_tankless_state()

        samples = []
        for thm in self.coordinator.get_thm_devices():
            zone_name = thm.name.lower().replace(" ", "_")
            raw = thm.raw

            room_temp = self._extract_room_temp(raw)
            floor_temp = self._extract_floor_temp(raw, zone_name)
            hvac_mode = self._extract_hvac_mode(raw)
            hvac_action = self._extract_hvac_action(raw)
            offset = params.zone_offsets.get(zone_name, 0.0)
            target = self.controller.zone_target(offset)

            # Get the actual commanded setpoint on the device
            target_block = raw.get("target", {})
            commanded = float(target_block.get("value", 0)) if isinstance(target_block, dict) else None

            sample = {
                "ts": timestamp,
                "outdoor_temp": outdoor_temp,
                "zone": zone_name,
                "room_temp": room_temp,
                "floor_temp": floor_temp,
                "curve_target": target,
                "commanded_setpoint": commanded,
                "hvac_mode": hvac_mode,
                "hvac_action": hvac_action,
                "base": params.base,
                "overshoot": params.overshoot,
                "shutdown": params.shutdown,
                "design_outdoor": params.design_outdoor,
                "floor_max": params.floor_max,
                "zone_offset": offset,
                "enabled": params.enabled,
                **ecobee_data,
                **tankless_data,
            }
            samples.append(sample)

        await self.hass.async_add_executor_job(self._write_samples, samples)

    def _write_samples(self, samples: list[dict[str, Any]]) -> None:
        """Write samples to JSONL file (runs in executor)."""
        try:
            path = self._log_path
            with open(path, "a", encoding="utf-8") as f:
                for sample in samples:
                    f.write(json.dumps(sample, separators=(",", ":")) + "\n")
        except OSError as e:
            _LOGGER.warning("Failed to write thermal log: %s", e)

    def _get_ecobee_state(self) -> dict[str, Any]:
        """Collect ecobee main HVAC state, remote sensors, and occupancy."""
        result: dict[str, Any] = {}

        # Main climate entity
        ecobee = self.hass.states.get(ECOBEE_CLIMATE_ENTITY)
        if ecobee and ecobee.state not in ("unavailable", "unknown"):
            attrs = ecobee.attributes
            result["ecobee_mode"] = ecobee.state
            result["ecobee_action"] = attrs.get("hvac_action")
            result["ecobee_temp"] = attrs.get("current_temperature")
            result["ecobee_setpoint"] = attrs.get("temperature")
            result["ecobee_humidity"] = attrs.get("current_humidity")
            result["ecobee_fan"] = attrs.get("fan_mode")
        else:
            result["ecobee_mode"] = None
            result["ecobee_action"] = None
            result["ecobee_temp"] = None
            result["ecobee_setpoint"] = None
            result["ecobee_humidity"] = None
            result["ecobee_fan"] = None

        # Remote sensor temperatures
        sensor_temps = {}
        for name, entity_id in ECOBEE_REMOTE_SENSORS.items():
            state = self.hass.states.get(entity_id)
            if state and state.state not in ("unavailable", "unknown"):
                try:
                    sensor_temps[name] = float(state.state)
                except (ValueError, TypeError):
                    sensor_temps[name] = None
            else:
                sensor_temps[name] = None
        result["ecobee_sensors"] = sensor_temps

        # Occupancy
        occupancy = {}
        for name, entity_id in OCCUPANCY_SENSORS.items():
            state = self.hass.states.get(entity_id)
            if state and state.state not in ("unavailable", "unknown"):
                occupancy[name] = state.state == "on"
            else:
                occupancy[name] = None
        result["occupancy"] = occupancy

        return result

    def _get_tankless_state(self) -> dict[str, Any]:
        """Collect Optimal Tankless water heater state for thermal analysis."""
        result: dict[str, Any] = {}

        # Numeric sensors
        for key, entity_id in TANKLESS_SENSORS.items():
            state = self.hass.states.get(entity_id)
            if state and state.state not in ("unavailable", "unknown"):
                try:
                    result[f"wh_{key}"] = float(state.state)
                except (ValueError, TypeError):
                    result[f"wh_{key}"] = None
            else:
                result[f"wh_{key}"] = None

        # Binary sensors
        for key, entity_id in TANKLESS_BINARY.items():
            state = self.hass.states.get(entity_id)
            if state and state.state not in ("unavailable", "unknown"):
                result[f"wh_{key}"] = state.state == "on"
            else:
                result[f"wh_{key}"] = None

        # Water heater control entity (target temp + state)
        wh = self.hass.states.get(TANKLESS_WATER_HEATER)
        if wh and wh.state not in ("unavailable", "unknown"):
            result["wh_state"] = wh.state
            result["wh_target_temp"] = wh.attributes.get("temperature")
            result["wh_current_temp"] = wh.attributes.get("current_temperature")
        else:
            result["wh_state"] = None
            result["wh_target_temp"] = None
            result["wh_current_temp"] = None

        # Computed delta-T
        supply = result.get("wh_supply_temp")
        ret = result.get("wh_return_temp")
        if supply is not None and ret is not None:
            result["wh_delta_t"] = round(supply - ret, 1)
        else:
            result["wh_delta_t"] = None

        # Computed BTU/hr (500 * GPM * delta-T)
        flow = result.get("wh_flow_rate_gpm")
        dt = result.get("wh_delta_t")
        if flow and dt and flow > 0 and dt > 0:
            result["wh_btu_hr"] = round(500 * flow * dt, 0)
        else:
            result["wh_btu_hr"] = None

        return result

    def _get_outdoor_temp(self) -> float | None:
        """Get outdoor temperature from weather station."""
        state = self.hass.states.get(OUTDOOR_TEMP_ENTITY)
        if state is None or state.state in ("unavailable", "unknown"):
            return None
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return None

    def _extract_room_temp(self, raw: dict) -> float | None:
        """Extract room temp from device raw data."""
        block = raw.get("temperature")
        if isinstance(block, dict) and block.get("value") is not None:
            return float(block["value"])
        rm = raw.get("rm") or raw.get("rmT")
        return float(rm) if rm is not None else None

    def _extract_floor_temp(self, raw: dict, zone_name: str) -> float | None:
        """Extract floor temp — try raw data first, then HA entity."""
        flr = raw.get("flr")
        if flr is not None:
            return float(flr)
        state = self.hass.states.get(f"sensor.{zone_name}_floor_temperature")
        if state and state.state not in ("unavailable", "unknown"):
            try:
                return float(state.state)
            except (ValueError, TypeError):
                pass
        return None

    def _extract_hvac_mode(self, raw: dict) -> str:
        """Extract current HVAC mode."""
        if raw.get("offMode") == 1:
            return "off"
        for entry in raw.get("changeover", []) or []:
            if isinstance(entry, dict) and entry.get("activated"):
                return entry.get("key", "unknown")
        return "off"

    def _extract_hvac_action(self, raw: dict) -> str:
        """Extract current HVAC action from demand byte."""
        dmd = raw.get("dmd")
        if not isinstance(dmd, int):
            return "unknown"
        if dmd & 0x02:
            return "heating"
        if dmd & 0x40:
            return "cooling"
        if dmd & 0x80:
            return "fan"
        return "idle"


async def async_setup_thermal_logger(
    hass: HomeAssistant,
    coordinator: SensorlinxCoordinator,
    controller: OutdoorResetController,
) -> ThermalDataLogger:
    """Create and start the thermal data logger."""
    logger = ThermalDataLogger(hass, coordinator, controller)
    await logger.async_setup()
    return logger
