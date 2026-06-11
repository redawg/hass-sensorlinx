#!/usr/bin/env python3
"""Check thermal dynamics - current state + recent log data."""
import requests
import json

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
headers = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

# Current state
r = requests.get(f"{BASE}/api/states", headers=headers)
states = r.json()

def find(entity_id):
    for s in states:
        if s["entity_id"] == entity_id:
            return s
    return None

# Outdoor temp
outdoor = find("sensor.home_weather_station_temperature")
print(f"=== Current State (right now) ===")
print(f"Outdoor Temp: {outdoor['state']}°F" if outdoor else "Outdoor: not found")

# Heating curve
target_sensor = find("sensor.sensorlinx_outdoor_reset_heating_curve_target")
if target_sensor:
    print(f"Heating Curve Target: {target_sensor['state']}°F")
print()

# Zones
print("=== Zone Status ===")
print(f"{'Zone':<15} {'Mode':<6} {'Room°F':<8} {'Floor°F':<9} {'Target°F':<10} {'Action':<8}")
print("-" * 60)
zones = ["laundry", "living_room", "main_area", "main_office"]
for z in zones:
    climate = find(f"climate.{z}_{z}")
    floor = find(f"sensor.{z}_floor_temperature")
    if climate:
        attrs = climate["attributes"]
        room = attrs.get("current_temperature", "?")
        tgt = attrs.get("temperature", "?")
        action = attrs.get("hvac_action", "?")
        mode = climate["state"]
        flr = floor["state"] if floor else "?"
        print(f"{z:<15} {mode:<6} {room:<8} {flr:<9} {tgt:<10} {action:<8}")
print()

# Ecobee
ecobee = find("climate.main_floor")
if ecobee:
    attrs = ecobee["attributes"]
    print(f"=== Main HVAC (Ecobee) ===")
    print(f"  Mode: {ecobee['state']} | Action: {attrs.get('hvac_action', '?')}")
    print(f"  Current: {attrs.get('current_temperature')}°F | Target: {attrs.get('temperature')}°F")
    print(f"  Humidity: {attrs.get('current_humidity')}%")
print()

# Thermal log - fetch recent data
print("=== Thermal Log (last 24 hours summary) ===")
from datetime import datetime
month = datetime.now().strftime("%Y-%m")
log_url = f"{BASE}/local/sensorlinx_thermal_log/thermal_{month}.jsonl"
r = requests.get(log_url, headers=headers)
if r.status_code == 200:
    lines = r.text.strip().split("\n")
    print(f"Total samples this month: {len(lines)}")
    
    # Parse last 24 hours (288 samples at 5-min intervals)
    recent = []
    for line in lines[-288:]:
        try:
            sample = json.loads(line)
            recent.append(sample)
        except:
            pass
    
    if recent:
        print(f"Samples in last 24h window: {len(recent)}")
        print(f"Time range: {recent[0].get('ts', '?')} to {recent[-1].get('ts', '?')}")
        print()
        
        # Analyze per zone
        print(f"{'Zone':<15} {'Room Min':<10} {'Room Max':<10} {'Room Avg':<10} {'Floor Min':<10} {'Floor Max':<10} {'Floor Avg':<10}")
        print("-" * 75)
        
        for z in zones:
            rooms = []
            floors = []
            for s in recent:
                zone_data = s.get("zones", {}).get(z, {})
                rm = zone_data.get("room_temp")
                fl = zone_data.get("floor_temp")
                if rm is not None and rm != "unavailable":
                    try:
                        rooms.append(float(rm))
                    except:
                        pass
                if fl is not None and fl != "unavailable":
                    try:
                        floors.append(float(fl))
                    except:
                        pass
            
            if rooms:
                print(f"{z:<15} {min(rooms):<10.1f} {max(rooms):<10.1f} {sum(rooms)/len(rooms):<10.1f} ", end="")
            else:
                print(f"{z:<15} {'N/A':<10} {'N/A':<10} {'N/A':<10} ", end="")
            
            if floors:
                print(f"{min(floors):<10.1f} {max(floors):<10.1f} {sum(floors)/len(floors):<10.1f}")
            else:
                print(f"{'N/A':<10} {'N/A':<10} {'N/A':<10}")
        
        # Outdoor temp range
        outdoor_temps = []
        for s in recent:
            ot = s.get("outdoor_temp")
            if ot is not None:
                try:
                    outdoor_temps.append(float(ot))
                except:
                    pass
        if outdoor_temps:
            print()
            print(f"Outdoor temp range: {min(outdoor_temps):.1f}F to {max(outdoor_temps):.1f}F (avg {sum(outdoor_temps)/len(outdoor_temps):.1f}F)")
        
        # Heating activity
        print()
        print("=== Heating Activity (last 24h) ===")
        for z in zones:
            heating_count = 0
            total_count = 0
            for s in recent:
                zone_data = s.get("zones", {}).get(z, {})
                action = zone_data.get("hvac_action")
                if action:
                    total_count += 1
                    if action == "heating":
                        heating_count += 1
            if total_count > 0:
                pct = heating_count / total_count * 100
                print(f"  {z:<15}: heating {pct:.0f}% of the time ({heating_count}/{total_count} samples)")
        
        # Ecobee activity
        ecobee_heating = 0
        ecobee_cooling = 0
        ecobee_total = 0
        for s in recent:
            ec = s.get("ecobee", {})
            action = ec.get("hvac_action")
            if action:
                ecobee_total += 1
                if action == "heating":
                    ecobee_heating += 1
                elif action == "cooling":
                    ecobee_cooling += 1
        if ecobee_total > 0:
            print(f"  {'ecobee(main)':<15}: heating {ecobee_heating/ecobee_total*100:.0f}% | cooling {ecobee_cooling/ecobee_total*100:.0f}%")
else:
    print(f"Could not fetch thermal log: {r.status_code}")
