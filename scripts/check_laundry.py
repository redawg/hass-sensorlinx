#!/usr/bin/env python3
"""Diagnose why laundry zone is not calling for heat."""
import requests
import json

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
print("  LAUNDRY ZONE HEAT CALL DIAGNOSTIC")
print("=" * 60)

# 1. Climate entity state
print("\n--- Climate Entity ---")
state, attrs = get("climate.laundry_laundry")
print(f"  State: {state}")
print(f"  HVAC Action: {attrs.get('hvac_action')}")
print(f"  Temperature (setpoint): {attrs.get('temperature')}")
print(f"  Current Temp: {attrs.get('current_temperature')}")

# 2. Outdoor temperature
print("\n--- Outdoor Temperature ---")
state_outdoor, _ = get("sensor.quail_creek_ames_lake_279th_ct_ne_temperature")
print(f"  Outdoor Temp: {state_outdoor} F")

# 3. System shutdown temp
print("\n--- Shutdown / WWSD Settings ---")
state_shutdown, _ = get("number.sensorlinx_outdoor_reset_heating_curve_shutdown_temp")
print(f"  System Shutdown Temp: {state_shutdown} F")
state_zone_sd, _ = get("number.sensorlinx_zone_shutdown_laundry")
print(f"  Laundry Zone Shutdown: {state_zone_sd} F")

# 4. Floor mode status
print("\n--- Floor Mode ---")
state_fm, _ = get("switch.sensorlinx_outdoor_reset_floor_mode_laundry")
if state_fm is None:
    # Try alternate naming
    for eid in sorted(states):
        if "floor_mode" in eid and "laundry" in eid:
            state_fm, _ = get(eid)
            print(f"  Found: {eid} = {state_fm}")
            break
else:
    print(f"  Floor Mode (laundry): {state_fm}")

# 5. Floor temperature
print("\n--- Floor Temperature ---")
for eid in sorted(states):
    if "laundry" in eid and "floor" in eid and "temp" in eid:
        st, at = get(eid)
        print(f"  {eid}: {st} {at.get('unit_of_measurement', '')}")

# 6. Floor target
print("\n--- Floor Target ---")
for eid in sorted(states):
    if "floor_target" in eid and "laundry" in eid:
        st, _ = get(eid)
        print(f"  {eid}: {st}")

# 7. Zone target (computed by outdoor reset)
print("\n--- Outdoor Reset Zone Target ---")
for eid in sorted(states):
    if "outdoor_reset_target" in eid and "laundry" in eid:
        st, at = get(eid)
        print(f"  {eid}: {st}")

# 8. Heating curve target
print("\n--- Heating Curve ---")
state_target, attrs_target = get("sensor.sensorlinx_outdoor_reset_heating_curve_target")
print(f"  Heating Curve Target: {state_target} F")
print(f"  Outdoor: {attrs_target.get('outdoor_temp')}")
print(f"  Shutdown: {attrs_target.get('shutdown')}")
print(f"  Base: {attrs_target.get('base')}")
print(f"  Overshoot: {attrs_target.get('overshoot')}")

# 9. Outdoor Reset enabled?
print("\n--- Outdoor Reset System ---")
for eid in sorted(states):
    if "outdoor_reset_enabled" in eid:
        st, _ = get(eid)
        print(f"  {eid}: {st}")

# 10. Away mode
print("\n--- Away Mode ---")
for eid in sorted(states):
    if "laundry" in eid and "away" in eid:
        st, _ = get(eid)
        print(f"  {eid}: {st}")

# 11. Valve/relay status from ZON
print("\n--- ZON Relays / Active Zones ---")
for eid in sorted(states):
    if "active_zone" in eid or ("relay" in eid and "sensorlinx" in eid):
        st, at = get(eid)
        print(f"  {eid}: {st}")
        if at:
            for k, v in sorted(at.items()):
                if k != "friendly_name" and k != "icon":
                    print(f"    {k}: {v}")

# 12. Demand entities
print("\n--- Demand Status ---")
for eid in sorted(states):
    if "demand" in eid and "sensorlinx" in eid:
        st, at = get(eid)
        print(f"  {eid}: {st}")

# Analysis
print("\n" + "=" * 60)
print("  ANALYSIS")
print("=" * 60)

outdoor = float(state_outdoor) if state_outdoor not in (None, "unavailable", "unknown") else None
shutdown = float(state_shutdown) if state_shutdown not in (None, "unavailable", "unknown") else None

if outdoor is not None and shutdown is not None:
    if outdoor >= shutdown:
        print(f"\n  >>> OUTDOOR ({outdoor}F) >= SHUTDOWN ({shutdown}F)")
        print(f"  >>> System is in WARM WEATHER SHUTDOWN mode")
        print(f"  >>> This is why laundry is not calling for heat!")
        if state_fm == "on":
            print(f"  >>> Floor mode IS enabled - zone should still heat in floor mode")
            print(f"  >>> unless outdoor also exceeds per-zone shutdown")
    else:
        print(f"\n  Outdoor ({outdoor}F) < Shutdown ({shutdown}F) - system should be active")
        if state == "off":
            print(f"  >>> Climate entity is OFF - check if away mode or manual off")
        elif state == "heat" and attrs.get("hvac_action") == "idle":
            cur = attrs.get("current_temperature")
            sp = attrs.get("temperature")
            if cur is not None and sp is not None and cur >= sp:
                print(f"  >>> Room temp ({cur}F) >= setpoint ({sp}F) - satisfied, no call")
            else:
                print(f"  >>> Setpoint is {sp}F, room is {cur}F - should be calling!")
                print(f"  >>> Check THM demand signal and ZON relay")
