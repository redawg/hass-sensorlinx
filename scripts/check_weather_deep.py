#!/usr/bin/env python3
"""Deep check for any forecast-capable entities."""
import requests
import json

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

r = requests.get(f"{BASE}/api/states", headers=HEADERS, timeout=10)
states = r.json()

# Find ALL entities with 'forecast' in attributes or name
print("=== Entities with 'forecast' in attributes ===")
for s in states:
    if "forecast" in json.dumps(s.get("attributes", {})).lower():
        print(f"  {s['entity_id']}: {s['state']}")
        fc = s["attributes"].get("forecast")
        if fc:
            print(f"    has {len(fc)} forecast entries")

# Check all config entries for weather integrations
print("\n=== Config Entries (weather-related) ===")
r2 = requests.get(f"{BASE}/api/config/config_entries/entry", headers=HEADERS, timeout=10)
if r2.status_code == 200:
    entries = r2.json()
    for e in entries:
        domain = e.get("domain", "")
        if any(x in domain.lower() for x in ["weather", "met", "owm", "openweather", "tempest", "weatherflow"]):
            print(f"  {e['domain']}: title={e.get('title')}, state={e.get('state')}, entry_id={e.get('entry_id')}")
else:
    print(f"  Could not fetch config entries: {r2.status_code}")

# Check available services
r3 = requests.get(f"{BASE}/api/services", headers=HEADERS, timeout=10)
services = r3.json()
for svc in services:
    if svc["domain"] == "weather":
        print(f"\n=== Weather domain services ===")
        for name in svc.get("services", {}):
            print(f"  weather.{name}")

# Try to find the Met.no entity
met_entities = [s for s in states if "met" in s["entity_id"].lower() or "forecast" in s["entity_id"].lower()]
print(f"\n=== Met/Forecast entities ({len(met_entities)}) ===")
for m in met_entities[:10]:
    print(f"  {m['entity_id']}: {m['state']}")
