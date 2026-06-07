"""Outdoor Reset Heating Curve for HBX SensorLinx radiant floors.

Adjusts THM zone setpoints based on outdoor temperature using a linear
heating curve. Includes a floor temperature safety cap for wood floors.

Formula:
  target = base + overshoot * ((shutdown - T_outdoor) / (shutdown - design_outdoor))
  Clamped to [base, base + overshoot]
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode, RestoreNumber
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval, async_track_state_change_event
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from datetime import timedelta

from .const import DOMAIN
from .coordinator import SensorlinxCoordinator, SensorlinxDeviceData

_LOGGER = logging.getLogger(__name__)

OUTDOOR_TEMP_ENTITY = "sensor.home_weather_station_temperature"
UPDATE_INTERVAL = timedelta(minutes=15)

DEFAULT_BASE = 70.0
DEFAULT_OVERSHOOT = 6.0
DEFAULT_SHUTDOWN = 65.0
DEFAULT_DESIGN_OUTDOOR = 25.0
DEFAULT_FLOOR_MAX = 80.0


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


class OutdoorResetController:
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

    async def async_setup(self) -> None:
        """Start periodic updates."""
        self._unsub_interval = async_track_time_interval(
            self.hass, self._async_update, UPDATE_INTERVAL
        )
        self._unsub_state = async_track_state_change_event(
            self.hass, [OUTDOOR_TEMP_ENTITY], self._async_on_outdoor_change
        )

    @callback
    def async_unload(self) -> None:
        """Remove listeners."""
        if self._unsub_interval:
            self._unsub_interval()
        if self._unsub_state:
            self._unsub_state()

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

        target = self.calculated_target

        if outdoor >= self.params.shutdown:
            _LOGGER.info("Outdoor %.1f°F >= shutdown %.1f°F, turning off zones",
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
            offset = self.params.zone_offsets.get(zone_name, 0.0)
            zone_target = self.zone_target(offset)

            # Safety cap: check floor temp
            floor_entity_id = f"sensor.{zone_name}_floor_temperature"
            floor_state = self.hass.states.get(floor_entity_id)
            if floor_state and floor_state.state not in ("unavailable", "unknown"):
                try:
                    floor_temp = float(floor_state.state)
                    if floor_temp >= self.params.floor_max:
                        _LOGGER.warning(
                            "Floor temp %.1f°F >= max %.1f°F for %s, turning off",
                            floor_temp, self.params.floor_max, zone_name,
                        )
                        await self.hass.services.async_call(
                            "climate", "turn_off",
                            {"entity_id": entity_id},
                            blocking=True,
                        )
                        continue
                except (ValueError, TypeError):
                    pass

            _LOGGER.debug(
                "Setting %s to %.1f°F (outdoor=%.1f, base_target=%.1f, offset=%.1f)",
                zone_name, zone_target, outdoor, target, offset,
            )
            await self.hass.services.async_call(
                "climate", "set_temperature",
                {"entity_id": entity_id, "temperature": zone_target, "hvac_mode": "heat"},
                blocking=True,
            )


class OutdoorResetParams:
    """Stores mutable outdoor reset parameters, updated by number entities."""

    def __init__(self) -> None:
        self.enabled: bool = True
        self.base: float = DEFAULT_BASE
        self.overshoot: float = DEFAULT_OVERSHOOT
        self.shutdown: float = DEFAULT_SHUTDOWN
        self.design_outdoor: float = DEFAULT_DESIGN_OUTDOOR
        self.floor_max: float = DEFAULT_FLOOR_MAX
        self.zone_offsets: dict[str, float] = {}


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
    controller = OutdoorResetController(hass, coordinator, params)
    await controller.async_setup()
    return controller


def get_number_entities(
    coordinator: SensorlinxCoordinator,
    controller: OutdoorResetController,
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
            "Max Floor Temp (Safety Cap)", DEFAULT_FLOOR_MAX, 75, 85, 1,
            "mdi:alert-octagon",
        ),
    ]

    for thm in coordinator.get_thm_devices():
        zone_name = thm.name.lower().replace(" ", "_")
        entities.append(
            OutdoorResetZoneOffsetEntity(
                coordinator, controller, zone_name,
                f"Zone Offset: {thm.name}", 0, -5, 5, 0.5,
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
) -> list[SwitchEntity]:
    """Return the enable/disable switch."""
    return [OutdoorResetEnableSwitch(coordinator, controller)]


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
