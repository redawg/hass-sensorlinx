#!/usr/bin/env python3
"""Find all outdoor/weather temperature sensors."""
import requests

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

r = requests.get(f"{BASE}/api/states", headers=HEADERS, timeout=10)
states = r.json()

print("=== Temperature sensors with 'weather', 'outdoor', 'outside', 'tempest' ===\n")
keywords = ["weather", "outdoor", "outside", "tempest", "station"]
for s in sorted(states, key=lambda x: x["entity_id"]):
    eid = s["entity_id"]
    if not eid.startswith("sensor."):
        continue
    if s["attributes"].get("device_class") != "temperature":
        continue
    name = s["attributes"].get("friendly_name", "").lower()
    if any(k in eid.lower() or k in name for k in keywords):
        print(f"  {eid}: {s['state']} {s['attributes'].get('unit_of_measurement', '')}")
        print(f"    name: {s['attributes'].get('friendly_name')}")
        print()

# Also check what the outdoor reset uses
print("\n=== What OUTDOOR_TEMP_ENTITY is set to in code ===")
print("  (check outdoor_reset.py for OUTDOOR_TEMP_ENTITY constant)")
