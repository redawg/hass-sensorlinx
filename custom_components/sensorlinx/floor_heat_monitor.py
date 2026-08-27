"""Ecoflow Ch 1/3 floor heat source + Tapo pump power monitoring.

Heat source (240 V tankless): EcoFlow Power Ocean (Forest) Heater Floor Water Heater (Ch 1/3).
Controller + pumps: Tapo ``switch.radiant_floor_contoller`` (current × voltage).

Guards against stale Ecoflow power readings (frozen W while energy counter still climbs).
Daily kWh resets at Pacific midnight to align with the Ecoflow app.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.recorder import history
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

PACIFIC = ZoneInfo("America/Los_Angeles")
POWER_STALE_AFTER = timedelta(minutes=30)
INTEGRATE_INTERVAL = timedelta(minutes=1)

ECOFLOW_HEAT_POWER = (
    "sensor.ecoflow_power_ocean_forest_heater_floor_water_heater_ch_1_3_power"
)
ECOFLOW_HEAT_ENERGY = (
    "sensor.ecoflow_power_ocean_forest_heater_floor_water_heater_ch_1_3_energy"
)
TAPO_RADIANT_CURRENT = "sensor.radiant_floor_contoller_current"
TAPO_RADIANT_VOLTAGE = "sensor.radiant_floor_contoller_voltage"

# Canonical HA entity_id suffixes (has_entity_name=False → stable IDs for YAML/reports)
ENTITY_FLOOR_HEAT_POWER_GUARDED = "sensor.sensorlinx_floor_heat_ecoflow_power_guarded"
ENTITY_FLOOR_HEAT_ENERGY_TODAY = "sensor.sensorlinx_floor_heat_source_energy_today"
ENTITY_FLOOR_HEAT_ECOFLOW_ENERGY_TODAY = "sensor.sensorlinx_floor_heat_ecoflow_energy_today"
ENTITY_FLOOR_HEAT_PUMPS_POWER = "sensor.sensorlinx_floor_heat_pumps_power"
ENTITY_FLOOR_HEAT_POWER_STALE = "binary_sensor.sensorlinx_floor_heat_ecoflow_power_stale"


@dataclass
class FloorHeatSnapshot:
    """Latest floor heat power/energy evaluation."""

    checked_at: datetime | None = None
    power_w: float | None = None
    power_stale: bool = False
    power_stale_seconds: float | None = None
    pumps_power_w: float | None = None
    integrated_kwh_today: float = 0.0
    ecoflow_counter_kwh: float | None = None
    ecoflow_baseline_kwh: float | None = None
    ecoflow_delta_kwh: float | None = None
    pacific_day: str | None = None
    last_reason: str = "startup"


class FloorHeatMonitor:
    """Track guarded Ecoflow power and Pacific-day energy."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.snapshot = FloorHeatSnapshot()
        self._unsub: list[Callable[[], None]] = []
        self._entities: list[Any] = []
        self._last_integrate_at: datetime | None = None
        self._pacific_day: str | None = None

    async def async_setup(self) -> None:
        """Start listeners and periodic integration."""
        watch = [
            ECOFLOW_HEAT_POWER,
            ECOFLOW_HEAT_ENERGY,
            TAPO_RADIANT_CURRENT,
            TAPO_RADIANT_VOLTAGE,
        ]
        self._unsub.append(
            async_track_state_change_event(hass=self.hass, entity_ids=watch, action=self._async_on_state)
        )
        self._unsub.append(
            async_track_time_interval(
                self.hass, self._async_integrate_tick, INTEGRATE_INTERVAL
            )
        )
        await self._async_reset_pacific_day_if_needed(datetime.now(PACIFIC))
        await self._async_backfill_integrated_today(datetime.now(PACIFIC).date())
        self._evaluate(reason="startup", integrate=False)

    def async_unload(self) -> None:
        """Remove listeners."""
        for unsub in self._unsub:
            unsub()
        self._unsub.clear()

    def register_entity(self, entity: Any) -> None:
        """Track entities that refresh when snapshot updates."""
        self._entities.append(entity)

    @callback
    def _async_on_state(self, event: Event) -> None:
        """Re-evaluate on watched entity changes."""
        entity_id = event.data.get("entity_id")
        self._evaluate(reason=f"state:{entity_id}", integrate=False)

    async def _async_integrate_tick(self, _now: datetime | None = None) -> None:
        """Integrate guarded power into today's kWh."""
        now = (_now or datetime.now()).astimezone(PACIFIC)
        await self._async_reset_pacific_day_if_needed(now)
        self._evaluate(reason="interval", integrate=True)

    async def _async_reset_pacific_day_if_needed(self, now_pacific: datetime) -> None:
        """Reset daily counters at Pacific midnight."""
        day = now_pacific.date().isoformat()
        if self._pacific_day == day and self.snapshot.ecoflow_baseline_kwh is not None:
            return
        self._pacific_day = day
        self._last_integrate_at = None
        self.snapshot.integrated_kwh_today = 0.0
        baseline = await self._async_baseline_at_pacific_midnight(now_pacific.date())
        if baseline is None:
            baseline = self._read_float(ECOFLOW_HEAT_ENERGY)
        self.snapshot.ecoflow_baseline_kwh = baseline
        self.snapshot.pacific_day = day
        _LOGGER.info(
            "Floor heat energy day reset (%s Pacific); Ecoflow baseline=%.3f kWh",
            day,
            baseline or 0.0,
        )

    async def _async_baseline_at_pacific_midnight(self, day: date) -> float | None:
        """Read Ecoflow counter near Pacific midnight from recorder history."""
        midnight_pt = datetime.combine(day, time.min, tzinfo=PACIFIC)
        start = midnight_pt.astimezone(timezone.utc)
        end = start + timedelta(hours=1)

        def _read_history() -> float | None:
            states_map = history.get_significant_states(
                self.hass,
                start,
                end,
                [ECOFLOW_HEAT_ENERGY],
                include_start_time_state=True,
            )
            states = states_map.get(ECOFLOW_HEAT_ENERGY, [])
            for state in states:
                if state.state in ("unavailable", "unknown"):
                    continue
                try:
                    return float(state.state)
                except (TypeError, ValueError):
                    continue
            return None

        try:
            return await self.hass.async_add_executor_job(_read_history)
        except Exception as err:  # noqa: BLE001 — history may be unavailable briefly
            _LOGGER.warning("Floor heat history baseline failed: %s", err)
            return None

    async def _async_backfill_integrated_today(self, day: date) -> None:
        """Integrate guarded Ecoflow power history since Pacific midnight."""
        midnight_pt = datetime.combine(day, time.min, tzinfo=PACIFIC)
        start = midnight_pt.astimezone(timezone.utc)
        end = datetime.now(timezone.utc)

        def _integrate() -> float:
            states_map = history.get_significant_states(
                self.hass,
                start,
                end,
                [ECOFLOW_HEAT_POWER],
                include_start_time_state=True,
            )
            states = states_map.get(ECOFLOW_HEAT_POWER, [])
            total_kwh = 0.0
            prev_time = None
            prev_power = None
            for state in states:
                if state.state in ("unavailable", "unknown"):
                    continue
                try:
                    power_w = float(state.state)
                except (TypeError, ValueError):
                    continue
                ts = state.last_changed
                if (
                    prev_time is not None
                    and prev_power is not None
                    and prev_power >= 1.0
                ):
                    gap_s = (ts - prev_time).total_seconds()
                    if 0 < gap_s <= POWER_STALE_AFTER.total_seconds():
                        total_kwh += prev_power * (gap_s / 3600.0) / 1000.0
                prev_time = ts
                prev_power = power_w
            return round(total_kwh, 3)

        try:
            integrated = await self.hass.async_add_executor_job(_integrate)
            self.snapshot.integrated_kwh_today = integrated
            self._last_integrate_at = datetime.now(PACIFIC)
            _LOGGER.info("Floor heat integrated backfill (%s Pacific): %.3f kWh", day, integrated)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Floor heat integration backfill failed: %s", err)

    def _read_float(self, entity_id: str) -> float | None:
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unavailable", "unknown"):
            return None
        try:
            return float(state.state)
        except (TypeError, ValueError):
            return None

    def _power_staleness(self) -> tuple[bool, float | None]:
        """Return (stale, seconds_since_update)."""
        state = self.hass.states.get(ECOFLOW_HEAT_POWER)
        if state is None or state.last_updated is None:
            return True, None
        age = datetime.now(state.last_updated.tzinfo) - state.last_updated
        seconds = age.total_seconds()
        return seconds > POWER_STALE_AFTER.total_seconds(), seconds

    def _read_guarded_power_w(self) -> float | None:
        stale, _ = self._power_staleness()
        if stale:
            return None
        return self._read_float(ECOFLOW_HEAT_POWER)

    def _read_pumps_power_w(self) -> float | None:
        current = self._read_float(TAPO_RADIANT_CURRENT)
        voltage = self._read_float(TAPO_RADIANT_VOLTAGE)
        if current is None or voltage is None:
            return None
        return round(current * voltage, 1)

    def _evaluate(self, *, reason: str, integrate: bool) -> None:
        """Refresh snapshot; optionally integrate power since last tick."""
        now = datetime.now().astimezone(PACIFIC)
        stale, stale_seconds = self._power_staleness()
        power_w = self._read_guarded_power_w()

        if integrate and not stale and power_w is not None and power_w >= 1.0:
            if self._last_integrate_at is not None:
                dt_h = (now - self._last_integrate_at).total_seconds() / 3600.0
                if 0 < dt_h < 2.0:
                    self.snapshot.integrated_kwh_today += power_w * dt_h / 1000.0
            self._last_integrate_at = now

        ecoflow_counter = self._read_float(ECOFLOW_HEAT_ENERGY)
        baseline = self.snapshot.ecoflow_baseline_kwh
        ecoflow_delta = self.snapshot.ecoflow_delta_kwh
        if (
            not stale
            and ecoflow_counter is not None
            and baseline is not None
            and ecoflow_counter >= baseline
        ):
            ecoflow_delta = round(ecoflow_counter - baseline, 3)

        self.snapshot = FloorHeatSnapshot(
            checked_at=now,
            power_w=power_w,
            power_stale=stale,
            power_stale_seconds=stale_seconds,
            pumps_power_w=self._read_pumps_power_w(),
            integrated_kwh_today=round(self.snapshot.integrated_kwh_today, 3),
            ecoflow_counter_kwh=ecoflow_counter,
            ecoflow_baseline_kwh=baseline,
            ecoflow_delta_kwh=ecoflow_delta,
            pacific_day=self._pacific_day,
            last_reason=reason,
        )
        self._notify_entities()

    def _notify_entities(self) -> None:
        for entity in self._entities:
            if getattr(entity, "hass", None) is not None:
                entity.async_write_ha_state()

    def status_dict(self) -> dict[str, Any]:
        snap = self.snapshot
        return {
            "pacific_day": snap.pacific_day,
            "power_w": snap.power_w,
            "power_stale": snap.power_stale,
            "power_stale_seconds": snap.power_stale_seconds,
            "pumps_power_w": snap.pumps_power_w,
            "integrated_kwh_today": snap.integrated_kwh_today,
            "ecoflow_counter_kwh": snap.ecoflow_counter_kwh,
            "ecoflow_baseline_kwh": snap.ecoflow_baseline_kwh,
            "ecoflow_delta_kwh": snap.ecoflow_delta_kwh,
            "ecoflow_power_entity": ECOFLOW_HEAT_POWER,
            "ecoflow_energy_entity": ECOFLOW_HEAT_ENERGY,
            "last_reason": snap.last_reason,
            "checked_at": snap.checked_at.isoformat() if snap.checked_at else None,
        }


def _device_info() -> dict[str, Any]:
    return {
        "identifiers": {(DOMAIN, "floor_heat_monitor")},
        "name": "SensorLinx Floor Heat Monitor",
        "manufacturer": "HBX Controls",
        "model": "Ecoflow Ch 1/3 + Tapo pumps",
    }


class FloorHeatEcoflowPowerStaleBinarySensor(BinarySensorEntity):
    """True when Ecoflow Ch 1/3 power has not updated recently."""

    _attr_has_entity_name = False
    _attr_name = "Sensorlinx floor heat ecoflow power stale"
    _attr_icon = "mdi:power-plug-off-outline"

    def __init__(self, monitor: FloorHeatMonitor) -> None:
        self._monitor = monitor
        self._attr_unique_id = "sensorlinx_floor_heat_ecoflow_power_stale"
        monitor.register_entity(self)

    @property
    def device_info(self) -> dict[str, Any]:
        return _device_info()

    @property
    def is_on(self) -> bool:
        return self._monitor.snapshot.power_stale

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        snap = self._monitor.snapshot
        return {
            "stale_after_minutes": int(POWER_STALE_AFTER.total_seconds() // 60),
            "power_stale_seconds": snap.power_stale_seconds,
            "ecoflow_power_entity": ECOFLOW_HEAT_POWER,
        }


class FloorHeatEcoflowPowerGuardedSensor(SensorEntity):
    """Ecoflow Ch 1/3 power (W) — unavailable when stale."""

    _attr_has_entity_name = False
    _attr_name = "Sensorlinx floor heat ecoflow power guarded"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:water-boiler"

    def __init__(self, monitor: FloorHeatMonitor) -> None:
        self._monitor = monitor
        self._attr_unique_id = "sensorlinx_floor_heat_ecoflow_power_guarded"
        monitor.register_entity(self)

    @property
    def device_info(self) -> dict[str, Any]:
        return _device_info()

    @property
    def native_value(self) -> float | None:
        return self._monitor.snapshot.power_w

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        snap = self._monitor.snapshot
        return {
            "source_entity": ECOFLOW_HEAT_POWER,
            "power_stale": snap.power_stale,
            "power_stale_seconds": snap.power_stale_seconds,
        }


class FloorHeatSourceEnergyTodaySensor(SensorEntity):
    """Pacific-day kWh from guarded Ecoflow power integration."""

    _attr_has_entity_name = False
    _attr_name = "Sensorlinx floor heat source energy today"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_icon = "mdi:lightning-bolt"

    def __init__(self, monitor: FloorHeatMonitor) -> None:
        self._monitor = monitor
        self._attr_unique_id = "sensorlinx_floor_heat_source_energy_today"
        monitor.register_entity(self)

    @property
    def device_info(self) -> dict[str, Any]:
        return _device_info()

    @property
    def native_value(self) -> float | None:
        snap = self._monitor.snapshot
        if snap.power_stale and snap.integrated_kwh_today <= 0:
            return None
        return snap.integrated_kwh_today

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._monitor.status_dict()


class FloorHeatEcoflowEnergyTodaySensor(SensorEntity):
    """Pacific-day kWh delta from Ecoflow energy counter (cross-check)."""

    _attr_has_entity_name = False
    _attr_name = "Sensorlinx floor heat ecoflow energy today"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_icon = "mdi:counter"

    def __init__(self, monitor: FloorHeatMonitor) -> None:
        self._monitor = monitor
        self._attr_unique_id = "sensorlinx_floor_heat_ecoflow_energy_today"
        monitor.register_entity(self)

    @property
    def device_info(self) -> dict[str, Any]:
        return _device_info()

    @property
    def native_value(self) -> float | None:
        return self._monitor.snapshot.ecoflow_delta_kwh

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        snap = self._monitor.snapshot
        return {
            "ecoflow_counter_kwh": snap.ecoflow_counter_kwh,
            "ecoflow_baseline_kwh": snap.ecoflow_baseline_kwh,
            "power_stale": snap.power_stale,
            "pacific_day": snap.pacific_day,
        }


class FloorHeatPumpsPowerSensor(SensorEntity):
    """Tapo radiant controller + pumps power (W)."""

    _attr_has_entity_name = False
    _attr_name = "Sensorlinx floor heat pumps power"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:pump"

    def __init__(self, monitor: FloorHeatMonitor) -> None:
        self._monitor = monitor
        self._attr_unique_id = "sensorlinx_floor_heat_pumps_power"
        monitor.register_entity(self)

    @property
    def device_info(self) -> dict[str, Any]:
        return _device_info()

    @property
    def native_value(self) -> float | None:
        return self._monitor.snapshot.pumps_power_w

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "current_entity": TAPO_RADIANT_CURRENT,
            "voltage_entity": TAPO_RADIANT_VOLTAGE,
        }


async def async_setup_floor_heat_monitor(
    hass: HomeAssistant, entry: ConfigEntry
) -> FloorHeatMonitor:
    """Create and start the floor heat monitor."""
    monitor = FloorHeatMonitor(hass, entry)
    await monitor.async_setup()
    return monitor


def get_floor_heat_sensor_entities(monitor: FloorHeatMonitor) -> list[SensorEntity]:
    """Sensors for floor heat monitoring."""
    return [
        FloorHeatEcoflowPowerGuardedSensor(monitor),
        FloorHeatSourceEnergyTodaySensor(monitor),
        FloorHeatEcoflowEnergyTodaySensor(monitor),
        FloorHeatPumpsPowerSensor(monitor),
    ]


def get_floor_heat_binary_sensor_entities(
    monitor: FloorHeatMonitor,
) -> list[BinarySensorEntity]:
    """Binary sensors for floor heat monitoring."""
    return [FloorHeatEcoflowPowerStaleBinarySensor(monitor)]
