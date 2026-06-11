#!/usr/bin/env python3
"""Find all ecobee and HVAC-related entities in HA."""
import json
import urllib.request

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"

req = urllib.request.Request(
    f"{BASE}/api/states",
    headers={"Authorization": f"Bearer {TOKEN}"},
)
data = json.loads(urllib.request.urlopen(req, timeout=15).read())

print("=== ECOBEE / CLIMATE ENTITIES ===")
for e in data:
    eid = e["entity_id"]
    if "ecobee" in eid.lower() or (eid.startswith("climate.") and "laundry" not in eid and "living_room" not in eid and "main_area" not in eid and "main_office" not in eid):
        attrs = e.get("attributes", {})
        print(f"  {eid}")
        print(f"    state={e['state']}")
        relevant_attrs = {k: v for k, v in attrs.items() if k in (
            "hvac_action", "hvac_modes", "current_temperature", "temperature",
            "target_temp_high", "target_temp_low", "fan_mode", "preset_mode",
            "friendly_name", "current_humidity"
        )}
        if relevant_attrs:
            print(f"    {json.dumps(relevant_attrs)}")
        print()

print("=== ECOBEE SENSORS (temp, occupancy, etc) ===")
for e in data:
    eid = e["entity_id"]
    if "ecobee" in eid.lower() or ("sensor." in eid and any(k in eid for k in ["main_floor", "upstairs", "family_room", "office_temperature"])):
        if e["entity_id"].startswith("sensor.") or e["entity_id"].startswith("binary_sensor."):
            attrs = e.get("attributes", {})
            print(f"  {eid} = {e['state']} {attrs.get('unit_of_measurement', '')}")
