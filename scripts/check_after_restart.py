#!/usr/bin/env python3
"""Check if options survived the full restart."""
import requests
import time

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

# Wait for HA
print("Waiting for HA to respond...")
for i in range(24):
    try:
        r = requests.get(f"{BASE}/api/", headers=HEADERS, timeout=5)
        if r.status_code == 200:
            print(f"  HA is up! (attempt {i+1})")
            break
    except Exception:
        pass
    time.sleep(5)
else:
    print("TIMEOUT waiting for HA")
    exit(1)

time.sleep(15)  # Let integrations fully load

print("\n=== Delta-T sensor attributes after full restart ===\n")
r = requests.get(f"{BASE}/api/states/sensor.sensorlinx_outdoor_reset_hydronic_delta_t",
                 headers=HEADERS, timeout=10)
if r.status_code == 200:
    attrs = r.json().get("attributes", {})
    supply = attrs.get("supply_sensor", "NOT SET")
    ret = attrs.get("return_sensor", "NOT SET")
    flow = attrs.get("flow_rate_sensor", "NOT SET")
    print(f"  supply_sensor: {supply}")
    print(f"  return_sensor: {ret}")
    print(f"  flow_rate_sensor: {flow}")
    print(f"  supply_temp: {attrs.get('supply_temp')}")
    print(f"  return_temp: {attrs.get('return_temp')}")
    print(f"  actual_flow_gpm: {attrs.get('actual_flow_gpm')}")

    if supply and "outlet" in str(supply):
        print("\n  *** HYDRONIC SENSORS PERSISTED ACROSS RESTART ***")
        print("  async_update_entry IS working - the REST API just hides options.")
    elif supply == "NOT SET" or supply == "not configured":
        print("\n  *** HYDRONIC SENSORS DID NOT PERSIST ***")
        print("  async_update_entry is NOT saving to disk.")
    else:
        print(f"\n  Unexpected value: {supply}")
else:
    print(f"  Delta-T sensor returned status {r.status_code}")
    # Try again after more time
    time.sleep(30)
    r = requests.get(f"{BASE}/api/states/sensor.sensorlinx_outdoor_reset_hydronic_delta_t",
                     headers=HEADERS, timeout=10)
    if r.status_code == 200:
        attrs = r.json().get("attributes", {})
        print(f"  (retry) supply_sensor: {attrs.get('supply_sensor', 'NOT SET')}")
