#!/usr/bin/env python3
"""Fix the hydronic sensor wiring - return sensor was incorrectly set to input_voltage."""
import requests
import time

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

# Wait for HA
for i in range(10):
    try:
        r = requests.get(f"{BASE}/api/", headers=HEADERS, timeout=5)
        if r.status_code == 200:
            break
    except Exception:
        pass
    time.sleep(10)

time.sleep(3)

# Fix: set correct hydronic sensors
print("Fixing hydronic sensor wiring...")
r = requests.post(
    f"{BASE}/api/services/sensorlinx/set_hydronic_sensors",
    headers=HEADERS,
    json={
        "supply_temp_sensor": "sensor.main_water_heater_outlet_temperature",
        "return_temp_sensor": "sensor.main_water_heater_inlet_temperature",
        "flow_rate_sensor": "sensor.main_water_heater_flow_rate",
    },
)
print(f"  set_hydronic_sensors: {r.status_code}")

time.sleep(3)

# Verify
r = requests.get(f"{BASE}/api/states/sensor.sensorlinx_outdoor_reset_hydronic_delta_t", headers=HEADERS, timeout=10)
if r.status_code == 200:
    s = r.json()
    print(f"\n  Delta-T: {s['state']}°F")
    print(f"  Supply sensor: {s['attributes'].get('supply_sensor')}")
    print(f"  Return sensor: {s['attributes'].get('return_sensor')}")
    print(f"  Supply temp: {s['attributes'].get('supply_temp')}")
    print(f"  Return temp: {s['attributes'].get('return_temp')}")
