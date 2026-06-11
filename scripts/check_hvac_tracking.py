#!/usr/bin/env python3
"""Analyze main HVAC (Ecobee) activity and its effect on temps."""
import requests
import json
from datetime import datetime, timedelta

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
headers = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

month = datetime.now().strftime("%Y-%m")
log_url = f"{BASE}/local/sensorlinx_thermal_log/thermal_{month}.jsonl"
r = requests.get(log_url, headers=headers)
lines = r.text.strip().split("\n")
samples = [json.loads(l) for l in lines]

# Use one zone's data for ecobee fields (they're the same across all zone rows)
cutoff = datetime.now() - timedelta(hours=22)
ecobee_samples = [s for s in samples if s.get("zone") == "laundry" and s.get("ts", "") >= cutoff.isoformat()]

print(f"=== Main HVAC (Ecobee) Tracking Status ===")
print(f"Samples analyzed: {len(ecobee_samples)} (one per 5-min interval)")
print()

# Check what fields we're capturing
if ecobee_samples:
    last = ecobee_samples[-1]
    print("Fields being logged for Ecobee:")
    ecobee_fields = ["ecobee_mode", "ecobee_action", "ecobee_temp", "ecobee_setpoint",
                     "ecobee_humidity", "ecobee_fan", "ecobee_sensors", "occupancy"]
    for f in ecobee_fields:
        val = last.get(f)
        if val is not None:
            if isinstance(val, dict):
                print(f"  {f}: {json.dumps(val)}")
            else:
                print(f"  {f}: {val}")
        else:
            print(f"  {f}: NOT CAPTURED")
    print()

# Ecobee state transitions
print("=== Ecobee State Transitions (last 22h) ===")
prev_action = None
transitions = []
for s in ecobee_samples:
    action = s.get("ecobee_action")
    if action != prev_action and prev_action is not None:
        transitions.append({
            "ts": s["ts"],
            "from": prev_action,
            "to": action,
            "temp": s.get("ecobee_temp"),
            "setpoint": s.get("ecobee_setpoint"),
            "outdoor": s.get("outdoor_temp"),
        })
    prev_action = action

if transitions:
    print(f"Total transitions: {len(transitions)}")
    print()
    print(f"{'Time':<20} {'From':<10} {'To':<10} {'Temp':<8} {'Target':<8} {'Outdoor'}")
    print("-" * 70)
    for t in transitions:
        ts = t["ts"][11:16]
        print(f"{ts:<20} {t['from'] or '?':<10} {t['to'] or '?':<10} {t['temp'] or '?':<8} {t['setpoint'] or '?':<8} {t['outdoor'] or '?':.1f}F")
else:
    print("No state transitions found - Ecobee has been in same state entire period")
    # Show what state
    actions = [s.get("ecobee_action") for s in ecobee_samples]
    from collections import Counter
    counts = Counter(actions)
    print(f"  States seen: {dict(counts)}")

print()

# Ecobee activity summary by hour
print("=== Ecobee Hourly Breakdown ===")
print(f"{'Hour':<8} {'Action':<12} {'Temp':<8} {'Setpoint':<10} {'Humidity':<10} {'Sensors'}")
print("-" * 80)

now = datetime.now()
for hours_ago in range(21, -1, -3):
    start = now - timedelta(hours=hours_ago+3)
    end = now - timedelta(hours=hours_ago)
    block = [s for s in ecobee_samples if start.isoformat() <= s.get("ts","") <= end.isoformat()]
    if not block:
        continue
    
    heating = sum(1 for s in block if s.get("ecobee_action") == "heating")
    cooling = sum(1 for s in block if s.get("ecobee_action") == "cooling")
    idle = sum(1 for s in block if s.get("ecobee_action") == "idle")
    
    temps = [s.get("ecobee_temp") for s in block if s.get("ecobee_temp") is not None]
    setpoints = [s.get("ecobee_setpoint") for s in block if s.get("ecobee_setpoint") is not None]
    humidity = [s.get("ecobee_humidity") for s in block if s.get("ecobee_humidity") is not None]
    
    if heating > 0:
        action_str = f"HEAT {heating}/{len(block)}"
    elif cooling > 0:
        action_str = f"COOL {cooling}/{len(block)}"
    else:
        action_str = "idle"
    
    label = end.strftime("%I:%M%p").lstrip("0")
    temp_str = f"{sum(temps)/len(temps):.0f}F" if temps else "?"
    sp_str = f"{sum(setpoints)/len(setpoints):.0f}F" if setpoints else "?"
    hum_str = f"{sum(humidity)/len(humidity):.0f}%" if humidity else "?"
    
    # Get sensor averages for this block
    sensor_strs = []
    sensor_keys = set()
    for s in block:
        for k in s.get("ecobee_sensors", {}).keys():
            sensor_keys.add(k)
    for k in sorted(sensor_keys):
        vals = [s.get("ecobee_sensors", {}).get(k) for s in block if s.get("ecobee_sensors", {}).get(k)]
        if vals:
            sensor_strs.append(f"{k[:6]}={sum(vals)/len(vals):.0f}")
    
    print(f"{label:<8} {action_str:<12} {temp_str:<8} {sp_str:<10} {hum_str:<10} {', '.join(sensor_strs)}")

print()

# Cross-correlation: when Ecobee heats, how do floor zone temps change?
print("=== Impact of Main HVAC on Floor Zones ===")
# Find periods where ecobee was heating
all_zone_samples = [s for s in samples if s.get("ts", "") >= cutoff.isoformat()]
heating_periods = []
in_heating = False
start_temps = {}

for s in ecobee_samples:
    if s.get("ecobee_action") == "heating" and not in_heating:
        in_heating = True
        start_temps = {
            "ts": s["ts"],
            "ecobee": s.get("ecobee_temp"),
            "outdoor": s.get("outdoor_temp"),
        }
    elif s.get("ecobee_action") != "heating" and in_heating:
        in_heating = False
        heating_periods.append({
            "start": start_temps,
            "end": {
                "ts": s["ts"],
                "ecobee": s.get("ecobee_temp"),
                "outdoor": s.get("outdoor_temp"),
            }
        })

if heating_periods:
    print(f"Found {len(heating_periods)} Ecobee heating periods")
    for i, p in enumerate(heating_periods[:5]):
        start_t = p["start"]["ts"][11:16]
        end_t = p["end"]["ts"][11:16]
        temp_change = (p["end"]["ecobee"] or 0) - (p["start"]["ecobee"] or 0)
        print(f"  Period {i+1}: {start_t}-{end_t} | Ecobee {p['start']['ecobee']}F -> {p['end']['ecobee']}F ({'+' if temp_change>=0 else ''}{temp_change}F)")
else:
    print("No Ecobee heating periods detected in this window.")
    print("The main HVAC has been idle - radiant floor is handling heating load alone.")
