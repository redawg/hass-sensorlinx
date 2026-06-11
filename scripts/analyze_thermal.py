#!/usr/bin/env python3
"""Analyze current thermal log data."""
import json
import urllib.request
from collections import defaultdict

URL = "http://172.16.255.250:8123/local/sensorlinx_thermal_log/thermal_2026-06.jsonl"

req = urllib.request.Request(URL)
data = urllib.request.urlopen(req, timeout=15).read().decode()
lines = data.strip().split("\n")
samples = [json.loads(l) for l in lines]

print(f"Total samples: {len(samples)}")
print(f"Time span: {samples[0]['ts']} to {samples[-1]['ts']}")
hours = len(samples) / 4 * 5 / 60  # 4 zones per interval, 5 min intervals
print(f"Approx duration: {hours:.1f} hours of data")

# Per-zone analysis
zones = defaultdict(list)
for s in samples:
    zones[s["zone"]].append(s)

print("\n=== Per-Zone Summary ===")
for zone, zs in zones.items():
    room_temps = [s["room_temp"] for s in zs if s["room_temp"] is not None]
    floor_temps = [s["floor_temp"] for s in zs if s["floor_temp"] is not None]
    heating_count = sum(1 for s in zs if s["hvac_action"] == "heating")
    idle_count = sum(1 for s in zs if s["hvac_action"] == "idle")
    print(f"\n  {zone}:")
    print(f"    Samples: {len(zs)}")
    if room_temps:
        print(f"    Room temp:  {min(room_temps):.1f} - {max(room_temps):.1f}°F (avg {sum(room_temps)/len(room_temps):.1f})")
    if floor_temps:
        print(f"    Floor temp: {min(floor_temps):.1f} - {max(floor_temps):.1f}°F (avg {sum(floor_temps)/len(floor_temps):.1f})")
    print(f"    Heating: {heating_count}/{len(zs)} samples ({100*heating_count/len(zs):.0f}%)")
    print(f"    Idle:    {idle_count}/{len(zs)} samples ({100*idle_count/len(zs):.0f}%)")

# Outdoor temp range
outdoor = [s["outdoor_temp"] for s in samples if s["outdoor_temp"] is not None]
print(f"\n=== Outdoor Temperature ===")
print(f"  Range: {min(outdoor):.1f}°F - {max(outdoor):.1f}°F")
print(f"  Current: {outdoor[-1]:.1f}°F")

# Heating curve targets
targets = [s["curve_target"] for s in samples if s["curve_target"] is not None]
print(f"\n=== Heating Curve Targets ===")
print(f"  Range: {min(targets):.1f}°F - {max(targets):.1f}°F")
print(f"  Current: {targets[-1]:.1f}°F")

# Last sample per zone
print(f"\n=== Latest State (per zone) ===")
for zone, zs in zones.items():
    s = zs[-1]
    print(f"  {zone}: room={s['room_temp']}°F floor={s['floor_temp']}°F "
          f"target={s['curve_target']}°F action={s['hvac_action']}")
