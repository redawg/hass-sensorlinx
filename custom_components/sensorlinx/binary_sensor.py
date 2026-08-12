"""Binary sensor platform for HBX SensorLinx devices."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SensorlinxCoordinator, SensorlinxDeviceData
from .helpers import thm_device_info, zon_device_info, zon_zone_number


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up SensorLinx binary sensors."""
    coordinator: SensorlinxCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[BinarySensorEntity] = []

    for device_data in coordinator.get_thm_devices():
        entities.append(SensorlinxHeatingBinarySensor(coordinator, device_data))
        entities.append(SensorlinxCoolingBinarySensor(coordinator, device_data))

    for device_data in coordinator.get_zon_devices():
        raw = device_data.raw
        relays = raw.get("relays") or []
        for index in range(len(relays)):
            entities.append(
                SensorlinxZoneRelayBinarySensor(coordinator, device_data, index)
            )
        for demand_entry in raw.get("demands") or []:
            if isinstance(demand_entry, dict) and demand_entry.get("key"):
                entities.append(
                    SensorlinxZonDemandBinarySensor(
                        coordinator, device_data, demand_entry
                    )
                )
        for pump_entry in raw.get("pumps") or []:
            if isinstance(pump_entry, dict) and pump_entry.get("key"):
                entities.append(
                    SensorlinxZonPumpBinarySensor(
                        coordinator, device_data, pump_entry
                    )
                )

    openings_guard = hass.data[DOMAIN].get(f"{entry.entry_id}_openings_guard")
    if openings_guard is not None:
        from .openings_guard import get_openings_binary_sensor_entities

        entities.extend(get_openings_binary_sensor_entities(openings_guard))

    async_add_entities(entities)


class SensorlinxDemandBinarySensor(CoordinatorEntity[SensorlinxCoordinator], BinarySensorEntity):
    """Base binary sensor driven by THM demand bitfield."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SensorlinxCoordinator,
        device_data: SensorlinxDeviceData,
        *,
        key: str,
        name: str,
        bit: int,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._device_data = device_data
        self._bit = bit
        self._attr_unique_id = f"{device_data.device_id}_{key}"
        self._attr_name = name

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device registry information."""
        return thm_device_info(self.coordinator, self._device_data)

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return (
            super().available
            and self._device_data.device_id in self.coordinator.data
        )

    @property
    def is_on(self) -> bool:
        """Return whether the demand bit is active."""
        dmd = self.coordinator.data[self._device_data.device_id].raw.get("dmd")
        return isinstance(dmd, int) and bool(dmd & self._bit)


class SensorlinxHeatingBinarySensor(SensorlinxDemandBinarySensor):
    """Heating demand for THM thermostats."""

    _attr_device_class = BinarySensorDeviceClass.HEAT

    def __init__(
        self,
        coordinator: SensorlinxCoordinator,
        device_data: SensorlinxDeviceData,
    ) -> None:
        """Initialize."""
        super().__init__(
            coordinator,
            device_data,
            key="heating_demand",
            name="Heating demand",
            bit=0x02,
        )


class SensorlinxCoolingBinarySensor(SensorlinxDemandBinarySensor):
    """Cooling demand for THM thermostats."""

    _attr_device_class = BinarySensorDeviceClass.COLD

    def __init__(
        self,
        coordinator: SensorlinxCoordinator,
        device_data: SensorlinxDeviceData,
    ) -> None:
        """Initialize."""
        super().__init__(
            coordinator,
            device_data,
            key="cooling_demand",
            name="Cooling demand",
            bit=0x40,
        )


class SensorlinxZonBinarySensorBase(CoordinatorEntity[SensorlinxCoordinator], BinarySensorEntity):
    """Base binary sensor for ZON-0600."""

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
        self._attr_unique_id = f"{device_data.device_id}_{key}"
        self._attr_name = name

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


class SensorlinxZoneRelayBinarySensor(SensorlinxZonBinarySensorBase):
    """Floor heating zone relay state."""

    _attr_device_class = BinarySensorDeviceClass.HEAT

    def __init__(
        self,
        coordinator: SensorlinxCoordinator,
        device_data: SensorlinxDeviceData,
        relay_index: int,
    ) -> None:
        """Initialize."""
        zone_number = zon_zone_number(device_data.raw, relay_index)
        super().__init__(
            coordinator,
            device_data,
            key=f"zone_{zone_number}_relay",
            name=f"Zone {zone_number}",
        )
        self._relay_index = relay_index

    @property
    def is_on(self) -> bool:
        """Return whether the zone relay is active."""
        relays = self.coordinator.data[self._device_data.device_id].raw.get("relays")
        if not isinstance(relays, list) or self._relay_index >= len(relays):
            return False
        return bool(relays[self._relay_index])


class SensorlinxZonDemandBinarySensor(SensorlinxZonBinarySensorBase):
    """ZON system demand (heat, cool, app)."""

    def __init__(
        self,
        coordinator: SensorlinxCoordinator,
        device_data: SensorlinxDeviceData,
        demand_entry: dict[str, Any],
    ) -> None:
        """Initialize."""
        key = str(demand_entry.get("key", "demand"))
        title = str(demand_entry.get("title") or key)
        super().__init__(
            coordinator,
            device_data,
            key=f"demand_{key.lower()}",
            name=f"{title} demand",
        )
        self._demand_key = key

    @property
    def is_on(self) -> bool:
        """Return whether this demand is active."""
        for entry in self.coordinator.data[self._device_data.device_id].raw.get("demands") or []:
            if isinstance(entry, dict) and entry.get("key") == self._demand_key:
                return bool(entry.get("activated"))
        return False


class SensorlinxZonPumpBinarySensor(SensorlinxZonBinarySensorBase):
    """ZON circulation pump state."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(
        self,
        coordinator: SensorlinxCoordinator,
        device_data: SensorlinxDeviceData,
        pump_entry: dict[str, Any],
    ) -> None:
        """Initialize."""
        key = str(pump_entry.get("key", "pump"))
        title = str(pump_entry.get("title") or key)
        super().__init__(
            coordinator,
            device_data,
            key=f"pump_{key.lower()}",
            name=title,
        )
        self._pump_key = key

    @property
    def is_on(self) -> bool:
        """Return whether this pump is running."""
        for entry in self.coordinator.data[self._device_data.device_id].raw.get("pumps") or []:
            if isinstance(entry, dict) and entry.get("key") == self._pump_key:
                return bool(entry.get("activated"))
        return False
