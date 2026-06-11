"""Outdoor Reset Heating Curve for HBX SensorLinx radiant floors.

Adjusts THM zone setpoints based on outdoor temperature using a linear
heating curve. Includes a floor temperature safety cap for wood floors.

Formula:
  target = base + overshoot * ((shutdown - T_outdoor) / (shutdown - design_outdoor))
  Clamped to [base, base + overshoot]
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode, RestoreNumber
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval, async_track_state_change_event
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SensorlinxCoordinator, SensorlinxDeviceData
from .helpers import thm_device_info
from .night_setback import (
    NightSetbackMixin,
    NightSetbackParams,
    get_night_setback_number_entities,
    get_night_setback_switch_entities,
)

_LOGGER = logging.getLogger(__name__)

OUTDOOR_TEMP_ENTITY = "sensor.quail_creek_ames_lake_279th_ct_ne_temperature"
UPDATE_INTERVAL = timedelta(minutes=15)

DEFAULT_BASE = 70.0
DEFAULT_OVERSHOOT = 6.0
DEFAULT_SHUTDOWN = 65.0
DEFAULT_DESIGN_OUTDOOR = 25.0
DEFAULT_FLOOR_MAX = 80.0  # wood floor safety cap
DEFAULT_TILE_FLOOR_MAX = 88.0  # tile zones (e.g. laundry) may run hotter
TILE_FLOOR_ZONES = frozenset({"laundry"})
DEFAULT_FLOOR_TARGET = 70.0


def default_zone_floor_max(zone_key: str, wood_default: float = DEFAULT_FLOOR_MAX) -> float:
    """Default per-zone floor safety cap."""
    if zone_key in TILE_FLOOR_ZONES:
        return DEFAULT_TILE_FLOOR_MAX
    return wood_default


# Negative bias = sensor reads hot (e.g. probe at loop supply/manifold).
DEFAULT_ZONE_FLOOR_SENSOR_BIAS: dict[str, float] = {
    "living_room": -5.0,
}

DEFAULT_FLOOR_BOOST = 2.0  # extra degrees added at design outdoor (cold)
FLOOR_CONTROL_GAIN = 3.0  # degrees to overshoot room setpoint when floor is below target

# Supply water temperature reset defaults
DEFAULT_SUPPLY_TEMP_MIN = 100.0  # mild weather supply water temp
DEFAULT_SUPPLY_TEMP_MAX = 140.0  # cold weather supply water temp
TANKLESS_MAX_TEMP = 140.0  # physical maximum the Optimal Tankless can produce
SUPPLY_DEADBAND = 2.0  # don't re-command if within this range
# Supply boost: fast heat-up without holding max temp longer than needed
BOOST_TARGET_DELTA_T = 10.0  # ideal loop extraction (F) — step down when sustained
BOOST_EFFICIENT_DELTA_T = 8.0  # minimum meaningful extraction during warm-up
BOOST_HIGH_DELTA_T = 18.0  # step down early if over-extracting (saves power)
BOOST_STABLE_CHECKS = 2  # consecutive monitor ticks at target delta-T
BOOST_HEADROOM_F = 15.0  # boost setpoint = optimal + headroom (not always max)
BOOST_MAX_DURATION = timedelta(minutes=25)
BOOST_COOLDOWN = timedelta(minutes=20)
TANKLESS_FAST_SCAN_INTERVAL = 15  # seconds during supply boost
TANKLESS_NORMAL_SCAN_INTERVAL = 60  # seconds during normal operation
BOOST_MONITOR_INTERVAL = timedelta(seconds=15)

# Preheat defaults
DEFAULT_THERMAL_LAG = 20.0  # minutes per degree F of floor temp rise
PREHEAT_CHECK_INTERVAL = timedelta(minutes=15)
TREND_WINDOW_MINUTES = 120  # use last 2 hours of outdoor temp for trend prediction

# Hydronic loop monitoring
DEFAULT_VALVE_COUNT = 1  # valves per zone (for flow estimation)
DEFAULT_FLOW_RATE_PER_VALVE = 0.5  # GPM per valve (typical 1/2" zone valve)
DEFAULT_ELECTRICITY_COST = 0.105  # $/kWh (10.5 cents)
WATER_BTU_FACTOR = 500  # BTU/hr per GPM per degree F delta-T

SIGNAL_FLOOR_MODE_CHANGED = f"{DOMAIN}_floor_mode_changed"


def compute_target(outdoor: float, base: float, overshoot: float,
                   shutdown: float, design_outdoor: float) -> float:
    """Calculate the heating curve target setpoint."""
    temp_range = shutdown - design_outdoor
    if temp_range <= 0:
        return base
    if outdoor >= shutdown:
        return base
    if outdoor <= design_outdoor:
        return base + overshoot
    return round(base + overshoot * ((shutdown - outdoor) / temp_range), 1)


class OutdoorResetController(NightSetbackMixin):
    """Manages the outdoor reset logic and periodically updates zone setpoints."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: SensorlinxCoordinator,
        params: OutdoorResetParams,
    ) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self.params = params
        self._unsub_interval = None
        self._unsub_state = None
        self._unsub_zone_boost = None
        self._unsub_inlet_boost = None
        self._unsub_boost_monitor = None
        # Preheat: track outdoor temp history for trend prediction
        self._temp_history: deque[tuple[datetime, float]] = deque(maxlen=24)
        self._preheat_active: bool = False
        # Supply water fast heat-up boost
        self._supply_boost_active: bool = False
        self._supply_boost_optimal: float | None = None
        self._supply_boost_target: float | None = None
        self._boost_start_inlet: float | None = None
        self._boost_start_time: datetime | None = None
        self._boost_stable_count: int = 0
        self._boost_last_end: datetime | None = None
        self._tankless_scan_interval: int | None = None
        self._unsub_night_schedule = None
        self._unsub_night_motion = None
        self._unsub_day_restore = None

    @property
    def enabled(self) -> bool:
        return self.params.enabled

    @property
    def outdoor_temp(self) -> float | None:
        state = self.hass.states.get(OUTDOOR_TEMP_ENTITY)
        if state is None or state.state in ("unavailable", "unknown"):
            return None
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return None

    @property
    def calculated_target(self) -> float:
        outdoor = self.outdoor_temp
        if outdoor is None:
            return self.params.base
        return compute_target(
            outdoor, self.params.base, self.params.overshoot,
            self.params.shutdown, self.params.design_outdoor,
        )

    def zone_target(self, zone_offset: float) -> float:
        return round(self.calculated_target + zone_offset, 1)

    def zone_floor_max(self, zone_name: str) -> float:
        """Per-zone floor safety cap (wood default 80F, tile zones higher)."""
        if zone_name in self.params.zone_floor_max:
            return self.params.zone_floor_max[zone_name]
        return default_zone_floor_max(zone_name, self.params.floor_max)

    def floor_sensor_bias(self, zone_name: str) -> float:
        """Per-zone correction added to raw floor sensor (negative if probe runs hot)."""
        if zone_name in self.params.zone_floor_sensor_bias:
            return self.params.zone_floor_sensor_bias[zone_name]
        return DEFAULT_ZONE_FLOOR_SENSOR_BIAS.get(zone_name, 0.0)

    def effective_floor_temp(self, zone_name: str, raw_floor_temp: float | None) -> float | None:
        """Estimate true slab temp from a misplaced or biased floor probe."""
        if raw_floor_temp is None:
            return None
        return round(raw_floor_temp + self.floor_sensor_bias(zone_name), 1)

    def _read_zone_floor_temp(self, zone_name: str) -> float | None:
        """Read raw floor sensor from HA."""
        floor_state = self.hass.states.get(f"sensor.{zone_name}_floor_temperature")
        if floor_state and floor_state.state not in ("unavailable", "unknown"):
            try:
                return float(floor_state.state)
            except (ValueError, TypeError):
                pass
        return None

    async def async_setup(self) -> None:
        """Start periodic updates."""
        self._unsub_interval = async_track_time_interval(
            self.hass, self._async_update, UPDATE_INTERVAL
        )
        self._unsub_state = async_track_state_change_event(
            self.hass, [OUTDOOR_TEMP_ENTITY], self._async_on_outdoor_change
        )
        zone_entities = self._zone_climate_entities()
        if zone_entities:
            self._unsub_zone_boost = async_track_state_change_event(
                self.hass, zone_entities, self._async_on_zone_heating_change
            )
        inlet_entity = self.params.return_temp_sensor
        if inlet_entity:
            self._unsub_inlet_boost = async_track_state_change_event(
                self.hass, [inlet_entity], self._async_on_inlet_temp_change
            )
        await self._setup_night_setback()

    @callback
    def async_unload(self) -> None:
        """Remove listeners."""
        self._unload_night_setback()
        if self._unsub_interval:
            self._unsub_interval()
        if self._unsub_state:
            self._unsub_state()
        if self._unsub_zone_boost:
            self._unsub_zone_boost()
        if self._unsub_inlet_boost:
            self._unsub_inlet_boost()
        self._stop_boost_monitor()

    async def _async_update(self, _now=None) -> None:
        """Apply setpoints to all THM zones."""
        if not self.enabled:
            return
        await self._apply_setpoints()

    async def _async_on_outdoor_change(self, event) -> None:
        """React to outdoor temperature changes."""
        if not self.enabled:
            return
        await self._apply_setpoints()

    async def _apply_setpoints(self) -> None:
        """Set temperature on each climate entity based on the curve."""
        outdoor = self.outdoor_temp
        if outdoor is None:
            _LOGGER.debug("Outdoor temp unavailable, skipping reset update")
            return

        # Adjust supply water temperature alongside zone setpoints
        await self._apply_supply_water_temp(outdoor)

        # Check preheat before shutdown logic
        await self._check_preheat(outdoor)

        target = self.calculated_target

        if outdoor >= self.params.shutdown:
            if self._preheat_active:
                # Preheat override: keep heating even though outdoor is above shutdown
                _LOGGER.info(
                    "Preheat active: outdoor %.1f°F >= shutdown %.1f°F but pre-warming floors",
                    outdoor, self.params.shutdown,
                )
                # Fall through to normal heating logic below
            else:
                _LOGGER.info("Outdoor %.1f\u00b0F >= shutdown %.1f\u00b0F, turning off zones",
                             outdoor, self.params.shutdown)
                for thm in self.coordinator.get_thm_devices():
                    entity_id = f"climate.{thm.name.lower().replace(' ', '_')}_{thm.name.lower().replace(' ', '_')}"
                    await self.hass.services.async_call(
                        "climate", "turn_off",
                        {"entity_id": entity_id},
                        blocking=True,
                    )
                return

        for thm in self.coordinator.get_thm_devices():
            zone_name = thm.name.lower().replace(" ", "_")
            entity_id = f"climate.{zone_name}_{zone_name}"

            raw_floor_temp = self._read_zone_floor_temp(zone_name)
            floor_temp = self.effective_floor_temp(zone_name, raw_floor_temp)

            # Safety cap: turn off if corrected floor exceeds zone max
            zone_cap = self.zone_floor_max(zone_name)
            if floor_temp is not None and floor_temp >= zone_cap:
                bias = self.floor_sensor_bias(zone_name)
                if bias and raw_floor_temp is not None:
                    _LOGGER.warning(
                        "Floor temp %.1f°F (sensor %.1f°F, bias %+.1f°F) >= max %.1f°F "
                        "for %s, turning off",
                        floor_temp, raw_floor_temp, bias, zone_cap, zone_name,
                    )
                else:
                    _LOGGER.warning(
                        "Floor temp %.1f°F >= max %.1f°F for %s, turning off",
                        floor_temp, zone_cap, zone_name,
                    )
                await self.hass.services.async_call(
                    "climate", "turn_off",
                    {"entity_id": entity_id},
                    blocking=True,
                )
                continue

            # Determine setpoint based on control mode
            floor_mode = self.params.floor_control_enabled.get(zone_name, False)

            if self.params.night_setback.active:
                zone_target = self._zone_night_target(
                    zone_name, floor_mode, floor_temp, outdoor
                )
            elif floor_mode:
                # Floor control mode: dynamic floor target based on outdoor temp
                base_floor_target = self.params.floor_targets.get(zone_name, DEFAULT_FLOOR_TARGET)
                floor_target = self._dynamic_floor_target(base_floor_target, outdoor)
                zone_target = self._compute_floor_mode_setpoint(
                    floor_temp, floor_target, zone_name
                )
            else:
                # Room control mode: use outdoor reset curve + offset
                offset = self.params.zone_offsets.get(zone_name, 0.0)
                zone_target = self.zone_target(offset)

            # Deadband: skip if climate entity already at this setpoint (avoids churn)
            climate_state = self.hass.states.get(entity_id)
            if climate_state:
                current_setpoint = climate_state.attributes.get("temperature")
                current_mode = climate_state.state
                if (current_mode == "heat"
                        and current_setpoint is not None
                        and abs(float(current_setpoint) - zone_target) < 0.5):
                    _LOGGER.debug("Skipping %s - already at %.1f°F", zone_name, zone_target)
                    continue

            _LOGGER.debug(
                "Setting %s to %.1f°F (mode=%s, outdoor=%.1f)",
                zone_name, zone_target, "floor" if floor_mode else "room", outdoor,
            )
            await self.hass.services.async_call(
                "climate", "set_temperature",
                {"entity_id": entity_id, "temperature": zone_target, "hvac_mode": "heat"},
                blocking=True,
            )

    def _dynamic_floor_target(self, base_target: float, outdoor: float) -> float:
        """Compute dynamic floor target: base in mild weather, base+boost in cold.

        At shutdown temp: floor target = base (e.g. 70F)
        At design outdoor: floor target = base + boost (e.g. 72F)
        Linear interpolation between.
        """
        temp_range = self.params.shutdown - self.params.design_outdoor
        if temp_range <= 0:
            return base_target
        if outdoor >= self.params.shutdown:
            return base_target
        if outdoor <= self.params.design_outdoor:
            return base_target + self.params.floor_boost
        ratio = (self.params.shutdown - outdoor) / temp_range
        return round(base_target + self.params.floor_boost * ratio, 1)

    def _compute_floor_mode_setpoint(
        self, floor_temp: float | None, floor_target: float, zone_name: str
    ) -> float:
        """Compute the THM room setpoint to achieve a desired floor temperature.

        Since the THM controls based on room temp, we adjust the room setpoint
        to drive the floor to the desired temperature:
        - Floor below target: set room setpoint higher to call for more heat
        - Floor at target: set room setpoint to maintain
        - Floor above target: reduce room setpoint to let it coast down
        """
        if floor_temp is None:
            return floor_target + FLOOR_CONTROL_GAIN

        error = floor_target - floor_temp
        if error > 1.0:
            # Floor is cold — drive harder
            setpoint = floor_target + FLOOR_CONTROL_GAIN
        elif error < -1.0:
            # Floor is too warm — back off
            setpoint = floor_target - 2.0
        else:
            # Floor is close to target — maintain
            setpoint = floor_target + 1.0

        # Clamp to reasonable range below zone safety cap
        cap = self.zone_floor_max(zone_name)
        return round(max(65.0, min(setpoint, cap - 2)), 1)

    @property
    def supply_boost_active(self) -> bool:
        """Whether supply water fast heat-up boost is active."""
        return self._supply_boost_active

    def _zone_climate_entities(self) -> list[str]:
        return [
            f"climate.{thm.name.lower().replace(' ', '_')}_{thm.name.lower().replace(' ', '_')}"
            for thm in self.coordinator.get_thm_devices()
        ]

    def _climate_is_heating(self, state) -> bool:
        if state is None or state.state in ("unavailable", "unknown"):
            return False
        if state.attributes.get("hvac_action") == "heating":
            return True
        return state.state == "heat"

    def _any_zone_heating(self) -> bool:
        for entity_id in self._zone_climate_entities():
            if self._climate_is_heating(self.hass.states.get(entity_id)):
                return True
        return False

    @callback
    def _async_on_zone_heating_change(self, event) -> None:
        """Start boost when a zone first calls for heat."""
        self.hass.async_create_task(self._handle_zone_heating_change(event))

    async def _handle_zone_heating_change(self, event) -> None:
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        was_heating = self._climate_is_heating(old_state)
        is_heating = self._climate_is_heating(new_state)
        if is_heating and not was_heating:
            await self._async_start_supply_boost()
        elif was_heating and not is_heating and not self._any_zone_heating():
            await self._async_end_supply_boost(cancelled=True)

    @callback
    def _async_on_inlet_temp_change(self, event) -> None:
        """Check boost completion when inlet temperature updates."""
        if self._supply_boost_active:
            self.hass.async_create_task(self._async_check_boost_progress())

    def _start_boost_monitor(self) -> None:
        if self._unsub_boost_monitor:
            return
        self._unsub_boost_monitor = async_track_time_interval(
            self.hass, self._async_check_boost_progress, BOOST_MONITOR_INTERVAL
        )

    def _stop_boost_monitor(self) -> None:
        if self._unsub_boost_monitor:
            self._unsub_boost_monitor()
            self._unsub_boost_monitor = None

    def _effective_supply_max(self) -> float:
        """Configured max capped at tankless hardware limit."""
        return min(self.params.supply_temp_max, TANKLESS_MAX_TEMP)

    def _clamp_supply_temp(self, temp: float) -> float:
        """Never command above tankless physical max or configured ceiling."""
        return round(min(temp, self._effective_supply_max(), TANKLESS_MAX_TEMP), 1)

    def _compute_boost_target(self, optimal: float) -> float:
        """Boost only as hot as needed — optimal + headroom, capped at tankless max."""
        return self._clamp_supply_temp(optimal + BOOST_HEADROOM_F)

    def _boost_cooldown_active(self) -> bool:
        if self._boost_last_end is None:
            return False
        return datetime.now() - self._boost_last_end < BOOST_COOLDOWN

    async def _async_start_supply_boost(self) -> None:
        """Raise supply temp briefly when a zone calls for heat and loop dT is low."""
        if not self.params.supply_control_enabled or not self.params.supply_boost_enabled:
            return
        if self._supply_boost_active or not self.params.supply_entity_id:
            return
        if self._boost_cooldown_active():
            _LOGGER.debug("Supply boost skipped: cooldown active")
            return

        outdoor = self.outdoor_temp
        if outdoor is None:
            return

        optimal = self._compute_supply_water_temp(outdoor)
        boost_target = self._compute_boost_target(optimal)
        if boost_target <= optimal + SUPPLY_DEADBAND:
            return

        dt = self.delta_t
        if dt is not None and dt >= BOOST_TARGET_DELTA_T:
            _LOGGER.debug(
                "Supply boost skipped: delta-T %.1fF already at target", dt,
            )
            return

        inlet = self.return_water_actual
        if (
            dt is not None
            and dt >= BOOST_EFFICIENT_DELTA_T
            and inlet is not None
            and self.supply_water_actual is not None
            and abs(self.supply_water_actual - optimal) < SUPPLY_DEADBAND * 3
        ):
            _LOGGER.debug("Supply boost skipped: loop already efficient")
            return

        self._supply_boost_active = True
        self._supply_boost_optimal = optimal
        self._supply_boost_target = boost_target
        self._boost_start_inlet = inlet
        self._boost_start_time = datetime.now()
        self._boost_stable_count = 0
        _LOGGER.info(
            "Supply boost START: inlet=%s, dT=%s, boost=%.1fF, optimal=%.1fF",
            f"{inlet:.1f}F" if inlet is not None else "unknown",
            f"{dt:.1f}F" if dt is not None else "unknown",
            boost_target,
            optimal,
        )
        await self._async_set_supply_temperature(boost_target, force=True)
        await self._async_set_tankless_scan_interval(TANKLESS_FAST_SCAN_INTERVAL)
        self._start_boost_monitor()

    async def _async_end_supply_boost(
        self, *, cancelled: bool = False, reason: str = "complete",
    ) -> None:
        """Restore optimal supply temp and normal tankless polling."""
        if not self._supply_boost_active:
            return

        optimal = self._supply_boost_optimal
        inlet = self.return_water_actual
        dt = self.delta_t
        self._supply_boost_active = False
        self._supply_boost_optimal = None
        self._supply_boost_target = None
        self._boost_start_inlet = None
        self._boost_start_time = None
        self._boost_stable_count = 0
        self._boost_last_end = datetime.now()
        self._stop_boost_monitor()

        if cancelled:
            _LOGGER.info("Supply boost CANCELLED (%s)", reason)
        else:
            _LOGGER.info(
                "Supply boost DONE (%s): inlet=%s, dT=%s, restoring optimal %.1fF",
                reason,
                f"{inlet:.1f}F" if inlet is not None else "unknown",
                f"{dt:.1f}F" if dt is not None else "unknown",
                optimal or 0,
            )

        if optimal is not None:
            await self._async_set_supply_temperature(optimal, force=True)
        await self._async_set_tankless_scan_interval(TANKLESS_NORMAL_SCAN_INTERVAL)

    async def _async_check_boost_progress(self, _now=None) -> None:
        """End boost when delta-T shows efficient heat transfer (power-optimal)."""
        if not self._supply_boost_active:
            return
        if not self._any_zone_heating():
            await self._async_end_supply_boost(cancelled=True, reason="no_zone_demand")
            return

        if (
            self._boost_start_time is not None
            and datetime.now() - self._boost_start_time >= BOOST_MAX_DURATION
        ):
            await self._async_end_supply_boost(reason="max_duration")
            return

        dt = self.delta_t
        if dt is None:
            return

        if dt >= BOOST_HIGH_DELTA_T:
            await self._async_end_supply_boost(reason="high_delta_t")
            return

        if dt >= BOOST_TARGET_DELTA_T:
            self._boost_stable_count += 1
            if self._boost_stable_count >= BOOST_STABLE_CHECKS:
                await self._async_end_supply_boost(reason="delta_t_stable")
        else:
            self._boost_stable_count = 0

    async def _async_set_tankless_scan_interval(self, seconds: int) -> None:
        """Change Optimal Tankless cloud polling interval."""
        if self._tankless_scan_interval == seconds:
            return
        if not self.hass.services.has_service("optimaltankless", "set_scan_interval"):
            _LOGGER.debug("optimaltankless.set_scan_interval not available")
            return
        await self.hass.services.async_call(
            "optimaltankless",
            "set_scan_interval",
            {"scan_interval": seconds},
            blocking=True,
        )
        self._tankless_scan_interval = seconds
        _LOGGER.info("Tankless scan interval set to %ds", seconds)

    async def _async_set_supply_temperature(self, target: float, *, force: bool = False) -> bool:
        """Command the supply water heater entity to a target temperature."""
        entity_id = self.params.supply_entity_id
        if not entity_id:
            return False

        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unavailable", "unknown"):
            _LOGGER.debug("Supply entity %s unavailable", entity_id)
            return False

        try:
            if entity_id.startswith("climate."):
                current = float(state.attributes.get("temperature", 0))
            else:
                current = float(state.state)
        except (ValueError, TypeError):
            current = 0.0

        target = self._clamp_supply_temp(target)
        if not force and abs(current - target) < SUPPLY_DEADBAND:
            return False

        set_value = round(target) if entity_id.startswith("water_heater.") else target
        set_value = min(set_value, TANKLESS_MAX_TEMP) if entity_id.startswith("water_heater.") else set_value
        _LOGGER.info("Setting supply %s to %sF (was %.1fF)", entity_id, set_value, current)

        if entity_id.startswith("climate."):
            await self.hass.services.async_call(
                "climate", "set_temperature",
                {"entity_id": entity_id, "temperature": set_value},
                blocking=True,
            )
        elif entity_id.startswith("number."):
            await self.hass.services.async_call(
                "number", "set_value",
                {"entity_id": entity_id, "value": set_value},
                blocking=True,
            )
        elif entity_id.startswith("water_heater."):
            await self.hass.services.async_call(
                "water_heater", "set_temperature",
                {"entity_id": entity_id, "temperature": set_value},
                blocking=True,
            )
        else:
            _LOGGER.warning("Unsupported entity type for supply control: %s", entity_id)
            return False
        return True

    def _compute_supply_water_temp(self, outdoor: float) -> float:
        """Compute target supply water temperature based on outdoor temp.

        Linear interpolation:
        - At shutdown (65F outdoor): supply = supply_temp_min (100F)
        - At design outdoor (25F): supply = supply_temp_max (140F)
        """
        temp_range = self.params.shutdown - self.params.design_outdoor
        if temp_range <= 0:
            return self.params.supply_temp_min
        if outdoor >= self.params.shutdown:
            return self.params.supply_temp_min
        supply_max = self._effective_supply_max()
        if outdoor <= self.params.design_outdoor:
            return supply_max
        ratio = (self.params.shutdown - outdoor) / temp_range
        return self._clamp_supply_temp(
            self.params.supply_temp_min
            + (supply_max - self.params.supply_temp_min) * ratio
        )

    async def _apply_supply_water_temp(self, outdoor: float) -> None:
        """Adjust the water heater setpoint based on outdoor temperature."""
        if not self.params.supply_control_enabled:
            return
        if self._supply_boost_active:
            return

        target_supply = self._compute_supply_water_temp(outdoor)
        await self._async_set_supply_temperature(target_supply)
        if self._tankless_scan_interval != TANKLESS_NORMAL_SCAN_INTERVAL:
            await self._async_set_tankless_scan_interval(TANKLESS_NORMAL_SCAN_INTERVAL)

    # ------------------------------------------------------------------
    # Preheat: start heating before outdoor drops below shutdown
    # ------------------------------------------------------------------

    def _record_outdoor_temp(self, outdoor: float) -> None:
        """Record outdoor temp reading for trend analysis."""
        self._temp_history.append((datetime.now(), outdoor))

    async def _estimate_minutes_to_shutdown_async(self, outdoor: float) -> float | None:
        """Async version that checks forecast first, then falls back to trend."""
        shutdown = self.params.shutdown
        if outdoor < shutdown:
            return 0.0

        minutes_from_forecast = await self._check_forecast_for_shutdown()
        if minutes_from_forecast is not None:
            return minutes_from_forecast

        return self._estimate_minutes_to_shutdown_trend(outdoor)

    def _estimate_minutes_to_shutdown(self, outdoor: float) -> float | None:
        """Sync trend-only estimate (used by sensor for attributes)."""
        if outdoor < self.params.shutdown:
            return 0.0
        return self._estimate_minutes_to_shutdown_trend(outdoor)

    def _estimate_minutes_to_shutdown_trend(self, outdoor: float) -> float | None:
        """Estimate using trend analysis on recent outdoor temp history.

        Returns None if temp is not dropping toward shutdown.
        """
        shutdown = self.params.shutdown

        # Fallback: trend-based using temp history
        if len(self._temp_history) < 4:
            return None

        # Get readings from last TREND_WINDOW_MINUTES
        now = datetime.now()
        cutoff = now - timedelta(minutes=TREND_WINDOW_MINUTES)
        recent = [(t, temp) for t, temp in self._temp_history if t >= cutoff]

        if len(recent) < 3:
            return None

        # Calculate rate of change (degrees per minute)
        first_time, first_temp = recent[0]
        last_time, last_temp = recent[-1]
        elapsed_min = (last_time - first_time).total_seconds() / 60.0

        if elapsed_min < 15:
            return None

        rate = (last_temp - first_temp) / elapsed_min  # deg/min (negative = cooling)

        if rate >= 0:
            # Temp is rising or stable — no preheat needed
            return None

        # How many minutes until we hit shutdown?
        degrees_to_go = outdoor - shutdown
        minutes_to_shutdown = degrees_to_go / abs(rate)

        _LOGGER.debug(
            "Preheat trend: rate=%.3f deg/min, degrees_to_go=%.1f, ETA=%.0f min",
            rate, degrees_to_go, minutes_to_shutdown,
        )
        return minutes_to_shutdown

    async def _check_forecast_for_shutdown(self) -> float | None:
        """Check weather forecast for when temp will cross shutdown.

        Uses HA's weather.get_forecasts service (2024+) with fallback to
        entity attributes. Tries forecast_entity_id first, then any available
        weather entity.
        """
        forecast_entities = []
        if self.params.forecast_entity_id:
            forecast_entities.append(self.params.forecast_entity_id)

        # Auto-discover weather entities as fallback
        for state in self.hass.states.async_all("weather"):
            if state.entity_id not in forecast_entities:
                forecast_entities.append(state.entity_id)

        if not forecast_entities:
            return None

        for entity_id in forecast_entities:
            result = await self._get_hourly_forecast(entity_id)
            if result is not None:
                return result

        return None

    async def _get_hourly_forecast(self, entity_id: str) -> float | None:
        """Get hourly forecast from a weather entity and find shutdown crossing."""
        shutdown = self.params.shutdown
        now = datetime.now()

        # Try HA 2024+ service call (weather.get_forecasts)
        try:
            response = await self.hass.services.async_call(
                "weather", "get_forecasts",
                {"entity_id": entity_id, "type": "hourly"},
                blocking=True,
                return_response=True,
            )
            if response and entity_id in response:
                forecast = response[entity_id].get("forecast", [])
                if forecast:
                    return self._find_shutdown_crossing(forecast, shutdown, now)
        except Exception as exc:
            _LOGGER.debug("Forecast service call failed for %s: %s", entity_id, exc)

        # Fallback: check entity attributes (older HA)
        state = self.hass.states.get(entity_id)
        if state and state.attributes.get("forecast"):
            forecast = state.attributes["forecast"]
            return self._find_shutdown_crossing(forecast, shutdown, now)

        return None

    def _find_shutdown_crossing(
        self, forecast: list[dict], shutdown: float, now: datetime
    ) -> float | None:
        """Scan forecast entries to find when temp first drops below shutdown."""
        for entry in forecast:
            fc_time_str = entry.get("datetime")
            fc_temp = entry.get("temperature")
            if fc_time_str is None or fc_temp is None:
                continue

            # Convert forecast temp to F if needed (check unit)
            try:
                fc_temp_f = float(fc_temp)
            except (ValueError, TypeError):
                continue

            if fc_temp_f < shutdown:
                try:
                    fc_time = datetime.fromisoformat(
                        fc_time_str.replace("Z", "+00:00")
                    )
                    # Strip timezone for comparison with local now
                    fc_time_naive = fc_time.replace(tzinfo=None)
                    minutes_away = (fc_time_naive - now).total_seconds() / 60.0
                except (ValueError, TypeError):
                    continue

                if minutes_away > 0:
                    _LOGGER.debug(
                        "Preheat forecast: %.1f°F < shutdown %.1f°F at %s (%.0f min)",
                        fc_temp_f, shutdown, fc_time_str, minutes_away,
                    )
                    return minutes_away

        return None

    def _compute_preheat_lead_time(self) -> float:
        """Compute how many minutes before shutdown the system needs to start.

        Based on thermal lag and how far below target the floors currently are.
        """
        max_deficit = 0.0
        for thm in self.coordinator.get_thm_devices():
            zone_name = thm.name.lower().replace(" ", "_")
            raw_floor = self._read_zone_floor_temp(zone_name)
            floor_temp = self.effective_floor_temp(zone_name, raw_floor)
            if floor_temp is not None:
                floor_target = self.params.floor_targets.get(zone_name, DEFAULT_FLOOR_TARGET)
                deficit = max(0, floor_target - floor_temp)
                max_deficit = max(max_deficit, deficit)

        # If floors are already at target, still need some lead time for thermal mass
        lead_minutes = self.params.thermal_lag * max(max_deficit, 2.0)
        return lead_minutes

    async def _check_preheat(self, outdoor: float) -> None:
        """Check if preheat should be activated based on forecast/trend."""
        if not self.params.preheat_enabled:
            self._preheat_active = False
            return

        # Only relevant when outdoor is ABOVE shutdown (system would normally be off)
        if outdoor < self.params.shutdown:
            self._preheat_active = False
            return

        self._record_outdoor_temp(outdoor)

        minutes_to_shutdown = await self._estimate_minutes_to_shutdown_async(outdoor)
        if minutes_to_shutdown is None:
            # Can't estimate — not cooling, no preheat needed
            if self._preheat_active:
                _LOGGER.info("Preheat: trend reversed, deactivating")
                self._preheat_active = False
            return

        lead_time = self._compute_preheat_lead_time()

        if minutes_to_shutdown <= lead_time:
            if not self._preheat_active:
                _LOGGER.info(
                    "PREHEAT ACTIVATED: outdoor=%.1f, ETA to shutdown=%.0f min, "
                    "lead_time=%.0f min — starting floor heating now",
                    outdoor, minutes_to_shutdown, lead_time,
                )
                self._preheat_active = True
        else:
            if self._preheat_active:
                _LOGGER.info("Preheat: no longer needed (ETA=%.0f > lead=%.0f), deactivating",
                             minutes_to_shutdown, lead_time)
            self._preheat_active = False

    @property
    def preheat_active(self) -> bool:
        """Whether preheat mode is currently active."""
        return self._preheat_active

    # ------------------------------------------------------------------
    # Hydronic loop monitoring: supply/return temps, delta-T, BTU output
    # ------------------------------------------------------------------

    @property
    def supply_water_actual(self) -> float | None:
        """Read actual supply (input) water temperature from sensor."""
        entity_id = self.params.supply_temp_sensor
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
    def return_water_actual(self) -> float | None:
        """Read actual return (output) water temperature from sensor."""
        entity_id = self.params.return_temp_sensor
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
    def delta_t(self) -> float | None:
        """Temperature drop across the floor loop (supply - return)."""
        supply = self.supply_water_actual
        ret = self.return_water_actual
        if supply is None or ret is None:
            return None
        return round(supply - ret, 1)

    @property
    def actual_flow_rate(self) -> float | None:
        """Read actual system flow rate from configured sensor (GPM)."""
        entity_id = self.params.flow_rate_sensor
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unavailable", "unknown"):
            return None
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return None

    def zone_flow_rate(self, zone_name: str) -> float:
        """Estimated flow rate for a zone based on valve count (GPM)."""
        valves = self.params.zone_valve_counts.get(zone_name, DEFAULT_VALVE_COUNT)
        return valves * self.params.flow_rate_per_valve

    def zone_btu_output(self, zone_name: str) -> float | None:
        """Estimated BTU/hr heat delivery for a zone.

        Formula: BTU/hr = 500 * GPM * delta-T
        (500 factor = 60 min/hr * 8.33 lb/gal * 1 BTU/lb/°F)
        """
        dt = self.delta_t
        if dt is None or dt <= 0:
            return None
        flow = self.zone_flow_rate(zone_name)
        return round(WATER_BTU_FACTOR * flow * dt, 0)

    def total_system_btu(self) -> float | None:
        """Total BTU/hr across all zones currently heating.

        Uses actual flow sensor from tankless if available, otherwise
        sums estimated per-zone flows for active zones.
        """
        dt = self.delta_t
        if dt is None or dt <= 0:
            return None
        actual_flow = self.actual_flow_rate
        if actual_flow is not None:
            if actual_flow <= 0:
                return None
            return round(WATER_BTU_FACTOR * actual_flow * dt, 0)
        total_flow = 0.0
        for thm in self.coordinator.get_thm_devices():
            zone_name = thm.name.lower().replace(" ", "_")
            entity_id = f"climate.{zone_name}_{zone_name}"
            state = self.hass.states.get(entity_id)
            if state and state.state == "heat":
                total_flow += self.zone_flow_rate(zone_name)
        if total_flow == 0:
            return None
        return round(WATER_BTU_FACTOR * total_flow * dt, 0)


class OutdoorResetParams:
    """Stores mutable outdoor reset parameters, updated by number entities."""

    def __init__(self) -> None:
        self.enabled: bool = True
        self.base: float = DEFAULT_BASE
        self.overshoot: float = DEFAULT_OVERSHOOT
        self.shutdown: float = DEFAULT_SHUTDOWN
        self.design_outdoor: float = DEFAULT_DESIGN_OUTDOOR
        self.floor_max: float = DEFAULT_FLOOR_MAX
        self.zone_floor_max: dict[str, float] = {}
        self.zone_floor_sensor_bias: dict[str, float] = {}
        self.zone_offsets: dict[str, float] = {}
        self.floor_control_enabled: dict[str, bool] = {}
        self.floor_targets: dict[str, float] = {}
        self.floor_boost: float = DEFAULT_FLOOR_BOOST
        # Supply water temperature reset
        self.supply_temp_min: float = DEFAULT_SUPPLY_TEMP_MIN
        self.supply_temp_max: float = DEFAULT_SUPPLY_TEMP_MAX
        self.supply_entity_id: str | None = None  # set via text entity or config
        self.supply_control_enabled: bool = False
        self.supply_boost_enabled: bool = True
        # Preheat
        self.preheat_enabled: bool = True
        self.thermal_lag: float = DEFAULT_THERMAL_LAG  # min per degree F
        self.forecast_entity_id: str | None = None  # weather.X entity for hourly forecast
        # Hydronic loop monitoring
        self.supply_temp_sensor: str | None = None  # sensor for water input temp
        self.return_temp_sensor: str | None = None  # sensor for water output temp
        self.flow_rate_sensor: str | None = None  # actual GPM sensor (e.g. from tankless)
        self.zone_valve_counts: dict[str, int] = {}  # zone_name -> number of valves
        self.flow_rate_per_valve: float = DEFAULT_FLOW_RATE_PER_VALVE
        self.electricity_cost_per_kwh: float = DEFAULT_ELECTRICITY_COST
        self.night_setback: NightSetbackParams = NightSetbackParams()


# ---------------------------------------------------------------------------
# Entity setup (called from __init__.py)
# ---------------------------------------------------------------------------

async def async_setup_outdoor_reset(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: SensorlinxCoordinator,
) -> OutdoorResetController:
    """Create the outdoor reset controller and register it."""
    params = OutdoorResetParams()

    # Restore persisted options
    supply_entity = entry.options.get("supply_entity_id")
    if supply_entity:
        params.supply_entity_id = supply_entity
    forecast_entity = entry.options.get("forecast_entity_id")
    if forecast_entity:
        params.forecast_entity_id = forecast_entity
    supply_temp_sensor = entry.options.get("supply_temp_sensor")
    if supply_temp_sensor:
        params.supply_temp_sensor = supply_temp_sensor
    return_temp_sensor = entry.options.get("return_temp_sensor")
    if return_temp_sensor:
        params.return_temp_sensor = return_temp_sensor
    flow_rate_sensor = entry.options.get("flow_rate_sensor")
    if flow_rate_sensor:
        params.flow_rate_sensor = flow_rate_sensor
    electricity_cost = entry.options.get("electricity_cost_per_kwh")
    if electricity_cost is not None:
        try:
            params.electricity_cost_per_kwh = float(electricity_cost)
        except (ValueError, TypeError):
            pass

    # Restore per-zone valve counts and floor mode from options
    for key, value in entry.options.items():
        if key.startswith("zone_valves_"):
            zone_key = key[len("zone_valves_"):]
            try:
                params.zone_valve_counts[zone_key] = int(value)
            except (ValueError, TypeError):
                pass
        elif key.startswith("floor_mode_"):
            zone_key = key[len("floor_mode_"):]
            params.floor_control_enabled[zone_key] = bool(value)
        elif key.startswith("floor_max_"):
            zone_key = key[len("floor_max_"):]
            try:
                params.zone_floor_max[zone_key] = float(value)
            except (ValueError, TypeError):
                pass
        elif key.startswith("floor_bias_"):
            zone_key = key[len("floor_bias_"):]
            try:
                params.zone_floor_sensor_bias[zone_key] = float(value)
            except (ValueError, TypeError):
                pass

    controller = OutdoorResetController(hass, coordinator, params)
    await controller.async_setup()

    # Register services for configuring external entity links
    async def handle_set_supply_entity(call):
        entity_id = call.data.get("entity_id")
        controller.params.supply_entity_id = entity_id
        live_entry = hass.config_entries.async_get_entry(entry.entry_id)
        if live_entry is None:
            _LOGGER.error("Cannot find config entry for options update")
            return
        new_options = dict(live_entry.options)
        new_options["supply_entity_id"] = entity_id
        hass.data.setdefault(DOMAIN, {})[f"{entry.entry_id}_skip_reload"] = True
        hass.config_entries.async_update_entry(live_entry, options=new_options)
        _LOGGER.info("Supply water entity set to: %s", entity_id)

    async def handle_set_forecast_entity(call):
        entity_id = call.data.get("entity_id")
        controller.params.forecast_entity_id = entity_id
        live_entry = hass.config_entries.async_get_entry(entry.entry_id)
        if live_entry is None:
            _LOGGER.error("Cannot find config entry for options update")
            return
        new_options = dict(live_entry.options)
        new_options["forecast_entity_id"] = entity_id
        hass.data.setdefault(DOMAIN, {})[f"{entry.entry_id}_skip_reload"] = True
        hass.config_entries.async_update_entry(live_entry, options=new_options)
        _LOGGER.info("Forecast entity set to: %s", entity_id)

    async def handle_set_hydronic_sensors(call):
        supply_sensor = call.data.get("supply_temp_sensor")
        return_sensor = call.data.get("return_temp_sensor")
        flow_sensor = call.data.get("flow_rate_sensor")
        live_entry = hass.config_entries.async_get_entry(entry.entry_id)
        if live_entry is None:
            _LOGGER.error("Cannot find config entry %s for options update", entry.entry_id)
            return
        new_options = dict(live_entry.options)
        if supply_sensor:
            controller.params.supply_temp_sensor = supply_sensor
            new_options["supply_temp_sensor"] = supply_sensor
        if return_sensor:
            controller.params.return_temp_sensor = return_sensor
            new_options["return_temp_sensor"] = return_sensor
        if flow_sensor:
            controller.params.flow_rate_sensor = flow_sensor
            new_options["flow_rate_sensor"] = flow_sensor
        hass.data.setdefault(DOMAIN, {})[f"{entry.entry_id}_skip_reload"] = True
        hass.config_entries.async_update_entry(live_entry, options=new_options)
        _LOGGER.info(
            "Hydronic sensors set: supply=%s, return=%s, flow=%s",
            supply_sensor, return_sensor, flow_sensor,
        )

    hass.services.async_register(
        DOMAIN, "set_supply_entity", handle_set_supply_entity,
    )
    hass.services.async_register(
        DOMAIN, "set_forecast_entity", handle_set_forecast_entity,
    )
    hass.services.async_register(
        DOMAIN, "set_hydronic_sensors", handle_set_hydronic_sensors,
    )

    async def handle_apply_night_setback(_call):
        await controller._async_apply_night_setback("service call")

    async def handle_restore_day_setback(_call):
        await controller._async_restore_day_setback("service call")

    hass.services.async_register(DOMAIN, "apply_night_setback", handle_apply_night_setback)
    hass.services.async_register(DOMAIN, "restore_day_setback", handle_restore_day_setback)

    return controller


def get_number_entities(
    coordinator: SensorlinxCoordinator,
    controller: OutdoorResetController,
    entry_id: str | None = None,
) -> list[NumberEntity]:
    """Return number entities for outdoor reset parameters."""
    entities: list[NumberEntity] = [
        OutdoorResetNumberEntity(
            coordinator, controller, "heating_curve_base",
            "Heating Curve: Base Temp", DEFAULT_BASE, 65, 75, 0.5,
            "mdi:thermometer-low",
        ),
        OutdoorResetNumberEntity(
            coordinator, controller, "heating_curve_overshoot",
            "Heating Curve: Overshoot", DEFAULT_OVERSHOOT, 0, 12, 0.5,
            "mdi:thermometer-chevron-up",
        ),
        OutdoorResetNumberEntity(
            coordinator, controller, "heating_curve_shutdown",
            "Heating Curve: Shutdown Temp", DEFAULT_SHUTDOWN, 55, 75, 1,
            "mdi:weather-sunny",
        ),
        OutdoorResetNumberEntity(
            coordinator, controller, "heating_curve_design_outdoor",
            "Heating Curve: Design Outdoor", DEFAULT_DESIGN_OUTDOOR, 0, 40, 1,
            "mdi:snowflake",
        ),
        OutdoorResetNumberEntity(
            coordinator, controller, "floor_temp_max",
            "Max Floor Temp (Wood Default)", DEFAULT_FLOOR_MAX, 75, 85, 1,
            "mdi:alert-octagon",
        ),
        OutdoorResetNumberEntity(
            coordinator, controller, "floor_boost",
            "Floor Boost (Cold Weather)", DEFAULT_FLOOR_BOOST, 0, 5, 0.5,
            "mdi:thermometer-chevron-up",
        ),
        OutdoorResetNumberEntity(
            coordinator, controller, "supply_temp_min",
            "Supply Water: Min Temp (Mild)", DEFAULT_SUPPLY_TEMP_MIN, 80, 130, 5,
            "mdi:water-thermometer-outline",
        ),
        OutdoorResetNumberEntity(
            coordinator, controller, "supply_temp_max",
            "Supply Water: Max Temp (Cold)", DEFAULT_SUPPLY_TEMP_MAX, 110, 140, 5,
            "mdi:water-thermometer",
        ),
        OutdoorResetNumberEntity(
            coordinator, controller, "thermal_lag",
            "Preheat: Thermal Lag (min/°F)", DEFAULT_THERMAL_LAG, 5, 60, 5,
            "mdi:clock-fast",
        ),
        OutdoorResetNumberEntity(
            coordinator, controller, "flow_rate_per_valve",
            "Hydronic: Flow Rate per Valve (GPM)", DEFAULT_FLOW_RATE_PER_VALVE, 0.25, 2.0, 0.25,
            "mdi:pipe-valve",
        ),
        ElectricityCostNumberEntity(coordinator, controller),
    ]
    entities.extend(get_night_setback_number_entities(coordinator, controller))

    for thm in coordinator.get_thm_devices():
        zone_name = thm.name.lower().replace(" ", "_")
        entities.append(
            OutdoorResetZoneOffsetEntity(
                coordinator, controller, zone_name,
                f"Zone Offset: {thm.name}", 0, -5, 5, 0.5,
            )
        )
        entities.append(
            ZoneValveCountEntity(
                coordinator, controller, zone_name,
                f"Valve Count: {thm.name}", thm, entry_id,
            )
        )
        zone_cap = default_zone_floor_max(zone_name, controller.params.floor_max)
        cap_slider_max = 95 if zone_name in TILE_FLOOR_ZONES else 85
        entities.append(
            OutdoorResetZoneFloorMaxEntity(
                coordinator, controller, zone_name,
                f"Floor Max (Safety): {thm.name}", zone_cap, 70, cap_slider_max, 1,
                thm, entry_id,
            )
        )
        bias_default = DEFAULT_ZONE_FLOOR_SENSOR_BIAS.get(zone_name, 0.0)
        entities.append(
            OutdoorResetZoneFloorBiasEntity(
                coordinator, controller, zone_name,
                f"Floor Sensor Bias: {thm.name}", bias_default, -15, 5, 0.5,
                thm, entry_id,
            )
        )
        entities.append(
            OutdoorResetFloorTargetEntity(
                coordinator, controller, zone_name,
                f"Floor Target: {thm.name}", DEFAULT_FLOOR_TARGET, 65, zone_cap, 0.5,
                thm, entry_id,
            )
        )

    return entities


def get_sensor_entities(
    coordinator: SensorlinxCoordinator,
    controller: OutdoorResetController,
) -> list[SensorEntity]:
    """Return sensor entities that report the calculated targets."""
    entities: list[SensorEntity] = [
        OutdoorResetTargetSensor(coordinator, controller, "target_setpoint", "Heating Curve Target"),
        SupplyWaterTargetSensor(coordinator, controller),
        PreheatStatusSensor(coordinator, controller),
        HydronicDeltaTSensor(coordinator, controller),
        HydronicBtuSensor(coordinator, controller),
    ]
    for thm in coordinator.get_thm_devices():
        zone_name = thm.name.lower().replace(" ", "_")
        entities.append(
            OutdoorResetZoneTargetSensor(
                coordinator, controller, zone_name, f"Target: {thm.name}",
            )
        )
    return entities


def get_switch_entities(
    coordinator: SensorlinxCoordinator,
    controller: OutdoorResetController,
    entry_id: str | None = None,
) -> list[SwitchEntity]:
    """Return the enable/disable switch and per-zone floor mode switches."""
    entities: list[SwitchEntity] = [
        OutdoorResetEnableSwitch(coordinator, controller),
        SupplyWaterControlSwitch(coordinator, controller),
        SupplyWaterBoostSwitch(coordinator, controller),
        PreheatEnableSwitch(coordinator, controller),
    ]
    entities.extend(get_night_setback_switch_entities(coordinator, controller))
    for thm in coordinator.get_thm_devices():
        zone_name = thm.name.lower().replace(" ", "_")
        entities.append(
            OutdoorResetFloorModeSwitch(
                coordinator, controller, zone_name, thm.name, thm, entry_id,
            )
        )
    return entities


# ---------------------------------------------------------------------------
# Number entities for configurable parameters
# ---------------------------------------------------------------------------

class OutdoorResetNumberEntity(RestoreNumber):
    """Configurable number for outdoor reset parameters."""

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self,
        coordinator: SensorlinxCoordinator,
        controller: OutdoorResetController,
        key: str,
        name: str,
        default: float,
        min_val: float,
        max_val: float,
        step: float,
        icon: str = "mdi:tune-vertical",
    ) -> None:
        self._coordinator = coordinator
        self._controller = controller
        self._key = key
        self._default = default
        self._attr_name = name
        self._attr_native_min_value = min_val
        self._attr_native_max_value = max_val
        self._attr_native_step = step
        self._attr_icon = icon
        self._attr_unique_id = f"sensorlinx_outdoor_reset_{key}"
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
        """Restore last known value."""
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last and last.native_value is not None:
            self._attr_native_value = last.native_value
        self._sync_to_params()

    async def async_set_native_value(self, value: float) -> None:
        """Update the value."""
        self._attr_native_value = value
        self._sync_to_params()
        self.async_write_ha_state()

    def _sync_to_params(self) -> None:
        """Push current value to the controller params."""
        params = self._controller.params
        if self._key == "heating_curve_base":
            params.base = self._attr_native_value
        elif self._key == "heating_curve_overshoot":
            params.overshoot = self._attr_native_value
        elif self._key == "heating_curve_shutdown":
            params.shutdown = self._attr_native_value
        elif self._key == "heating_curve_design_outdoor":
            params.design_outdoor = self._attr_native_value
        elif self._key == "floor_temp_max":
            params.floor_max = self._attr_native_value
        elif self._key == "floor_boost":
            params.floor_boost = self._attr_native_value
        elif self._key == "supply_temp_min":
            params.supply_temp_min = self._attr_native_value
        elif self._key == "supply_temp_max":
            params.supply_temp_max = self._attr_native_value
        elif self._key == "thermal_lag":
            params.thermal_lag = self._attr_native_value
        elif self._key == "flow_rate_per_valve":
            params.flow_rate_per_valve = self._attr_native_value


class ElectricityCostNumberEntity(RestoreNumber):
    """Configurable electricity rate for tankless energy cost reporting."""

    _attr_has_entity_name = True
    _attr_name = "Electricity Cost per kWh"
    _attr_native_unit_of_measurement = "$/kWh"
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:currency-usd"
    _attr_native_min_value = 0.01
    _attr_native_max_value = 2.0
    _attr_native_step = 0.001

    def __init__(
        self,
        coordinator: SensorlinxCoordinator,
        controller: OutdoorResetController,
    ) -> None:
        self._coordinator = coordinator
        self._controller = controller
        self._attr_unique_id = "sensorlinx_outdoor_reset_electricity_cost_per_kwh"
        self._attr_native_value = controller.params.electricity_cost_per_kwh

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, "outdoor_reset")},
            "name": "SensorLinx Outdoor Reset",
            "manufacturer": "HBX Controls",
            "model": "Heating Curve Controller",
        }

    async def async_added_to_hass(self) -> None:
        """Restore last known value."""
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last and last.native_value is not None:
            self._attr_native_value = last.native_value
        self._controller.params.electricity_cost_per_kwh = self._attr_native_value

    async def async_set_native_value(self, value: float) -> None:
        """Update the electricity rate."""
        self._attr_native_value = value
        self._controller.params.electricity_cost_per_kwh = value
        self.async_write_ha_state()


class OutdoorResetZoneOffsetEntity(RestoreNumber):
    """Per-zone offset number entity."""

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self,
        coordinator: SensorlinxCoordinator,
        controller: OutdoorResetController,
        zone_key: str,
        name: str,
        default: float,
        min_val: float,
        max_val: float,
        step: float,
    ) -> None:
        self._coordinator = coordinator
        self._controller = controller
        self._zone_key = zone_key
        self._default = default
        self._attr_name = name
        self._attr_native_min_value = min_val
        self._attr_native_max_value = max_val
        self._attr_native_step = step
        self._attr_icon = "mdi:tune-vertical"
        self._attr_unique_id = f"sensorlinx_outdoor_reset_offset_{zone_key}"
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
        self._controller.params.zone_offsets[self._zone_key] = self._attr_native_value

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self._controller.params.zone_offsets[self._zone_key] = value
        self.async_write_ha_state()


class OutdoorResetZoneFloorMaxEntity(RestoreNumber):
    """Per-zone floor temperature safety cap (wood vs tile)."""

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:alert-octagon"

    def __init__(
        self,
        coordinator: SensorlinxCoordinator,
        controller: OutdoorResetController,
        zone_key: str,
        name: str,
        default: float,
        min_val: float,
        max_val: float,
        step: float,
        thm_device: SensorlinxDeviceData | None = None,
        entry_id: str | None = None,
    ) -> None:
        self._coordinator = coordinator
        self._controller = controller
        self._zone_key = zone_key
        self._thm_device = thm_device
        self._entry_id = entry_id
        self._default = default
        self._attr_name = name
        self._attr_native_min_value = min_val
        self._attr_native_max_value = max_val
        self._attr_native_step = step
        self._attr_unique_id = f"sensorlinx_outdoor_reset_floor_max_{zone_key}"
        self._attr_native_value = default

    @property
    def device_info(self) -> dict[str, Any]:
        if self._thm_device:
            return thm_device_info(self._coordinator, self._thm_device)
        return {
            "identifiers": {(DOMAIN, "outdoor_reset")},
            "name": "SensorLinx Outdoor Reset",
            "manufacturer": "HBX Controls",
            "model": "Heating Curve Controller",
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._entry_id:
            entry = self.hass.config_entries.async_get_entry(self._entry_id)
            if entry:
                key = f"floor_max_{self._zone_key}"
                saved = entry.options.get(key)
                if saved is not None:
                    self._attr_native_value = float(saved)
        if self._attr_native_value == self._default:
            last = await self.async_get_last_number_data()
            if last and last.native_value is not None:
                self._attr_native_value = last.native_value
        self._controller.params.zone_floor_max[self._zone_key] = self._attr_native_value

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self._controller.params.zone_floor_max[self._zone_key] = value
        self.async_write_ha_state()
        self._persist_to_options(value)

    @callback
    def _persist_to_options(self, value: float) -> None:
        if not self._entry_id:
            return
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None:
            return
        key = f"floor_max_{self._zone_key}"
        new_options = dict(entry.options)
        new_options[key] = value
        self.hass.data.setdefault(DOMAIN, {})[f"{self._entry_id}_skip_reload"] = True
        self.hass.config_entries.async_update_entry(entry, options=new_options)


class OutdoorResetZoneFloorBiasEntity(RestoreNumber):
    """Per-zone floor sensor correction (negative if probe is near loop supply)."""

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:thermometer-minus"

    def __init__(
        self,
        coordinator: SensorlinxCoordinator,
        controller: OutdoorResetController,
        zone_key: str,
        name: str,
        default: float,
        min_val: float,
        max_val: float,
        step: float,
        thm_device: SensorlinxDeviceData | None = None,
        entry_id: str | None = None,
    ) -> None:
        self._coordinator = coordinator
        self._controller = controller
        self._zone_key = zone_key
        self._thm_device = thm_device
        self._entry_id = entry_id
        self._default = default
        self._attr_name = name
        self._attr_native_min_value = min_val
        self._attr_native_max_value = max_val
        self._attr_native_step = step
        self._attr_unique_id = f"sensorlinx_outdoor_reset_floor_bias_{zone_key}"
        self._attr_native_value = default

    @property
    def device_info(self) -> dict[str, Any]:
        if self._thm_device:
            return thm_device_info(self._coordinator, self._thm_device)
        return {
            "identifiers": {(DOMAIN, "outdoor_reset")},
            "name": "SensorLinx Outdoor Reset",
            "manufacturer": "HBX Controls",
            "model": "Heating Curve Controller",
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._entry_id:
            entry = self.hass.config_entries.async_get_entry(self._entry_id)
            if entry:
                key = f"floor_bias_{self._zone_key}"
                saved = entry.options.get(key)
                if saved is not None:
                    self._attr_native_value = float(saved)
        if self._attr_native_value == self._default:
            last = await self.async_get_last_number_data()
            if last and last.native_value is not None:
                self._attr_native_value = last.native_value
        self._controller.params.zone_floor_sensor_bias[self._zone_key] = (
            self._attr_native_value
        )

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self._controller.params.zone_floor_sensor_bias[self._zone_key] = value
        self.async_write_ha_state()
        self._persist_to_options(value)

    @callback
    def _persist_to_options(self, value: float) -> None:
        if not self._entry_id:
            return
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None:
            return
        key = f"floor_bias_{self._zone_key}"
        new_options = dict(entry.options)
        new_options[key] = value
        self.hass.data.setdefault(DOMAIN, {})[f"{self._entry_id}_skip_reload"] = True
        self.hass.config_entries.async_update_entry(entry, options=new_options)


# ---------------------------------------------------------------------------
# Sensor entities for computed targets
# ---------------------------------------------------------------------------

class OutdoorResetTargetSensor(SensorEntity):
    """Displays the calculated heating curve target setpoint."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
    _attr_state_class = "measurement"
    _attr_icon = "mdi:thermostat"

    def __init__(
        self,
        coordinator: SensorlinxCoordinator,
        controller: OutdoorResetController,
        key: str,
        name: str,
    ) -> None:
        self._coordinator = coordinator
        self._controller = controller
        self._attr_name = name
        self._attr_unique_id = f"sensorlinx_outdoor_reset_{key}"

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, "outdoor_reset")},
            "name": "SensorLinx Outdoor Reset",
            "manufacturer": "HBX Controls",
            "model": "Heating Curve Controller",
        }

    @property
    def native_value(self) -> float:
        return self._controller.calculated_target

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "outdoor_temp": self._controller.outdoor_temp,
            "base": self._controller.params.base,
            "overshoot": self._controller.params.overshoot,
            "shutdown": self._controller.params.shutdown,
            "design_outdoor": self._controller.params.design_outdoor,
        }


class OutdoorResetZoneTargetSensor(SensorEntity):
    """Displays the per-zone target setpoint (base + offset)."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
    _attr_state_class = "measurement"
    _attr_icon = "mdi:thermostat"

    def __init__(
        self,
        coordinator: SensorlinxCoordinator,
        controller: OutdoorResetController,
        zone_key: str,
        name: str,
    ) -> None:
        self._coordinator = coordinator
        self._controller = controller
        self._zone_key = zone_key
        self._attr_name = name
        self._attr_unique_id = f"sensorlinx_outdoor_reset_target_{zone_key}"

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, "outdoor_reset")},
            "name": "SensorLinx Outdoor Reset",
            "manufacturer": "HBX Controls",
            "model": "Heating Curve Controller",
        }

    @property
    def native_value(self) -> float:
        offset = self._controller.params.zone_offsets.get(self._zone_key, 0.0)
        return self._controller.zone_target(offset)


# ---------------------------------------------------------------------------
# Switch entity for enable/disable
# ---------------------------------------------------------------------------

class OutdoorResetEnableSwitch(SwitchEntity):
    """Enable or disable the outdoor reset system."""

    _attr_has_entity_name = True
    _attr_name = "Outdoor Reset Enabled"
    _attr_icon = "mdi:thermostat-auto"

    def __init__(
        self,
        coordinator: SensorlinxCoordinator,
        controller: OutdoorResetController,
    ) -> None:
        self._coordinator = coordinator
        self._controller = controller
        self._attr_unique_id = "sensorlinx_outdoor_reset_enabled"

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
        return self._controller.params.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._controller.params.enabled = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._controller.params.enabled = False
        self.async_write_ha_state()


class OutdoorResetFloorModeSwitch(SwitchEntity, RestoreEntity):
    """Per-zone switch: when ON, targets floor temperature instead of room temperature."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:heat-wave"

    def __init__(
        self,
        coordinator: SensorlinxCoordinator,
        controller: OutdoorResetController,
        zone_key: str,
        zone_name: str,
        thm_device: SensorlinxDeviceData | None = None,
        entry_id: str | None = None,
    ) -> None:
        self._coordinator = coordinator
        self._controller = controller
        self._zone_key = zone_key
        self._thm_device = thm_device
        self._entry_id = entry_id
        self._attr_name = f"Floor Control Mode: {zone_name}"
        self._attr_unique_id = f"sensorlinx_outdoor_reset_floor_mode_{zone_key}"

    @property
    def device_info(self) -> dict[str, Any]:
        if self._thm_device:
            return thm_device_info(self._coordinator, self._thm_device)
        return {
            "identifiers": {(DOMAIN, "outdoor_reset")},
            "name": "SensorLinx Outdoor Reset",
            "manufacturer": "HBX Controls",
            "model": "Heating Curve Controller",
        }

    @property
    def is_on(self) -> bool:
        return self._controller.params.floor_control_enabled.get(self._zone_key, False)

    async def async_added_to_hass(self) -> None:
        """Restore floor mode state from config entry options."""
        await super().async_added_to_hass()
        if self._entry_id:
            entry = self.hass.config_entries.async_get_entry(self._entry_id)
            if entry:
                key = f"floor_mode_{self._zone_key}"
                saved = entry.options.get(key)
                if saved is not None:
                    self._controller.params.floor_control_enabled[self._zone_key] = bool(saved)

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._controller.params.floor_control_enabled[self._zone_key] = True
        self.async_write_ha_state()
        async_dispatcher_send(self.hass, SIGNAL_FLOOR_MODE_CHANGED, self._zone_key)
        self._persist_to_options(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._controller.params.floor_control_enabled[self._zone_key] = False
        self.async_write_ha_state()
        async_dispatcher_send(self.hass, SIGNAL_FLOOR_MODE_CHANGED, self._zone_key)
        self._persist_to_options(False)

    @callback
    def _persist_to_options(self, enabled: bool) -> None:
        """Save floor mode state to config entry options."""
        if not self._entry_id:
            return
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None:
            return
        key = f"floor_mode_{self._zone_key}"
        new_options = dict(entry.options)
        new_options[key] = enabled
        self.hass.data.setdefault(DOMAIN, {})[f"{self._entry_id}_skip_reload"] = True
        self.hass.config_entries.async_update_entry(entry, options=new_options)


class OutdoorResetFloorTargetEntity(RestoreNumber):
    """Per-zone floor temperature target (used when floor control mode is ON)."""

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:heat-wave"

    def __init__(
        self,
        coordinator: SensorlinxCoordinator,
        controller: OutdoorResetController,
        zone_key: str,
        name: str,
        default: float,
        min_val: float,
        max_val: float,
        step: float,
        thm_device: SensorlinxDeviceData | None = None,
        entry_id: str | None = None,
    ) -> None:
        self._coordinator = coordinator
        self._controller = controller
        self._zone_key = zone_key
        self._thm_device = thm_device
        self._entry_id = entry_id
        self._default = default
        self._attr_name = name
        self._attr_native_min_value = min_val
        self._attr_native_max_value = max_val
        self._attr_native_step = step
        self._attr_unique_id = f"sensorlinx_outdoor_reset_floor_target_{zone_key}"
        self._attr_native_value = default

    @property
    def device_info(self) -> dict[str, Any]:
        if self._thm_device:
            return thm_device_info(self._coordinator, self._thm_device)
        return {
            "identifiers": {(DOMAIN, "outdoor_reset")},
            "name": "SensorLinx Outdoor Reset",
            "manufacturer": "HBX Controls",
            "model": "Heating Curve Controller",
        }

    @property
    def available(self) -> bool:
        """Only available when floor control mode is enabled for this zone."""
        return self._controller.params.floor_control_enabled.get(self._zone_key, False)

    @callback
    def _handle_floor_mode_changed(self, zone_key: str) -> None:
        """Refresh state when floor mode toggles for this zone."""
        if zone_key == self._zone_key:
            self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Prioritize config entry options (survives unavailable state)
        if self._entry_id:
            entry = self.hass.config_entries.async_get_entry(self._entry_id)
            if entry:
                key = f"floor_target_{self._zone_key}"
                saved = entry.options.get(key)
                if saved is not None:
                    self._attr_native_value = float(saved)
        if self._attr_native_value == self._default:
            last = await self.async_get_last_number_data()
            if last and last.native_value is not None:
                self._attr_native_value = last.native_value
        self._controller.params.floor_targets[self._zone_key] = self._attr_native_value
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_FLOOR_MODE_CHANGED, self._handle_floor_mode_changed
            )
        )

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self._controller.params.floor_targets[self._zone_key] = value
        self.async_write_ha_state()
        self._persist_to_options(value)

    @callback
    def _persist_to_options(self, value: float) -> None:
        """Save floor target to config entry options."""
        if not self._entry_id:
            return
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None:
            return
        key = f"floor_target_{self._zone_key}"
        new_options = dict(entry.options)
        new_options[key] = value
        self.hass.data.setdefault(DOMAIN, {})[f"{self._entry_id}_skip_reload"] = True
        self.hass.config_entries.async_update_entry(entry, options=new_options)


# ---------------------------------------------------------------------------
# Supply water temperature reset entities
# ---------------------------------------------------------------------------

class SupplyWaterControlSwitch(SwitchEntity):
    """Enable/disable automatic supply water temperature adjustment."""

    _attr_has_entity_name = True
    _attr_name = "Supply Water Reset Enabled"
    _attr_icon = "mdi:water-boiler"

    def __init__(
        self,
        coordinator: SensorlinxCoordinator,
        controller: OutdoorResetController,
    ) -> None:
        self._coordinator = coordinator
        self._controller = controller
        self._attr_unique_id = "sensorlinx_supply_water_control_enabled"

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
        return self._controller.params.supply_control_enabled

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "supply_entity_id": self._controller.params.supply_entity_id or "not configured",
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._controller.params.supply_control_enabled = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._controller.params.supply_control_enabled = False
        self.async_write_ha_state()


class SupplyWaterBoostSwitch(SwitchEntity):
    """Enable fast heat-up boost when a zone first calls for heat."""

    _attr_has_entity_name = True
    _attr_name = "Supply Water Boost Enabled"
    _attr_icon = "mdi:rocket-launch"

    def __init__(
        self,
        coordinator: SensorlinxCoordinator,
        controller: OutdoorResetController,
    ) -> None:
        self._coordinator = coordinator
        self._controller = controller
        self._attr_unique_id = "sensorlinx_supply_water_boost_enabled"

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
        return self._controller.params.supply_boost_enabled

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        ctrl = self._controller
        return {
            "boost_active": ctrl.supply_boost_active,
            "boost_optimal_target": ctrl._supply_boost_optimal,
            "boost_setpoint": ctrl._supply_boost_target,
            "delta_t": ctrl.delta_t,
            "target_delta_t": BOOST_TARGET_DELTA_T,
            "fast_scan_seconds": TANKLESS_FAST_SCAN_INTERVAL,
            "normal_scan_seconds": TANKLESS_NORMAL_SCAN_INTERVAL,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._controller.params.supply_boost_enabled = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._controller.params.supply_boost_enabled = False
        if self._controller.supply_boost_active:
            await self._controller._async_end_supply_boost(cancelled=True)
        self.async_write_ha_state()


class SupplyWaterTargetSensor(SensorEntity):
    """Displays the calculated supply water temperature target."""

    _attr_has_entity_name = True
    _attr_name = "Supply Water Target"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
    _attr_state_class = "measurement"
    _attr_icon = "mdi:water-thermometer"

    def __init__(
        self,
        coordinator: SensorlinxCoordinator,
        controller: OutdoorResetController,
    ) -> None:
        self._coordinator = coordinator
        self._controller = controller
        self._attr_unique_id = "sensorlinx_supply_water_target"

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, "outdoor_reset")},
            "name": "SensorLinx Outdoor Reset",
            "manufacturer": "HBX Controls",
            "model": "Heating Curve Controller",
        }

    @property
    def native_value(self) -> float | None:
        outdoor = self._controller.outdoor_temp
        if outdoor is None:
            return None
        return self._controller._compute_supply_water_temp(outdoor)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "supply_min": self._controller.params.supply_temp_min,
            "supply_max": self._controller._effective_supply_max(),
            "tankless_max": TANKLESS_MAX_TEMP,
            "supply_entity_id": self._controller.params.supply_entity_id or "not configured",
            "supply_control_enabled": self._controller.params.supply_control_enabled,
            "supply_boost_enabled": self._controller.params.supply_boost_enabled,
            "supply_boost_active": self._controller.supply_boost_active,
            "boost_optimal_target": self._controller._supply_boost_optimal,
            "boost_setpoint": self._controller._supply_boost_target,
            "inlet_temp": self._controller.return_water_actual,
            "delta_t": self._controller.delta_t,
            "target_delta_t": BOOST_TARGET_DELTA_T,
        }


# ---------------------------------------------------------------------------
# Preheat entities
# ---------------------------------------------------------------------------

class PreheatEnableSwitch(SwitchEntity):
    """Enable/disable predictive preheat."""

    _attr_has_entity_name = True
    _attr_name = "Preheat Enabled"
    _attr_icon = "mdi:clock-start"

    def __init__(
        self,
        coordinator: SensorlinxCoordinator,
        controller: OutdoorResetController,
    ) -> None:
        self._coordinator = coordinator
        self._controller = controller
        self._attr_unique_id = "sensorlinx_preheat_enabled"

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
        return self._controller.params.preheat_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._controller.params.preheat_enabled = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._controller.params.preheat_enabled = False
        self.async_write_ha_state()


class PreheatStatusSensor(SensorEntity):
    """Shows preheat status and estimated time to shutdown."""

    _attr_has_entity_name = True
    _attr_name = "Preheat Status"
    _attr_icon = "mdi:clock-fast"

    def __init__(
        self,
        coordinator: SensorlinxCoordinator,
        controller: OutdoorResetController,
    ) -> None:
        self._coordinator = coordinator
        self._controller = controller
        self._attr_unique_id = "sensorlinx_preheat_status"

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, "outdoor_reset")},
            "name": "SensorLinx Outdoor Reset",
            "manufacturer": "HBX Controls",
            "model": "Heating Curve Controller",
        }

    @property
    def native_value(self) -> str:
        if not self._controller.params.preheat_enabled:
            return "disabled"
        if self._controller.preheat_active:
            return "active"
        outdoor = self._controller.outdoor_temp
        if outdoor is None:
            return "unknown"
        if outdoor < self._controller.params.shutdown:
            return "heating"
        return "standby"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        outdoor = self._controller.outdoor_temp
        attrs: dict[str, Any] = {
            "preheat_active": self._controller.preheat_active,
            "thermal_lag_min_per_deg": self._controller.params.thermal_lag,
            "temp_history_points": len(self._controller._temp_history),
        }
        if outdoor is not None and outdoor >= self._controller.params.shutdown:
            eta = self._controller._estimate_minutes_to_shutdown(outdoor)
            lead = self._controller._compute_preheat_lead_time()
            attrs["eta_to_shutdown_min"] = round(eta, 0) if eta is not None else None
            attrs["preheat_lead_time_min"] = round(lead, 0)
        return attrs


# ---------------------------------------------------------------------------
# Hydronic loop monitoring entities
# ---------------------------------------------------------------------------

class ZoneValveCountEntity(RestoreNumber):
    """Per-zone valve count for flow rate estimation (visible when floor mode is ON)."""

    _attr_has_entity_name = True
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:pipe-valve"

    def __init__(
        self,
        coordinator: SensorlinxCoordinator,
        controller: OutdoorResetController,
        zone_key: str,
        name: str,
        thm_device: SensorlinxDeviceData | None = None,
        entry_id: str | None = None,
    ) -> None:
        self._coordinator = coordinator
        self._controller = controller
        self._zone_key = zone_key
        self._thm_device = thm_device
        self._entry_id = entry_id
        self._attr_name = name
        self._attr_native_min_value = 1
        self._attr_native_max_value = 6
        self._attr_native_step = 1
        self._attr_unique_id = f"sensorlinx_zone_valve_count_{zone_key}"
        self._attr_native_value = DEFAULT_VALVE_COUNT

    @property
    def device_info(self) -> dict[str, Any]:
        if self._thm_device:
            return thm_device_info(self._coordinator, self._thm_device)
        return {
            "identifiers": {(DOMAIN, "outdoor_reset")},
            "name": "SensorLinx Outdoor Reset",
            "manufacturer": "HBX Controls",
            "model": "Heating Curve Controller",
        }

    @property
    def available(self) -> bool:
        """Only available when floor control mode is enabled for this zone."""
        return self._controller.params.floor_control_enabled.get(self._zone_key, False)

    @callback
    def _handle_floor_mode_changed(self, zone_key: str) -> None:
        """Refresh state when floor mode toggles for this zone."""
        if zone_key == self._zone_key:
            self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Prioritize config entry options (survives unavailable state)
        options_value = self._controller.params.zone_valve_counts.get(self._zone_key)
        if options_value is not None and options_value != DEFAULT_VALVE_COUNT:
            self._attr_native_value = float(options_value)
        else:
            last = await self.async_get_last_number_data()
            if last and last.native_value is not None:
                self._attr_native_value = last.native_value
        self._controller.params.zone_valve_counts[self._zone_key] = int(self._attr_native_value)
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_FLOOR_MODE_CHANGED, self._handle_floor_mode_changed
            )
        )

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self._controller.params.zone_valve_counts[self._zone_key] = int(value)
        self.async_write_ha_state()
        self._persist_to_options(int(value))

    @callback
    def _persist_to_options(self, value: int) -> None:
        """Save valve count to config entry options for reliable persistence."""
        if not self._entry_id:
            return
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None:
            return
        key = f"zone_valves_{self._zone_key}"
        new_options = dict(entry.options)
        new_options[key] = value
        self.hass.data.setdefault(DOMAIN, {})[f"{self._entry_id}_skip_reload"] = True
        self.hass.config_entries.async_update_entry(entry, options=new_options)


class HydronicDeltaTSensor(SensorEntity):
    """Shows the supply-return temperature differential (delta-T)."""

    _attr_has_entity_name = True
    _attr_name = "Hydronic Delta-T"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
    _attr_state_class = "measurement"
    _attr_icon = "mdi:thermometer-water"

    def __init__(
        self,
        coordinator: SensorlinxCoordinator,
        controller: OutdoorResetController,
    ) -> None:
        self._coordinator = coordinator
        self._controller = controller
        self._attr_unique_id = "sensorlinx_hydronic_delta_t"

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, "outdoor_reset")},
            "name": "SensorLinx Outdoor Reset",
            "manufacturer": "HBX Controls",
            "model": "Heating Curve Controller",
        }

    @property
    def native_value(self) -> float | None:
        return self._controller.delta_t

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "supply_temp": self._controller.supply_water_actual,
            "return_temp": self._controller.return_water_actual,
            "supply_sensor": self._controller.params.supply_temp_sensor or "not configured",
            "return_sensor": self._controller.params.return_temp_sensor or "not configured",
            "flow_rate_sensor": self._controller.params.flow_rate_sensor or "not configured",
            "actual_flow_gpm": self._controller.actual_flow_rate,
        }


class HydronicBtuSensor(SensorEntity):
    """Shows estimated total BTU/hr heat delivery across active zones."""

    _attr_has_entity_name = True
    _attr_name = "Hydronic Heat Output"
    _attr_native_unit_of_measurement = "BTU/hr"
    _attr_state_class = "measurement"
    _attr_icon = "mdi:fire"

    def __init__(
        self,
        coordinator: SensorlinxCoordinator,
        controller: OutdoorResetController,
    ) -> None:
        self._coordinator = coordinator
        self._controller = controller
        self._attr_unique_id = "sensorlinx_hydronic_btu_output"

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, "outdoor_reset")},
            "name": "SensorLinx Outdoor Reset",
            "manufacturer": "HBX Controls",
            "model": "Heating Curve Controller",
        }

    @property
    def native_value(self) -> float | None:
        return self._controller.total_system_btu()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        actual_flow = self._controller.actual_flow_rate
        using_actual = actual_flow is not None
        attrs: dict[str, Any] = {
            "delta_t": self._controller.delta_t,
            "flow_source": "actual (tankless sensor)" if using_actual else "estimated (valve count)",
            "actual_flow_gpm": actual_flow,
            "flow_rate_per_valve_gpm": self._controller.params.flow_rate_per_valve,
        }
        for thm in self._coordinator.get_thm_devices():
            zone_name = thm.name.lower().replace(" ", "_")
            valves = self._controller.params.zone_valve_counts.get(zone_name, DEFAULT_VALVE_COUNT)
            flow = self._controller.zone_flow_rate(zone_name)
            btu = self._controller.zone_btu_output(zone_name)
            attrs[f"{zone_name}_valves"] = valves
            attrs[f"{zone_name}_flow_gpm"] = flow
            attrs[f"{zone_name}_btu_hr"] = btu
        return attrs
