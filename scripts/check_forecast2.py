#!/usr/bin/env python3
"""Check available weather/forecast entities on HA (v2)."""
import requests
import json

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

r = requests.get(f"{BASE}/api/states", headers=HEADERS, timeout=10)
states = r.json()

# Find anything with forecast or weather in the name
weather_related = [s for s in states if "weather" in s["entity_id"].lower() or "forecast" in s["entity_id"].lower()]
print(f"=== Weather/Forecast entities ({len(weather_related)}) ===")
for w in weather_related:
    print(f"  {w['entity_id']}: state={w['state']}")
    fc = w["attributes"].get("forecast")
    if fc:
        print(f"    forecast entries: {len(fc)}")

# Also check for met.no, openweathermap, accuweather, etc.
integrations = [s for s in states if any(x in s["entity_id"] for x in ["met_", "owm_", "accuweather", "nws_", "pirateweather"])]
print(f"\n=== Known weather integrations ({len(integrations)}) ===")
for i in integrations:
    print(f"  {i['entity_id']}: {i['state']}")

# Check services available
r2 = requests.get(f"{BASE}/api/services", headers=HEADERS, timeout=10)
services = r2.json()
weather_svc = [s for s in services if s.get("domain") == "weather"]
print(f"\n=== Weather services ===")
for svc in weather_svc:
    print(f"  domain: {svc['domain']}")
    for name, info in svc.get("services", {}).items():
        print(f"    {name}: {info.get('description', '')[:80]}")

# Try the outdoor temp sensor - maybe it has forecast attributes
outdoor = next((s for s in states if s["entity_id"] == "sensor.home_weather_station_temperature"), None)
if outdoor:
    print(f"\n=== Outdoor Sensor ===")
    print(f"  {outdoor['entity_id']}: {outdoor['state']}F")
    print(f"  Attributes: {list(outdoor['attributes'].keys())}")
