"""Validate door/window openings and refresh stale HomeKit contacts.

HomeKit contact sensors can stick open while a paired August (or other)
door sensor reports closed. This guard:

1. Periodically calls ``homeassistant.update_entity`` on watched contacts
2. Cross-checks authority sensors when available
3. Exposes a validated ``any open`` binary sensor + status sensor
4. Optionally mirrors the radiant-floor interlock (HVAC off or opening open)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)

from .const import (
    CONF_MAIN_HVAC_CLIMATE,
    CONF_RADIANT_FLOOR_SWITCH,
    DEFAULT_MAIN_HVAC_CLIMATE,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

REFRESH_INTERVAL = timedelta(minutes=5)
CONFLICT_GRACE = timedelta(minutes=2)

DEFAULT_OPENING_CONTACTS = (
    "binary_sensor.wldj_contact",  # Kitchen Window
    "binary_sensor.wpxp_contact",  # Main office
    "binary_sensor.s92n_contact",  # Nanowall
    "binary_sensor.wk2n_contact",  # Garage Door contact
    "binary_sensor.basement_door_contact",
    "binary_sensor.front_door_contact",
)

# Prefer these when a HomeKit contact disagrees after refresh.
DEFAULT_AUTHORITY_PAIRS = {
    "binary_sensor.front_door_contact": "binary_sensor.front_door_door",
    "binary_sensor.wk2n_contact": "binary_sensor.garage_door_door",
}

DEFAULT_FLOOR_SWITCH = "switch.radiant_floor_contoller"
LEGACY_FLOOR_AUTOMATION = (
    "automation.sensorlinx_disable_floor_when_openings_open_or_thermostat_off"
)
ACTIVE_HVAC_MODES = frozenset({"heat", "cool", "heat_cool", "auto"})


@dataclass
class OpeningReading:
    """Validated reading for one opening contact."""

    entity_id: str
    name: str
    raw_open: bool | None
    effective_open: bool
    authority_entity_id: str | None = None
    authority_open: bool | None = None
    conflict: bool = False
    note: str = ""


@dataclass
class OpeningsSnapshot:
    """Latest validated openings evaluation."""

    checked_at: datetime | None = None
    refreshed_at: datetime | None = None
    any_open: bool = False
    open_names: list[str] = field(default_factory=list)
    conflict_names: list[str] = field(default_factory=list)
    readings: list[OpeningReading] = field(default_factory=list)
    hvac_mode: str | None = None
    floor_switch: str | None = None
    floor_state: str | None = None
    last_floor_action: str | None = None
    last_reason: str = "startup"


class OpeningsGuard:
    """Refresh, validate, and optionally enforce opening interlocks."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize."""
        self.hass = hass
        self.entry = entry
        self.contacts = list(DEFAULT_OPENING_CONTACTS)
        self.authority_pairs = dict(DEFAULT_AUTHORITY_PAIRS)
        self.floor_switch = entry.options.get(
            CONF_RADIANT_FLOOR_SWITCH, DEFAULT_FLOOR_SWITCH
        )
        self.hvac_entity = entry.options.get(
            CONF_MAIN_HVAC_CLIMATE, DEFAULT_MAIN_HVAC_CLIMATE
        )
        self.control_floor = True
        self.snapshot = OpeningsSnapshot()
        self._unsub: list[Callable[[], None]] = []
        self._entities: list[Any] = []
        self._refreshing = False
        self._pending_conflicts: dict[str, datetime] = {}

    async def async_setup(self) -> None:
        """Start listeners, refresh contacts, and take over floor interlock."""
        watch = list(
            dict.fromkeys(
                self.contacts
                + list(self.authority_pairs.values())
                + [self.hvac_entity, self.floor_switch]
            )
        )
        self._unsub.append(
            async_track_state_change_event(
                self.hass, watch, self._async_on_state_change
            )
        )
        self._unsub.append(
            async_track_time_interval(
                self.hass, self._async_periodic_refresh, REFRESH_INTERVAL
            )
        )
        await self._async_disable_legacy_automation()
        await self.async_refresh(force_update=True, reason="startup")

    def async_unload(self) -> None:
        """Remove listeners."""
        for unsub in self._unsub:
            unsub()
        self._unsub.clear()

    def register_entity(self, entity: Any) -> None:
        """Track entities that should refresh after evaluation."""
        self._entities.append(entity)

    @callback
    def _async_on_state_change(self, event: Event) -> None:
        """Re-evaluate when a watched entity changes (after a short debounce path)."""
        if self._refreshing:
            return
        entity_id = event.data.get("entity_id")
        # Floor switch changes from our own actions should not recurse.
        if entity_id == self.floor_switch:
            new_state = event.data.get("new_state")
            if new_state is not None:
                self.snapshot.floor_state = new_state.state
            self._notify_entities()
            return
        self.hass.async_create_task(
            self.async_refresh(force_update=False, reason=f"state:{entity_id}")
        )

    async def _async_periodic_refresh(self, _now=None) -> None:
        """Periodic HomeKit/August refresh + re-check."""
        await self.async_refresh(force_update=True, reason="interval")

    async def async_refresh(
        self, *, force_update: bool = True, reason: str = "manual"
    ) -> OpeningsSnapshot:
        """Optionally update entities, validate openings, enforce floor interlock."""
        if self._refreshing:
            return self.snapshot
        self._refreshing = True
        try:
            if force_update:
                await self._async_update_entities()
                self.snapshot.refreshed_at = datetime.now().astimezone()
            self._evaluate(reason=reason)
            if self.control_floor:
                await self._async_enforce_floor()
            self._notify_entities()
            return self.snapshot
        finally:
            self._refreshing = False

    async def _async_update_entities(self) -> None:
        """Ask HA integrations to refresh contact/authority entities."""
        entity_ids = list(
            dict.fromkeys(self.contacts + list(self.authority_pairs.values()))
        )
        existing = [eid for eid in entity_ids if self.hass.states.get(eid) is not None]
        if not existing:
            return
        try:
            await self.hass.services.async_call(
                "homeassistant",
                "update_entity",
                {"entity_id": existing},
                blocking=True,
            )
        except Exception as err:  # noqa: BLE001 — never break guard on refresh failure
            _LOGGER.warning("Opening entity refresh failed: %s", err)

    def _entity_open(self, entity_id: str) -> bool | None:
        """Return True/False for on/off binary sensors, else None."""
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unavailable", "unknown"):
            return None
        if state.state == STATE_ON:
            return True
        if state.state == STATE_OFF:
            return False
        return None

    def _entity_name(self, entity_id: str) -> str:
        """Friendly name for status reporting."""
        state = self.hass.states.get(entity_id)
        if state is None:
            return entity_id
        return state.attributes.get("friendly_name") or entity_id

    def _evaluate(self, *, reason: str) -> None:
        """Build validated readings from raw contacts + authority pairs."""
        now = datetime.now().astimezone()
        readings: list[OpeningReading] = []
        open_names: list[str] = []
        conflict_names: list[str] = []

        for contact_id in self.contacts:
            name = self._entity_name(contact_id)
            raw_open = self._entity_open(contact_id)
            authority_id = self.authority_pairs.get(contact_id)
            authority_open = (
                self._entity_open(authority_id) if authority_id else None
            )
            conflict = False
            note = "contact"
            effective = bool(raw_open)

            if authority_id and authority_open is not None:
                if raw_open is None:
                    effective = authority_open
                    note = "authority_only"
                elif raw_open != authority_open:
                    first_seen = self._pending_conflicts.get(contact_id)
                    if first_seen is None:
                        self._pending_conflicts[contact_id] = now
                        first_seen = now
                    # Prefer authority once the conflict has persisted a bit,
                    # or immediately when contact claims open and door claims closed
                    # (the stuck HomeKit failure mode we hit).
                    sticky_open_false_positive = raw_open and not authority_open
                    aged = (now - first_seen) >= CONFLICT_GRACE
                    if sticky_open_false_positive or aged:
                        effective = authority_open
                        conflict = True
                        note = (
                            "authority_override"
                            if sticky_open_false_positive
                            else "authority_override_aged"
                        )
                        conflict_names.append(name)
                    else:
                        effective = raw_open
                        note = "conflict_grace"
                else:
                    self._pending_conflicts.pop(contact_id, None)
                    effective = raw_open
                    note = "agree"
            elif raw_open is None:
                effective = False
                note = "unavailable"

            reading = OpeningReading(
                entity_id=contact_id,
                name=name,
                raw_open=raw_open,
                effective_open=effective,
                authority_entity_id=authority_id,
                authority_open=authority_open,
                conflict=conflict,
                note=note,
            )
            readings.append(reading)
            if effective:
                open_names.append(name)

        hvac = self.hass.states.get(self.hvac_entity)
        floor = self.hass.states.get(self.floor_switch)
        self.snapshot = OpeningsSnapshot(
            checked_at=now,
            refreshed_at=self.snapshot.refreshed_at,
            any_open=bool(open_names),
            open_names=open_names,
            conflict_names=conflict_names,
            readings=readings,
            hvac_mode=hvac.state if hvac else None,
            floor_switch=self.floor_switch,
            floor_state=floor.state if floor else None,
            last_floor_action=self.snapshot.last_floor_action,
            last_reason=reason,
        )
        if open_names or conflict_names:
            _LOGGER.info(
                "Openings guard (%s): open=%s conflicts=%s",
                reason,
                open_names or "none",
                conflict_names or "none",
            )

    async def _async_enforce_floor(self) -> None:
        """Turn radiant floor off when HVAC is off or a validated opening is open."""
        floor_state = self.hass.states.get(self.floor_switch)
        if floor_state is None:
            return
        hvac_mode = self.snapshot.hvac_mode
        should_disable = hvac_mode == "off" or self.snapshot.any_open
        should_enable = (
            hvac_mode in ACTIVE_HVAC_MODES
            and not self.snapshot.any_open
            and floor_state.state == STATE_OFF
        )

        if should_disable and floor_state.state == STATE_ON:
            await self.hass.services.async_call(
                "switch",
                "turn_off",
                {"entity_id": self.floor_switch},
                blocking=True,
            )
            reason = (
                "hvac_off"
                if hvac_mode == "off"
                else f"open:{','.join(self.snapshot.open_names)}"
            )
            self.snapshot.last_floor_action = f"turn_off ({reason})"
            _LOGGER.info("Openings guard disabled floor: %s", reason)
        elif should_enable:
            await self.hass.services.async_call(
                "switch",
                "turn_on",
                {"entity_id": self.floor_switch},
                blocking=True,
            )
            self.snapshot.last_floor_action = "turn_on (all closed)"
            _LOGGER.info("Openings guard enabled floor: all validated openings closed")

    async def _async_disable_legacy_automation(self) -> None:
        """Disable the UI automation that trusted raw stuck contacts."""
        state = self.hass.states.get(LEGACY_FLOOR_AUTOMATION)
        if state is None:
            return
        if state.state == STATE_OFF:
            return
        try:
            await self.hass.services.async_call(
                "automation",
                "turn_off",
                {"entity_id": LEGACY_FLOOR_AUTOMATION},
                blocking=True,
            )
            _LOGGER.info(
                "Disabled legacy openings automation %s (SensorLinx openings guard owns it)",
                LEGACY_FLOOR_AUTOMATION,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Could not disable legacy openings automation: %s", err)

    def _notify_entities(self) -> None:
        """Push state updates to registered HA entities."""
        for entity in self._entities:
            if getattr(entity, "hass", None) is not None:
                entity.async_write_ha_state()

    def status_dict(self) -> dict[str, Any]:
        """Serialize snapshot for sensor attributes."""
        snap = self.snapshot
        return {
            "any_open": snap.any_open,
            "open": snap.open_names,
            "conflicts": snap.conflict_names,
            "checked_at": snap.checked_at.isoformat() if snap.checked_at else None,
            "refreshed_at": (
                snap.refreshed_at.isoformat() if snap.refreshed_at else None
            ),
            "hvac_mode": snap.hvac_mode,
            "floor_switch": snap.floor_switch,
            "floor_state": snap.floor_state,
            "last_floor_action": snap.last_floor_action,
            "last_reason": snap.last_reason,
            "readings": [
                {
                    "entity_id": r.entity_id,
                    "name": r.name,
                    "raw_open": r.raw_open,
                    "effective_open": r.effective_open,
                    "authority": r.authority_entity_id,
                    "authority_open": r.authority_open,
                    "conflict": r.conflict,
                    "note": r.note,
                }
                for r in snap.readings
            ],
        }


def _device_info() -> dict[str, Any]:
    return {
        "identifiers": {(DOMAIN, "openings_guard")},
        "name": "SensorLinx Openings Guard",
        "manufacturer": "HBX Controls",
        "model": "Door/Window Validation",
    }


class OpeningsAnyOpenBinarySensor(BinarySensorEntity):
    """Validated any-opening-open binary sensor."""

    _attr_has_entity_name = True
    _attr_name = "Openings open"
    _attr_device_class = BinarySensorDeviceClass.OPENING
    _attr_icon = "mdi:door-open"

    def __init__(self, guard: OpeningsGuard) -> None:
        self._guard = guard
        self._attr_unique_id = "sensorlinx_openings_any_open"
        guard.register_entity(self)

    @property
    def device_info(self) -> dict[str, Any]:
        return _device_info()

    @property
    def is_on(self) -> bool:
        return self._guard.snapshot.any_open

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        snap = self._guard.snapshot
        return {
            "open": snap.open_names,
            "conflicts": snap.conflict_names,
            "last_reason": snap.last_reason,
            "refreshed_at": (
                snap.refreshed_at.isoformat() if snap.refreshed_at else None
            ),
        }


class OpeningsStatusSensor(SensorEntity):
    """Human-readable openings validation status."""

    _attr_has_entity_name = True
    _attr_name = "Openings status"
    _attr_icon = "mdi:door-sliding-lock"

    def __init__(self, guard: OpeningsGuard) -> None:
        self._guard = guard
        self._attr_unique_id = "sensorlinx_openings_status"
        guard.register_entity(self)

    @property
    def device_info(self) -> dict[str, Any]:
        return _device_info()

    @property
    def native_value(self) -> str:
        snap = self._guard.snapshot
        if snap.conflict_names and snap.any_open:
            return "open_with_conflicts"
        if snap.conflict_names:
            return "closed_authority_override"
        if snap.any_open:
            return "open"
        return "all_closed"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._guard.status_dict()


async def async_setup_openings_guard(
    hass: HomeAssistant, entry: ConfigEntry
) -> OpeningsGuard:
    """Create and start the openings guard."""
    guard = OpeningsGuard(hass, entry)
    await guard.async_setup()

    async def handle_refresh_openings(call) -> None:
        await guard.async_refresh(force_update=True, reason="service")

    hass.services.async_register(DOMAIN, "refresh_openings", handle_refresh_openings)
    return guard


def get_openings_binary_sensor_entities(guard: OpeningsGuard) -> list[BinarySensorEntity]:
    """Binary sensors for openings guard."""
    return [OpeningsAnyOpenBinarySensor(guard)]


def get_openings_sensor_entities(guard: OpeningsGuard) -> list[SensorEntity]:
    """Status sensors for openings guard."""
    return [OpeningsStatusSensor(guard)]
