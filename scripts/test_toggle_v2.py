#!/usr/bin/env python3
"""Test floor mode toggle with proper error handling."""
import requests
import time

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

zone = "main_office"
floor_switch = f"switch.sensorlinx_outdoor_reset_floor_control_mode_{zone}"
valve_entity = f"number.sensorlinx_outdoor_reset_valve_count_{zone}"
target_entity = f"number.sensorlinx_outdoor_reset_floor_target_{zone}"


def get_state(entity_id):
    try:
        r = requests.get(f"{BASE}/api/states/{entity_id}", headers=HEADERS, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return data.get("state", "ERROR")
        return f"HTTP {r.status_code}"
    except Exception as e:
        return f"ERROR: {e}"


# Wait for HA
print("Checking HA...")
for i in range(5):
    try:
        r = requests.get(f"{BASE}/api/", headers=HEADERS, timeout=5)
        if r.status_code == 200:
            print("  HA ready")
            break
    except Exception:
        pass
    print(f"  waiting ({i+1})...")
    time.sleep(10)

time.sleep(5)

# Step 1: Check before
print(f"\n--- BEFORE (floor mode OFF) ---")
print(f"  Floor switch:  {get_state(floor_switch)}")
print(f"  Valve count:   {get_state(valve_entity)}")
print(f"  Floor target:  {get_state(target_entity)}")

# Step 2: Enable floor mode
print(f"\n--- ENABLING floor mode ---")
r = requests.post(
    f"{BASE}/api/services/switch/turn_on",
    headers=HEADERS, json={"entity_id": floor_switch},
)
print(f"  turn_on result: {r.status_code}")
time.sleep(2)

# Step 3: Check after
print(f"\n--- AFTER (floor mode ON) ---")
print(f"  Floor switch:  {get_state(floor_switch)}")
print(f"  Valve count:   {get_state(valve_entity)}")
print(f"  Floor target:  {get_state(target_entity)}")

# Step 4: Set valve count
print(f"\n--- Setting valve count to 3 ---")
r = requests.post(
    f"{BASE}/api/services/number/set_value",
    headers=HEADERS, json={"entity_id": valve_entity, "value": 3},
)
print(f"  set_value result: {r.status_code}")
time.sleep(2)
print(f"  Valve count:   {get_state(valve_entity)}")

# Step 5: Disable floor mode
print(f"\n--- DISABLING floor mode ---")
r = requests.post(
    f"{BASE}/api/services/switch/turn_off",
    headers=HEADERS, json={"entity_id": floor_switch},
)
print(f"  turn_off result: {r.status_code}")
time.sleep(2)

print(f"\n--- AFTER (floor mode OFF again) ---")
print(f"  Floor switch:  {get_state(floor_switch)}")
print(f"  Valve count:   {get_state(valve_entity)}")
print(f"  Floor target:  {get_state(target_entity)}")
