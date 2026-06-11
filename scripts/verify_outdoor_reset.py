#!/usr/bin/env python3
"""Verify outdoor reset entities are loaded on Forest Home."""
import json
import urllib.request

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"


def api_get(path):
    req = urllib.request.Request(f"{BASE}{path}", headers={"Authorization": f"Bearer {TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode())
    except Exception as e:
        return 0, str(e)


# Check if HA is up
code, data = api_get("/api/")
if code != 200:
    print(f"HA not ready: {code} {data}")
    exit(1)
print("HA is running.\n")

# Get all states and find outdoor_reset entities
code, states = api_get("/api/states")
if code != 200:
    print(f"Cannot get states: {code}")
    exit(1)

print("=== Outdoor Reset Entities ===")
reset_entities = [s for s in states if "outdoor_reset" in s["entity_id"] or "heating_curve" in s["entity_id"] or "floor_temp_max" in s["entity_id"] or "floor_heating_target" in s["entity_id"]]
for e in sorted(reset_entities, key=lambda x: x["entity_id"]):
    print(f"  {e['entity_id']} = {e['state']}")

print(f"\nTotal outdoor reset entities: {len(reset_entities)}")

# Check if integration is loaded
code, entries = api_get("/api/config/config_entries/entry")
if code == 200:
    for entry in entries:
        if entry.get("domain") == "sensorlinx":
            print(f"\nSensorLinx config entry: state={entry.get('state')}")
            if entry.get("state") != "loaded":
                print(f"  WARNING: state is {entry.get('state')}, not loaded!")
                # Check HA logs for error
