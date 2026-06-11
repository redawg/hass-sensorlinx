#!/usr/bin/env python3
"""Check available weather/forecast entities on HA."""
import requests
import json

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

r = requests.get(f"{BASE}/api/states", headers=HEADERS, timeout=10)
states = r.json()

# Find weather entities
weather = [s for s in states if s["entity_id"].startswith("weather.")]
print(f"=== Weather Entities ({len(weather)}) ===")
for w in weather:
    attrs = w["attributes"]
    print(f"  {w['entity_id']}: state={w['state']}")
    print(f"    temperature={attrs.get('temperature')}, forecast available={bool(attrs.get('forecast'))}")
    if attrs.get("forecast"):
        fc = attrs["forecast"][:3]
        print(f"    Next forecasts ({len(attrs['forecast'])} total):")
        for f in fc:
            print(f"      {f.get('datetime')}: temp={f.get('temperature')}, low={f.get('templow')}")

# Check if there's an hourly forecast service
print("\n=== Trying weather.get_forecasts service ===")
r2 = requests.post(
    f"{BASE}/api/services/weather/get_forecasts",
    headers=HEADERS,
    json={"entity_id": weather[0]["entity_id"] if weather else "weather.home", "type": "hourly"},
)
if r2.status_code == 200:
    data = r2.json()
    if data:
        # The response is a list of state changes; forecast is in the response
        print(f"  Got response with {len(data)} items")
        for item in data[:1]:
            forecasts = item.get("attributes", {}).get("forecast", [])
            if not forecasts:
                # New format: response action
                print(f"  Keys: {list(item.keys())}")
                print(f"  Sample: {json.dumps(item, indent=2)[:500]}")
            else:
                print(f"  Hourly forecasts: {len(forecasts)}")
                for f in forecasts[:5]:
                    print(f"    {f.get('datetime')}: {f.get('temperature')}F")
else:
    print(f"  Error: {r2.status_code} {r2.text[:200]}")
