#!/usr/bin/env python3
"""Check current live data available for thermal optimization."""
import requests
import json
import time

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

# Wait for HA
for i in range(8):
    try:
        r = requests.get(f"{BASE}/api/", headers=HEADERS, timeout=5)
        if r.status_code == 200:
            break
    except Exception:
        pass
    time.sleep(10)
time.sleep(3)

r = requests.get(f"{BASE}/api/states", headers=HEADERS, timeout=10)
states = {s["entity_id"]: s for s in r.json()}

# Check the thermal log for the latest sample
print("=== LATEST THERMAL LOG SAMPLE ===\n")
try:
    # Read the last few lines of the current log
    r2 = requests.post(
        f"{BASE}/api/services/shell_command/read_log",
        headers=HEADERS, json={}, timeout=5
    )
except Exception:
    pass

# Check logger status
log_sensor = states.get("sensor.sensorlinx_outdoor_reset_thermal_log_samples")
if log_sensor:
    print(f"Logger status: {log_sensor['state']} samples")
    for k, v in log_sensor["attributes"].items():
        if k not in ("friendly_name", "icon"):
            print(f"  {k}: {v}")

# Current system state snapshot
print("\n\n=== LIVE SYSTEM STATE (what gets logged every sample) ===\n")

print("--- OUTDOOR ---")
outdoor = states.get("sensor.home_weather_station_temperature")
if outdoor:
    print(f"  Temperature: {outdoor['state']}°F")
feels = states.get("sensor.home_weather_station_feels_like")
if feels:
    print(f"  Feels like: {feels['state']}°F")

print("\n--- FLOOR ZONES ---")
zones = ["laundry", "living_room", "main_area", "main_office"]
for z in zones:
    climate = states.get(f"climate.{z}_{z}")
    floor = states.get(f"sensor.{z}_floor_temperature")
    target = states.get(f"sensor.sensorlinx_outdoor_reset_target_{z}")
    if climate:
        attrs = climate["attributes"]
        print(f"  {z}:")
        print(f"    room_temp={attrs.get('current_temperature')}°F  floor_temp={floor['state'] if floor else '?'}°F")
        print(f"    setpoint={attrs.get('temperature')}°F  curve_target={target['state'] if target else '?'}°F")
        print(f"    hvac_action={attrs.get('hvac_action')}  mode={climate['state']}")

print("\n--- OPTIMAL TANKLESS ---")
tankless_data = {
    "State": states.get("water_heater.main_water_heater"),
    "Heating": states.get("binary_sensor.main_water_heater_heating"),
    "Outlet (supply)": states.get("sensor.main_water_heater_outlet_temperature"),
    "Inlet (return)": states.get("sensor.main_water_heater_inlet_temperature"),
    "Flow rate": states.get("sensor.main_water_heater_flow_rate"),
    "Power draw": states.get("sensor.main_water_heater_power_draw"),
    "Available flow": states.get("sensor.main_water_heater_available_flow_rate"),
    "Capacity": states.get("sensor.main_water_heater_heater_capacity"),
    "Target temp": None,
}
wh = states.get("water_heater.main_water_heater")
if wh:
    tankless_data["Target temp"] = wh["attributes"].get("temperature")
for label, s in tankless_data.items():
    if s is None:
        continue
    if isinstance(s, dict):
        print(f"  {label}: {s}")
    else:
        unit = s.get("attributes", {}).get("unit_of_measurement", "") if isinstance(s, dict) else ""
        val = s["state"] if isinstance(s, dict) else s
        unit = s["attributes"].get("unit_of_measurement", "") if isinstance(s, dict) else ""
        print(f"  {label}: {s['state']} {s['attributes'].get('unit_of_measurement', '')}")

print("\n--- ECOBEE (Main HVAC) ---")
ecobee = states.get("climate.main_floor")
if ecobee:
    attrs = ecobee["attributes"]
    print(f"  Mode: {ecobee['state']}")
    print(f"  Current temp: {attrs.get('current_temperature')}°F")
    print(f"  Target: {attrs.get('temperature')}°F")
    print(f"  HVAC action: {attrs.get('hvac_action')}")
    print(f"  Humidity: {attrs.get('current_humidity')}%")
    print(f"  Fan: {attrs.get('fan_mode')}")

print("\n--- ENERGY ---")
energy_today = states.get("sensor.furnace_tankless_water_energy_today")
energy_month = states.get("sensor.furnace_tankless_water_energy_this_month")
power_now = states.get("sensor.furnace_tankless_water_power_minute_average")
if energy_today:
    print(f"  Today: {energy_today['state']} kWh")
if energy_month:
    print(f"  This month: {energy_month['state']} kWh")
if power_now:
    print(f"  Current power (1min avg): {power_now['state']} W")

print("\n--- HYDRONIC PERFORMANCE ---")
dt = states.get("sensor.sensorlinx_outdoor_reset_hydronic_delta_t")
btu = states.get("sensor.sensorlinx_outdoor_reset_hydronic_heat_output")
if dt:
    print(f"  Delta-T: {dt['state']}°F")
    print(f"    Supply: {dt['attributes'].get('supply_temp')}°F")
    print(f"    Return: {dt['attributes'].get('return_temp')}°F")
    print(f"    Flow sensor: {dt['attributes'].get('flow_rate_sensor')}")
    print(f"    Actual flow: {dt['attributes'].get('actual_flow_gpm')} GPM")
if btu:
    print(f"  BTU output: {btu['state']} BTU/hr")
    print(f"    Flow source: {btu['attributes'].get('flow_source')}")

print("\n--- PREHEAT / SUPPLY RESET ---")
preheat = states.get("sensor.sensorlinx_outdoor_reset_preheat_status")
supply = states.get("sensor.sensorlinx_outdoor_reset_supply_water_target")
if preheat:
    print(f"  Preheat: {preheat['state']}")
if supply:
    print(f"  Supply water target: {supply['state']}°F")
    print(f"    Control enabled: {supply['attributes'].get('supply_control_enabled')}")
    print(f"    Entity: {supply['attributes'].get('supply_entity_id')}")
