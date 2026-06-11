#!/usr/bin/env python3
"""Apply recommended zone adjustments."""
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
    return r.status_code == 200


def set_switch(entity_id, on=True):
    action = "turn_on" if on else "turn_off"
    r = requests.post(
        f"{BASE}/api/services/switch/{action}",
        headers=headers,
        json={"entity_id": entity_id},
    )
    status = "OK" if r.status_code == 200 else f"FAIL ({r.status_code})"
    print(f"  {entity_id} {'ON' if on else 'OFF'} ... {status}")
    return r.status_code == 200


print("=== Applying Adjustments ===")
print()

# 1. Living Room: zone offset +2
print("1. Living Room - zone offset +2 (lagging behind target)")
set_number("number.sensorlinx_outdoor_reset_zone_offset_living_room", 2.0)
print()

# 2. Main Area: zone offset -2
print("2. Main Area - zone offset -2 (overshooting target)")
set_number("number.sensorlinx_outdoor_reset_zone_offset_main_area", -2.0)
print()

# 3. Laundry: enable floor control mode at 74F
print("3. Laundry - enable floor control mode, target 74F")
set_number("number.sensorlinx_outdoor_reset_floor_target_laundry", 74.0)
set_switch("switch.sensorlinx_outdoor_reset_floor_control_mode_laundry", on=True)
print()

# 4. Main Office: no change (performing well)
print("4. Main Office - no change (performing well at offset 0)")
print()

# Verify
import time
time.sleep(5)
print("=== Verification ===")
r = requests.get(f"{BASE}/api/states", headers=headers)
states = r.json()

checks = [
    "number.sensorlinx_outdoor_reset_zone_offset_living_room",
    "number.sensorlinx_outdoor_reset_zone_offset_main_area",
    "number.sensorlinx_outdoor_reset_floor_target_laundry",
    "switch.sensorlinx_outdoor_reset_floor_control_mode_laundry",
]
for entity_id in checks:
    for s in states:
        if s["entity_id"] == entity_id:
            name = s["attributes"].get("friendly_name", entity_id)
            print(f"  {name}: {s['state']}")
            break
