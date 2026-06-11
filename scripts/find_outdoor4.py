#!/usr/bin/env python3
"""Find Quail Creek weather station temperature entities."""
import requests

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

r = requests.get(f"{BASE}/api/states", headers=HEADERS, timeout=10)
states = r.json()

print("=== All 'quail_creek' entities ===\n")
for s in sorted(states, key=lambda x: x["entity_id"]):
    eid = s["entity_id"]
    if "quail_creek" in eid:
        print(f"  {eid}: {s['state']} {s['attributes'].get('unit_of_measurement', '')}")
        print(f"    [{s['attributes'].get('friendly_name')}]  device_class={s['attributes'].get('device_class')}")

# Also check desert hot springs
print("\n=== All 'desert_hot_springs' entities (just temp-like) ===\n")
for s in sorted(states, key=lambda x: x["entity_id"]):
    eid = s["entity_id"]
    if "desert_hot_springs" in eid and ("temp" in eid or "air" in eid):
        print(f"  {eid}: {s['state']} {s['attributes'].get('unit_of_measurement', '')}")
        print(f"    [{s['attributes'].get('friendly_name')}]  device_class={s['attributes'].get('device_class')}")
