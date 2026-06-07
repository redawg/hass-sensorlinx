#!/usr/bin/env python3
"""Test: turn off Laundry via HA, then check cloud for offMode=1."""
import json
import os
import time
import urllib.request
import asyncio
import logging
from pysensorlinx import Sensorlinx

logging.disable(logging.CRITICAL)

BASE = "http://172.16.255.250:8123"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"


def ha_post(path, data):
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=body,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def ha_get(path):
    req = urllib.request.Request(
        f"{BASE}{path}",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


async def check_cloud():
    api = Sensorlinx()
    await api.login(os.environ["SENSORLINX_EMAIL"], os.environ["SENSORLINX_PASSWORD"])
    buildings = await api.get_buildings()
    bid = buildings[0]["_id"]
    devices = await api.get_devices(bid)
    for d in devices:
        if d.get("deviceType") == "THM" and "Laundry" in d.get("name", ""):
            print(f"  cngOvr={d.get('cngOvr')} offMode={d.get('offMode')} target.isOff={d.get('target',{}).get('isOff')}")
    await api.close()


# Step 1: Check current state
print("=== Before: Laundry state in HA ===")
code, data = ha_get("/api/states/climate.laundry_laundry")
print(f"  state={data.get('state')} attrs={json.dumps({k:data['attributes'][k] for k in ['hvac_modes','current_temperature','temperature'] if k in data.get('attributes',{})})}")

# Step 2: Turn off via HA service
print("\n=== Turning off Laundry via climate.turn_off ===")
code, resp = ha_post("/api/services/climate/turn_off", {
    "entity_id": "climate.laundry_laundry"
})
print(f"  HTTP {code}")

# Step 3: Wait for coordinator refresh
print("\n=== Waiting 8 seconds ===")
time.sleep(8)

# Step 4: Check HA state
print("\n=== After: Laundry state in HA ===")
code, data = ha_get("/api/states/climate.laundry_laundry")
print(f"  state={data.get('state')}")

# Step 5: Check cloud
print("\n=== Cloud check (SensorLinx API) ===")
asyncio.run(check_cloud())
