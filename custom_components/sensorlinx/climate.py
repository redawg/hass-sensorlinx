"""Climate platform for HBX THM thermostats."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from pysensorlinx import Temperature, ThmDevice

from .const import DOMAIN
from .coordinator import SensorlinxCoordinator, SensorlinxDeviceData
from .helpers import thm_device_info

_LOGGER = logging.getLogger(__name__)

THM_HVAC_TO_HA = {
    "auto": HVACMode.HEAT_COOL,
    "heat": HVACMode.HEAT,
    "cool": HVACMode.COOL,
    "off": HVACMode.OFF,
}

HA_HVAC_TO_THM = {v: k for k, v in THM_HVAC_TO_HA.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up THM climate entities."""
    coordinator: SensorlinxCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        SensorlinxClimateEntity(coordinator, device_data)
        for device_data in coordinator.get_thm_devices()
    )


class SensorlinxClimateEntity(CoordinatorEntity[SensorlinxCoordinator], ClimateEntity):
    """Climate entity for a THM-0600 thermostat."""

    _attr_has_entity_name = True
    _attr_temperature_unit = UnitOfTemperature.FAHRENHEIT
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.TURN_ON
    )
    _attr_hvac_modes = [
        HVACMode.HEAT_COOL,
        HVACMode.HEAT,
        HVACMode.COOL,
        HVACMode.OFF,
    ]

    def __init__(
        self,
        coordinator: SensorlinxCoordinator,
        device_data: SensorlinxDeviceData,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._device_data = device_data
        self._device: ThmDevice = device_data.device
        self._attr_unique_id = f"{device_data.device_id}_climate"
        self._attr_name = device_data.name

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
    def current_temperature(self) -> float | None:
        """Return current room temperature."""
        raw = self.coordinator.data[self._device_data.device_id].raw
        block = raw.get("temperature")
        if isinstance(block, dict) and block.get("value") is not None:
            return float(block["value"])
        room = raw.get("rm")
        return float(room) if room is not None else None

    @property
    def target_temperature(self) -> float | None:
        """Return active setpoint."""
        raw = self.coordinator.data[self._device_data.device_id].raw
        block = raw.get("target")
        if not isinstance(block, dict) or block.get("isOff"):
            return None
        value = block.get("value")
        return float(value) if value is not None else None

    @property
    def hvac_mode(self) -> HVACMode:
        """Return current HVAC mode.

        offMode=1 is the authoritative off indicator used by the SensorLinx
        app and physical device, taking precedence over the changeover array.
        """
        raw = self.coordinator.data[self._device_data.device_id].raw
        if raw.get("offMode") == 1:
            return HVACMode.OFF
        for entry in raw.get("changeover", []) or []:
            if isinstance(entry, dict) and entry.get("activated"):
                key = entry.get("key")
                if key in THM_HVAC_TO_HA:
                    return THM_HVAC_TO_HA[key]
        return HVACMode.OFF

    @property
    def hvac_action(self) -> str | None:
        """Return current HVAC action."""
        raw = self.coordinator.data[self._device_data.device_id].raw
        dmd = raw.get("dmd")
        if not isinstance(dmd, int):
            return None
        if dmd & 0x02:
            return "heating"
        if dmd & 0x40:
            return "cooling"
        if dmd & 0x80:
            return "fan"
        return "idle"

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode.

        The SensorLinx cloud requires both cngOvr AND offMode to be set
        for the app and physical device to reflect the change.
        """
        thm_mode = HA_HVAC_TO_THM.get(hvac_mode)
        if thm_mode is None:
            _LOGGER.error("Unsupported HVAC mode: %s", hvac_mode)
            return
        await self._device.set_hvac_mode(thm_mode)
        if hvac_mode == HVACMode.OFF:
            await self.coordinator.api.patch_device(
                self._device.building_id, self._device.device_id, offMode=1
            )
        else:
            await self.coordinator.api.patch_device(
                self._device.building_id, self._device.device_id, offMode=0
            )
        await self.coordinator.async_request_refresh()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        await self._device.set_target_temperature(Temperature(temperature, "F"))
        await self.coordinator.async_request_refresh()
