#!/usr/bin/env python3
"""Check HA logs for sensorlinx/outdoor_reset errors."""
import requests

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
headers = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

# Get error log
r = requests.get(f"{BASE}/api/error_log", headers=headers)
log = r.text

# Filter for sensorlinx related
lines = log.split("\n")
relevant = []
capture = False
for line in lines:
    if "sensorlinx" in line.lower() or "outdoor_reset" in line.lower():
        capture = True
        relevant.append(line)
    elif capture and (line.startswith(" ") or line.startswith("\t")):
        relevant.append(line)
    else:
        capture = False

if relevant:
    print("=== SensorLinx Log Entries ===")
    for line in relevant[-80:]:
        print(line)
else:
    print("No sensorlinx entries found in error log")
    # Show last 40 lines
    print("\n=== Last 40 lines of log ===")
    for line in lines[-40:]:
        print(line)
