#!/usr/bin/env python3
"""Test if we can turn on a zone and set temperature via the REST API."""
import requests

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
headers = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

# Try to set laundry to heat at 72.5
print("Setting climate.laundry_laundry to heat at 72.5°F...")
r = requests.post(
    f"{BASE}/api/services/climate/set_temperature",
    headers=headers,
    json={
        "entity_id": "climate.laundry_laundry",
        "temperature": 72.5,
        "hvac_mode": "heat",
    },
)
print(f"  Status: {r.status_code}")
if r.status_code != 200:
    print(f"  Response: {r.text[:500]}")

# Check state after
import time
time.sleep(3)
r = requests.get(f"{BASE}/api/states/climate.laundry_laundry", headers=headers)
state = r.json()
print(f"  After: mode={state['state']} target={state['attributes'].get('temperature')} action={state['attributes'].get('hvac_action')}")
