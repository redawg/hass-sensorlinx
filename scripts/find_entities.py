#!/usr/bin/env python3
import requests
BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

r = requests.get(f"{BASE}/api/states", headers=HEADERS, timeout=15)
states = r.json()
print(f"Total entities: {len(states)}")

print("\n=== Floor mode switches ===")
for s in sorted(states, key=lambda x: x["entity_id"]):
    if "floor_control" in s["entity_id"] or "floor_mode" in s["entity_id"]:
        print(f"  {s['entity_id']}: {s['state']}")

print("\n=== Valve count entities ===")
for s in sorted(states, key=lambda x: x["entity_id"]):
    if "valve_count" in s["entity_id"]:
        print(f"  {s['entity_id']}: {s['state']}")

print("\n=== Floor target entities ===")
for s in sorted(states, key=lambda x: x["entity_id"]):
    if "floor_target" in s["entity_id"]:
        print(f"  {s['entity_id']}: {s['state']}")

print("\n=== Outdoor reset entities (sample) ===")
for s in sorted(states, key=lambda x: x["entity_id"]):
    if "outdoor_reset" in s["entity_id"]:
        print(f"  {s['entity_id']}: {s['state']}")
        
