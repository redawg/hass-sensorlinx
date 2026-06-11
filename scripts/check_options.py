#!/usr/bin/env python3
"""Check what's actually persisted in config entry options."""
import requests
import json

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

r = requests.get(f"{BASE}/api/config/config_entries/entry", headers=HEADERS, timeout=10)
entries = r.json()

print("=== SensorLinx Config Entry Options ===\n")
for e in entries:
    if e.get("domain") == "sensorlinx":
        print(f"Entry ID: {e['entry_id']}")
        print(f"State: {e['state']}")
        print(f"Options ({len(e.get('options', {}))} keys):")
        opts = e.get("options", {})
        if not opts:
            print("  (EMPTY - nothing persisted!)")
        else:
            for k, v in sorted(opts.items()):
                print(f"  {k}: {v}")
        print()
        print(f"Data ({len(e.get('data', {}))} keys):")
        data = e.get("data", {})
        for k, v in sorted(data.items()):
            if "password" in k.lower():
                print(f"  {k}: ***")
            else:
                print(f"  {k}: {v}")
