#!/usr/bin/env python3
"""Enable floor control mode on all zones and set floor targets to 70F."""
import requests

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


def get_states():
    r = requests.get(f"{BASE}/api/states", headers=HEADERS)
    r.raise_for_status()
    return {s["entity_id"]: s for s in r.json()}


def call_service(domain, service, data):
    r = requests.post(
        f"{BASE}/api/services/{domain}/{service}",
        headers=HEADERS,
        json=data,
    )
    r.raise_for_status()
    return r.json()


states = get_states()

# Find all floor mode switches
floor_mode_switches = [
    eid for eid in states if eid.startswith("switch.") and "floor_control_mode" in eid
]
print(f"Found {len(floor_mode_switches)} floor mode switches:")
for sw in sorted(floor_mode_switches):
    current = states[sw]["state"]
    print(f"  {sw}: {current}")

# Enable floor control mode on all zones
for sw in floor_mode_switches:
    if states[sw]["state"] != "on":
        call_service("switch", "turn_on", {"entity_id": sw})
        print(f"  -> Enabled {sw}")
    else:
        print(f"  -> Already on: {sw}")

# Find floor target number entities and set to 70F
floor_targets = [
    eid for eid in states if "floor_target" in eid and eid.startswith("number.")
]
print(f"\nFound {len(floor_targets)} floor target sliders:")
for num in sorted(floor_targets):
    current = states[num]["state"]
    print(f"  {num}: {current}F")
    if float(current) != 70.0:
        call_service("number", "set_value", {"entity_id": num, "value": 70.0})
        print(f"    -> Set to 70.0F")

# Set floor boost to 2.0F
floor_boost = [eid for eid in states if "floor_boost" in eid and eid.startswith("number.")]
if floor_boost:
    print(f"\nFloor Boost entity: {floor_boost[0]} = {states[floor_boost[0]]['state']}")
    call_service("number", "set_value", {"entity_id": floor_boost[0], "value": 2.0})
    print(f"  -> Set to 2.0F (targets will be 70F mild, 72F cold)")

print("\nDone! All zones now in floor control mode targeting 70-72F.")
