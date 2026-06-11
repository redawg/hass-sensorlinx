#!/usr/bin/env python3
"""Analyze thermal dynamics from log data."""
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

if r.status_code != 200:
    print(f"Error fetching log: {r.status_code}")
    exit(1)

lines = r.text.strip().split("\n")
samples = []
for line in lines:
    try:
        samples.append(json.loads(line))
    except:
        pass

print(f"Total samples: {len(samples)}")
print(f"Date range: {samples[0]['ts'][:16]} to {samples[-1]['ts'][:16]}")
print()

# Filter last 24 hours
cutoff = datetime.now() - timedelta(hours=24)
recent = [s for s in samples if s.get("ts", "") >= cutoff.isoformat()]
print(f"Samples in last 24h: {len(recent)}")
print()

# Since zones are off for some period, let's also check since the fix (~midnight)
cutoff_fix = datetime.now() - timedelta(hours=22)
since_fix = [s for s in samples if s.get("ts", "") >= cutoff_fix.isoformat()]

# Group by zone
zones = ["laundry", "living_room", "main_area", "main_office"]
zone_data = defaultdict(list)
for s in since_fix:
    z = s.get("zone")
    if z:
        zone_data[z].append(s)

print("=== Zone Thermal Summary (since outdoor reset fix ~22h ago) ===")
print(f"{'Zone':<15} {'Room':<18} {'Floor':<18} {'Target':<10} {'Heat%':<7} {'Samples'}")
print("-" * 85)

for z in zones:
    data = zone_data[z]
    if not data:
        print(f"{z:<15} No data")
        continue
    
    rooms = [float(s["room_temp"]) for s in data if s.get("room_temp") is not None]
    floors = [float(s["floor_temp"]) for s in data if s.get("floor_temp") is not None]
    heating = sum(1 for s in data if s.get("hvac_action") == "heating")
    total = len(data)
    
    tgt = data[-1].get("commanded_setpoint", "?")
    
    if rooms and floors:
        rm_str = f"{min(rooms):.1f}-{max(rooms):.1f} (avg {sum(rooms)/len(rooms):.1f})"
        fl_str = f"{min(floors):.1f}-{max(floors):.1f} (avg {sum(floors)/len(floors):.1f})"
    else:
        rm_str = "N/A"
        fl_str = "N/A"
    
    heat_pct = f"{heating/total*100:.0f}%" if total > 0 else "?"
    print(f"{z:<15} {rm_str:<18} {fl_str:<18} {tgt:<10} {heat_pct:<7} {total}")

print()

# Outdoor temp trend
outdoor_temps = [(s["ts"], float(s["outdoor_temp"])) for s in since_fix if s.get("outdoor_temp") and s.get("zone") == "laundry"]
if outdoor_temps:
    temps = [t[1] for t in outdoor_temps]
    print(f"=== Outdoor Temp Range (last 22h) ===")
    print(f"  Low: {min(temps):.1f}F | High: {max(temps):.1f}F | Current: {temps[-1]:.1f}F")
    print(f"  Average: {sum(temps)/len(temps):.1f}F")
    
    # Show hourly trend (last 6 hours)
    print()
    print("  Hourly trend (recent):")
    now = datetime.now()
    for hours_ago in range(6, -1, -1):
        start = now - timedelta(hours=hours_ago+1)
        end = now - timedelta(hours=hours_ago)
        hour_temps = [t[1] for t in outdoor_temps if start.isoformat() <= t[0] <= end.isoformat()]
        if hour_temps:
            label = end.strftime("%I%p").lstrip("0")
            print(f"    {label}: {sum(hour_temps)/len(hour_temps):.1f}F")

print()

# Ecobee summary
ecobee_data = [s for s in since_fix if s.get("zone") == "laundry"]  # ecobee data is same on all zone rows
if ecobee_data:
    print("=== Ecobee (Main HVAC) Summary ===")
    ec_heating = sum(1 for s in ecobee_data if s.get("ecobee_action") == "heating")
    ec_cooling = sum(1 for s in ecobee_data if s.get("ecobee_action") == "cooling")
    ec_idle = sum(1 for s in ecobee_data if s.get("ecobee_action") == "idle")
    total_ec = len(ecobee_data)
    print(f"  Heating: {ec_heating/total_ec*100:.0f}% | Cooling: {ec_cooling/total_ec*100:.0f}% | Idle: {ec_idle/total_ec*100:.0f}%")
    
    # Sensor temps
    last = ecobee_data[-1]
    sensors = last.get("ecobee_sensors", {})
    if sensors:
        print(f"  Remote sensors now: {', '.join(f'{k}={v}F' for k,v in sensors.items())}")
    occ = last.get("occupancy", {})
    if occ:
        occupied = [k for k,v in occ.items() if v]
        print(f"  Occupied: {', '.join(occupied) if occupied else 'none'}")

print()
print("=== Key Observations ===")
# Check if zones are reaching target
for z in zones:
    data = zone_data[z]
    if not data:
        continue
    rooms = [float(s["room_temp"]) for s in data if s.get("room_temp") is not None]
    tgt = float(data[-1].get("commanded_setpoint", 72))
    if rooms:
        current = rooms[-1]
        avg = sum(rooms) / len(rooms)
        if current >= tgt:
            print(f"  {z}: AT TARGET (room {current}F >= target {tgt}F)")
        elif tgt - current <= 1:
            print(f"  {z}: NEAR TARGET (room {current}F, target {tgt}F, gap {tgt-current:.1f}F)")
        else:
            print(f"  {z}: BELOW TARGET (room {current}F, target {tgt}F, gap {tgt-current:.1f}F)")
