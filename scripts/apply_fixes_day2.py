#!/usr/bin/env python3
"""Day 2 adjustments based on specialist report findings."""
import requests

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
headers = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


def set_number(entity_id, value):
    r = requests.post(
        f"{BASE}/api/services/number/set_value",
        headers=headers,
        json={"entity_id": entity_id, "value": value},
    )
    status = "OK" if r.status_code == 200 else f"FAIL ({r.status_code})"
    print(f"  {entity_id} = {value} ... {status}")


print("=== Day 2 Corrections (HVAC Specialist) ===")
print()
print("Issue 1: Living Room overheated to 80.5F (exceeded wood floor safety cap)")
print("  Action: Reduce offset from +2 to +1, enable floor control mode at 74F")
set_number("number.sensorlinx_outdoor_reset_zone_offset_living_room", 1.0)
print()

print("Issue 2: Main Office peaked at 78.8F (1.2F from safety cap)")
print("  Action: Reduce offset to -1 (was 0). Office has solar gain and ")
print("  thermal mass from Ecobee office sensor showing 72-75F ambient.")
set_number("number.sensorlinx_outdoor_reset_zone_offset_main_office", -1.0)
print()

print("Issue 3: Overshoot parameter too aggressive for mild weather")
print("  Action: Reduce overshoot from 6 to 4.5F (gentler curve slope)")
set_number("number.sensorlinx_outdoor_reset_heating_curve_overshoot", 4.5)
print()

print("Issue 4: Safety cap needs tighter margin for wood floors")
print("  Action: Lower safety cap from 80 to 78F (2F margin for thermal lag)")
set_number("number.sensorlinx_outdoor_reset_max_floor_temp_safety_cap", 78.0)
print()

# Verify
import time
time.sleep(3)
print("=== Verification ===")
r = requests.get(f"{BASE}/api/states", headers=headers)
states = r.json()

checks = [
    "number.sensorlinx_outdoor_reset_zone_offset_living_room",
    "number.sensorlinx_outdoor_reset_zone_offset_main_office",
    "number.sensorlinx_outdoor_reset_heating_curve_overshoot",
    "number.sensorlinx_outdoor_reset_max_floor_temp_safety_cap",
    "sensor.sensorlinx_outdoor_reset_heating_curve_target",
]
for entity_id in checks:
    for s in states:
        if s["entity_id"] == entity_id:
            name = s["attributes"].get("friendly_name", entity_id)
            print(f"  {name}: {s['state']}")
            break
