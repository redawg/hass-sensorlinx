#!/usr/bin/env python3
"""Check HA version and test options persistence from the options flow path."""
import requests
import json

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

# Get HA version
r = requests.get(f"{BASE}/api/config", headers=HEADERS, timeout=10)
cfg = r.json()
print(f"HA Version: {cfg.get('version')}")
print(f"Config dir: {cfg.get('config_dir')}")

# Check HA error log for clues about async_update_entry
r2 = requests.get(f"{BASE}/api/error_log", headers=HEADERS, timeout=10)
log = r2.text
# Look for config entry or options related errors
lines = log.split("\n")
relevant = [l for l in lines if "sensorlinx" in l.lower() and ("option" in l.lower() or "update_entry" in l.lower() or "error" in l.lower())]
if relevant:
    print("\n=== Relevant log entries ===")
    for l in relevant[-20:]:
        print(f"  {l}")
else:
    print("\nNo options-related errors in log.")

# Search for any sensorlinx errors
errors = [l for l in lines if "sensorlinx" in l.lower() and ("error" in l.lower() or "exception" in l.lower() or "traceback" in l.lower())]
if errors:
    print(f"\n=== SensorLinx errors ({len(errors)} entries) ===")
    for l in errors[-10:]:
        print(f"  {l}")
