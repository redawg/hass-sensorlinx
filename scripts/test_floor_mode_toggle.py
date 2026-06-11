#!/usr/bin/env python3
"""Test: enable floor mode on a zone, verify valve count becomes available."""
import requests
import time

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

zone = "main_office"
floor_switch = f"switch.sensorlinx_outdoor_reset_floor_control_mode_{zone}"
valve_entity = f"number.sensorlinx_outdoor_reset_valve_count_{zone}"
target_entity = f"number.sensorlinx_outdoor_reset_floor_target_{zone}"

# 1. Verify valve count is unavailable (floor mode is off)
r = requests.get(f"{BASE}/api/states/{valve_entity}", headers=HEADERS, timeout=10)
print(f"BEFORE (floor mode OFF):")
print(f"  {valve_entity}: state={r.json()['state']}")
r2 = requests.get(f"{BASE}/api/states/{target_entity}", headers=HEADERS, timeout=10)
print(f"  {target_entity}: state={r2.json()['state']}")

# 2. Enable floor mode
print(f"\nEnabling floor mode: {floor_switch}")
r = requests.post(
    f"{BASE}/api/services/switch/turn_on",
    headers=HEADERS, json={"entity_id": floor_switch},
)
print(f"  Result: {r.status_code}")
time.sleep(3)

# 3. Verify valve count is now available
r = requests.get(f"{BASE}/api/states/{valve_entity}", headers=HEADERS, timeout=10)
print(f"\nAFTER (floor mode ON):")
print(f"  {valve_entity}: state={r.json()['state']}")
r2 = requests.get(f"{BASE}/api/states/{target_entity}", headers=HEADERS, timeout=10)
print(f"  {target_entity}: state={r2.json()['state']}")

# 4. Set valve count to 2
print(f"\nSetting valve count to 2...")
r = requests.post(
    f"{BASE}/api/services/number/set_value",
    headers=HEADERS, json={"entity_id": valve_entity, "value": 2},
)
print(f"  Result: {r.status_code}")
time.sleep(2)

r = requests.get(f"{BASE}/api/states/{valve_entity}", headers=HEADERS, timeout=10)
print(f"  {valve_entity}: state={r.json()['state']}")

# 5. Turn floor mode back off, verify unavailable again
print(f"\nDisabling floor mode...")
r = requests.post(
    f"{BASE}/api/services/switch/turn_off",
    headers=HEADERS, json={"entity_id": floor_switch},
)
time.sleep(3)
r = requests.get(f"{BASE}/api/states/{valve_entity}", headers=HEADERS, timeout=10)
print(f"\nAFTER (floor mode OFF again):")
print(f"  {valve_entity}: state={r.json()['state']}")
r2 = requests.get(f"{BASE}/api/states/{target_entity}", headers=HEADERS, timeout=10)
print(f"  {target_entity}: state={r2.json()['state']}")
