#!/usr/bin/env python3
"""Check HA logs and floor mode entities."""
import requests

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
headers = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

# Get all states and search for floor_mode or floor_control
r = requests.get(f"{BASE}/api/states", headers=headers)
states = r.json()

print("=== All switch entities with 'sensorlinx' ===")
for s in sorted(states, key=lambda x: x["entity_id"]):
    if "sensorlinx" in s["entity_id"] and s["entity_id"].startswith("switch."):
        print(f"  {s['entity_id']}: {s['state']}")

print()
print("=== All number entities with 'sensorlinx' ===")
for s in sorted(states, key=lambda x: x["entity_id"]):
    if "sensorlinx" in s["entity_id"] and s["entity_id"].startswith("number."):
        print(f"  {s['entity_id']}: {s['state']}")

print()
print("=== All sensor entities with 'sensorlinx' or 'outdoor_reset' ===")
for s in sorted(states, key=lambda x: x["entity_id"]):
    if ("sensorlinx" in s["entity_id"] or "outdoor_reset" in s["entity_id"]) and s["entity_id"].startswith("sensor."):
        print(f"  {s['entity_id']}: {s['state']}")

# Try to get logs via different endpoint
print()
print("=== HA Error Log ===")
r = requests.get(f"{BASE}/api/error_log", headers=headers)
if r.status_code == 200:
    log_text = r.text
    lines = log_text.split("\n")
    # Get sensorlinx lines or last 30
    slx_lines = [l for l in lines if "sensorlinx" in l.lower() or "outdoor" in l.lower()]
    if slx_lines:
        for l in slx_lines[-30:]:
            print(l)
    else:
        print(f"No sensorlinx entries. Log has {len(lines)} lines total.")
        for l in lines[-15:]:
            if l.strip():
                print(l)
else:
    print(f"Error log returned status {r.status_code}")

# Check integration status
print()
print("=== Integration Config Entries ===")
r = requests.get(f"{BASE}/api/config/config_entries/entry", headers=headers)
if r.status_code == 200:
    entries = r.json()
    for e in entries:
        if "sensorlinx" in str(e).lower():
            print(f"  domain={e.get('domain')} state={e.get('state')} title={e.get('title')}")
else:
    print(f"Config entries: {r.status_code}")
