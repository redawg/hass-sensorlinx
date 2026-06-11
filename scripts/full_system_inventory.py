#!/usr/bin/env python3
"""Full system inventory: all data sources available for thermal dynamics analysis."""
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

time.sleep(5)

r = requests.get(f"{BASE}/api/states", headers=HEADERS, timeout=10)
states = {s["entity_id"]: s for s in r.json()}

print("=" * 70)
print("COMPLETE SYSTEM DATA INVENTORY FOR THERMAL DYNAMICS")
print("=" * 70)

# 1. SensorLinx Floor Heating Zones
print("\n" + "=" * 70)
print("1. SENSORLINX RADIANT FLOOR ZONES (HBX THM-0600)")
print("=" * 70)
climate_entities = sorted([eid for eid in states if eid.startswith("climate.") and any(z in eid for z in ["main_area", "main_office", "living_room", "laundry"])])
for eid in climate_entities:
    s = states[eid]
    attrs = s["attributes"]
    print(f"\n  {eid}")
    print(f"    State: {s['state']}")
    print(f"    Current temp: {attrs.get('current_temperature')}°F")
    print(f"    Target temp: {attrs.get('temperature')}°F")
    print(f"    HVAC action: {attrs.get('hvac_action')}")

# Floor temp sensors
print("\n  --- Floor Temperature Sensors ---")
floor_sensors = sorted([eid for eid in states if "floor" in eid.lower() and "temp" in eid.lower() and eid.startswith("sensor.")])
for eid in floor_sensors:
    s = states[eid]
    print(f"    {eid}: {s['state']}°F ({s['attributes'].get('friendly_name')})")

# 2. Outdoor Reset System
print("\n" + "=" * 70)
print("2. OUTDOOR RESET / HEATING CURVE SYSTEM")
print("=" * 70)
reset_entities = sorted([eid for eid in states if "outdoor_reset" in eid or "heating_curve" in eid.lower()])
for eid in reset_entities:
    s = states[eid]
    unit = s["attributes"].get("unit_of_measurement", "")
    print(f"  {eid}: {s['state']} {unit}")

# 3. Optimal Tankless Water Heater
print("\n" + "=" * 70)
print("3. OPTIMAL TANKLESS WATER HEATER (Floor Heat Source)")
print("=" * 70)
tankless_entities = sorted([eid for eid in states if "main_water_heater" in eid])
for eid in tankless_entities:
    s = states[eid]
    unit = s["attributes"].get("unit_of_measurement", "")
    name = s["attributes"].get("friendly_name", "")
    print(f"  {eid}: {s['state']} {unit}  [{name}]")

# 4. Outdoor Temperature
print("\n" + "=" * 70)
print("4. OUTDOOR TEMPERATURE SOURCES")
print("=" * 70)
outdoor_keywords = ["outdoor", "outside", "exterior", "tempest", "weather"]
outdoor_temps = sorted([
    eid for eid in states
    if eid.startswith("sensor.") and
    any(k in eid.lower() for k in outdoor_keywords) and
    states[eid]["attributes"].get("device_class") == "temperature"
])
for eid in outdoor_temps:
    s = states[eid]
    print(f"  {eid}: {s['state']} {s['attributes'].get('unit_of_measurement', '')}")

# Weather entities
weather_entities = sorted([eid for eid in states if eid.startswith("weather.")])
print("\n  --- Weather Entities ---")
for eid in weather_entities:
    s = states[eid]
    temp = s["attributes"].get("temperature")
    print(f"  {eid}: state={s['state']}, temp={temp}°F")

# 5. Ecobee / Main HVAC
print("\n" + "=" * 70)
print("5. ECOBEE / MAIN HOUSE HVAC")
print("=" * 70)
ecobee_entities = sorted([eid for eid in states if "ecobee" in eid.lower() or "main_floor" in eid.lower()])
if not ecobee_entities:
    # broader search
    ecobee_entities = sorted([eid for eid in states if eid.startswith("climate.") and "main" in eid.lower() and "water" not in eid.lower() and "area" not in eid.lower() and "office" not in eid.lower()])
for eid in ecobee_entities:
    s = states[eid]
    attrs = s["attributes"]
    print(f"  {eid}: state={s['state']}")
    if "current_temperature" in attrs:
        print(f"    current_temp: {attrs['current_temperature']}°F, target: {attrs.get('temperature')}°F")
        print(f"    hvac_action: {attrs.get('hvac_action')}")

# Remote sensors (ecobee)
remote_sensors = sorted([eid for eid in states if "remote_sensor" in eid.lower() or "ecobee" in eid.lower()])
if remote_sensors:
    print("\n  --- Ecobee Remote Sensors ---")
    for eid in remote_sensors:
        s = states[eid]
        if s["attributes"].get("device_class") in ("temperature", "occupancy") or "temperature" in eid or "occupancy" in eid:
            print(f"    {eid}: {s['state']} {s['attributes'].get('unit_of_measurement', '')}")

# 6. Energy Monitoring
print("\n" + "=" * 70)
print("6. ENERGY MONITORING (Heating System)")
print("=" * 70)
energy_entities = sorted([
    eid for eid in states
    if ("tankless" in eid.lower() or "furnace" in eid.lower() or "hot_water" in eid.lower())
    and states[eid]["attributes"].get("device_class") in ("energy", "power")
])
for eid in energy_entities:
    s = states[eid]
    unit = s["attributes"].get("unit_of_measurement", "")
    print(f"  {eid}: {s['state']} {unit}")

# 7. Thermal Data Logger
print("\n" + "=" * 70)
print("7. THERMAL DATA LOGGER")
print("=" * 70)
log_sensor = states.get("sensor.sensorlinx_outdoor_reset_thermal_log_samples")
if log_sensor:
    print(f"  Samples collected: {log_sensor['state']}")
    for k, v in log_sensor["attributes"].items():
        if k != "friendly_name":
            print(f"    {k}: {v}")

# 8. Hydronic Performance
print("\n" + "=" * 70)
print("8. HYDRONIC LOOP PERFORMANCE")
print("=" * 70)
hydro_entities = sorted([eid for eid in states if "hydronic" in eid or "delta_t" in eid])
for eid in hydro_entities:
    s = states[eid]
    unit = s["attributes"].get("unit_of_measurement", "")
    print(f"  {eid}: {s['state']} {unit}")
    for k, v in sorted(s["attributes"].items()):
        if k not in ("friendly_name", "unit_of_measurement", "device_class", "state_class", "icon"):
            print(f"    {k}: {v}")

# Summary
print("\n" + "=" * 70)
print("SUMMARY")
print("=" * 70)
total = len(states)
print(f"  Total HA entities: {total}")
print(f"  SensorLinx entities: {len([e for e in states if 'sensorlinx' in e])}")
print(f"  Optimal Tankless entities: {len(tankless_entities)}")
print(f"  Climate entities: {len([e for e in states if e.startswith('climate.')])}")
print(f"  Temperature sensors: {len([e for e in states if states[e]['attributes'].get('device_class') == 'temperature'])}")
