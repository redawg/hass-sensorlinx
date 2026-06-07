"""Shared helpers for SensorLinx entities."""

from __future__ import annotations

from typing import Any

from .const import DOMAIN
from .coordinator import SensorlinxCoordinator, SensorlinxDeviceData


def thm_device_info(
    coordinator: SensorlinxCoordinator,
    device_data: SensorlinxDeviceData,
) -> dict[str, Any]:
    """Device registry entry for a THM thermostat."""
    parent_zon = coordinator.get_parent_zon_id(device_data.device_id)
    return {
        "identifiers": {(DOMAIN, device_data.device_id)},
        "name": device_data.name,
        "manufacturer": "HBX Controls",
        "model": "THM-0600",
        "via_device": (DOMAIN, parent_zon or device_data.building_id),
    }


def zon_device_info(device_data: SensorlinxDeviceData) -> dict[str, Any]:
    """Device registry entry for a ZON zone controller."""
    return {
        "identifiers": {(DOMAIN, device_data.device_id)},
        "name": device_data.name,
        "manufacturer": "HBX Controls",
        "model": "ZON-0600",
        "via_device": (DOMAIN, device_data.building_id),
    }


def zon_zone_number(raw: dict[str, Any], relay_index: int) -> int:
    """Map a relay index to the HBX app zone number."""
    sequence = 0
    block = raw.get("sequence")
    if isinstance(block, dict) and isinstance(block.get("value"), (int, float)):
        sequence = int(block["value"])
    elif isinstance(raw.get("znSeq"), (int, float)):
        sequence = int(raw["znSeq"])
    return sequence * 4 + relay_index + 1
