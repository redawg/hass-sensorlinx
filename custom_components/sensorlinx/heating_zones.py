"""Heating zone registry — SensorLinx THM zones plus external climate entities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant

from .const import (
    DEFAULT_PRIMARY_BATH_CLIMATE,
    DEFAULT_PRIMARY_BATH_ROOM_SENSOR,
)

if TYPE_CHECKING:
    from .coordinator import SensorlinxCoordinator, SensorlinxDeviceData


@dataclass(frozen=True)
class HeatingZone:
    """One controllable radiant / floor heating zone."""

    zone_key: str
    label: str
    climate_entity_id: str
    direct_floor_thermostat: bool = False
    schedule_managed: bool = False
    floor_temp_sensor: str | None = None
    room_temp_sensor: str | None = None

    @classmethod
    def from_thm(cls, thm: SensorlinxDeviceData) -> HeatingZone:
        zone_key = thm.name.lower().replace(" ", "_")
        return cls(
            zone_key=zone_key,
            label=thm.name,
            climate_entity_id=f"climate.{zone_key}_{zone_key}",
        )


DEFAULT_EXTERNAL_ZONES: tuple[HeatingZone, ...] = (
    HeatingZone(
        zone_key="primary_bath",
        label="Primary Bath",
        climate_entity_id=DEFAULT_PRIMARY_BATH_CLIMATE,
        direct_floor_thermostat=True,
        schedule_managed=True,
        room_temp_sensor=DEFAULT_PRIMARY_BATH_ROOM_SENSOR,
    ),
)


def get_heating_zones(
    hass: HomeAssistant,
    coordinator: SensorlinxCoordinator,
    external_zones: tuple[HeatingZone, ...] | list[HeatingZone] = DEFAULT_EXTERNAL_ZONES,
) -> list[HeatingZone]:
    """Return THM zones plus external zones whose climate entity exists in HA."""
    zones = [HeatingZone.from_thm(thm) for thm in coordinator.get_thm_devices()]
    for zone in external_zones:
        if hass.states.get(zone.climate_entity_id) is not None:
            zones.append(zone)
    return zones


def zone_for_key(
    zone_key: str,
    coordinator: SensorlinxCoordinator,
    external_zones: tuple[HeatingZone, ...] | list[HeatingZone] = DEFAULT_EXTERNAL_ZONES,
) -> HeatingZone | None:
    """Look up a zone definition by key."""
    for thm in coordinator.get_thm_devices():
        key = thm.name.lower().replace(" ", "_")
        if key == zone_key:
            return HeatingZone.from_thm(thm)
    for zone in external_zones:
        if zone.zone_key == zone_key:
            return zone
    return None
