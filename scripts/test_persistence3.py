#!/usr/bin/env python3
"""
Definitive persistence test.

Strategy:
1. Call set_hydronic_sensors service (sets options + triggers reload)
2. Wait for reload to complete
3. Check if the delta-T sensor attributes show the configured values
   (these are populated from entry.options at startup time)
4. Call HA restart (full reboot simulation)
5. After restart, check the delta-T sensor again
   If values persist across restart, options ARE being saved to disk.
"""
import requests
import time
import json

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


def get_delta_t_attrs():
    """Get the delta-T sensor attributes (populated from options at startup)."""
    r = requests.get(f"{BASE}/api/states/sensor.sensorlinx_outdoor_reset_hydronic_delta_t",
                     headers=HEADERS, timeout=10)
    if r.status_code == 200:
        return r.json().get("attributes", {})
    return None


def call_service(domain, service, data):
    r = requests.post(
        f"{BASE}/api/services/{domain}/{service}",
        headers=HEADERS, json=data, timeout=10,
    )
    return r.status_code


def wait_for_ha(max_wait=120):
    """Wait for HA to come back online."""
    for i in range(max_wait // 5):
        try:
            r = requests.get(f"{BASE}/api/", headers=HEADERS, timeout=5)
            if r.status_code == 200:
                time.sleep(10)  # Extra time for integrations to load
                return True
        except Exception:
            pass
        time.sleep(5)
    return False


# =====================================================================
print("=" * 60)
print("PHASE 1: Check current state of delta-T sensor")
print("=" * 60)
attrs = get_delta_t_attrs()
if attrs:
    print(f"  supply_sensor: {attrs.get('supply_sensor', 'NOT SET')}")
    print(f"  return_sensor: {attrs.get('return_sensor', 'NOT SET')}")
    print(f"  flow_rate_sensor: {attrs.get('flow_rate_sensor', 'NOT SET')}")
else:
    print("  Delta-T sensor not found!")
    exit(1)

# =====================================================================
print(f"\n{'=' * 60}")
print("PHASE 2: Call set_hydronic_sensors service")
print("=" * 60)
code = call_service("sensorlinx", "set_hydronic_sensors", {
    "supply_temp_sensor": "sensor.main_water_heater_outlet_temperature",
    "return_temp_sensor": "sensor.main_water_heater_inlet_temperature",
    "flow_rate_sensor": "sensor.main_water_heater_flow_rate",
})
print(f"  Service call status: {code}")
print("  Waiting 15s for reload...")
time.sleep(15)

attrs = get_delta_t_attrs()
if attrs:
    print(f"\n  After reload:")
    print(f"  supply_sensor: {attrs.get('supply_sensor', 'NOT SET')}")
    print(f"  return_sensor: {attrs.get('return_sensor', 'NOT SET')}")
    print(f"  flow_rate_sensor: {attrs.get('flow_rate_sensor', 'NOT SET')}")
    if attrs.get("supply_sensor") and "outlet" in attrs.get("supply_sensor", ""):
        print("\n  In-memory state: GOOD (values present after reload)")
    else:
        print("\n  In-memory state: MISSING after reload!")
        exit(1)

# =====================================================================
print(f"\n{'=' * 60}")
print("PHASE 3: Full HA restart (testing disk persistence)")
print("=" * 60)
print("  Sending restart command...")
code = call_service("homeassistant", "restart", {})
print(f"  Restart status: {code}")
print("  Waiting for HA to come back up...")
time.sleep(30)  # Give it time to shut down
if not wait_for_ha():
    print("  TIMEOUT: HA didn't come back!")
    exit(1)

# Wait extra for integrations
time.sleep(15)

# =====================================================================
print(f"\n{'=' * 60}")
print("PHASE 4: Check persistence after full restart")
print("=" * 60)
attrs = get_delta_t_attrs()
if attrs:
    supply = attrs.get("supply_sensor", "NOT SET")
    ret = attrs.get("return_sensor", "NOT SET")
    flow = attrs.get("flow_rate_sensor", "NOT SET")
    print(f"  supply_sensor: {supply}")
    print(f"  return_sensor: {ret}")
    print(f"  flow_rate_sensor: {flow}")

    if supply and "outlet" in supply:
        print("\n  *** OPTIONS ARE PERSISTING ACROSS RESTARTS ***")
        print("  The REST API just doesn't expose them.")
    else:
        print("\n  *** OPTIONS NOT SURVIVING RESTART ***")
        print("  async_update_entry is NOT saving to disk!")
else:
    print("  Delta-T sensor not available yet - may need more startup time")
    time.sleep(30)
    attrs = get_delta_t_attrs()
    if attrs:
        supply = attrs.get("supply_sensor", "NOT SET")
        print(f"  supply_sensor (retry): {supply}")
        if supply and "outlet" in supply:
            print("\n  *** OPTIONS ARE PERSISTING ACROSS RESTARTS ***")
        else:
            print("\n  *** OPTIONS NOT SURVIVING RESTART ***")
