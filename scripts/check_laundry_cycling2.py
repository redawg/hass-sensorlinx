#!/usr/bin/env python3
"""Check longer history for laundry cycling and outdoor temp around shutdown."""
import requests
from datetime import datetime, timedelta

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

# Look back 12 hours
now = datetime.utcnow()
start = (now - timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%SZ")

# 1. Climate state history (full, with attributes)
print("=" * 60)
print("  LAUNDRY 12-HOUR HISTORY")
print("=" * 60)

print("\n--- Climate State Changes (heat/off transitions) ---")
url = f"{BASE}/api/history/period/{start}?filter_entity_id=climate.laundry_laundry&minimal_response&no_attributes"
r = requests.get(url, headers=HEADERS, timeout=20)
if r.status_code == 200 and r.json() and r.json()[0]:
    entries = r.json()[0]
    transitions = []
    prev_state = None
    for e in entries:
        st = e.get("state")
        ts = e.get("last_changed", "")
        if st != prev_state:
            transitions.append((ts, st))
            prev_state = st
    print(f"  Total state updates: {len(entries)}")
    print(f"  Actual state transitions: {len(transitions)}")
    for ts, st in transitions:
        print(f"    {ts}: {st}")

    # Count on/off cycles
    off_count = sum(1 for _, st in transitions if st == "off")
    heat_count = sum(1 for _, st in transitions if st == "heat")
    print(f"\n  Heat->Off transitions: {off_count}")
    print(f"  Off->Heat transitions: {heat_count - (1 if transitions and transitions[0][1] == 'heat' else 0)}")
else:
    print(f"  No history or error: {r.status_code}")

# 2. Outdoor temp when it crossed the 65F shutdown threshold
print("\n--- Outdoor Temp: Crossings at 65F Shutdown ---")
url2 = f"{BASE}/api/history/period/{start}?filter_entity_id=sensor.quail_creek_ames_lake_279th_ct_ne_temperature&minimal_response&no_attributes"
r2 = requests.get(url2, headers=HEADERS, timeout=20)
if r2.status_code == 200 and r2.json() and r2.json()[0]:
    entries = r2.json()[0]
    print(f"  Total data points: {len(entries)}")
    
    crossings = []
    prev_val = None
    shutdown = 65.0
    for e in entries:
        try:
            val = float(e.get("state", ""))
        except (ValueError, TypeError):
            continue
        ts = e.get("last_changed", "")
        if prev_val is not None:
            if (prev_val < shutdown and val >= shutdown) or (prev_val >= shutdown and val < shutdown):
                direction = "ABOVE->BELOW" if val < shutdown else "BELOW->ABOVE"
                crossings.append((ts, prev_val, val, direction))
        prev_val = val
    
    print(f"  Crossings of 65F threshold: {len(crossings)}")
    for ts, prev, cur, direction in crossings:
        print(f"    {ts}: {prev:.1f}F -> {cur:.1f}F ({direction})")
    
    # Show the temp range in 1-hour blocks
    print(f"\n  Hourly outdoor temp summary:")
    from collections import defaultdict
    hourly = defaultdict(list)
    for e in entries:
        try:
            val = float(e.get("state", ""))
            ts = e.get("last_changed", "")
            hour = ts[:13]  # YYYY-MM-DDTHH
            hourly[hour].append(val)
        except (ValueError, TypeError):
            continue
    for hour in sorted(hourly.keys()):
        vals = hourly[hour]
        print(f"    {hour}: min={min(vals):.1f}, max={max(vals):.1f}, avg={sum(vals)/len(vals):.1f}")
else:
    print(f"  No data or error: {r2.status_code}")

# 3. Check hvac_action history (heating vs idle)
print("\n--- HVAC Action History (heating/idle) ---")
url3 = f"{BASE}/api/history/period/{start}?filter_entity_id=climate.laundry_laundry"
r3 = requests.get(url3, headers=HEADERS, timeout=20)
if r3.status_code == 200 and r3.json() and r3.json()[0]:
    entries = r3.json()[0]
    action_transitions = []
    prev_action = None
    for e in entries:
        attrs = e.get("attributes", {})
        action = attrs.get("hvac_action", "unknown")
        ts = e.get("last_changed", "")
        if action != prev_action:
            action_transitions.append((ts, e.get("state"), action, attrs.get("temperature")))
            prev_action = action
    
    print(f"  HVAC action transitions: {len(action_transitions)}")
    for ts, st, action, sp in action_transitions[-30:]:
        print(f"    {ts}: state={st}, action={action}, setpoint={sp}")
else:
    print(f"  Full history request error or empty: {r3.status_code}")
