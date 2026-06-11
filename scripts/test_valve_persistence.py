#!/usr/bin/env python3
"""Test zone valve count and floor mode persistence across restarts."""
import requests
import time
import json

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


def wait_for_ha(max_wait=150):
    for i in range(max_wait // 5):
        try:
            r = requests.get(f"{BASE}/api/", headers=HEADERS, timeout=5)
            if r.status_code == 200:
                time.sleep(10)
                return True
        except Exception:
            pass
        time.sleep(5)
    return False


def get_state(entity_id):
    r = requests.get(f"{BASE}/api/states/{entity_id}", headers=HEADERS, timeout=10)
    if r.status_code == 200:
        return r.json()
    return None


def call_service(domain, service, data):
    try:
        r = requests.post(
            f"{BASE}/api/services/{domain}/{service}",
            headers=HEADERS, json=data, timeout=30,
        )
        return r.status_code
    except Exception:
        return -1


# Wait for HA to be ready
print("Waiting for HA...")
if not wait_for_ha():
    print("FAILED: HA not responding")
    exit(1)
print("HA is up!")

# Step 1: Enable floor mode for laundry (so valve count entity becomes available)
print("\n=== STEP 1: Enable floor control mode for Laundry ===")
code = call_service("switch", "turn_on", {
    "entity_id": "switch.sensorlinx_outdoor_reset_floor_control_mode_laundry"
})
print(f"  turn_on result: {code}")
time.sleep(3)

# Verify floor mode is ON
s = get_state("switch.sensorlinx_outdoor_reset_floor_control_mode_laundry")
print(f"  Floor mode state: {s['state'] if s else 'NOT FOUND'}")

# Step 2: Set valve count to 3
print("\n=== STEP 2: Set valve count for Laundry to 3 ===")
code = call_service("number", "set_value", {
    "entity_id": "number.sensorlinx_outdoor_reset_valve_count_laundry",
    "value": 3,
})
print(f"  set_value result: {code}")
time.sleep(3)

s = get_state("number.sensorlinx_outdoor_reset_valve_count_laundry")
print(f"  Valve count state: {s['state'] if s else 'NOT FOUND'}")

# Step 3: Set floor target to 72
print("\n=== STEP 3: Set floor target for Laundry to 72 ===")
code = call_service("number", "set_value", {
    "entity_id": "number.sensorlinx_outdoor_reset_floor_target_laundry",
    "value": 72,
})
print(f"  set_value result: {code}")
time.sleep(3)

s = get_state("number.sensorlinx_outdoor_reset_floor_target_laundry")
print(f"  Floor target state: {s['state'] if s else 'NOT FOUND'}")

# Step 4: Restart HA
print("\n=== STEP 4: Restarting HA ===")
try:
    call_service("homeassistant", "restart", {})
except Exception:
    pass
print("  Restart sent, waiting...")
time.sleep(30)

if not wait_for_ha():
    print("FAILED: HA didn't come back!")
    exit(1)
print("  HA is back up!")
time.sleep(10)  # extra for integrations

# Step 5: Verify persistence
print("\n=== STEP 5: Verify persistence after restart ===")

# Floor mode
s = get_state("switch.sensorlinx_outdoor_reset_floor_control_mode_laundry")
floor_mode = s["state"] if s else "NOT FOUND"
print(f"  Floor mode:  {floor_mode}  {'PASS' if floor_mode == 'on' else 'FAIL'}")

# Valve count
s = get_state("number.sensorlinx_outdoor_reset_valve_count_laundry")
valve_count = s["state"] if s else "NOT FOUND"
print(f"  Valve count: {valve_count}  {'PASS' if valve_count == '3.0' or valve_count == '3' else 'FAIL'}")

# Floor target
s = get_state("number.sensorlinx_outdoor_reset_floor_target_laundry")
floor_target = s["state"] if s else "NOT FOUND"
print(f"  Floor target: {floor_target}  {'PASS' if floor_target == '72.0' or floor_target == '72' else 'FAIL'}")

# Overall
all_pass = (
    floor_mode == "on"
    and valve_count in ("3.0", "3")
    and floor_target in ("72.0", "72")
)
print(f"\n{'=' * 50}")
if all_pass:
    print("  ALL VALUES PERSISTED ACROSS RESTART!")
else:
    print("  SOME VALUES DID NOT PERSIST - check details above")
print(f"{'=' * 50}")
