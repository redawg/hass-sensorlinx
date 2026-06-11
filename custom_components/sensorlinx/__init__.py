"""HBX SensorLinx Home Assistant integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from pysensorlinx import InvalidCredentialsError, LoginError, Sensorlinx

from .const import CONF_BUILDING_ID, DOMAIN
from .coordinator import SensorlinxCoordinator
from .external_control import SensorlinxExternalControl
from .outdoor_reset import async_setup_outdoor_reset
from .daily_report_scheduler import async_setup_daily_report
from .thermal_logger import async_setup_thermal_logger

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.CLIMATE,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up HBX SensorLinx from a config entry."""
    api = Sensorlinx()
    try:
        await api.login(entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])
    except InvalidCredentialsError as err:
        _LOGGER.error("Invalid SensorLinx credentials")
        raise ConfigEntryNotReady("Invalid credentials") from err
    except LoginError as err:
        _LOGGER.error("Could not log in to SensorLinx")
        raise ConfigEntryNotReady("Cannot connect to SensorLinx") from err

    coordinator = SensorlinxCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()

    external_control = SensorlinxExternalControl(hass, entry, coordinator)
    await external_control.async_setup()

    outdoor_reset = await async_setup_outdoor_reset(hass, entry, coordinator)
    thermal_logger = await async_setup_thermal_logger(hass, coordinator, outdoor_reset)
    await async_setup_daily_report(hass)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator
    hass.data[DOMAIN][f"{entry.entry_id}_external"] = external_control
    hass.data[DOMAIN][f"{entry.entry_id}_outdoor_reset"] = outdoor_reset
    hass.data[DOMAIN][f"{entry.entry_id}_thermal_logger"] = thermal_logger

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when external switch config changes via options flow.

    Entity-level option saves (valve counts, floor mode, floor targets,
    hydronic sensors) set a skip flag to prevent unnecessary reloads.
    """
    domain_data = hass.data.get(DOMAIN, {})
    if domain_data.pop(f"{entry.entry_id}_skip_reload", False):
        return
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        thermal_logger = hass.data[DOMAIN].pop(f"{entry.entry_id}_thermal_logger", None)
        if thermal_logger is not None:
            thermal_logger.async_unload()
        outdoor_reset = hass.data[DOMAIN].pop(f"{entry.entry_id}_outdoor_reset", None)
        if outdoor_reset is not None:
            outdoor_reset.async_unload()
        external: SensorlinxExternalControl | None = hass.data[DOMAIN].pop(
            f"{entry.entry_id}_external", None
        )
        if external is not None:
            external.async_unload()
        coordinator: SensorlinxCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.api.close()
    return unload_ok
