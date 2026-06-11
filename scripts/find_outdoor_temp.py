#!/usr/bin/env python3
"""Find outdoor temperature entities in HA."""
import json
import urllib.request
import os

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"

req = urllib.request.Request(
    f"{BASE}/api/states",
    headers={"Authorization": f"Bearer {TOKEN}"},
)
data = json.loads(urllib.request.urlopen(req, timeout=15).read())

print("=== WEATHER ENTITIES ===")
for e in data:
    if e["entity_id"].startswith("weather."):
        attrs = e.get("attributes", {})
        print(f"  {e['entity_id']} state={e['state']} temp={attrs.get('temperature')} unit={attrs.get('temperature_unit')}")

print("\n=== OUTDOOR/OUTSIDE/TEMP SENSORS ===")
for e in data:
    eid = e["entity_id"].lower()
    if any(kw in eid for kw in ["outdoor", "outside", "external_temp", "weather_temp"]):
        print(f"  {e['entity_id']} state={e['state']} unit={e.get('attributes',{}).get('unit_of_measurement','')}")

print("\n=== ECOBEE SENSORS ===")
for e in data:
    if "ecobee" in e["entity_id"].lower():
        print(f"  {e['entity_id']} state={e['state']} unit={e.get('attributes',{}).get('unit_of_measurement','')}")

print("\n=== WEATHERFLOW SENSORS ===")
for e in data:
    if "weatherflow" in e["entity_id"].lower() and "temp" in e["entity_id"].lower():
        print(f"  {e['entity_id']} state={e['state']} unit={e.get('attributes',{}).get('unit_of_measurement','')}")