#!/usr/bin/env python3
"""Check current HA integration status."""
import requests

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

try:
    r = requests.get(f"{BASE}/api/", headers=HEADERS, timeout=5)
    print(f"HA API: {r.status_code} - {r.json().get('message', '')}")
except Exception as e:
    print(f"HA API error: {e}")
    exit()

r = requests.get(f"{BASE}/api/states", headers=HEADERS, timeout=10)
states = {s["entity_id"]: s for s in r.json()}

# Climate entities
zone_ids = [eid for eid in states if eid.startswith("climate.") and (
    "living_room_living" in eid or "main_office_main" in eid
    or "laundry_laundry" in eid or "main_area_main" in eid
)]
print(f"\n=== Climate Zones ({len(zone_ids)}) ===")
for eid in sorted(zone_ids):
    s = states[eid]
    attrs = s["attributes"]
    print(f"  {eid}: state={s['state']}, setpoint={attrs.get('temperature')}F, current={attrs.get('current_temperature')}F")

# Supply water entities
supply = [eid for eid in states if "supply_water" in eid]
print(f"\n=== Supply Water Control ({len(supply)}) ===")
for eid in sorted(supply):
    s = states[eid]
    attrs = s["attributes"]
    print(f"  {eid}: {s['state']}")
    if attrs.get("supply_entity_id"):
        print(f"    supply_entity: {attrs.get('supply_entity_id')}")
    if attrs.get("supply_min"):
        print(f"    range: {attrs.get('supply_min')}F - {attrs.get('supply_max')}F")

# Floor control switches
floor_sw = [eid for eid in states if "floor_control_mode" in eid]
print(f"\n=== Floor Control Mode ({len(floor_sw)}) ===")
for eid in sorted(floor_sw):
    print(f"  {eid}: {states[eid]['state']}")

# Floor targets
floor_t = [eid for eid in states if "floor_target" in eid and eid.startswith("number.")]
print(f"\n=== Floor Targets ({len(floor_t)}) ===")
for eid in sorted(floor_t):
    print(f"  {eid}: {states[eid]['state']}F")

# Floor boost
boost = [eid for eid in states if "floor_boost" in eid]
for eid in boost:
    print(f"\n  Floor Boost: {states[eid]['state']}F")

# Outdoor temp and curve
outdoor_eid = "sensor.home_weather_station_temperature"
curve_eid = next((eid for eid in states if "heating_curve_target" in eid), None)
print(f"\n=== System Status ===")
if outdoor_eid in states:
    print(f"  Outdoor temp: {states[outdoor_eid]['state']}F")
if curve_eid:
    s = states[curve_eid]
    attrs = s["attributes"]
    print(f"  Curve target: {s['state']}F")
    print(f"  Params: base={attrs.get('base')}, overshoot={attrs.get('overshoot')}, shutdown={attrs.get('shutdown')}")

# Supply water target sensor
swt = next((eid for eid in states if eid == "sensor.sensorlinx_outdoor_reset_supply_water_target"), None)
if swt:
    s = states[swt]
    print(f"  Supply water target: {s['state']}F")
    print(f"    enabled={s['attributes'].get('supply_control_enabled')}, entity={s['attributes'].get('supply_entity_id')}")
