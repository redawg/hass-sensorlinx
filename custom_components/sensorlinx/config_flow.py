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

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return the options flow handler."""
        return SensorlinxOptionsFlowHandler(config_entry)

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


CONF_SUPPLY_ENTITY = "supply_entity_id"
CONF_SUPPLY_TEMP_SENSOR = "supply_temp_sensor"
CONF_RETURN_TEMP_SENSOR = "return_temp_sensor"
CONF_FLOW_RATE_SENSOR = "flow_rate_sensor"
CONF_FORECAST_ENTITY = "forecast_entity_id"
CONF_ZONE_VALVE_PREFIX = "zone_valves_"


def _external_switch_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Schema for linking physical HA switches to SensorLinx."""
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
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["switch"])
            ),
            vol.Optional(
                CONF_HEATED_FLOOR_CONTROLLER,
                default=defaults.get(CONF_HEATED_FLOOR_CONTROLLER, ""),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["switch", "light"])
            ),
        }
    )


def _hydronic_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Schema for hydronic loop and supply water configuration."""
    return vol.Schema(
        {
            vol.Optional(
                CONF_SUPPLY_ENTITY,
                default=defaults.get(CONF_SUPPLY_ENTITY, ""),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["water_heater", "climate", "number"])
            ),
            vol.Optional(
                CONF_SUPPLY_TEMP_SENSOR,
                default=defaults.get(CONF_SUPPLY_TEMP_SENSOR, ""),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["sensor"], device_class="temperature")
            ),
            vol.Optional(
                CONF_RETURN_TEMP_SENSOR,
                default=defaults.get(CONF_RETURN_TEMP_SENSOR, ""),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["sensor"], device_class="temperature")
            ),
            vol.Optional(
                CONF_FLOW_RATE_SENSOR,
                default=defaults.get(CONF_FLOW_RATE_SENSOR, ""),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["sensor"])
            ),
            vol.Optional(
                CONF_FORECAST_ENTITY,
                default=defaults.get(CONF_FORECAST_ENTITY, ""),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["weather"])
            ),
        }
    )


def _zone_valves_schema(zone_names: list[tuple[str, str]], defaults: dict[str, Any]) -> vol.Schema:
    """Schema for per-zone valve count configuration."""
    schema_dict: dict[Any, Any] = {}
    for zone_key, zone_label in zone_names:
        key = f"{CONF_ZONE_VALVE_PREFIX}{zone_key}"
        schema_dict[vol.Optional(key, default=defaults.get(key, 1))] = selector.NumberSelector(
            selector.NumberSelectorConfig(min=1, max=6, step=1, mode="slider")
        )
    return vol.Schema(schema_dict)


class SensorlinxOptionsFlowHandler(config_entries.OptionsFlow):
    """Options flow for external switches, hydronic sensors, and zone valve counts."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize."""
        self._config_entry = config_entry
        self._options: dict[str, Any] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 1: External switch links."""
        if user_input is not None:
            self._options.update(
                {k: v.strip() for k, v in user_input.items() if isinstance(v, str) and v.strip()}
            )
            return await self.async_step_hydronic()

        return self.async_show_form(
            step_id="init",
            data_schema=_external_switch_schema(dict(self._config_entry.options)),
            description_placeholders={"step_title": "External Switches"},
        )

    async def async_step_hydronic(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 2: Hydronic sensors and supply water entity."""
        if user_input is not None:
            self._options.update(
                {k: v.strip() for k, v in user_input.items() if isinstance(v, str) and v.strip()}
            )
            return await self.async_step_zone_valves()

        return self.async_show_form(
            step_id="hydronic",
            data_schema=_hydronic_schema(dict(self._config_entry.options)),
        )

    async def async_step_zone_valves(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 3: Per-zone valve counts."""
        if user_input is not None:
            self._options.update(user_input)
            return await self._save_all_options()

        zone_names = self._get_zone_names()
        if not zone_names:
            return await self._save_all_options()

        return self.async_show_form(
            step_id="zone_valves",
            data_schema=_zone_valves_schema(zone_names, dict(self._config_entry.options)),
        )

    def _get_zone_names(self) -> list[tuple[str, str]]:
        """Get zone key/label pairs from the coordinator."""
        domain_data = self.hass.data.get(DOMAIN, {})
        coordinator = domain_data.get(self._config_entry.entry_id)
        if coordinator is None:
            return []
        zones: list[tuple[str, str]] = []
        for thm in coordinator.get_thm_devices():
            zone_key = thm.name.lower().replace(" ", "_")
            zones.append((zone_key, thm.name))
        return zones

    async def _save_all_options(self) -> config_entries.ConfigFlowResult:
        """Persist all options and propagate to the outdoor reset controller."""
        new_options = dict(self._config_entry.options)
        new_options.update(self._options)

        # Propagate to the outdoor reset controller if loaded
        domain_data = self.hass.data.get(DOMAIN, {})
        controller = domain_data.get(f"{self._config_entry.entry_id}_outdoor_reset")
        if controller is not None:
            if new_options.get(CONF_SUPPLY_ENTITY):
                controller.params.supply_entity_id = new_options[CONF_SUPPLY_ENTITY]
            if new_options.get(CONF_SUPPLY_TEMP_SENSOR):
                controller.params.supply_temp_sensor = new_options[CONF_SUPPLY_TEMP_SENSOR]
            if new_options.get(CONF_RETURN_TEMP_SENSOR):
                controller.params.return_temp_sensor = new_options[CONF_RETURN_TEMP_SENSOR]
            if new_options.get(CONF_FLOW_RATE_SENSOR):
                controller.params.flow_rate_sensor = new_options[CONF_FLOW_RATE_SENSOR]
            if new_options.get(CONF_FORECAST_ENTITY):
                controller.params.forecast_entity_id = new_options[CONF_FORECAST_ENTITY]
            for key, value in new_options.items():
                if key.startswith(CONF_ZONE_VALVE_PREFIX):
                    zone_key = key[len(CONF_ZONE_VALVE_PREFIX):]
                    controller.params.zone_valve_counts[zone_key] = int(value)

        return self.async_create_entry(title="", data=new_options)
