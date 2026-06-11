#!/usr/bin/env python3
"""Check the thermal log data format."""
import requests
import json
from datetime import datetime

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
headers = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

month = datetime.now().strftime("%Y-%m")
log_url = f"{BASE}/local/sensorlinx_thermal_log/thermal_{month}.jsonl"
r = requests.get(log_url, headers=headers)

if r.status_code == 200:
    lines = r.text.strip().split("\n")
    # Show last 3 samples pretty-printed
    print(f"Total lines: {len(lines)}")
    print()
    for i, line in enumerate(lines[-3:]):
        sample = json.loads(line)
        print(f"--- Sample {len(lines) - 2 + i} ---")
        print(json.dumps(sample, indent=2, default=str)[:2000])
        print()
else:
    print(f"Error: {r.status_code}")
