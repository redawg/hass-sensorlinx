#!/usr/bin/env python3
"""
Deploy the Outdoor Reset Heating Curve automation to Forest Home via WebSocket API.

Creates input_number helpers, template sensors, and automations entirely
through the HA WebSocket API — no file system access needed.
"""
import asyncio
import json
import aiohttp

BASE = "ws://172.16.255.250:8123/api/websocket"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"

MSG_ID = 1


def next_id():
    global MSG_ID
    MSG_ID += 1
    return MSG_ID


async def ws_call(ws, msg_type, **kwargs):
    """Send a WebSocket message and return the response."""
    mid = next_id()
    payload = {"id": mid, "type": msg_type, **kwargs}
    await ws.send_json(payload)
    while True:
        resp = await ws.receive_json()
        if resp.get("id") == mid:
            return resp
        # Skip event messages
        if resp.get("type") == "event":
            continue


async def create_input_number(ws, name, object_id, min_val, max_val, step, unit, icon, initial, mode="slider"):
    """Create an input_number helper via the helpers WS API."""
    resp = await ws_call(
        ws, "config/helpers/create",
        domain="input_number",
        name=name,
        icon=icon,
        min=min_val,
        max=max_val,
        step=step,
        unit_of_measurement=unit,
        mode=mode,
        initial=initial,
    )
    if resp.get("success"):
        print(f"  [OK] input_number.{object_id}: {name} (initial={initial})")
    else:
        error = resp.get("error", {})
        if "already" in str(error).lower():
            print(f"  [SKIP] input_number.{object_id}: already exists")
        else:
            print(f"  [ERR] input_number.{object_id}: {error}")
    return resp


async def create_input_boolean(ws, name, object_id, icon, initial=True):
    """Create an input_boolean helper."""
    resp = await ws_call(
        ws, "config/helpers/create",
        domain="input_boolean",
        name=name,
        icon=icon,
        initial=initial,
    )
    if resp.get("success"):
        print(f"  [OK] input_boolean.{object_id}: {name}")
    else:
        error = resp.get("error", {})
        if "already" in str(error).lower():
            print(f"  [SKIP] input_boolean.{object_id}: already exists")
        else:
            print(f"  [ERR] input_boolean.{object_id}: {error}")
    return resp


async def create_template_sensor(ws, name, unique_id, state_template, unit="°F", device_class="temperature", icon="mdi:thermostat", attrs_template=None):
    """Create a template sensor via the template config WS API."""
    config = {
        "name": name,
        "state": state_template,
        "unit_of_measurement": unit,
        "device_class": device_class,
        "icon": icon,
    }
    if attrs_template:
        config["attributes"] = attrs_template
    resp = await ws_call(
        ws, "config/template/create",
        platform="sensor",
        **config,
    )
    if resp.get("success"):
        print(f"  [OK] sensor.{unique_id}: {name}")
    else:
        error = resp.get("error", {})
        if "already" in str(error).lower():
            print(f"  [SKIP] sensor.{unique_id}: already exists")
        else:
            print(f"  [ERR] sensor.{unique_id}: {error}")
    return resp


async def create_automation(ws, automation_config):
    """Create an automation via the config API."""
    alias = automation_config.get("alias", "unknown")
    resp = await ws_call(ws, "config/automation/config/create", config=automation_config)
    if resp.get("success"):
        print(f"  [OK] automation: {alias}")
    else:
        error = resp.get("error", {})
        if "already" in str(error).lower():
            print(f"  [SKIP] automation: {alias} already exists")
        else:
            print(f"  [ERR] automation: {alias}: {error}")
    return resp


async def main():
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(BASE) as ws:
            # Auth
            await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": TOKEN})
            auth_resp = await ws.receive_json()
            if auth_resp.get("type") != "auth_ok":
                print(f"Auth failed: {auth_resp}")
                return

            print("=== Connected to Forest Home ===\n")

            # --- Input Helpers ---
            print("--- Creating Input Number Helpers ---")

            await create_input_number(ws, "Heating Curve: Base Comfort Temp",
                "heating_curve_base", 65, 75, 0.5, "°F", "mdi:thermometer-low", 70)

            await create_input_number(ws, "Heating Curve: Overshoot",
                "heating_curve_overshoot", 0, 12, 0.5, "°F", "mdi:thermometer-chevron-up", 6)

            await create_input_number(ws, "Heating Curve: Outdoor Shutdown Temp",
                "heating_curve_shutdown_temp", 55, 75, 1, "°F", "mdi:weather-sunny", 65)

            await create_input_number(ws, "Heating Curve: Design Outdoor Temp",
                "heating_curve_design_outdoor", 0, 40, 1, "°F", "mdi:snowflake", 25)

            await create_input_number(ws, "Heating Curve: Max Floor Temp",
                "floor_temp_max", 75, 85, 1, "°F", "mdi:alert-octagon", 80)

            await create_input_number(ws, "Zone Offset: Laundry",
                "heating_curve_offset_laundry", -5, 5, 0.5, "°F", "mdi:tune-vertical", 0)

            await create_input_number(ws, "Zone Offset: Living Room",
                "heating_curve_offset_living_room", -5, 5, 0.5, "°F", "mdi:tune-vertical", 0)

            await create_input_number(ws, "Zone Offset: Main Area",
                "heating_curve_offset_main_area", -5, 5, 0.5, "°F", "mdi:tune-vertical", 0)

            await create_input_number(ws, "Zone Offset: Main Office",
                "heating_curve_offset_main_office", -5, 5, 0.5, "°F", "mdi:tune-vertical", 0)

            print("\n--- Creating Input Boolean ---")
            await create_input_boolean(ws, "Outdoor Reset: Enabled",
                "outdoor_reset_enabled", "mdi:thermostat-auto", initial=True)

            # --- Template Sensors ---
            print("\n--- Creating Template Sensors ---")

            main_curve_template = """{% set outdoor = states('sensor.home_weather_station_temperature') | float(50) %}
{% set base = states('input_number.heating_curve_base') | float(70) %}
{% set overshoot = states('input_number.heating_curve_overshoot') | float(6) %}
{% set shutdown = states('input_number.heating_curve_shutdown_temp') | float(65) %}
{% set design = states('input_number.heating_curve_design_outdoor') | float(25) %}
{% set range = shutdown - design %}
{% if range <= 0 %}{{ base }}
{% elif outdoor >= shutdown %}{{ base }}
{% elif outdoor <= design %}{{ base + overshoot }}
{% else %}{{ (base + overshoot * ((shutdown - outdoor) / range)) | round(1) }}
{% endif %}"""

            await create_template_sensor(ws,
                "Floor Heating Target Setpoint",
                "floor_heating_target_setpoint",
                main_curve_template,
            )

            zone_template = """{% set target = states('sensor.floor_heating_target_setpoint') | float(70) %}
{% set offset = states('input_number.heating_curve_offset_ZONE') | float(0) %}
{{ (target + offset) | round(1) }}"""

            for zone_name, zone_key in [
                ("Laundry", "laundry"),
                ("Living Room", "living_room"),
                ("Main Area", "main_area"),
                ("Main Office", "main_office"),
            ]:
                tmpl = zone_template.replace("ZONE", zone_key)
                await create_template_sensor(ws,
                    f"Floor Heating Target: {zone_name}",
                    f"floor_heating_target_{zone_key}",
                    tmpl,
                )

            # --- Automations ---
            print("\n--- Creating Automations ---")

            # Main outdoor reset automation
            main_auto = {
                "alias": "Outdoor Reset: Update Zone Setpoints",
                "description": "Adjusts radiant floor zone setpoints based on the outdoor reset heating curve every 15 minutes or on significant outdoor temp change.",
                "mode": "single",
                "triggers": [
                    {"trigger": "time_pattern", "minutes": "/15"},
                    {"trigger": "state", "entity_id": "sensor.home_weather_station_temperature", "for": "00:02:00"},
                    {"trigger": "state", "entity_id": "input_boolean.outdoor_reset_enabled", "to": "on"},
                ],
                "conditions": [
                    {"condition": "state", "entity_id": "input_boolean.outdoor_reset_enabled", "state": "on"},
                    {"condition": "not", "conditions": [
                        {"condition": "state", "entity_id": "sensor.home_weather_station_temperature", "state": "unavailable"},
                        {"condition": "state", "entity_id": "sensor.home_weather_station_temperature", "state": "unknown"},
                    ]},
                ],
                "actions": [
                    {"variables": {
                        "outdoor": "{{ states('sensor.home_weather_station_temperature') | float(50) }}",
                        "shutdown": "{{ states('input_number.heating_curve_shutdown_temp') | float(65) }}",
                    }},
                    {"choose": [
                        {
                            "conditions": [{"condition": "template", "value_template": "{{ outdoor >= shutdown }}"}],
                            "sequence": [
                                {"action": "climate.turn_off", "target": {"entity_id": [
                                    "climate.laundry_laundry",
                                    "climate.living_room_living_room",
                                    "climate.main_area_main_area",
                                    "climate.main_office_main_office",
                                ]}}
                            ],
                        }
                    ], "default": [
                        {"action": "climate.set_temperature", "target": {"entity_id": "climate.laundry_laundry"},
                         "data": {"temperature": "{{ states('sensor.floor_heating_target_laundry') | float(70) }}", "hvac_mode": "heat"}},
                        {"action": "climate.set_temperature", "target": {"entity_id": "climate.living_room_living_room"},
                         "data": {"temperature": "{{ states('sensor.floor_heating_target_living_room') | float(70) }}", "hvac_mode": "heat"}},
                        {"action": "climate.set_temperature", "target": {"entity_id": "climate.main_area_main_area"},
                         "data": {"temperature": "{{ states('sensor.floor_heating_target_main_area') | float(70) }}", "hvac_mode": "heat"}},
                        {"action": "climate.set_temperature", "target": {"entity_id": "climate.main_office_main_office"},
                         "data": {"temperature": "{{ states('sensor.floor_heating_target_main_office') | float(70) }}", "hvac_mode": "heat"}},
                    ]},
                ],
            }
            await create_automation(ws, main_auto)

            # Safety cap automation
            safety_auto = {
                "alias": "Outdoor Reset: Floor Temperature Safety Cap",
                "description": "Turns off a zone if its floor temperature exceeds the max floor temp setting (80°F for wood).",
                "mode": "queued",
                "max": 4,
                "triggers": [
                    {"trigger": "numeric_state", "entity_id": "sensor.laundry_floor_temperature",
                     "above": "input_number.floor_temp_max", "id": "laundry_max"},
                    {"trigger": "numeric_state", "entity_id": "sensor.living_room_floor_temperature",
                     "above": "input_number.floor_temp_max", "id": "living_room_max"},
                    {"trigger": "numeric_state", "entity_id": "sensor.main_area_floor_temperature",
                     "above": "input_number.floor_temp_max", "id": "main_area_max"},
                    {"trigger": "numeric_state", "entity_id": "sensor.main_office_floor_temperature",
                     "above": "input_number.floor_temp_max", "id": "main_office_max"},
                ],
                "conditions": [
                    {"condition": "state", "entity_id": "input_boolean.outdoor_reset_enabled", "state": "on"},
                ],
                "actions": [{"choose": [
                    {"conditions": [{"condition": "trigger", "id": "laundry_max"}],
                     "sequence": [
                         {"action": "climate.turn_off", "target": {"entity_id": "climate.laundry_laundry"}},
                         {"action": "persistent_notification.create", "data": {
                             "title": "Floor Safety Cap",
                             "message": "Laundry floor exceeded {{ states('input_number.floor_temp_max') }}°F — zone turned off."}},
                     ]},
                    {"conditions": [{"condition": "trigger", "id": "living_room_max"}],
                     "sequence": [
                         {"action": "climate.turn_off", "target": {"entity_id": "climate.living_room_living_room"}},
                         {"action": "persistent_notification.create", "data": {
                             "title": "Floor Safety Cap",
                             "message": "Living Room floor exceeded {{ states('input_number.floor_temp_max') }}°F — zone turned off."}},
                     ]},
                    {"conditions": [{"condition": "trigger", "id": "main_area_max"}],
                     "sequence": [
                         {"action": "climate.turn_off", "target": {"entity_id": "climate.main_area_main_area"}},
                         {"action": "persistent_notification.create", "data": {
                             "title": "Floor Safety Cap",
                             "message": "Main Area floor exceeded {{ states('input_number.floor_temp_max') }}°F — zone turned off."}},
                     ]},
                    {"conditions": [{"condition": "trigger", "id": "main_office_max"}],
                     "sequence": [
                         {"action": "climate.turn_off", "target": {"entity_id": "climate.main_office_main_office"}},
                         {"action": "persistent_notification.create", "data": {
                             "title": "Floor Safety Cap",
                             "message": "Main Office floor exceeded {{ states('input_number.floor_temp_max') }}°F — zone turned off."}},
                     ]},
                ]}],
            }
            await create_automation(ws, safety_auto)

            # Recovery automation
            recovery_auto = {
                "alias": "Outdoor Reset: Floor Safety Recovery",
                "description": "Re-enables a zone when the floor cools back below (max - 3°F) after being turned off by the safety cap.",
                "mode": "queued",
                "max": 4,
                "triggers": [
                    {"trigger": "template",
                     "value_template": "{{ states('sensor.laundry_floor_temperature') | float(0) < (states('input_number.floor_temp_max') | float(80) - 3) and is_state('climate.laundry_laundry', 'off') }}",
                     "id": "laundry_recover"},
                    {"trigger": "template",
                     "value_template": "{{ states('sensor.living_room_floor_temperature') | float(0) < (states('input_number.floor_temp_max') | float(80) - 3) and is_state('climate.living_room_living_room', 'off') }}",
                     "id": "living_room_recover"},
                    {"trigger": "template",
                     "value_template": "{{ states('sensor.main_area_floor_temperature') | float(0) < (states('input_number.floor_temp_max') | float(80) - 3) and is_state('climate.main_area_main_area', 'off') }}",
                     "id": "main_area_recover"},
                    {"trigger": "template",
                     "value_template": "{{ states('sensor.main_office_floor_temperature') | float(0) < (states('input_number.floor_temp_max') | float(80) - 3) and is_state('climate.main_office_main_office', 'off') }}",
                     "id": "main_office_recover"},
                ],
                "conditions": [
                    {"condition": "state", "entity_id": "input_boolean.outdoor_reset_enabled", "state": "on"},
                ],
                "actions": [{"choose": [
                    {"conditions": [{"condition": "trigger", "id": "laundry_recover"}],
                     "sequence": [{"action": "climate.set_temperature", "target": {"entity_id": "climate.laundry_laundry"},
                                   "data": {"temperature": "{{ states('sensor.floor_heating_target_laundry') | float(70) }}", "hvac_mode": "heat"}}]},
                    {"conditions": [{"condition": "trigger", "id": "living_room_recover"}],
                     "sequence": [{"action": "climate.set_temperature", "target": {"entity_id": "climate.living_room_living_room"},
                                   "data": {"temperature": "{{ states('sensor.floor_heating_target_living_room') | float(70) }}", "hvac_mode": "heat"}}]},
                    {"conditions": [{"condition": "trigger", "id": "main_area_recover"}],
                     "sequence": [{"action": "climate.set_temperature", "target": {"entity_id": "climate.main_area_main_area"},
                                   "data": {"temperature": "{{ states('sensor.floor_heating_target_main_area') | float(70) }}", "hvac_mode": "heat"}}]},
                    {"conditions": [{"condition": "trigger", "id": "main_office_recover"}],
                     "sequence": [{"action": "climate.set_temperature", "target": {"entity_id": "climate.main_office_main_office"},
                                   "data": {"temperature": "{{ states('sensor.floor_heating_target_main_office') | float(70) }}", "hvac_mode": "heat"}}]},
                ]}],
            }
            await create_automation(ws, recovery_auto)

            # --- Set initial values for input_numbers ---
            print("\n--- Setting initial values ---")
            for entity_id, value in [
                ("input_number.heating_curve_base", 70),
                ("input_number.heating_curve_overshoot", 6),
                ("input_number.heating_curve_shutdown_temp", 65),
                ("input_number.heating_curve_design_outdoor", 25),
                ("input_number.floor_temp_max", 80),
                ("input_number.heating_curve_offset_laundry", 0),
                ("input_number.heating_curve_offset_living_room", 0),
                ("input_number.heating_curve_offset_main_area", 0),
                ("input_number.heating_curve_offset_main_office", 0),
            ]:
                resp = await ws_call(ws, "call_service",
                    domain="input_number", service="set_value",
                    service_data={"entity_id": entity_id, "value": value})
                if resp.get("success"):
                    print(f"  [OK] {entity_id} = {value}")
                else:
                    print(f"  [ERR] {entity_id}: {resp.get('error', {})}")

            # Enable the outdoor reset toggle
            resp = await ws_call(ws, "call_service",
                domain="input_boolean", service="turn_on",
                service_data={"entity_id": "input_boolean.outdoor_reset_enabled"})
            if resp.get("success"):
                print("  [OK] outdoor_reset_enabled = on")

            # --- Verify ---
            print("\n--- Verifying deployment ---")
            resp = await ws_call(ws, "get_states")
            states = resp.get("result", [])
            targets = [
                "input_number.heating_curve_base",
                "input_number.heating_curve_overshoot",
                "input_boolean.outdoor_reset_enabled",
                "sensor.floor_heating_target_setpoint",
                "sensor.floor_heating_target_laundry",
            ]
            for t in targets:
                for s in states:
                    if s["entity_id"] == t:
                        print(f"  {t} = {s['state']}")
                        break
                else:
                    print(f"  {t} = NOT FOUND (may need restart)")

            print("\n=== Deployment complete! ===")


asyncio.run(main())
