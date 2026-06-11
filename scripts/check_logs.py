#!/usr/bin/env python3
"""Check HA error log for sensorlinx entries."""
import requests

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

r = requests.get(f"{BASE}/api/error_log", headers=HEADERS, timeout=15)
log = r.text
lines = log.split("\n")

print("=== SensorLinx log entries (last 50) ===\n")
relevant = [l for l in lines if "sensorlinx" in l.lower()]
for l in relevant[-50:]:
    print(l)

print(f"\n\n=== Total sensorlinx entries: {len(relevant)} ===")
