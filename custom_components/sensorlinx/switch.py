"""Switch platform for HBX SensorLinx devices."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from pysensorlinx import ThmDevice, ZonDevice

from .const import DOMAIN
from .coordinator import SensorlinxCoordinator, SensorlinxDeviceData
from .helpers import thm_device_info, zon_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up SensorLinx switches."""
    coordinator: SensorlinxCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SwitchEntity] = [
        SensorlinxAwayModeSwitch(coordinator, device_data)
        for device_data in coordinator.get_thm_devices()
    ]
    entities.extend(
        SensorlinxAppButtonSwitch(coordinator, device_data)
        for device_data in coordinator.get_zon_devices()
    )
    async_add_entities(entities)


class SensorlinxAwayModeSwitch(CoordinatorEntity[SensorlinxCoordinator], SwitchEntity):
    """Away mode switch for THM thermostats."""

    _attr_has_entity_name = True
    _attr_name = "Away mode"

    def __init__(
        self,
        coordinator: SensorlinxCoordinator,
        device_data: SensorlinxDeviceData,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._device_data = device_data
        self._device: ThmDevice = device_data.device
        self._attr_unique_id = f"{device_data.device_id}_away_mode"

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
        """Return whether away mode is active."""
        away = self.coordinator.data[self._device_data.device_id].raw.get("awayMode")
        if isinstance(away, dict):
            return bool(away.get("activated"))
        return bool(self.coordinator.data[self._device_data.device_id].raw.get("away"))

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable away mode."""
        await self._device.set_away_mode(True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable away mode."""
        await self._device.set_away_mode(False)
        await self.coordinator.async_request_refresh()


class SensorlinxAppButtonSwitch(CoordinatorEntity[SensorlinxCoordinator], SwitchEntity):
    """App button on ZON-0600 (drives relay 12)."""

    _attr_has_entity_name = True
    _attr_name = "App button"

    def __init__(
        self,
        coordinator: SensorlinxCoordinator,
        device_data: SensorlinxDeviceData,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._device_data = device_data
        self._device: ZonDevice = device_data.device
        self._attr_unique_id = f"{device_data.device_id}_app_button"

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
    def is_on(self) -> bool:
        """Return whether the app button is active."""
        raw = self.coordinator.data[self._device_data.device_id].raw
        block = raw.get("appButton")
        if isinstance(block, dict):
            return bool(block.get("activated"))
        return bool(raw.get("aBut"))

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Activate app button."""
        await self._device.set_app_button(True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Deactivate app button."""
        await self._device.set_app_button(False)
        await self.coordinator.async_request_refresh()
