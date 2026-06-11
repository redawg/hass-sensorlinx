#!/usr/bin/env python3
"""Deep dive on Main Office zone thermal performance."""
import requests
import json
from datetime import datetime, timedelta
from collections import defaultdict

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
headers = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

month = datetime.now().strftime("%Y-%m")
log_url = f"{BASE}/local/sensorlinx_thermal_log/thermal_{month}.jsonl"
r = requests.get(log_url, headers=headers)
lines = r.text.strip().split("\n")
samples = [json.loads(l) for l in lines]

# Get office data since fix
cutoff = datetime.now() - timedelta(hours=22)
office = [s for s in samples if s.get("zone") == "main_office" and s.get("ts", "") >= cutoff.isoformat()]

print(f"=== Main Office Deep Dive ({len(office)} samples, ~22h) ===")
print()

rooms = [float(s["room_temp"]) for s in office if s.get("room_temp") is not None]
floors = [float(s["floor_temp"]) for s in office if s.get("floor_temp") is not None]
outdoors = [float(s["outdoor_temp"]) for s in office if s.get("outdoor_temp") is not None]
targets = [float(s["commanded_setpoint"]) for s in office if s.get("commanded_setpoint") is not None]

print(f"Room temp:    min={min(rooms):.1f}  max={max(rooms):.1f}  avg={sum(rooms)/len(rooms):.1f}  current={rooms[-1]:.1f}F")
print(f"Floor temp:   min={min(floors):.1f}  max={max(floors):.1f}  avg={sum(floors)/len(floors):.1f}  current={floors[-1]:.1f}F")
print(f"Setpoint:     min={min(targets):.1f}  max={max(targets):.1f}  avg={sum(targets)/len(targets):.1f}  current={targets[-1]:.1f}F")
print(f"Outdoor:      min={min(outdoors):.1f}  max={max(outdoors):.1f}  avg={sum(outdoors)/len(outdoors):.1f}  current={outdoors[-1]:.1f}F")
print()

# Heating activity
heating = sum(1 for s in office if s.get("hvac_action") == "heating")
idle = sum(1 for s in office if s.get("hvac_action") == "idle")
total = len(office)
print(f"Heating: {heating}/{total} samples ({heating/total*100:.0f}%)")
print(f"Idle:    {idle}/{total} samples ({idle/total*100:.0f}%)")
print()

# Time-of-day breakdown (3-hour blocks)
print("=== Hourly Performance ===")
print(f"{'Time':<8} {'Outdoor':<10} {'Room':<10} {'Floor':<10} {'Target':<10} {'Heating'}")
print("-" * 55)

now = datetime.now()
for hours_ago in range(21, -1, -3):
    start = now - timedelta(hours=hours_ago+3)
    end = now - timedelta(hours=hours_ago)
    block = [s for s in office if start.isoformat() <= s.get("ts","") <= end.isoformat()]
    if not block:
        continue
    
    br = [float(s["room_temp"]) for s in block if s.get("room_temp")]
    bf = [float(s["floor_temp"]) for s in block if s.get("floor_temp")]
    bo = [float(s["outdoor_temp"]) for s in block if s.get("outdoor_temp")]
    bt = [float(s["commanded_setpoint"]) for s in block if s.get("commanded_setpoint")]
    bh = sum(1 for s in block if s.get("hvac_action") == "heating")
    
    label = end.strftime("%I:%M%p").lstrip("0")
    out_avg = f"{sum(bo)/len(bo):.0f}F" if bo else "?"
    rm_avg = f"{sum(br)/len(br):.1f}F" if br else "?"
    fl_avg = f"{sum(bf)/len(bf):.1f}F" if bf else "?"
    tgt_avg = f"{sum(bt)/len(bt):.1f}F" if bt else "?"
    heat_pct = f"{bh}/{len(block)} ({bh/len(block)*100:.0f}%)"
    print(f"{label:<8} {out_avg:<10} {rm_avg:<10} {fl_avg:<10} {tgt_avg:<10} {heat_pct}")

print()

# Response time analysis - how quickly does room respond to heating?
print("=== Responsiveness ===")
# Find transitions from idle to heating
heating_runs = []
current_run = None
for s in office:
    action = s.get("hvac_action")
    if action == "heating":
        if current_run is None:
            current_run = {"start_temp": float(s["room_temp"]), "samples": 1}
        else:
            current_run["samples"] += 1
    else:
        if current_run is not None:
            current_run["end_temp"] = float(s["room_temp"])
            heating_runs.append(current_run)
            current_run = None

if current_run:
    current_run["end_temp"] = float(office[-1]["room_temp"])
    heating_runs.append(current_run)

if heating_runs:
    gains = [r["end_temp"] - r["start_temp"] for r in heating_runs if "end_temp" in r]
    durations = [r["samples"] * 5 for r in heating_runs]  # 5 min per sample
    print(f"  Heating cycles: {len(heating_runs)}")
    print(f"  Avg duration: {sum(durations)/len(durations):.0f} min")
    if gains:
        print(f"  Avg temp gain per cycle: {sum(gains)/len(gains):.1f}F")
        print(f"  Max temp gain: {max(gains):.1f}F")
else:
    print("  No distinct heating cycles detected")

# Room vs floor delta
deltas = [float(s["room_temp"]) - float(s["floor_temp"]) for s in office 
          if s.get("room_temp") and s.get("floor_temp")]
if deltas:
    print()
    print(f"  Room-Floor delta: avg={sum(deltas)/len(deltas):.2f}F (max={max(deltas):.1f}F)")
    print(f"  (Positive = room warmer than floor)")

# Compare with ecobee office sensor
ecobee_office = [s.get("ecobee_sensors", {}).get("office") for s in office if s.get("ecobee_sensors", {}).get("office")]
if ecobee_office:
    print()
    print(f"  Ecobee 'office' remote sensor: avg={sum(ecobee_office)/len(ecobee_office):.1f}F  current={ecobee_office[-1]:.1f}F")
    print(f"  (vs THM room sensor current: {rooms[-1]:.1f}F -- delta: {ecobee_office[-1] - rooms[-1]:.1f}F)")
