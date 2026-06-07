"""Data update coordinator for HBX SensorLinx."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pysensorlinx import Sensorlinx, ThmDevice, ZonDevice, device_for

from .const import CONF_BUILDING_ID, DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


@dataclass
class SensorlinxDeviceData:
    """Cached state for one HBX device."""

    device_id: str
    building_id: str
    name: str
    device_type: str
    raw: dict[str, Any]
    device: Any


class SensorlinxCoordinator(DataUpdateCoordinator[dict[str, SensorlinxDeviceData]]):
    """Fetch and cache SensorLinx device state."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: Sensorlinx) -> None:
        """Initialize."""
        self.api = api
        self.building_id = entry.data[CONF_BUILDING_ID]
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.config_entry = entry

    async def _async_update_data(self) -> dict[str, SensorlinxDeviceData]:
        """Refresh all devices for the configured building."""
        try:
            devices = await self.api.get_devices(self.building_id)
        except Exception as err:
            raise UpdateFailed(f"Error communicating with SensorLinx API: {err}") from err

        if not isinstance(devices, list):
            raise UpdateFailed("Unexpected API response while listing devices")

        result: dict[str, SensorlinxDeviceData] = {}
        for raw in devices:
            if not isinstance(raw, dict):
                continue
            device_id = raw.get("syncCode") or raw.get("id") or raw.get("_id")
            if not device_id:
                continue

            helper = device_for(self.api, self.building_id, raw)
            name = raw.get("name") or device_id
            device_type = (raw.get("deviceType") or "").upper()

            # Re-fetch full device payload so subclasses have fresh state.
            try:
                fresh = await self.api.get_devices(self.building_id, device_id)
                if isinstance(fresh, dict):
                    raw = fresh
                    helper = device_for(self.api, self.building_id, raw)
                    name = raw.get("name") or name
            except Exception:
                _LOGGER.debug("Using list payload for device %s", device_id)

            result[device_id] = SensorlinxDeviceData(
                device_id=device_id,
                building_id=self.building_id,
                name=name,
                device_type=device_type,
                raw=raw,
                device=helper,
            )

        return result

    def get_thm_devices(self) -> list[SensorlinxDeviceData]:
        """Return thermostat devices."""
        return [
            d
            for d in self.data.values()
            if d.device_type == "THM" and isinstance(d.device, ThmDevice)
        ]

    def get_zon_devices(self) -> list[SensorlinxDeviceData]:
        """Return zone controller devices."""
        return [
            d
            for d in self.data.values()
            if d.device_type == "ZON" and isinstance(d.device, ZonDevice)
        ]

    def get_eco_devices(self) -> list[SensorlinxDeviceData]:
        """Return ECO controller devices."""
        return [d for d in self.data.values() if d.device_type == "ECO"]

    def get_parent_zon_id(self, thm_device_id: str) -> str | None:
        """Return the ZON-0600 sync code that owns this THM, if linked."""
        for zon in self.get_zon_devices():
            thm_codes = zon.raw.get("thmInfo") or []
            if thm_device_id in thm_codes:
                return zon.device_id
        return None
