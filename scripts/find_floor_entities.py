#!/usr/bin/env python3
"""Find all sensorlinx floor-related entities."""
import requests

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

r = requests.get(f"{BASE}/api/states", headers=HEADERS, timeout=10)
states = r.json()

print("=== All SensorLinx entities ===")
sensorlinx_entities = [s for s in states if "sensorlinx" in s["entity_id"].lower() or "outdoor_reset" in s["entity_id"].lower()]
for s in sorted(sensorlinx_entities, key=lambda x: x["entity_id"]):
    print(f"  {s['entity_id']}: state={s['state']}")

print(f"\n  Total: {len(sensorlinx_entities)} entities")

# Also search for floor_mode or floor_control
print("\n=== Floor/valve related ===")
floor_entities = [s for s in states if any(k in s["entity_id"].lower() for k in ["floor_mode", "floor_control", "valve_count", "floor_target"])]
for s in sorted(floor_entities, key=lambda x: x["entity_id"]):
    print(f"  {s['entity_id']}: state={s['state']}")
