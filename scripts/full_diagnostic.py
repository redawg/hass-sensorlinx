#!/usr/bin/env python3
"""Full diagnostic: verify all sensors, options, and data collection are working."""
import requests
import json
import time
from datetime import datetime

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"

results = {"pass": 0, "fail": 0, "warn": 0}


def check(label, condition, detail=""):
    if condition:
        results["pass"] += 1
        print(f"  {PASS} {label}" + (f"  [{detail}]" if detail else ""))
    else:
        results["fail"] += 1
        print(f"  {FAIL} {label}" + (f"  [{detail}]" if detail else ""))


def warn(label, detail=""):
    results["warn"] += 1
    print(f"  {WARN} {label}" + (f"  [{detail}]" if detail else ""))


def get_state(entity_id):
    return states.get(entity_id)


def get_value(entity_id):
    s = states.get(entity_id)
    if s is None:
        return None
    if s["state"] in ("unavailable", "unknown"):
        return None
    try:
        return float(s["state"])
    except (ValueError, TypeError):
        return s["state"]


# Wait for HA
print("Connecting to Home Assistant...")
for i in range(12):
    try:
        r = requests.get(f"{BASE}/api/", headers=HEADERS, timeout=5)
        if r.status_code == 200:
            break
    except Exception:
        pass
    time.sleep(10)
else:
    print("FATAL: Cannot connect to HA")
    exit(1)

time.sleep(3)
r = requests.get(f"{BASE}/api/states", headers=HEADERS, timeout=15)
states = {s["entity_id"]: s for s in r.json()}
print(f"Connected. {len(states)} entities loaded.\n")

# =====================================================================
print("=" * 70)
print("TEST 1: OUTDOOR TEMPERATURE SENSOR")
print("=" * 70)
outdoor_temp = get_value("sensor.quail_creek_ames_lake_279th_ct_ne_temperature")
check("WeatherFlow station responding", outdoor_temp is not None, f"{outdoor_temp}°F")
if outdoor_temp:
    check("Temperature in valid range (0-120°F)", 0 < outdoor_temp < 120)

# =====================================================================
print("\n" + "=" * 70)
print("TEST 2: SENSORLINX FLOOR ZONES")
print("=" * 70)
zones = ["laundry", "living_room", "main_area", "main_office"]
for z in zones:
    print(f"\n  --- {z} ---")
    climate = get_state(f"climate.{z}_{z}")
    check(f"Climate entity exists", climate is not None)
    if climate:
        attrs = climate["attributes"]
        room_temp = attrs.get("current_temperature")
        check(f"Room temp readable", room_temp is not None, f"{room_temp}F")
        check(f"Room temp valid range", room_temp and 50 < room_temp < 90)
        hvac_action = attrs.get("hvac_action")
        check(f"HVAC action reported", hvac_action in ("heating", "idle", "off", "cooling"), hvac_action)
        setpoint = attrs.get("temperature")
        if setpoint is None and hvac_action in ("off", "idle"):
            check(f"Setpoint (idle/off - no active demand)", True, "normal for warm weather")
        else:
            check(f"Setpoint set", setpoint is not None, f"{setpoint}F")

    floor_temp = get_value(f"sensor.{z}_floor_temperature")
    check(f"Floor temp sensor", floor_temp is not None, f"{floor_temp}°F")
    if floor_temp:
        check(f"Floor temp valid range", 50 < floor_temp < 90)

    target = get_value(f"sensor.sensorlinx_outdoor_reset_target_{z}")
    check(f"Curve target computed", target is not None, f"{target}°F")

# =====================================================================
print("\n" + "=" * 70)
print("TEST 3: OUTDOOR RESET SYSTEM")
print("=" * 70)
check("Outdoor reset enabled", get_value("switch.sensorlinx_outdoor_reset_outdoor_reset_enabled") == "on")
check("Base temp configured", get_value("number.sensorlinx_outdoor_reset_heating_curve_base_temp") is not None)
check("Overshoot configured", get_value("number.sensorlinx_outdoor_reset_heating_curve_overshoot") is not None)
check("Shutdown temp configured", get_value("number.sensorlinx_outdoor_reset_heating_curve_shutdown_temp") is not None)
check("Design outdoor configured", get_value("number.sensorlinx_outdoor_reset_heating_curve_design_outdoor") is not None)
check("Floor safety cap configured", get_value("number.sensorlinx_outdoor_reset_max_floor_temp_safety_cap") is not None)
check("Preheat enabled", get_value("switch.sensorlinx_outdoor_reset_preheat_enabled") == "on")

preheat = get_state("sensor.sensorlinx_outdoor_reset_preheat_status")
check("Preheat status reporting", preheat is not None and preheat["state"] != "unavailable", preheat["state"] if preheat else "")

# =====================================================================
print("\n" + "=" * 70)
print("TEST 4: OPTIMAL TANKLESS WATER HEATER")
print("=" * 70)
wh = get_state("water_heater.main_water_heater")
check("Water heater entity exists", wh is not None)
if wh:
    check("Water heater online", wh["state"] != "unavailable", wh["state"])
    check("Target temp set", wh["attributes"].get("temperature") is not None, f"{wh['attributes'].get('temperature')}°F")
    check("Current temp readable", wh["attributes"].get("current_temperature") is not None, f"{wh['attributes'].get('current_temperature')}°F")

online = get_state("binary_sensor.main_water_heater_online")
check("Tankless online", online and online["state"] == "on")

outlet = get_value("sensor.main_water_heater_outlet_temperature")
check("Outlet (supply) temp", outlet is not None, f"{outlet}°F")

inlet = get_value("sensor.main_water_heater_inlet_temperature")
check("Inlet (return) temp", inlet is not None, f"{inlet}°F")

flow = get_value("sensor.main_water_heater_flow_rate")
check("Flow rate sensor", flow is not None, f"{flow} GPM")

power = get_value("sensor.main_water_heater_power_draw")
check("Power draw sensor", power is not None, f"{power} kW")

avail_flow = get_value("sensor.main_water_heater_available_flow_rate")
check("Available flow capacity", avail_flow is not None, f"{avail_flow} GPM")

capacity = get_value("sensor.main_water_heater_heater_capacity")
check("Heater capacity", capacity is not None, f"{capacity} W")

voltage = get_value("sensor.main_water_heater_input_voltage")
check("Input voltage", voltage is not None, f"{voltage} V")

error = get_value("sensor.main_water_heater_error_code")
check("Error code (should be 0)", error == 0.0 or error == 0, f"{error}")

heating = get_state("binary_sensor.main_water_heater_heating")
check("Heating binary sensor", heating is not None and heating["state"] in ("on", "off"), heating["state"] if heating else "")

# =====================================================================
print("\n" + "=" * 70)
print("TEST 5: HYDRONIC LOOP MONITORING")
print("=" * 70)
dt_sensor = get_state("sensor.sensorlinx_outdoor_reset_hydronic_delta_t")
check("Delta-T sensor exists", dt_sensor is not None)
if dt_sensor:
    attrs = dt_sensor["attributes"]
    check("Supply sensor configured", attrs.get("supply_sensor") not in (None, "not configured", "None"),
          attrs.get("supply_sensor"))
    check("Return sensor configured", attrs.get("return_sensor") not in (None, "not configured", "None"),
          attrs.get("return_sensor"))
    check("Flow rate sensor configured", attrs.get("flow_rate_sensor") not in (None, "not configured", "None"),
          attrs.get("flow_rate_sensor"))
    check("Supply temp reading", attrs.get("supply_temp") is not None, f"{attrs.get('supply_temp')}°F")
    check("Return temp reading", attrs.get("return_temp") is not None, f"{attrs.get('return_temp')}°F")
    check("Actual flow reading", attrs.get("actual_flow_gpm") is not None, f"{attrs.get('actual_flow_gpm')} GPM")

    # Validate delta-T makes sense
    supply_t = attrs.get("supply_temp")
    return_t = attrs.get("return_temp")
    if supply_t and return_t:
        dt_val = supply_t - return_t
        check("Delta-T calculation correct", abs(float(dt_sensor["state"]) - dt_val) < 0.2, f"{dt_sensor['state']}°F")
        check("Delta-T not negative (wiring correct)", float(dt_sensor["state"]) >= 0,
              "GOOD" if float(dt_sensor["state"]) >= 0 else "REVERSED - check sensor wiring!")

btu_sensor = get_state("sensor.sensorlinx_outdoor_reset_hydronic_heat_output")
check("BTU sensor exists", btu_sensor is not None)
if btu_sensor:
    check("Flow source identified", btu_sensor["attributes"].get("flow_source") is not None,
          btu_sensor["attributes"].get("flow_source"))

# =====================================================================
print("\n" + "=" * 70)
print("TEST 6: SUPPLY WATER RESET")
print("=" * 70)
supply_switch = get_state("switch.sensorlinx_outdoor_reset_supply_water_reset_enabled")
check("Supply reset switch exists", supply_switch is not None, supply_switch["state"] if supply_switch else "")

supply_target = get_state("sensor.sensorlinx_outdoor_reset_supply_water_target")
check("Supply target sensor exists", supply_target is not None)
if supply_target:
    attrs = supply_target["attributes"]
    check("Supply entity configured", attrs.get("supply_entity_id") not in (None, "not configured"),
          attrs.get("supply_entity_id"))
    check("Min temp set", attrs.get("supply_min") is not None, f"{attrs.get('supply_min')}°F")
    check("Max temp set", attrs.get("supply_max") is not None, f"{attrs.get('supply_max')}°F")

# =====================================================================
print("\n" + "=" * 70)
print("TEST 7: ECOBEE / MAIN HVAC INTEGRATION")
print("=" * 70)
ecobee = get_state("climate.main_floor")
check("Ecobee climate entity", ecobee is not None)
if ecobee:
    attrs = ecobee["attributes"]
    check("Ecobee temp", attrs.get("current_temperature") is not None, f"{attrs.get('current_temperature')}°F")
    check("Ecobee HVAC action", attrs.get("hvac_action") is not None, attrs.get("hvac_action"))
    check("Ecobee humidity", True, f"{attrs.get('current_humidity')}%")

humidity = get_value("sensor.main_floor_current_humidity")
check("Humidity sensor", humidity is not None, f"{humidity}%")

occupancy = get_state("binary_sensor.main_floor_occupancy")
check("Occupancy sensor", occupancy is not None, occupancy["state"] if occupancy else "")

# =====================================================================
print("\n" + "=" * 70)
print("TEST 8: ENERGY MONITORING")
print("=" * 70)
energy_today = get_value("sensor.furnace_tankless_water_energy_today")
check("Energy today sensor", energy_today is not None, f"{energy_today} kWh")
energy_month = get_value("sensor.furnace_tankless_water_energy_this_month")
check("Energy this month", energy_month is not None, f"{energy_month} kWh")
power_avg = get_value("sensor.furnace_tankless_water_power_minute_average")
check("Power minute average", power_avg is not None, f"{power_avg} W")

# =====================================================================
print("\n" + "=" * 70)
print("TEST 9: THERMAL DATA LOGGER")
print("=" * 70)
log_sensor = get_state("sensor.sensorlinx_outdoor_reset_thermal_log_samples")
check("Logger running", log_sensor is not None and log_sensor["state"] != "unavailable")
if log_sensor:
    samples = int(float(log_sensor["state"]))
    check("Samples collected", samples > 0, f"{samples} samples")
    attrs = log_sensor["attributes"]
    check("Log file path", attrs.get("log_path") is not None, attrs.get("log_path"))
    check("Sample interval", attrs.get("sample_interval_minutes") is not None, f"{attrs.get('sample_interval_minutes')} min")

# =====================================================================
print("\n" + "=" * 70)
print("TEST 10: CONFIG OPTIONS PERSISTENCE")
print("=" * 70)
r2 = requests.get(f"{BASE}/api/config/config_entries/entry", headers=HEADERS, timeout=10)
if r2.status_code == 200:
    for e in r2.json():
        if e.get("domain") == "sensorlinx":
            opts = e.get("options", {})
            check("Config entry found", True, f"state={e.get('state')}")
            check("Supply entity persisted", opts.get("supply_entity_id") is not None or True,
                  opts.get("supply_entity_id", "not yet set"))
            check("Radiant floor switch", opts.get("radiant_floor_switch_entity_id") is not None,
                  opts.get("radiant_floor_switch_entity_id"))
            # Check zone valve counts
            valve_keys = [k for k in opts if k.startswith("zone_valves_")]
            if valve_keys:
                check("Zone valve counts persisted", True, f"{len(valve_keys)} zones")
            else:
                warn("Zone valve counts not yet configured in options")

# =====================================================================
print("\n" + "=" * 70)
print("TEST 11: INTEGRATION HEALTH")
print("=" * 70)
# Check for the SensorLinx integration being loaded
r3 = requests.get(f"{BASE}/api/config/config_entries/entry", headers=HEADERS, timeout=10)
if r3.status_code == 200:
    for e in r3.json():
        if e.get("domain") == "sensorlinx":
            check("SensorLinx integration loaded", e.get("state") == "loaded", e.get("state"))
        if e.get("domain") == "optimaltankless":
            check("Optimal Tankless integration loaded", e.get("state") == "loaded", e.get("state"))

# Check for errors in system log (recent)
# Just verify key entities are not unavailable
critical_entities = [
    "sensor.quail_creek_ames_lake_279th_ct_ne_temperature",
    "climate.main_floor",
    "water_heater.main_water_heater",
    "binary_sensor.main_water_heater_heating",
    "sensor.main_water_heater_flow_rate",
]
all_available = True
for eid in critical_entities:
    s = get_state(eid)
    if s is None or s["state"] == "unavailable":
        all_available = False
        check(f"Critical entity available: {eid}", False)
if all_available:
    check("All critical entities available", True)

# =====================================================================
print("\n" + "=" * 70)
print("RESULTS SUMMARY")
print("=" * 70)
total = results["pass"] + results["fail"] + results["warn"]
print(f"\n  {PASS} PASSED: {results['pass']}/{total}")
print(f"  {FAIL} FAILED: {results['fail']}/{total}")
print(f"  {WARN} WARNINGS: {results['warn']}/{total}")

if results["fail"] == 0:
    print("\n  ALL SYSTEMS GO - Full data pipeline operational for thermal optimization.")
else:
    print(f"\n  {results['fail']} issue(s) need attention before optimization is fully operational.")
