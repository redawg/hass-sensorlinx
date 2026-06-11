#!/usr/bin/env python3
"""Find weatherflow_cloud entities."""
import requests

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

r = requests.get(f"{BASE}/api/states", headers=HEADERS, timeout=10)
states = r.json()

# Find all sensor entities that have temperature device_class and a numeric state
print("=== ALL outdoor-capable temperature sensors ===\n")
for s in sorted(states, key=lambda x: x["entity_id"]):
    eid = s["entity_id"]
    if not eid.startswith("sensor."):
        continue
    attrs = s["attributes"]
    # Temperature sensors that could be outdoor
    if attrs.get("device_class") == "temperature" or attrs.get("unit_of_measurement") in ("F", "C"):
        name = attrs.get("friendly_name", "").lower()
        # Exclude indoor/zone/ecobee/water sensors
        if any(x in eid.lower() or x in name for x in [
            "outdoor", "outside", "tempest", "weather", "air_temp",
            "feels_like", "dew_point", "wet_bulb", "wind_chill",
        ]):
            try:
                val = float(s["state"])
                print(f"  {eid}: {s['state']} {attrs.get('unit_of_measurement', '')}")
                print(f"    friendly_name: {attrs.get('friendly_name')}")
                print()
            except (ValueError, TypeError):
                pass

# Also find by looking at entity registry for weatherflow_cloud platform
print("\n=== Searching by entity_id containing common patterns ===")
patterns = ["air_temp", "temperature", "temp"]
for s in sorted(states, key=lambda x: x["entity_id"]):
    eid = s["entity_id"]
    if "tempest" in eid or "weatherflow" in eid or "wf_" in eid:
        print(f"  {eid}: {s['state']} {s['attributes'].get('unit_of_measurement', '')}  [{s['attributes'].get('friendly_name')}]")
