#!/usr/bin/env python3
"""Find Optimal Tankless integration entities and sensors."""
import requests
import json

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

r = requests.get(f"{BASE}/api/states", headers=HEADERS, timeout=10)
states = r.json()

# Search for anything related to optimal, tankless, water heater
keywords = ["optimal", "tankless", "water_heater", "hot_water", "rinnai", "navien", "rheem"]
print("=== Searching for tankless/water heater entities ===\n")

found = []
for s in states:
    eid = s["entity_id"].lower()
    name = s["attributes"].get("friendly_name", "").lower()
    if any(k in eid or k in name for k in keywords):
        found.append(s)

if found:
    print(f"Found {len(found)} entities:\n")
    for s in sorted(found, key=lambda x: x["entity_id"]):
        attrs = s["attributes"]
        print(f"  {s['entity_id']}")
        print(f"    state: {s['state']}")
        print(f"    friendly_name: {attrs.get('friendly_name')}")
        # Print all attributes
        for k, v in sorted(attrs.items()):
            if k != "friendly_name":
                print(f"    {k}: {v}")
        print()
else:
    print("No entities found with those keywords.")
    print("\nSearching more broadly for water/heater/boiler...")
    broader = ["water", "heater", "boiler", "hwh"]
    found2 = []
    for s in states:
        eid = s["entity_id"].lower()
        name = s["attributes"].get("friendly_name", "").lower()
        if any(k in eid or k in name for k in broader):
            found2.append(s)
    print(f"\nBroader search found {len(found2)} entities:")
    for s in sorted(found2, key=lambda x: x["entity_id"]):
        print(f"  {s['entity_id']}: state={s['state']} ({s['attributes'].get('friendly_name', '')})")

# Also check config entries for the integration
print("\n=== Config Entries (water heater related) ===")
r2 = requests.get(f"{BASE}/api/config/config_entries/entry", headers=HEADERS, timeout=10)
if r2.status_code == 200:
    entries = r2.json()
    for e in entries:
        domain = e.get("domain", "").lower()
        title = e.get("title", "").lower()
        if any(k in domain or k in title for k in keywords + ["water", "heater", "boiler"]):
            print(f"  domain={e['domain']}, title={e.get('title')}, state={e.get('state')}")
            print(f"    entry_id={e.get('entry_id')}")
            if e.get("options"):
                print(f"    options={json.dumps(e['options'])[:200]}")
