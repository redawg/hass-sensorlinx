"""Config flow for HBX SensorLinx."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector
from pysensorlinx import InvalidCredentialsError, LoginError, Sensorlinx

from .const import (
    CONF_BUILDING_ID,
    CONF_HEATED_FLOOR_CONTROLLER,
    CONF_HOT_WATER_SWITCH,
    CONF_RADIANT_FLOOR_SWITCH,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


async def _validate_login(hass: HomeAssistant, username: str, password: str) -> list[dict[str, Any]]:
    """Log in and return available buildings."""
    api = Sensorlinx()
    try:
        await api.login(username, password)
        buildings = await api.get_buildings()
    except InvalidCredentialsError as err:
        raise InvalidAuth from err
    except LoginError as err:
        raise CannotConnect from err
    except Exception as err:
        _LOGGER.exception("Unexpected error during SensorLinx login")
        raise CannotConnect from err
    finally:
        await api.close()

    if not buildings:
        raise NoBuildings

    if isinstance(buildings, dict):
        return [buildings]
    return list(buildings)


class SensorlinxConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for HBX SensorLinx."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize."""
        self._username: str | None = None
        self._password: str | None = None
        self._buildings: list[dict[str, Any]] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._username = user_input[CONF_USERNAME]
            self._password = user_input[CONF_PASSWORD]
            try:
                self._buildings = await _validate_login(
                    self.hass, self._username, self._password
                )
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except NoBuildings:
                errors["base"] = "no_buildings"
            else:
                if len(self._buildings) == 1:
                    return await self._create_entry(self._buildings[0])
                return await self.async_step_building()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_building(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Let the user pick a building when multiple exist."""
        if user_input is not None:
            building_id = user_input[CONF_BUILDING_ID]
            building = next(
                (b for b in self._buildings if b.get("_id") == building_id),
                None,
            )
            if building is None:
                return self.async_show_form(
                    step_id="building",
                    data_schema=self._building_schema(),
                    errors={"base": "building_not_found"},
                )
            return await self._create_entry(building)

        return self.async_show_form(
            step_id="building",
            data_schema=self._building_schema(),
        )

    def _building_schema(self) -> vol.Schema:
        """Schema for building selection."""
        return vol.Schema(
            {
                vol.Required(CONF_BUILDING_ID): vol.In(
                    {
                        b["_id"]: b.get("name") or b["_id"]
                        for b in self._buildings
                        if b.get("_id")
                    }
                )
            }
        )

    async def _create_entry(self, building: dict[str, Any]) -> config_entries.ConfigFlowResult:
        """Create the config entry."""
        building_id = building["_id"]
        await self.async_set_unique_id(building_id)
        self._abort_if_unique_id_configured()

        title = building.get("name") or "HBX SensorLinx"
        return self.async_create_entry(
            title=title,
            data={
                CONF_USERNAME: self._username,
                CONF_PASSWORD: self._password,
                CONF_BUILDING_ID: building_id,
            },
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate invalid credentials."""


class NoBuildings(HomeAssistantError):
    """Error to indicate the account has no buildings."""


def _external_switch_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Schema for linking physical HA switches to SensorLinx."""
    switch_selector = selector.EntitySelector(
        selector.EntitySelectorConfig(domain=["switch"])
    )
    floor_selector = selector.EntitySelector(
        selector.EntitySelectorConfig(domain=["switch", "light"])
    )
    return vol.Schema(
        {
            vol.Optional(
                CONF_HOT_WATER_SWITCH,
                default=defaults.get(CONF_HOT_WATER_SWITCH, ""),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["switch"])
            ),
            vol.Optional(
                CONF_RADIANT_FLOOR_SWITCH,
                default=defaults.get(CONF_RADIANT_FLOOR_SWITCH, ""),
            ): switch_selector,
            vol.Optional(
                CONF_HEATED_FLOOR_CONTROLLER,
                default=defaults.get(CONF_HEATED_FLOOR_CONTROLLER, ""),
            ): floor_selector,
        }
    )


@callback
def async_get_options_flow(
    config_entry: config_entries.ConfigEntry,
) -> config_entries.OptionsFlow:
    """Return the options flow handler."""
    return SensorlinxOptionsFlowHandler(config_entry)


class SensorlinxOptionsFlowHandler(config_entries.OptionsFlow):
    """Options flow for linking external HA switches."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Manage external switch links."""
        if user_input is not None:
            cleaned = {
                key: value.strip()
                for key, value in user_input.items()
                if isinstance(value, str) and value.strip()
            }
            return self.async_create_entry(title="", data=cleaned)

        return self.async_show_form(
            step_id="init",
            data_schema=_external_switch_schema(dict(self._config_entry.options)),
        )
