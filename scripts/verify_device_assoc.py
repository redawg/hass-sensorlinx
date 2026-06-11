#!/usr/bin/env python3
"""Verify per-zone entities are now associated with THM devices."""
import requests

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

r = requests.get(f"{BASE}/api/states", headers=HEADERS, timeout=10)
states = {s["entity_id"]: s for s in r.json()}

# Check floor mode switches
print("=== Floor Mode Switches (per thermostat) ===")
floor_mode_entities = [eid for eid in states if "floor_mode" in eid and "sensorlinx" in eid]
for eid in sorted(floor_mode_entities):
    s = states[eid]
    print(f"  {eid}: state={s['state']}")

# Check valve count entities
print("\n=== Valve Count Entities (per thermostat, floor mode only) ===")
valve_entities = [eid for eid in states if "valve_count" in eid]
for eid in sorted(valve_entities):
    s = states[eid]
    print(f"  {eid}: state={s['state']} (available={s['state'] != 'unavailable'})")

# Check floor target entities
print("\n=== Floor Target Entities (per thermostat, floor mode only) ===")
target_entities = [eid for eid in states if "floor_target" in eid and "sensorlinx" in eid]
for eid in sorted(target_entities):
    s = states[eid]
    print(f"  {eid}: state={s['state']} (available={s['state'] != 'unavailable'})")

# Now enable floor mode on one zone and verify valve becomes available
print("\n=== Testing: Enable floor mode on one zone ===")
# Find first floor mode switch
if floor_mode_entities:
    test_switch = sorted(floor_mode_entities)[0]
    print(f"  Turning ON: {test_switch}")
    r = requests.post(
        f"{BASE}/api/services/switch/turn_on",
        headers=HEADERS,
        json={"entity_id": test_switch},
    )
    print(f"  Result: {r.status_code}")

    import time
    time.sleep(3)

    # Re-check valve counts
    r2 = requests.get(f"{BASE}/api/states", headers=HEADERS, timeout=10)
    states2 = {s["entity_id"]: s for s in r2.json()}

    print("\n  After enabling floor mode:")
    for eid in sorted(valve_entities):
        s = states2.get(eid)
        if s:
            print(f"    {eid}: state={s['state']}")
    for eid in sorted(target_entities):
        s = states2.get(eid)
        if s:
            print(f"    {eid}: state={s['state']}")
