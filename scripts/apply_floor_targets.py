#!/usr/bin/env python3
"""Apply floor-first heating targets and reset Ecobee to supplement-only."""
import time

import requests

BASE = "http://172.16.255.250:8123"
TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ."
    "Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
)
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


def call(service, entity_id=None, **data):
    payload = dict(data)
    if entity_id:
        payload["entity_id"] = entity_id
    r = requests.post(
        f"{BASE}/api/services/{service}",
        headers=HEADERS,
        json=payload,
        timeout=60,
    )
    ok = r.status_code == 200
    print(f"  {service} {payload} ... {'OK' if ok else f'FAIL {r.status_code}'}")
    return ok


def set_number(entity_id, value):
    return call("number/set_value", entity_id=entity_id, value=value)


def set_switch(entity_id, on=True):
    return call(f"switch/turn_{'on' if on else 'off'}", entity_id=entity_id)


print("=== Floor targets: 72F wood, 74F tile ===")
wood_zones = ["main_area", "living_room", "main_office"]
for zone in wood_zones:
    set_switch(f"switch.sensorlinx_outdoor_reset_floor_control_mode_{zone}", on=True)
    time.sleep(0.5)
    set_number(f"number.sensorlinx_outdoor_reset_floor_target_{zone}", 72.0)

set_switch("switch.sensorlinx_outdoor_reset_floor_control_mode_laundry", on=True)
time.sleep(0.5)
set_number("number.sensorlinx_outdoor_reset_floor_target_laundry", 74.0)

print("\n=== Reset room-mode offsets (floor control owns setpoints) ===")
for zone in wood_zones:
    set_number(f"number.sensorlinx_outdoor_reset_zone_offset_{zone}", 0.0)

print("\n=== Re-run HVAC orchestrator (radiant-first after deploy) ===")
call("sensorlinx/run_hvac_orchestrator")

time.sleep(8)
print("\n=== Verification ===")
states = {s["entity_id"]: s for s in requests.get(f"{BASE}/api/states", headers=HEADERS, timeout=60).json()}
checks = [
    "climate.main_area_main_area",
    "climate.living_room_living_room",
    "climate.main_office_main_office",
    "climate.laundry_laundry",
    "climate.main_floor",
    "sensor.sensorlinx_hvac_orchestrator_hvac_orchestrator_status",
]
for eid in checks:
    s = states.get(eid, {})
    a = s.get("attributes") or {}
    print(
        f"  {a.get('friendly_name', eid)}: mode={s.get('state')} "
        f"set={a.get('temperature')} current={a.get('current_temperature')} "
        f"action={a.get('hvac_action')} fan={a.get('fan_mode')}"
    )
    if "orchestrator_status" in eid:
        print(
            f"    decision={a.get('last_decision')} reason={a.get('last_reason')} "
            f"main={a.get('main_floor_temp')}"
        )

for zone in wood_zones + ["laundry"]:
    ft = states.get(f"number.sensorlinx_outdoor_reset_floor_target_{zone}")
    fm = states.get(f"switch.sensorlinx_outdoor_reset_floor_control_mode_{zone}")
    if ft or fm:
        print(
            f"  {zone}: floor_mode={fm.get('state') if fm else 'n/a'} "
            f"floor_target={ft.get('state') if ft else 'n/a'}"
        )
