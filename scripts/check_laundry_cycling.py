#!/usr/bin/env python3
"""Diagnose laundry zone cycling on/off behavior."""
import requests
import json
from datetime import datetime, timedelta

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

r = requests.get(f"{BASE}/api/states", headers=HEADERS, timeout=20)
states = {s["entity_id"]: s for s in r.json()}


def get(entity_id):
    s = states.get(entity_id)
    if s is None:
        return None, {}
    return s["state"], s.get("attributes", {})


print("=" * 60)
print("  LAUNDRY CYCLING DIAGNOSTIC")
print("=" * 60)

# Current state snapshot
print("\n--- Current State ---")
state, attrs = get("climate.laundry_laundry")
print(f"  Climate state: {state}")
print(f"  HVAC action: {attrs.get('hvac_action')}")
print(f"  Room temp: {attrs.get('current_temperature')} F")
print(f"  Setpoint: {attrs.get('temperature')} F")

outdoor_state, _ = get("sensor.quail_creek_ames_lake_279th_ct_ne_temperature")
print(f"  Outdoor temp: {outdoor_state} F")

shutdown_state, _ = get("number.sensorlinx_outdoor_reset_heating_curve_shutdown_temp")
print(f"  Shutdown temp: {shutdown_state} F")

floor_state, _ = get("sensor.laundry_floor_temperature")
print(f"  Floor temp: {floor_state} F")

floor_target, _ = get("number.sensorlinx_outdoor_reset_floor_target_laundry")
print(f"  Floor target: {floor_target} F")

floor_max_state, _ = get("number.sensorlinx_outdoor_reset_floor_max_safety_laundry")
if floor_max_state is None:
    for eid in sorted(states):
        if "floor_max" in eid and "laundry" in eid:
            floor_max_state, _ = get(eid)
            print(f"  Floor max (safety): {floor_max_state} F  ({eid})")
            break
else:
    print(f"  Floor max (safety): {floor_max_state} F")

# Check floor mode
fm_state = None
for eid in sorted(states):
    if "floor_mode" in eid and "laundry" in eid:
        fm_state, _ = get(eid)
        print(f"  Floor mode: {fm_state}  ({eid})")
        break

# Check the outdoor reset heating curve target
target_state, target_attrs = get("sensor.sensorlinx_outdoor_reset_heating_curve_target")
print(f"\n--- Heating Curve ---")
print(f"  Computed target: {target_state} F")

# Check history of climate.laundry_laundry for recent state changes
print("\n--- Recent History (climate.laundry_laundry) ---")
try:
    now = datetime.utcnow()
    start = (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    hist_url = f"{BASE}/api/history/period/{start}?filter_entity_id=climate.laundry_laundry&minimal_response&no_attributes"
    r2 = requests.get(hist_url, headers=HEADERS, timeout=15)
    if r2.status_code == 200:
        history = r2.json()
        if history and history[0]:
            changes = history[0]
            print(f"  State changes in last 2 hours: {len(changes)}")
            for entry in changes[-20:]:
                ts = entry.get("last_changed", "")
                st = entry.get("state", "?")
                print(f"    {ts}: {st}")
        else:
            print("  No history data returned")
    else:
        print(f"  History API returned {r2.status_code}")
except Exception as e:
    print(f"  History error: {e}")

# Check history of outdoor temp (is it oscillating around shutdown?)
print("\n--- Recent Outdoor Temp History ---")
try:
    hist_url2 = f"{BASE}/api/history/period/{start}?filter_entity_id=sensor.quail_creek_ames_lake_279th_ct_ne_temperature&minimal_response&no_attributes"
    r3 = requests.get(hist_url2, headers=HEADERS, timeout=15)
    if r3.status_code == 200:
        history2 = r3.json()
        if history2 and history2[0]:
            entries = history2[0]
            print(f"  Data points in last 2 hours: {len(entries)}")
            # Show last 10
            for entry in entries[-10:]:
                ts = entry.get("last_changed", "")
                st = entry.get("state", "?")
                print(f"    {ts}: {st} F")
        else:
            print("  No history data")
    else:
        print(f"  Outdoor history API returned {r3.status_code}")
except Exception as e:
    print(f"  Outdoor history error: {e}")

# Check floor temp history
print("\n--- Recent Floor Temp History ---")
try:
    hist_url3 = f"{BASE}/api/history/period/{start}?filter_entity_id=sensor.laundry_floor_temperature&minimal_response&no_attributes"
    r4 = requests.get(hist_url3, headers=HEADERS, timeout=15)
    if r4.status_code == 200:
        history3 = r4.json()
        if history3 and history3[0]:
            entries = history3[0]
            print(f"  Data points in last 2 hours: {len(entries)}")
            for entry in entries[-10:]:
                ts = entry.get("last_changed", "")
                st = entry.get("state", "?")
                print(f"    {ts}: {st} F")
        else:
            print("  No history data")
    else:
        print(f"  Floor history API returned {r4.status_code}")
except Exception as e:
    print(f"  Floor history error: {e}")

# Analysis
print("\n" + "=" * 60)
print("  CYCLING ANALYSIS")
print("=" * 60)

outdoor = float(outdoor_state) if outdoor_state not in (None, "unavailable", "unknown") else None
shutdown = float(shutdown_state) if shutdown_state not in (None, "unavailable", "unknown") else None
floor = float(floor_state) if floor_state not in (None, "unavailable", "unknown") else None
floor_max = float(floor_max_state) if floor_max_state not in (None, "unavailable", "unknown") else None
floor_tgt = float(floor_target) if floor_target not in (None, "unavailable", "unknown") else None

if outdoor is not None and shutdown is not None:
    margin = shutdown - outdoor
    print(f"\n  Outdoor-to-shutdown margin: {margin:.1f} F")
    if abs(margin) < 3:
        print(f"  >>> OUTDOOR TEMP ({outdoor}F) IS VERY CLOSE TO SHUTDOWN ({shutdown}F)")
        print(f"  >>> As outdoor oscillates around {shutdown}F, the system turns ON/OFF")
        print(f"  >>> This is the most likely cause of cycling!")
        print(f"  >>> FIX: Add a deadband (hysteresis) to the shutdown logic")
        print(f"  >>>       e.g., shut off at {shutdown}F, don't restart until {shutdown-2}F")

if floor is not None and floor_max is not None:
    floor_margin = floor_max - floor
    print(f"\n  Floor-to-safety-cap margin: {floor_margin:.1f} F")
    if floor_margin < 3:
        print(f"  >>> FLOOR TEMP ({floor}F) IS NEAR SAFETY CAP ({floor_max}F)")
        print(f"  >>> Floor hits cap -> zone turns off -> floor cools -> zone turns on")
        print(f"  >>> This can cause rapid cycling!")

if floor is not None and floor_tgt is not None:
    print(f"\n  Floor ({floor}F) vs Target ({floor_tgt}F): delta = {floor_tgt - floor:.1f} F")
