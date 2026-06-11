#!/usr/bin/env python3
"""Find all temperature sensors in HA."""
import json
import urllib.request

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"

req = urllib.request.Request(
    f"{BASE}/api/states",
    headers={"Authorization": f"Bearer {TOKEN}"},
)
data = json.loads(urllib.request.urlopen(req, timeout=15).read())

print("=== ALL TEMPERATURE SENSORS (unit=F or C) ===")
for e in data:
    unit = e.get("attributes", {}).get("unit_of_measurement", "")
    dev_class = e.get("attributes", {}).get("device_class", "")
    if unit in ("°F", "°C") or dev_class == "temperature":
        print(f"  {e['entity_id']} state={e['state']} unit={unit} class={dev_class}")

print("\n=== INTEGRATIONS WITH 'met' or 'forecast' ===")
for e in data:
    eid = e["entity_id"].lower()
    if "met" in eid or "forecast" in eid or "weather" in eid:
        print(f"  {e['entity_id']} state={e['state']}")