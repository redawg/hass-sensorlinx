#!/usr/bin/env python3
"""Check if thermal log is being written on Forest Home."""
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

# Check sensorlinx config entry state
code, entries = api_get("/api/config/config_entries/entry")
if code == 200:
    for entry in entries:
        if entry.get("domain") == "sensorlinx":
            print(f"SensorLinx state: {entry.get('state')}")
            if entry.get("state") != "loaded":
                print(f"  ERROR reason: {entry.get('reason')}")

# Check the thermal log sensor
code, states = api_get("/api/states")
if code == 200:
    for s in states:
        if "thermal_log" in s["entity_id"]:
            print(f"\n{s['entity_id']} = {s['state']}")
            print(f"  attrs: {json.dumps(s.get('attributes', {}), indent=2)}")

# Check for errors in HA logs
print("\n=== Checking HA error log (last entries) ===")
code, data = api_get("/api/error_log")
if code == 200:
    lines = str(data) if not isinstance(data, str) else data
    # Filter for sensorlinx-related entries
    for line in lines.split("\n"):
        if "sensorlinx" in line.lower() or "thermal" in line.lower():
            print(f"  {line[:200]}")
