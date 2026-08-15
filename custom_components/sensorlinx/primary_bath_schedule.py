"""Backup schedule for Primary Bath Watts tile floor."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)

from .const import (
    DEFAULT_ANDREW_PRESENCE_ENTITIES,
    DEFAULT_BECKY_PRESENCE_ENTITIES,
    DEFAULT_PRIMARY_BATH_CLIMATE,
)

if TYPE_CHECKING:
    from .outdoor_reset import OutdoorResetController

_LOGGER = logging.getLogger(__name__)

CLIMATE = DEFAULT_PRIMARY_BATH_CLIMATE
ZONE_KEY = "primary_bath"
NIGHT_START_HOUR = 21  # 9 PM
DAY_START_HOUR = 7  # 7 AM
BEDTIME_START_HOUR = 23
BEDTIME_START_MINUTE = 30  # 11:30 PM
BEDTIME_END_HOUR = 1  # through 1:00 AM
BEDTIME_END_MINUTE = 0
NIGHT_TEMP = 75.0
DAY_TEMP = 71.0
BEDTIME_TEMP = 77.0
WATCHDOG_INTERVAL = timedelta(hours=1)

_PERSON_AWAY_STATES = frozenset({STATE_UNKNOWN, STATE_UNAVAILABLE, "not_home"})


def _now(now: datetime | None) -> datetime:
    return now or datetime.now()


def _minutes_since_midnight(now: datetime) -> int:
    return now.hour * 60 + now.minute


def _is_bedtime_boost(now: datetime | None = None) -> bool:
    """11:30 PM through 1:00 AM for shower / bedtime."""
    current = _now(now)
    minute = _minutes_since_midnight(current)
    start = BEDTIME_START_HOUR * 60 + BEDTIME_START_MINUTE
    end = BEDTIME_END_HOUR * 60 + BEDTIME_END_MINUTE
    return minute >= start or minute <= end


def _is_night(now: datetime | None = None) -> bool:
    hour = _now(now).hour
    return hour >= NIGHT_START_HOUR or hour < DAY_START_HOUR


def _target_temp(now: datetime | None = None) -> float:
    if _is_bedtime_boost(now):
        return BEDTIME_TEMP
    if _is_night(now):
        return NIGHT_TEMP
    return DAY_TEMP


def _period_label(now: datetime | None = None) -> str:
    if _is_bedtime_boost(now):
        return "bedtime"
    if _is_night(now):
        return "night"
    return "day"


def _entity_home(hass: HomeAssistant, entity_id: str) -> bool:
    """True when a person or device_tracker reports home at Forest house."""
    state = hass.states.get(entity_id)
    if state is None:
        return False
    value = state.state
    if value in (STATE_UNKNOWN, STATE_UNAVAILABLE):
        return False
    if entity_id.startswith("person."):
        return value not in _PERSON_AWAY_STATES
    return value == "home"


def _any_home(hass: HomeAssistant, entity_ids: tuple[str, ...]) -> bool:
    return any(_entity_home(hass, entity_id) for entity_id in entity_ids)


def _both_occupants_away(hass: HomeAssistant) -> bool:
    """Both Andrew and Becky away — safe to shut off Primary Bath floor heat."""
    return not _any_home(hass, DEFAULT_ANDREW_PRESENCE_ENTITIES) and not _any_home(
        hass, DEFAULT_BECKY_PRESENCE_ENTITIES
    )


def _presence_entities(hass: HomeAssistant) -> list[str]:
    entities: list[str] = []
    for entity_id in (
        *DEFAULT_ANDREW_PRESENCE_ENTITIES,
        *DEFAULT_BECKY_PRESENCE_ENTITIES,
    ):
        if hass.states.get(entity_id) is not None:
            entities.append(entity_id)
    return entities


class PrimaryBathFloorSchedule:
    """Push day/night setpoints to the Watts climate entity when WWSD allows."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        controller: OutdoorResetController | None = None,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self._controller = controller
        self._unsub: list[Callable[[], None]] = []

    def _wwsd_active(self) -> bool:
        controller = self._controller
        if controller is None or not controller.enabled:
            return False
        outdoor = controller.outdoor_temp
        if outdoor is None:
            return False
        if controller.preheat_active:
            return False
        return controller.is_zone_shutdown(ZONE_KEY, outdoor)

    async def async_setup(self) -> None:
        """Start schedule timers and apply the current period."""
        if self.hass.states.get(CLIMATE) is None:
            _LOGGER.warning("Primary Bath floor climate %s not found", CLIMATE)
            return

        async def _on_boundary(now: datetime) -> None:
            await self.async_apply(reason="boundary")

        self._unsub.append(
            async_track_time_change(
                self.hass,
                _on_boundary,
                hour=[NIGHT_START_HOUR, DAY_START_HOUR],
                minute=0,
                second=0,
            )
        )
        self._unsub.append(
            async_track_time_change(
                self.hass,
                _on_boundary,
                hour=[BEDTIME_START_HOUR],
                minute=[BEDTIME_START_MINUTE],
                second=0,
            )
        )
        self._unsub.append(
            async_track_time_change(
                self.hass,
                _on_boundary,
                hour=[BEDTIME_END_HOUR],
                minute=[BEDTIME_END_MINUTE],
                second=0,
            )
        )
        self._unsub.append(
            async_track_time_interval(
                self.hass, self._async_watchdog, WATCHDOG_INTERVAL
            )
        )

        presence_entities = _presence_entities(self.hass)
        if presence_entities:
            self._unsub.append(
                async_track_state_change_event(
                    self.hass, presence_entities, self._on_presence_change
                )
            )
        else:
            _LOGGER.warning(
                "Primary Bath away shutdown: no occupancy entities found"
            )

        await self.async_apply(reason="startup")

    def async_unload(self) -> None:
        """Remove listeners."""
        for unsub in self._unsub:
            unsub()
        self._unsub.clear()

    @callback
    def _on_presence_change(self, _event) -> None:
        self.hass.async_create_task(self._async_on_presence_change())

    async def _async_on_presence_change(self) -> None:
        if _both_occupants_away(self.hass):
            await self._async_turn_off(reason="occupancy_away")
            return
        await self.async_apply(reason="occupancy_return")

    async def _async_watchdog(self, _now=None) -> None:
        """Re-apply schedule or enforce WWSD / away if the stat drifted."""
        if _both_occupants_away(self.hass):
            state = self.hass.states.get(CLIMATE)
            if state is not None and state.state != STATE_OFF:
                await self._async_turn_off(reason="watchdog_away")
            return

        if self._wwsd_active():
            state = self.hass.states.get(CLIMATE)
            if state is not None and state.state != STATE_OFF:
                await self._async_turn_off(reason="watchdog_wwsd")
            return

        state = self.hass.states.get(CLIMATE)
        if state is None:
            return
        target = _target_temp()
        current = state.attributes.get("temperature")
        try:
            current_f = float(current) if current is not None else None
        except (TypeError, ValueError):
            current_f = None
        if state.state == STATE_OFF or current_f != target:
            await self.async_apply(reason="watchdog")

    async def _async_turn_off(self, *, reason: str) -> None:
        controller = self._controller
        outdoor = controller.outdoor_temp if controller else None
        away = _both_occupants_away(self.hass)
        _LOGGER.info(
            "Primary Bath floor off (%s): turning off (outdoor=%s, occupants_away=%s)",
            reason,
            f"{outdoor:.1f}F" if outdoor is not None else "unknown",
            away,
        )
        await self.hass.services.async_call(
            "climate",
            "turn_off",
            {"entity_id": CLIMATE},
            blocking=True,
        )

    async def async_apply(self, *, reason: str = "manual") -> None:
        """Turn on heat and set the scheduled floor target when allowed."""
        if _both_occupants_away(self.hass):
            await self._async_turn_off(reason=f"{reason}_away")
            return

        if self._wwsd_active():
            await self._async_turn_off(reason=f"{reason}_wwsd")
            return

        target = _target_temp()
        period = _period_label()
        _LOGGER.info(
            "Primary Bath floor schedule (%s): setting heat to %.0fF (%s)",
            reason,
            target,
            period,
        )
        await self.hass.services.async_call(
            "climate",
            "turn_on",
            {"entity_id": CLIMATE},
            blocking=True,
        )
        await self.hass.services.async_call(
            "climate",
            "set_hvac_mode",
            {"entity_id": CLIMATE, "hvac_mode": "heat"},
            blocking=True,
        )
        await self.hass.services.async_call(
            "climate",
            "set_temperature",
            {"entity_id": CLIMATE, "temperature": target, "hvac_mode": "heat"},
            blocking=True,
        )


async def async_setup_primary_bath_schedule(
    hass: HomeAssistant,
    entry: ConfigEntry,
    controller: OutdoorResetController | None = None,
) -> PrimaryBathFloorSchedule:
    """Create and start the Primary Bath backup schedule."""
    scheduler = PrimaryBathFloorSchedule(hass, entry, controller)

    async def handle_refresh(_call) -> None:
        await scheduler.async_apply(reason="service")

    hass.services.async_register(
        "sensorlinx", "apply_primary_bath_floor_schedule", handle_refresh
    )
    await scheduler.async_setup()
    return scheduler
