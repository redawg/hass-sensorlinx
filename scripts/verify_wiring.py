#!/usr/bin/env python3
"""Verify hydronic wiring after service calls."""
import requests
import time

time.sleep(5)

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

r = requests.get(f"{BASE}/api/states", headers=HEADERS, timeout=10)
states = {s["entity_id"]: s for s in r.json()}

targets = [
    "switch.sensorlinx_outdoor_reset_supply_water_reset_enabled",
    "sensor.sensorlinx_outdoor_reset_hydronic_delta_t",
    "sensor.sensorlinx_outdoor_reset_hydronic_heat_output",
    "sensor.sensorlinx_outdoor_reset_supply_water_target",
]

for t in targets:
    s = states.get(t)
    if s:
        state = s["state"]
        attrs = s["attributes"]
        print(f"{t}:")
        print(f"  state={state}")
        for k, v in sorted(attrs.items()):
            if k != "friendly_name":
                print(f"  {k}={v}")
        print()
    else:
        print(f"{t}: NOT FOUND\n")

# Also check config entry options to confirm persistence
r2 = requests.get(f"{BASE}/api/config/config_entries/entry", headers=HEADERS, timeout=10)
if r2.status_code == 200:
    for e in r2.json():
        if e.get("domain") == "sensorlinx":
            print(f"SensorLinx config entry options:")
            opts = e.get("options", {})
            for k in ["supply_entity_id", "supply_temp_sensor", "return_temp_sensor", "forecast_entity_id"]:
                print(f"  {k}: {opts.get(k, 'NOT SET')}")
