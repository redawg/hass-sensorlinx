#!/usr/bin/env python3
"""Test if async_update_entry actually persists options."""
import requests
import time
import json

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


def get_options():
    r = requests.get(f"{BASE}/api/config/config_entries/entry", headers=HEADERS, timeout=10)
    for e in r.json():
        if e.get("domain") == "sensorlinx":
            return e.get("options", {})
    return None


def call_service(domain, service, data):
    r = requests.post(
        f"{BASE}/api/services/{domain}/{service}",
        headers=HEADERS,
        json=data,
        timeout=10,
    )
    return r.status_code, r.text


# Step 1: Check current options
print("STEP 1: Current options")
opts = get_options()
print(f"  Options before: {json.dumps(opts, indent=2)}")

# Step 2: Call set_hydronic_sensors to trigger async_update_entry
print("\nSTEP 2: Calling sensorlinx.set_hydronic_sensors...")
code, text = call_service("sensorlinx", "set_hydronic_sensors", {
    "supply_temp_sensor": "sensor.main_water_heater_outlet_temperature",
    "return_temp_sensor": "sensor.main_water_heater_inlet_temperature",
    "flow_rate_sensor": "sensor.main_water_heater_flow_rate",
})
print(f"  Response: {code}")

# Step 3: Wait for reload to complete (update listener triggers reload)
print("\nSTEP 3: Waiting 15s for reload to complete...")
time.sleep(15)

# Step 4: Check options again
print("\nSTEP 4: Options after service call")
opts = get_options()
print(f"  Options after: {json.dumps(opts, indent=2)}")

if opts:
    print("\n  RESULT: Options ARE persisting!")
else:
    print("\n  RESULT: Options are NOT persisting - bug confirmed!")
    print("  The async_update_entry call triggers the update_listener which")
    print("  calls async_reload. The reload may be clearing options.")
