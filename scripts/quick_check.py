#!/usr/bin/env python3
import requests, json
BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

r = requests.get(f"{BASE}/api/config/config_entries/entry", headers=HEADERS, timeout=10)
for e in r.json():
    if e["domain"] == "sensorlinx":
        print(f"SensorLinx: state={e['state']}")
        if e.get("reason"):
            print(f"  reason: {e['reason']}")

# Check error log
r2 = requests.get(f"{BASE}/api/error_log", headers=HEADERS, timeout=10)
lines = r2.text.split("\n")
errors = [l for l in lines if "sensorlinx" in l.lower() or "outdoor_reset" in l.lower()]
if errors:
    print(f"\nErrors ({len(errors)} lines):")
    for l in errors[-20:]:
        print(f"  {l[:200]}")
else:
    print("\nNo errors in log.")
