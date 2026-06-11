#!/usr/bin/env python3
"""Check for WeatherFlow/Tempest forecast entities and services."""
import requests
import json

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

r = requests.get(f"{BASE}/api/states", headers=HEADERS, timeout=10)
states = r.json()

# Find all weatherflow/tempest related entities
wf = [s for s in states if any(x in s["entity_id"].lower() for x in ["weatherflow", "tempest", "weather."])]
print(f"=== WeatherFlow/Tempest/Weather entities ({len(wf)}) ===")
for w in sorted(wf, key=lambda x: x["entity_id"]):
    attrs = w["attributes"]
    has_forecast = "forecast" in attrs
    print(f"  {w['entity_id']}: state={w['state']}")
    if has_forecast:
        fc = attrs["forecast"]
        print(f"    FORECAST: {len(fc)} entries")
        if fc:
            print(f"    First: {json.dumps(fc[0], indent=6)[:200]}")

# Check integrations/config entries
print("\n=== Config Entries ===")
r2 = requests.get(f"{BASE}/api/config", headers=HEADERS, timeout=10)
config = r2.json()
print(f"  Components loaded: {len(config.get('components', []))}")
# Check for weatherflow in components
components = config.get("components", [])
wf_comp = [c for c in components if "weather" in c.lower() or "tempest" in c.lower()]
print(f"  Weather-related components: {wf_comp}")

# Try weather.get_forecasts service with return_response
print("\n=== Trying weather.get_forecasts ===")
# First find any weather.* entity
weather_entities = [s["entity_id"] for s in states if s["entity_id"].startswith("weather.")]
print(f"  Weather entities: {weather_entities}")
if weather_entities:
    for we in weather_entities:
        r3 = requests.post(
            f"{BASE}/api/services/weather/get_forecasts?return_response",
            headers=HEADERS,
            json={"entity_id": we, "type": "hourly"},
        )
        print(f"  {we} hourly forecast: status={r3.status_code}")
        if r3.status_code == 200:
            data = r3.json()
            print(f"    Response: {json.dumps(data, indent=4)[:500]}")
