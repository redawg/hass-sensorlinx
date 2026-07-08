"""Config flow for HBX SensorLinx."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er, device_registry as dr, selector
from pysensorlinx import InvalidCredentialsError, LoginError, Sensorlinx

from .const import (
    CONF_BUILDING_ID,
    CONF_HEATED_FLOOR_CONTROLLER,
    CONF_HOT_WATER_SWITCH,
    CONF_MAIN_FLOOR_TEMP_SENSOR,
    CONF_MAIN_HVAC_CLIMATE,
    CONF_RADIANT_FLOOR_SWITCH,
    CONF_UPSTAIRS_TEMP_SENSOR,
    DEFAULT_MAIN_FLOOR_TEMP_SENSOR,
    DEFAULT_MAIN_HVAC_CLIMATE,
    DEFAULT_UPSTAIRS_TEMP_SENSOR,
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
CONF_HEATING_SOURCE = "heating_source_device"
CONF_ELECTRICITY_COST = "electricity_cost_per_kwh"
CONF_ZONE_VALVE_PREFIX = "zone_valves_"
DEFAULT_ELECTRICITY_COST = 0.105

OPTIMALTANKLESS_DOMAIN = "optimaltankless"


def _external_switch_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Schema for linking physical HA switches to SensorLinx."""
    schema_dict: dict[Any, Any] = {}
    hot_water = defaults.get(CONF_HOT_WATER_SWITCH)
    if hot_water:
        schema_dict[vol.Optional(CONF_HOT_WATER_SWITCH, default=hot_water)] = (
            selector.EntitySelector(selector.EntitySelectorConfig(domain=["switch"]))
        )
    else:
        schema_dict[vol.Optional(CONF_HOT_WATER_SWITCH)] = (
            selector.EntitySelector(selector.EntitySelectorConfig(domain=["switch"]))
        )
    radiant = defaults.get(CONF_RADIANT_FLOOR_SWITCH)
    if radiant:
        schema_dict[vol.Optional(CONF_RADIANT_FLOOR_SWITCH, default=radiant)] = (
            selector.EntitySelector(selector.EntitySelectorConfig(domain=["switch"]))
        )
    else:
        schema_dict[vol.Optional(CONF_RADIANT_FLOOR_SWITCH)] = (
            selector.EntitySelector(selector.EntitySelectorConfig(domain=["switch"]))
        )
    return vol.Schema(schema_dict)


def _heating_source_schema(
    water_heaters: dict[str, str], defaults: dict[str, Any]
) -> vol.Schema:
    """Schema for selecting floor heating source from available water heaters."""
    schema_dict: dict[Any, Any] = {}
    if water_heaters:
        heating_default = defaults.get(CONF_HEATING_SOURCE)
        if heating_default:
            schema_dict[vol.Optional(CONF_HEATING_SOURCE, default=heating_default)] = (
                selector.SelectSelector(selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value=eid, label=label)
                        for eid, label in water_heaters.items()
                    ],
                    mode="dropdown",
                ))
            )
        else:
            schema_dict[vol.Optional(CONF_HEATING_SOURCE)] = (
                selector.SelectSelector(selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value=eid, label=label)
                        for eid, label in water_heaters.items()
                    ],
                    mode="dropdown",
                ))
            )
    forecast = defaults.get(CONF_FORECAST_ENTITY)
    if forecast:
        schema_dict[vol.Optional(CONF_FORECAST_ENTITY, default=forecast)] = (
            selector.EntitySelector(selector.EntitySelectorConfig(domain=["weather"]))
        )
    else:
        schema_dict[vol.Optional(CONF_FORECAST_ENTITY)] = (
            selector.EntitySelector(selector.EntitySelectorConfig(domain=["weather"]))
        )
    cost_default = defaults.get(CONF_ELECTRICITY_COST, DEFAULT_ELECTRICITY_COST)
    schema_dict[vol.Optional(CONF_ELECTRICITY_COST, default=cost_default)] = (
        selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.01, max=2.0, step=0.001, mode="box", unit_of_measurement="$ / kWh"
            )
        )
    )
    hvac_default = defaults.get(CONF_MAIN_HVAC_CLIMATE, DEFAULT_MAIN_HVAC_CLIMATE)
    schema_dict[vol.Optional(CONF_MAIN_HVAC_CLIMATE, default=hvac_default)] = (
        selector.EntitySelector(selector.EntitySelectorConfig(domain=["climate"]))
    )
    upstairs_default = defaults.get(CONF_UPSTAIRS_TEMP_SENSOR, DEFAULT_UPSTAIRS_TEMP_SENSOR)
    schema_dict[vol.Optional(CONF_UPSTAIRS_TEMP_SENSOR, default=upstairs_default)] = (
        selector.EntitySelector(selector.EntitySelectorConfig(domain=["sensor"]))
    )
    main_floor_default = defaults.get(
        CONF_MAIN_FLOOR_TEMP_SENSOR, DEFAULT_MAIN_FLOOR_TEMP_SENSOR
    )
    schema_dict[vol.Optional(CONF_MAIN_FLOOR_TEMP_SENSOR, default=main_floor_default)] = (
        selector.EntitySelector(selector.EntitySelectorConfig(domain=["sensor"]))
    )
    return vol.Schema(schema_dict)


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
    """Options flow for external switches, heating source, and zone valve counts."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize."""
        self._options: dict[str, Any] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 1: External switch links."""
        if user_input is not None:
            self._options.update(
                {k: v for k, v in user_input.items()
                 if isinstance(v, str) and v.strip()}
            )
            return await self.async_step_heating_source()

        return self.async_show_form(
            step_id="init",
            data_schema=_external_switch_schema(dict(self.config_entry.options)),
        )

    async def async_step_heating_source(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 2: Floor heating source (Optimal Tankless auto-discovery)."""
        if user_input is not None:
            selected = user_input.get(CONF_HEATING_SOURCE, "")
            if selected:
                self._options[CONF_HEATING_SOURCE] = selected
                self._auto_wire_heating_source(selected)
            forecast = user_input.get(CONF_FORECAST_ENTITY, "")
            if forecast:
                self._options[CONF_FORECAST_ENTITY] = forecast
            cost = user_input.get(CONF_ELECTRICITY_COST)
            if cost is not None:
                self._options[CONF_ELECTRICITY_COST] = float(cost)
            hvac = user_input.get(CONF_MAIN_HVAC_CLIMATE, "")
            if hvac:
                self._options[CONF_MAIN_HVAC_CLIMATE] = hvac
            upstairs = user_input.get(CONF_UPSTAIRS_TEMP_SENSOR, "")
            if upstairs:
                self._options[CONF_UPSTAIRS_TEMP_SENSOR] = upstairs
            main_floor = user_input.get(CONF_MAIN_FLOOR_TEMP_SENSOR, "")
            if main_floor:
                self._options[CONF_MAIN_FLOOR_TEMP_SENSOR] = main_floor
            return await self.async_step_zone_valves()

        water_heaters = self._discover_water_heaters()
        return self.async_show_form(
            step_id="heating_source",
            data_schema=_heating_source_schema(
                water_heaters, dict(self.config_entry.options)
            ),
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
            data_schema=_zone_valves_schema(zone_names, dict(self.config_entry.options)),
        )

    def _discover_water_heaters(self) -> dict[str, str]:
        """Find water heater entities, preferring Optimal Tankless integration."""
        ent_reg = er.async_get(self.hass)
        water_heaters: dict[str, str] = {}

        # Look for Optimal Tankless entities first
        for entry in ent_reg.entities.values():
            if entry.domain == "water_heater" and not entry.disabled:
                state = self.hass.states.get(entry.entity_id)
                name = entry.original_name or entry.entity_id
                if state:
                    name = state.attributes.get("friendly_name", name)
                # Mark Optimal Tankless entries prominently
                if entry.platform == OPTIMALTANKLESS_DOMAIN:
                    name = f"⚡ {name} (Optimal Tankless)"
                water_heaters[entry.entity_id] = name

        return water_heaters

    def _auto_wire_heating_source(self, water_heater_entity_id: str) -> None:
        """Auto-discover related sensors from the same device as the water heater."""
        ent_reg = er.async_get(self.hass)
        dev_reg = dr.async_get(self.hass)

        # Find the device that owns the selected water heater
        wh_entry = ent_reg.async_get(water_heater_entity_id)
        if wh_entry is None or wh_entry.device_id is None:
            self._options[CONF_SUPPLY_ENTITY] = water_heater_entity_id
            return

        device_id = wh_entry.device_id
        self._options[CONF_SUPPLY_ENTITY] = water_heater_entity_id

        # Find all sensor entities on the same device
        device_entities = er.async_entries_for_device(ent_reg, device_id)

        for ent in device_entities:
            if ent.disabled:
                continue
            eid = ent.entity_id
            orig_name = (ent.original_name or "").lower()

            if ent.domain == "sensor":
                if "outlet" in orig_name and "temperature" in orig_name:
                    self._options[CONF_SUPPLY_TEMP_SENSOR] = eid
                elif "inlet" in orig_name and "temperature" in orig_name:
                    self._options[CONF_RETURN_TEMP_SENSOR] = eid
                elif "flow_rate" in eid and "available" not in eid:
                    self._options[CONF_FLOW_RATE_SENSOR] = eid

        _LOGGER.info(
            "Auto-wired heating source from device %s: supply=%s, "
            "supply_temp=%s, return_temp=%s, flow=%s",
            device_id,
            self._options.get(CONF_SUPPLY_ENTITY),
            self._options.get(CONF_SUPPLY_TEMP_SENSOR),
            self._options.get(CONF_RETURN_TEMP_SENSOR),
            self._options.get(CONF_FLOW_RATE_SENSOR),
        )

    def _get_zone_names(self) -> list[tuple[str, str]]:
        """Get zone key/label pairs from the coordinator."""
        domain_data = self.hass.data.get(DOMAIN, {})
        coordinator = domain_data.get(self.config_entry.entry_id)
        if coordinator is None:
            return []
        zones: list[tuple[str, str]] = []
        for thm in coordinator.get_thm_devices():
            zone_key = thm.name.lower().replace(" ", "_")
            zones.append((zone_key, thm.name))
        return zones

    async def _save_all_options(self) -> config_entries.ConfigFlowResult:
        """Persist all options and propagate to the outdoor reset controller."""
        new_options = dict(self.config_entry.options)
        new_options.update(self._options)

        _LOGGER.info(
            "Saving options flow result (%d keys): %s",
            len(new_options), list(new_options.keys()),
        )

        # Propagate to the outdoor reset controller if loaded
        domain_data = self.hass.data.get(DOMAIN, {})
        controller = domain_data.get(f"{self.config_entry.entry_id}_outdoor_reset")
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
            if new_options.get(CONF_ELECTRICITY_COST) is not None:
                controller.params.electricity_cost_per_kwh = float(
                    new_options[CONF_ELECTRICITY_COST]
                )
            if new_options.get(CONF_MAIN_HVAC_CLIMATE):
                controller.params.main_hvac_climate_entity_id = new_options[
                    CONF_MAIN_HVAC_CLIMATE
                ]
            if new_options.get(CONF_UPSTAIRS_TEMP_SENSOR):
                controller.params.cooling_control.upstairs_sensor = new_options[
                    CONF_UPSTAIRS_TEMP_SENSOR
                ]
            if new_options.get(CONF_MAIN_FLOOR_TEMP_SENSOR):
                controller.params.cooling_control.main_floor_sensor = new_options[
                    CONF_MAIN_FLOOR_TEMP_SENSOR
                ]
            for key, value in new_options.items():
                if key.startswith(CONF_ZONE_VALVE_PREFIX):
                    zone_key = key[len(CONF_ZONE_VALVE_PREFIX):]
                    controller.params.zone_valve_counts[zone_key] = int(value)

        return self.async_create_entry(title="", data=new_options)
