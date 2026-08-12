"""Sensor platform for HBX SensorLinx devices."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SensorlinxCoordinator, SensorlinxDeviceData
from .helpers import thm_device_info, zon_device_info
from .outdoor_reset import get_sensor_entities
from .thermal_logger import ThermalDataLogger


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up SensorLinx sensors."""
    coordinator: SensorlinxCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = []

    for device_data in coordinator.get_thm_devices():
        entities.append(SensorlinxRoomTemperatureSensor(coordinator, device_data))
        entities.append(SensorlinxFloorTemperatureSensor(coordinator, device_data))
        entities.append(SensorlinxHumiditySensor(coordinator, device_data))

    for device_data in coordinator.get_eco_devices():
        entities.append(SensorlinxOutdoorTemperatureSensor(coordinator, device_data))

    for device_data in coordinator.get_zon_devices():
        entities.append(SensorlinxActiveZonesSensor(coordinator, device_data))

    controller = hass.data[DOMAIN].get(f"{entry.entry_id}_outdoor_reset")
    if controller:
        entities.extend(get_sensor_entities(coordinator, controller))

    thermal_logger = hass.data[DOMAIN].get(f"{entry.entry_id}_thermal_logger")
    if thermal_logger:
        entities.append(ThermalLogStatusSensor(thermal_logger))

    openings_guard = hass.data[DOMAIN].get(f"{entry.entry_id}_openings_guard")
    if openings_guard is not None:
        from .openings_guard import get_openings_sensor_entities

        entities.extend(get_openings_sensor_entities(openings_guard))

    async_add_entities(entities)


class SensorlinxSensorBase(CoordinatorEntity[SensorlinxCoordinator], SensorEntity):
    """Base sensor for SensorLinx."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SensorlinxCoordinator,
        device_data: SensorlinxDeviceData,
        *,
        key: str,
        name: str,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._device_data = device_data
        self._key = key
        self._attr_unique_id = f"{device_data.device_id}_{key}"
        self._attr_name = name

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device registry information."""
        if self._device_data.device_type == "THM":
            return thm_device_info(self.coordinator, self._device_data)
        if self._device_data.device_type == "ZON":
            return zon_device_info(self._device_data)
        model = self._device_data.device_type or "HBX Controller"
        return {
            "identifiers": {(DOMAIN, self._device_data.device_id)},
            "name": self._device_data.name,
            "manufacturer": "HBX Controls",
            "model": model,
            "via_device": (DOMAIN, self._device_data.building_id),
        }

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return (
            super().available
            and self._device_data.device_id in self.coordinator.data
        )

    def _raw(self) -> dict[str, Any]:
        """Return latest raw device payload."""
        return self.coordinator.data[self._device_data.device_id].raw


class SensorlinxRoomTemperatureSensor(SensorlinxSensorBase):
    """Room temperature for THM thermostats."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
    _attr_state_class = "measurement"

    def __init__(
        self,
        coordinator: SensorlinxCoordinator,
        device_data: SensorlinxDeviceData,
    ) -> None:
        """Initialize."""
        super().__init__(
            coordinator,
            device_data,
            key="room_temperature",
            name="Room temperature",
        )

    @property
    def native_value(self) -> float | None:
        """Return room temperature."""
        raw = self._raw()
        block = raw.get("temperature")
        if isinstance(block, dict) and block.get("value") is not None:
            return float(block["value"])
        room = raw.get("rm")
        return float(room) if room is not None else None


class SensorlinxFloorTemperatureSensor(SensorlinxSensorBase):
    """Floor temperature for THM thermostats."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
    _attr_state_class = "measurement"

    def __init__(
        self,
        coordinator: SensorlinxCoordinator,
        device_data: SensorlinxDeviceData,
    ) -> None:
        """Initialize."""
        super().__init__(
            coordinator,
            device_data,
            key="floor_temperature",
            name="Floor temperature",
        )

    @property
    def native_value(self) -> float | None:
        """Return floor temperature."""
        floor = self._raw().get("flr")
        return float(floor) if floor is not None else None


class SensorlinxHumiditySensor(SensorlinxSensorBase):
    """Humidity for THM thermostats."""

    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = "measurement"

    def __init__(
        self,
        coordinator: SensorlinxCoordinator,
        device_data: SensorlinxDeviceData,
    ) -> None:
        """Initialize."""
        super().__init__(
            coordinator,
            device_data,
            key="humidity",
            name="Humidity",
        )

    @property
    def native_value(self) -> float | None:
        """Return humidity."""
        humidity = self._raw().get("hm")
        return float(humidity) if humidity is not None else None


class SensorlinxActiveZonesSensor(SensorlinxSensorBase):
    """Count of active heating zones on ZON-0600."""

    _attr_state_class = "measurement"

    def __init__(
        self,
        coordinator: SensorlinxCoordinator,
        device_data: SensorlinxDeviceData,
    ) -> None:
        """Initialize."""
        super().__init__(
            coordinator,
            device_data,
            key="active_zones",
            name="Active zones",
        )

    @property
    def native_value(self) -> int | None:
        """Return number of active zone relays."""
        relays = self._raw().get("relays")
        if not isinstance(relays, list):
            return None
        return sum(1 for relay in relays if relay)


class SensorlinxOutdoorTemperatureSensor(SensorlinxSensorBase):
    """Outdoor temperature from ECO controller weather data."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
    _attr_state_class = "measurement"

    def __init__(
        self,
        coordinator: SensorlinxCoordinator,
        device_data: SensorlinxDeviceData,
    ) -> None:
        """Initialize."""
        super().__init__(
            coordinator,
            device_data,
            key="outdoor_temperature",
            name="Outdoor temperature",
        )

    @property
    def native_value(self) -> float | None:
        """Return outdoor temperature."""
        weather = self._raw().get("weather")
        if isinstance(weather, dict) and weather.get("temp") is not None:
            return float(weather["temp"])
        return None


class ThermalLogStatusSensor(SensorEntity):
    """Reports the status of thermal data collection for AI training."""

    _attr_has_entity_name = True
    _attr_name = "Thermal Log Samples"
    _attr_icon = "mdi:database-clock"
    _attr_state_class = "total_increasing"

    def __init__(self, logger: ThermalDataLogger) -> None:
        self._logger = logger
        self._attr_unique_id = "sensorlinx_thermal_log_samples"
        self._sample_count = 0

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, "outdoor_reset")},
            "name": "SensorLinx Outdoor Reset",
            "manufacturer": "HBX Controls",
            "model": "Heating Curve Controller",
        }

    @property
    def native_value(self) -> int:
        """Return approximate sample count from current month's log."""
        import os
        path = self._logger._log_path
        try:
            if os.path.exists(path):
                size = os.path.getsize(path)
                return size // 200  # ~200 bytes per sample line
        except OSError:
            pass
        return 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose log file path and interval for diagnostics."""
        import os
        path = self._logger._log_path
        size_kb = 0
        try:
            if os.path.exists(path):
                size_kb = round(os.path.getsize(path) / 1024, 1)
        except OSError:
            pass
        return {
            "log_path": path,
            "sample_interval_minutes": 5,
            "log_size_kb": size_kb,
            "zones_per_sample": len(self._logger.controller.get_heating_zones()),
        }
