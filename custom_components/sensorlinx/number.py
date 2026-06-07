"""Number platform for HBX ZON-0600 writable setpoints."""

from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from pysensorlinx import Temperature, ZonDevice

from .const import DOMAIN
from .coordinator import SensorlinxCoordinator, SensorlinxDeviceData
from .helpers import zon_device_info
from .outdoor_reset import get_number_entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ZON number entities and outdoor reset parameter entities."""
    coordinator: SensorlinxCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        SensorlinxAuxSetpointNumber(coordinator, device_data)
        for device_data in coordinator.get_zon_devices()
    ]

    controller = hass.data[DOMAIN].get(f"{entry.entry_id}_outdoor_reset")
    if controller:
        entities.extend(get_number_entities(coordinator, controller))

    async_add_entities(entities)


class SensorlinxAuxSetpointNumber(CoordinatorEntity[SensorlinxCoordinator], NumberEntity):
    """Auxiliary heat setpoint for ZON-0600."""

    _attr_has_entity_name = True
    _attr_name = "Auxiliary setpoint"
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
    _attr_mode = NumberMode.AUTO
    _attr_native_min_value = 33
    _attr_native_max_value = 180
    _attr_native_step = 1

    def __init__(
        self,
        coordinator: SensorlinxCoordinator,
        device_data: SensorlinxDeviceData,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._device_data = device_data
        self._device: ZonDevice = device_data.device
        self._attr_unique_id = f"{device_data.device_id}_aux_setpoint"

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device registry information."""
        return zon_device_info(self._device_data)

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return (
            super().available
            and self._device_data.device_id in self.coordinator.data
        )

    @property
    def native_value(self) -> float | None:
        """Return auxiliary setpoint."""
        block = self.coordinator.data[self._device_data.device_id].raw.get("auxSetpoint")
        if isinstance(block, dict) and block.get("value") is not None:
            return float(block["value"])
        dhw = self.coordinator.data[self._device_data.device_id].raw.get("dhwT")
        return float(dhw) if dhw is not None else None

    async def async_set_native_value(self, value: float) -> None:
        """Set auxiliary setpoint."""
        await self._device.set_aux_setpoint(Temperature(value, "F"))
        await self.coordinator.async_request_refresh()
