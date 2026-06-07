"""Sync external HA switches with SensorLinx floor / hot-water state."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from pysensorlinx import SensorlinxDevice, ThmDevice, ZonDevice

from .const import (
    CONF_HEATED_FLOOR_CONTROLLER,
    CONF_HOT_WATER_SWITCH,
    CONF_RADIANT_FLOOR_SWITCH,
)
from .coordinator import SensorlinxCoordinator, SensorlinxDeviceData

_LOGGER = logging.getLogger(__name__)

THM_HVAC_KEYS = ("auto", "heat", "cool", "off")


@dataclass
class _ThmSnapshot:
    """Saved THM state before an external shutdown."""

    hvac_mode: str
    away: bool


@dataclass
class _HotWaterSnapshot:
    """Saved hot-water related state."""

    dhw_enabled: dict[str, bool] = field(default_factory=dict)
    thm_away: dict[str, bool] = field(default_factory=dict)


@dataclass
class _RadiantSnapshot:
    """Saved radiant-floor state."""

    thm: dict[str, _ThmSnapshot] = field(default_factory=dict)
    app_button: dict[str, bool] = field(default_factory=dict)


def _entity_is_on(state: str | None) -> bool:
    """Return True when an on/off entity is considered active."""
    return state == STATE_ON


def _thm_away_from_raw(raw: dict[str, Any]) -> bool:
    """Read away-mode flag from a THM device payload."""
    away = raw.get("awayMode")
    if isinstance(away, dict):
        return bool(away.get("activated"))
    return bool(raw.get("away"))


def _thm_hvac_from_raw(raw: dict[str, Any]) -> str:
    """Read the active THM changeover mode from device payload."""
    for entry in raw.get("changeover", []) or []:
        if isinstance(entry, dict) and entry.get("activated"):
            key = entry.get("key")
            if isinstance(key, str) and key in THM_HVAC_KEYS:
                return key
    return "off"


def _zon_app_button_from_raw(raw: dict[str, Any]) -> bool:
    """Read ZON app-button state from device payload."""
    block = raw.get("appButton")
    if isinstance(block, dict):
        return bool(block.get("activated"))
    return bool(raw.get("aBut"))


def _dhw_enabled_from_raw(raw: dict[str, Any]) -> bool | None:
    """Read DHW-enabled flag when present on a device payload."""
    block = raw.get("dhwOn")
    if isinstance(block, dict):
        if "value" in block:
            return bool(block["value"])
        if "activated" in block:
            return bool(block["activated"])
    if "dhwOn" in raw:
        return bool(raw["dhwOn"])
    return None


class SensorlinxExternalControl:
    """Mirror physical HA switches into SensorLinx cloud state."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coordinator: SensorlinxCoordinator,
    ) -> None:
        """Initialize."""
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self._unsub: list[Callable[[], None]] = []
        self._hot_water_snapshot: _HotWaterSnapshot | None = None
        self._radiant_snapshot: _RadiantSnapshot | None = None

    async def async_setup(self) -> None:
        """Register listeners for configured external entities."""
        links = (
            (CONF_HOT_WATER_SWITCH, self._on_hot_water_change),
            (CONF_RADIANT_FLOOR_SWITCH, self._on_radiant_floor_change),
            (CONF_HEATED_FLOOR_CONTROLLER, self._on_radiant_floor_change),
        )
        for option_key, handler in links:
            entity_id = self.entry.options.get(option_key)
            if not entity_id:
                continue
            state = self.hass.states.get(entity_id)
            if state is None:
                _LOGGER.warning(
                    "Configured external entity %s (%s) does not exist yet",
                    entity_id,
                    option_key,
                )
            self._unsub.append(
                async_track_state_change_event(
                    self.hass,
                    [entity_id],
                    self._wrap_handler(option_key, handler),
                )
            )
            _LOGGER.info(
                "Linked SensorLinx to external entity %s (%s)",
                entity_id,
                option_key,
            )
            if state is not None:
                await handler(entity_id, state.state)

    def async_unload(self) -> None:
        """Remove listeners."""
        for unsub in self._unsub:
            unsub()
        self._unsub.clear()

    def _wrap_handler(
        self,
        option_key: str,
        handler: Callable[[str, str | None], Any],
    ) -> Callable[[Event], Any]:
        """Return an event callback that ignores our own writes."""

        @callback
        def _on_event(event: Event) -> None:
            entity_id = event.data.get("entity_id")
            new_state = event.data.get("new_state")
            state = new_state.state if new_state else None
            self.hass.async_create_task(handler(entity_id, state))

        return _on_event

    async def _on_hot_water_change(self, entity_id: str, state: str | None) -> None:
        """React to the hot-water heater switch."""
        if _entity_is_on(state):
            await self._restore_hot_water()
            if not self._any_radiant_switch_off():
                await self._restore_radiant()
        else:
            await self._shutdown_hot_water()

    async def _on_radiant_floor_change(self, entity_id: str, state: str | None) -> None:
        """React to radiant-floor or heated-floor controller switches."""
        if not self._radiant_switches_configured():
            return
        if self._any_radiant_switch_off():
            await self._shutdown_radiant()
        else:
            await self._restore_radiant()

    def _radiant_switches_configured(self) -> bool:
        """Return True when at least one radiant link is configured."""
        return bool(
            self.entry.options.get(CONF_RADIANT_FLOOR_SWITCH)
            or self.entry.options.get(CONF_HEATED_FLOOR_CONTROLLER)
        )

    def _radiant_switch_entity_ids(self) -> list[str]:
        """Return configured radiant / heated-floor entity IDs."""
        ids: list[str] = []
        for key in (CONF_RADIANT_FLOOR_SWITCH, CONF_HEATED_FLOOR_CONTROLLER):
            entity_id = self.entry.options.get(key)
            if entity_id:
                ids.append(entity_id)
        return ids

    def _any_radiant_switch_off(self) -> bool:
        """Return True when any linked radiant switch is off."""
        for entity_id in self._radiant_switch_entity_ids():
            state = self.hass.states.get(entity_id)
            if state is None or not _entity_is_on(state.state):
                return True
        return False

    def _hot_water_switch_off(self) -> bool:
        """Return True when the linked hot-water switch is off."""
        entity_id = self.entry.options.get(CONF_HOT_WATER_SWITCH)
        if not entity_id:
            return False
        state = self.hass.states.get(entity_id)
        return state is None or not _entity_is_on(state.state)

    async def _shutdown_hot_water(self) -> None:
        """Disable hot-water demand in SensorLinx when the heater switch is off."""
        if self._hot_water_snapshot is not None:
            return

        snapshot = _HotWaterSnapshot()
        for device_data in self._dhw_capable_devices():
            raw = device_data.raw
            enabled = _dhw_enabled_from_raw(raw)
            if enabled is not None:
                snapshot.dhw_enabled[device_data.device_id] = enabled
                if enabled:
                    await self._set_dhw_enabled(device_data, False)

        for device_data in self.coordinator.get_thm_devices():
            away = _thm_away_from_raw(device_data.raw)
            snapshot.thm_away[device_data.device_id] = away
            if not away:
                await device_data.device.set_away_mode(True)

        self._hot_water_snapshot = snapshot
        await self.coordinator.async_request_refresh()
        _LOGGER.info("Hot-water external switch off: SensorLinx hot water suppressed")

    async def _restore_hot_water(self) -> None:
        """Restore hot-water state after the heater switch returns on."""
        snapshot = self._hot_water_snapshot
        if snapshot is None:
            return

        for device_data in self._dhw_capable_devices():
            enabled = snapshot.dhw_enabled.get(device_data.device_id)
            if enabled:
                await self._set_dhw_enabled(device_data, True)

        if not self._any_radiant_switch_off():
            for device_data in self.coordinator.get_thm_devices():
                away = snapshot.thm_away.get(device_data.device_id)
                if away is False:
                    await device_data.device.set_away_mode(False)

        self._hot_water_snapshot = None
        await self.coordinator.async_request_refresh()
        _LOGGER.info("Hot-water external switch on: SensorLinx hot water restored")

    async def _shutdown_radiant(self) -> None:
        """Stop floor heating demand when radiant controller switches are off."""
        if self._radiant_snapshot is not None:
            return

        snapshot = _RadiantSnapshot()
        for device_data in self.coordinator.get_thm_devices():
            raw = device_data.raw
            snapshot.thm[device_data.device_id] = _ThmSnapshot(
                hvac_mode=_thm_hvac_from_raw(raw),
                away=_thm_away_from_raw(raw),
            )
            device: ThmDevice = device_data.device
            if _thm_hvac_from_raw(raw) != "off":
                await device.set_hvac_mode("off")
            if not _thm_away_from_raw(raw):
                await device.set_away_mode(True)

        for device_data in self.coordinator.get_zon_devices():
            raw = device_data.raw
            active = _zon_app_button_from_raw(raw)
            snapshot.app_button[device_data.device_id] = active
            if active:
                await device_data.device.set_app_button(False)

        self._radiant_snapshot = snapshot
        await self.coordinator.async_request_refresh()
        _LOGGER.info(
            "Radiant-floor external switch off: SensorLinx floor heating suppressed"
        )

    async def _restore_radiant(self) -> None:
        """Restore floor heating after radiant controller switches return on."""
        snapshot = self._radiant_snapshot
        if snapshot is None:
            return
        if self._hot_water_switch_off():
            return

        for device_data in self.coordinator.get_thm_devices():
            saved = snapshot.thm.get(device_data.device_id)
            if saved is None:
                continue
            device: ThmDevice = device_data.device
            if saved.hvac_mode != "off":
                await device.set_hvac_mode(saved.hvac_mode)
            if not saved.away:
                await device.set_away_mode(False)

        for device_data in self.coordinator.get_zon_devices():
            if snapshot.app_button.get(device_data.device_id):
                await device_data.device.set_app_button(True)

        self._radiant_snapshot = None
        await self.coordinator.async_request_refresh()
        _LOGGER.info(
            "Radiant-floor external switch on: SensorLinx floor heating restored"
        )

    def _dhw_capable_devices(self) -> list[SensorlinxDeviceData]:
        """Return ZON and ECO devices that expose DHW enable."""
        devices: list[SensorlinxDeviceData] = []
        devices.extend(self.coordinator.get_zon_devices())
        devices.extend(self.coordinator.get_eco_devices())
        return devices

    async def _set_dhw_enabled(
        self,
        device_data: SensorlinxDeviceData,
        enabled: bool,
    ) -> None:
        """Call set_dhw_enabled when the device helper supports it."""
        device = device_data.device
        if not isinstance(device, SensorlinxDevice):
            return
        await device.set_dhw_enabled(enabled)
