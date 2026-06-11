#!/usr/bin/env python3
"""Wire the Optimal Tankless sensors into the SensorLinx integration."""
import requests

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


def call_service(domain, service, data):
    r = requests.post(
        f"{BASE}/api/services/{domain}/{service}",
        headers=HEADERS, json=data,
    )
    print(f"  {domain}.{service}: {r.status_code}")
    return r


# 1. Set the supply water heater control entity
print("1. Setting supply water heater entity (for temp control)...")
call_service("sensorlinx", "set_supply_entity", {
    "entity_id": "water_heater.main_water_heater"
})

# 2. Set the hydronic loop sensors (inlet = return, outlet = supply)
print("\n2. Setting hydronic loop temperature sensors...")
call_service("sensorlinx", "set_hydronic_sensors", {
    "supply_temp_sensor": "sensor.main_water_heater_outlet_temperature",
    "return_temp_sensor": "sensor.main_water_heater_inlet_temperature",
})

# 3. Verify
print("\n3. Verifying configuration...")
r = requests.get(f"{BASE}/api/states", headers=HEADERS, timeout=10)
states = {s["entity_id"]: s for s in r.json()}

# Check supply water switch
sw = states.get("switch.sensorlinx_outdoor_reset_supply_water_reset_enabled")
if sw:
    print(f"  Supply Water Reset switch: {sw['state']}")
    print(f"    supply_entity_id: {sw['attributes'].get('supply_entity_id')}")

# Check delta-T sensor
dt = states.get("sensor.sensorlinx_outdoor_reset_hydronic_delta_t")
if dt:
    print(f"  Hydronic Delta-T: {dt['state']}")
    print(f"    supply_sensor: {dt['attributes'].get('supply_sensor')}")
    print(f"    return_sensor: {dt['attributes'].get('return_sensor')}")
    print(f"    supply_temp: {dt['attributes'].get('supply_temp')}")
    print(f"    return_temp: {dt['attributes'].get('return_temp')}")

# Check BTU sensor
btu = states.get("sensor.sensorlinx_outdoor_reset_hydronic_heat_output")
if btu:
    print(f"  Hydronic BTU Output: {btu['state']}")

# Current tankless state
wh = states.get("water_heater.main_water_heater")
if wh:
    print(f"\n  Optimal Tankless status:")
    print(f"    state: {wh['state']}")
    print(f"    current_temp: {wh['attributes'].get('current_temperature')}F")
    print(f"    target: {wh['attributes'].get('temperature')}F")
    print(f"    range: {wh['attributes'].get('min_temp')}-{wh['attributes'].get('max_temp')}F")

flow = states.get("sensor.main_water_heater_flow_rate")
if flow:
    print(f"    flow_rate: {flow['state']} GPM")

print("\nDone! Hydronic loop monitoring is now wired to the Optimal Tankless.")
