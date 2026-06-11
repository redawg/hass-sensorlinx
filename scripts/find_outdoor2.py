#!/usr/bin/env python3
"""Find the WeatherFlow station entity - broader search."""
import requests

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

r = requests.get(f"{BASE}/api/states", headers=HEADERS, timeout=10)
states = r.json()

# Search for anything with "home_weather" or "weatherflow" in entity_id
print("=== Entities with 'home_weather' or 'weatherflow' ===\n")
for s in sorted(states, key=lambda x: x["entity_id"]):
    eid = s["entity_id"]
    if "home_weather" in eid.lower() or "weatherflow" in eid.lower():
        print(f"  {eid}: {s['state']} {s['attributes'].get('unit_of_measurement', '')}")

# Check if entity exists but is unavailable
print("\n\n=== Direct check: sensor.home_weather_station_temperature ===")
target = next((s for s in states if s["entity_id"] == "sensor.home_weather_station_temperature"), None)
if target:
    print(f"  Found! state={target['state']}")
    print(f"  device_class={target['attributes'].get('device_class')}")
else:
    print("  NOT FOUND in entity list!")
    
# Check config entries for weatherflow
print("\n=== WeatherFlow config entry ===")
r2 = requests.get(f"{BASE}/api/config/config_entries/entry", headers=HEADERS, timeout=10)
if r2.status_code == 200:
    for e in r2.json():
        if "weather" in e.get("domain", "").lower() or "weather" in e.get("title", "").lower():
            print(f"  domain={e['domain']}, title={e.get('title')}, state={e.get('state')}")
