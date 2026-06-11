#!/usr/bin/env python3
"""Check what's actually persisted by examining entity attributes and states."""
import requests
import json

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

r = requests.get(f"{BASE}/api/states", headers=HEADERS, timeout=15)
states = {s["entity_id"]: s for s in r.json()}

print("=" * 60)
print("OPTIONS PERSISTENCE CHECK (post-restart)")
print("=" * 60)

# 1. Hydronic sensors (set via service call -> async_update_entry)
print("\n--- Hydronic Sensors (service call path) ---")
dt = states.get("sensor.sensorlinx_outdoor_reset_hydronic_delta_t", {})
attrs = dt.get("attributes", {})
print(f"  supply_sensor: {attrs.get('supply_sensor', 'NOT SET')}")
print(f"  return_sensor: {attrs.get('return_sensor', 'NOT SET')}")
print(f"  flow_rate_sensor: {attrs.get('flow_rate_sensor', 'NOT SET')}")
hydronic_ok = "outlet" in str(attrs.get("supply_sensor", ""))
print(f"  PERSISTED: {'YES' if hydronic_ok else 'NO'}")

# 2. Zone valve counts
print("\n--- Zone Valve Counts (options flow path) ---")
valve_entities = [eid for eid in states if "valve_count" in eid]
if valve_entities:
    for eid in sorted(valve_entities):
        s = states[eid]
        print(f"  {eid}: {s['state']} (available={s['state'] != 'unavailable'})")
else:
    print("  No valve count entities found!")

# Check valve count NUMBER entities
valve_numbers = [eid for eid in states if "valve_count" in eid and eid.startswith("number.")]
if valve_numbers:
    for eid in sorted(valve_numbers):
        s = states[eid]
        print(f"  {eid}: {s['state']}")

# 3. Radiant floor switch configuration
print("\n--- Radiant Floor Switch Config ---")
# The ExternalControl reads from options. If a radiant floor switch entity
# is configured, ExternalControl would be tracking it.
# Let's look for any indication it was configured
# Check if there's a switch.radiant_floor entity
radiant_candidates = [eid for eid in states if "radiant" in eid.lower() and "floor" in eid.lower()]
if radiant_candidates:
    print(f"  Candidate entities found:")
    for eid in radiant_candidates:
        print(f"    {eid}: {states[eid]['state']}")
else:
    print("  No 'radiant floor' entities found in HA")
    print("  (This means no physical radiant floor switch exists to configure)")

# 4. Supply water entity
print("\n--- Supply Water Entity ---")
supply_target = states.get("sensor.sensorlinx_outdoor_reset_supply_water_target", {})
supply_attrs = supply_target.get("attributes", {})
print(f"  supply_entity_id: {supply_attrs.get('supply_entity_id', 'NOT SET')}")
supply_ok = supply_attrs.get("supply_entity_id") not in (None, "not configured", "NOT SET")
print(f"  PERSISTED: {'YES' if supply_ok else 'NO'}")

# 5. Floor mode and targets
print("\n--- Floor Control Mode ---")
floor_switches = [eid for eid in states if "floor_control" in eid or "floor_mode" in eid]
for eid in sorted(floor_switches):
    s = states[eid]
    print(f"  {eid}: {s['state']}")

# 6. Overall summary
print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)
print(f"  Hydronic sensors (service call path): {'PERSISTED' if hydronic_ok else 'NOT PERSISTED'}")
print(f"  Supply water entity: {'PERSISTED' if supply_ok else 'NOT PERSISTED'}")
print(f"  Radiant floor switch: See above (may not have a valid entity to configure)")
print(f"  Zone valve counts: Set via options flow (need user to configure via UI)")
