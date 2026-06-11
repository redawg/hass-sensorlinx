#!/usr/bin/env python3
"""Check why laundry is at 70 instead of 76."""
import json
import urllib.request

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"

def get(path):
    req = urllib.request.Request(f"{BASE}{path}", headers={"Authorization": f"Bearer {TOKEN}"})
    return json.loads(urllib.request.urlopen(req, timeout=10).read())

# Outdoor temp
outdoor = get("/api/states/sensor.home_weather_station_temperature")
print(f"Outdoor temp: {outdoor['state']}°F")

# Heating curve target
target = get("/api/states/sensor.sensorlinx_outdoor_reset_heating_curve_target")
print(f"Heating curve target: {target['state']}°F")
print(f"  attrs: {json.dumps(target.get('attributes',{}))}")

# Laundry zone target
laundry_target = get("/api/states/sensor.sensorlinx_outdoor_reset_target_laundry")
print(f"Laundry zone target: {laundry_target['state']}°F")

# Laundry climate state
laundry = get("/api/states/climate.laundry_laundry")
print(f"\nLaundry climate:")
print(f"  state: {laundry['state']}")
print(f"  current_temp: {laundry['attributes'].get('current_temperature')}")
print(f"  target_temp: {laundry['attributes'].get('temperature')}")
print(f"  hvac_action: {laundry['attributes'].get('hvac_action')}")

# Outdoor reset enabled?
enabled = get("/api/states/switch.sensorlinx_outdoor_reset_outdoor_reset_enabled")
print(f"\nOutdoor reset enabled: {enabled['state']}")

# Shutdown temp
shutdown = get("/api/states/number.sensorlinx_outdoor_reset_heating_curve_shutdown_temp")
print(f"Shutdown temp: {shutdown['state']}°F")

# Check if outdoor > shutdown
outdoor_val = float(outdoor['state'])
shutdown_val = float(shutdown['state'])
print(f"\nOutdoor ({outdoor_val}) >= Shutdown ({shutdown_val})? {outdoor_val >= shutdown_val}")
if outdoor_val >= shutdown_val:
    print("  >>> THIS IS WHY! Outdoor temp is above shutdown threshold.")
    print("  >>> The automation turned off/reduced all zones.")
