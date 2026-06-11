#!/usr/bin/env python3
"""Wire all Optimal Tankless sensors into SensorLinx after fresh deploy."""
import requests
import time

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


def call_service(domain, service, data):
    r = requests.post(
        f"{BASE}/api/services/{domain}/{service}",
        headers=HEADERS, json=data,
    )
    print(f"  {domain}.{service}: {r.status_code}")
    if r.status_code != 200:
        print(f"    Response: {r.text[:200]}")
    return r


# Wait for HA to be ready
print("Checking HA availability...")
for attempt in range(10):
    try:
        r = requests.get(f"{BASE}/api/", headers=HEADERS, timeout=5)
        if r.status_code == 200:
            print(f"  HA is ready (attempt {attempt + 1})")
            break
    except Exception:
        pass
    print(f"  Waiting... (attempt {attempt + 1})")
    time.sleep(10)

time.sleep(5)

# 1. Set supply water heater entity
print("\n1. Setting supply water heater entity...")
call_service("sensorlinx", "set_supply_entity", {
    "entity_id": "water_heater.main_water_heater"
})

# 2. Set ALL hydronic sensors (supply, return, AND flow)
print("\n2. Setting hydronic loop sensors (supply, return, flow)...")
call_service("sensorlinx", "set_hydronic_sensors", {
    "supply_temp_sensor": "sensor.main_water_heater_outlet_temperature",
    "return_temp_sensor": "sensor.main_water_heater_inlet_temperature",
    "flow_rate_sensor": "sensor.main_water_heater_flow_rate",
})

# 3. Verify
print("\n3. Verifying...")
time.sleep(3)
r = requests.get(f"{BASE}/api/states", headers=HEADERS, timeout=10)
states = {s["entity_id"]: s for s in r.json()}

targets = {
    "sensor.sensorlinx_outdoor_reset_hydronic_delta_t": "Hydronic Delta-T",
    "sensor.sensorlinx_outdoor_reset_hydronic_heat_output": "Hydronic BTU Output",
    "sensor.sensorlinx_outdoor_reset_supply_water_target": "Supply Water Target",
    "switch.sensorlinx_outdoor_reset_supply_water_reset_enabled": "Supply Reset Switch",
}

for eid, label in targets.items():
    s = states.get(eid)
    if s:
        print(f"\n  {label} ({eid}):")
        print(f"    state = {s['state']}")
        for k, v in sorted(s["attributes"].items()):
            if k != "friendly_name":
                print(f"    {k} = {v}")
    else:
        print(f"\n  {label}: NOT FOUND")

# Show Optimal Tankless live data
print("\n\n=== Optimal Tankless Live Data ===")
tankless_sensors = [
    "sensor.main_water_heater_outlet_temperature",
    "sensor.main_water_heater_inlet_temperature",
    "sensor.main_water_heater_flow_rate",
    "sensor.main_water_heater_power_draw",
    "binary_sensor.main_water_heater_heating",
    "water_heater.main_water_heater",
]
for eid in tankless_sensors:
    s = states.get(eid)
    if s:
        name = s["attributes"].get("friendly_name", eid)
        unit = s["attributes"].get("unit_of_measurement", "")
        print(f"  {name}: {s['state']} {unit}")
