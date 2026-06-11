#!/usr/bin/env python3
"""Read the actual .storage/core.config_entries file to check persistence."""
import requests
import json

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

# Use a template sensor to read the .storage file
# First, let's try the config entry diagnostics endpoint
ENTRY_ID = "01KTFBBKK8DSSRWFADF208M6TY"
r = requests.get(
    f"{BASE}/api/diagnostics/config_entry/{ENTRY_ID}",
    headers=HEADERS,
    timeout=10,
)
print(f"Diagnostics: status={r.status_code}")
if r.status_code == 200:
    print(json.dumps(r.json(), indent=2)[:2000])

# Try getting full entry details via different endpoints
print("\n\n=== Trying config_entries/entry (list all) ===")
r2 = requests.get(f"{BASE}/api/config/config_entries/entry", headers=HEADERS, timeout=10)
for e in r2.json():
    if e.get("domain") == "sensorlinx":
        print(f"Full entry response:")
        print(json.dumps(e, indent=2))

# Let's also check if there's a /config_entries/entry/<id> endpoint
print("\n\n=== Single entry endpoint ===")
r3 = requests.get(
    f"{BASE}/api/config/config_entries/entry/01KTFBBKK8DSSRWFADF208M6TY",
    headers=HEADERS, timeout=10,
)
print(f"Status: {r3.status_code}")
if r3.status_code == 200:
    print(json.dumps(r3.json(), indent=2))
