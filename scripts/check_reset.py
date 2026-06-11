#!/usr/bin/env python3
"""Check outdoor reset and zone status."""
import requests

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
headers = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

# Get all states
r = requests.get(f"{BASE}/api/states", headers=headers)
states = r.json()


def find(entity_id):
    for s in states:
        if s["entity_id"] == entity_id:
            return s
    return None


def find_all(pattern):
    return [s for s in states if pattern in s["entity_id"]]


# Outdoor temp
outdoor = find("sensor.home_weather_station_temperature")
print(f"Outdoor Temp: {outdoor['state']}°F" if outdoor else "Outdoor: not found")
print()

# Outdoor reset enabled
enabled = find("switch.sensorlinx_outdoor_reset_outdoor_reset_enabled")
print(f"Outdoor Reset Enabled: {enabled['state']}" if enabled else "Enabled switch: not found")

# Heating curve target
target = find("sensor.sensorlinx_outdoor_reset_heating_curve_target")
if target:
    print(f"Heating Curve Target: {target['state']}°F")
    attrs = target.get("attributes", {})
    for k, v in attrs.items():
        if k not in ("friendly_name", "icon", "device_class", "unit_of_measurement", "state_class"):
            print(f"  {k}: {v}")
print()

# Climate zones
print("=== Zone Thermostats ===")
climates = [s for s in states if s["entity_id"].startswith("climate.") and "main_floor" not in s["entity_id"] and s["attributes"].get("current_temperature") is not None]
for c in sorted(climates, key=lambda x: x["entity_id"]):
    attrs = c["attributes"]
    name = attrs.get("friendly_name", c["entity_id"])
    mode = c["state"]
    current = attrs.get("current_temperature")
    tgt = attrs.get("temperature")
    action = attrs.get("hvac_action", "?")
    print(f"  {name}: {mode} | room={current}°F | target={tgt}°F | action={action}")
print()

# Floor temps
print("=== Floor Temperatures ===")
for s in sorted(find_all("floor_temperature"), key=lambda x: x["entity_id"]):
    name = s["attributes"].get("friendly_name", s["entity_id"])
    print(f"  {name}: {s['state']}°F")
print()

# Floor control modes
print("=== Floor Control Mode (per-zone) ===")
for s in sorted(find_all("floor_mode"), key=lambda x: x["entity_id"]):
    name = s["attributes"].get("friendly_name", s["entity_id"])
    print(f"  {name}: {s['state']}")
print()

# Floor targets
print("=== Floor Targets (per-zone) ===")
for s in sorted(find_all("floor_target"), key=lambda x: x["entity_id"]):
    name = s["attributes"].get("friendly_name", s["entity_id"])
    print(f"  {name}: {s['state']}°F")
print()

# Zone offsets
print("=== Zone Offsets ===")
for s in sorted(find_all("offset_"), key=lambda x: x["entity_id"]):
    if "sensorlinx" in s["entity_id"]:
        name = s["attributes"].get("friendly_name", s["entity_id"])
        print(f"  {name}: {s['state']}°F")
print()

# Heating curve params
print("=== Heating Curve Parameters ===")
for pattern in ["heating_curve_base", "heating_curve_overshoot", "heating_curve_shutdown", "heating_curve_design", "floor_temp_max"]:
    matches = find_all(pattern)
    for s in matches:
        name = s["attributes"].get("friendly_name", s["entity_id"])
        print(f"  {name}: {s['state']}")
