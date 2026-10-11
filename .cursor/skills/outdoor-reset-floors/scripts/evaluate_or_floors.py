#!/usr/bin/env python3
"""Snapshot outdoor reset + closed-loop hydronics + floor zones.

Usage (from repo root):
  python .cursor/skills/outdoor-reset-floors/scripts/evaluate_or_floors.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

BASE = os.environ.get("HA_URL", "http://172.16.255.250:8123")
TOKEN = os.environ.get(
    "HA_TOKEN",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs",
)

# zone_key, climate, floor sensor, room air, floor target number, valve_count number
ZONES = [
    (
        "living_room",
        "climate.living_room_living_room",
        "sensor.living_room_floor_temperature",
        "sensor.living_room_temperature",
        "number.sensorlinx_outdoor_reset_floor_target_living_room",
        "number.sensorlinx_outdoor_reset_valve_count_living_room",
    ),
    (
        "main_area",
        "climate.main_area_main_area",
        "sensor.main_area_floor_temperature",
        "sensor.family_room_temperature",
        "number.sensorlinx_outdoor_reset_floor_target_main_area",
        "number.sensorlinx_outdoor_reset_valve_count_main_area",
    ),
    (
        "main_office",
        "climate.main_office_main_office",
        "sensor.main_office_floor_temperature",
        "sensor.office_temperature",
        "number.sensorlinx_outdoor_reset_floor_target_main_office",
        "number.sensorlinx_outdoor_reset_valve_count_main_office",
    ),
    (
        "laundry",
        "climate.laundry_laundry",
        "sensor.laundry_floor_temperature",
        None,
        "number.sensorlinx_outdoor_reset_floor_target_laundry",
        "number.sensorlinx_outdoor_reset_valve_count_laundry",
    ),
]

TARGET_DELTA_T = 10.0  # matches BOOST_TARGET_DELTA_T in outdoor_reset.py
GPM_PER_LOOP_DEFAULT = 0.5


def get(eid: str):
    req = urllib.request.Request(
        f"{BASE}/api/states/{eid}",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())
    except Exception as exc:  # noqa: BLE001
        return {"entity_id": eid, "state": f"error:{exc}", "attributes": {}}


def fnum(state) -> float | None:
    try:
        return float(state)
    except (TypeError, ValueError):
        return None


def expected_supply(outdoor: float, shutdown: float, design: float, smin: float, smax: float) -> float:
    if outdoor >= shutdown:
        return smin
    if outdoor <= design:
        return smax
    ratio = (shutdown - outdoor) / (shutdown - design)
    return round(smin + (smax - smin) * ratio, 1)


def classify(outdoor: float, shutdown: float, design: float) -> str:
    if outdoor >= shutdown:
        return "above_WWSD"
    if outdoor <= design:
        return "at_design_cold"
    mid = (shutdown + design) / 2
    return "mild" if outdoor >= mid else "cold"


def is_calling(climate: dict) -> bool:
    a = climate.get("attributes") or {}
    if a.get("hvac_action") == "heating":
        return True
    # heat mode with SP meaningfully above sensed temp ≈ valve likely open soon
    try:
        sp = float(a.get("temperature"))
        cur = float(a.get("current_temperature"))
        return climate.get("state") == "heat" and sp > cur + 0.3
    except (TypeError, ValueError):
        return False


def main() -> int:
    weather = get("weather.quail_creek_ames_lake_279th_ct_ne")
    outdoor = fnum(weather.get("attributes", {}).get("temperature")) or fnum(weather.get("state"))
    shutdown = fnum(get("number.sensorlinx_outdoor_reset_heating_curve_shutdown_temp")["state"]) or 65.0
    design = fnum(get("number.sensorlinx_outdoor_reset_heating_curve_design_outdoor")["state"]) or 25.0
    smin = fnum(get("number.sensorlinx_outdoor_reset_supply_water_min_temp_mild")["state"]) or 80.0
    smax = fnum(get("number.sensorlinx_outdoor_reset_supply_water_max_temp_cold")["state"]) or 140.0
    supply_target = fnum(get("sensor.sensorlinx_outdoor_reset_supply_water_target")["state"])
    supply_reset = get("switch.sensorlinx_outdoor_reset_supply_water_reset_enabled")["state"]
    drive_gain = get("number.outside_front_sensorlinx_outdoor_reset_floor_control_drive_gain")["state"]
    gpm_per = fnum(get("number.sensorlinx_outdoor_reset_hydronic_flow_rate_per_valve_gpm")["state"]) or GPM_PER_LOOP_DEFAULT
    wh = get("water_heater.main_water_heater")
    outlet = fnum(get("sensor.main_water_heater_outlet_temperature")["state"])
    inlet = fnum(get("sensor.main_water_heater_inlet_temperature")["state"])
    flow = fnum(get("sensor.main_water_heater_flow_rate")["state"])
    dT_sensor = fnum(get("sensor.sensorlinx_outdoor_reset_hydronic_delta_t")["state"])
    heat_out = get("sensor.sensorlinx_outdoor_reset_hydronic_heat_output")["state"]

    if outdoor is None:
        print("Outdoor: unavailable")
        return 1

    calc = expected_supply(outdoor, shutdown, design, smin, smax)
    ratio = (smax - smin) / (shutdown - design) if shutdown != design else 0.0
    dT = None
    if outlet is not None and inlet is not None:
        dT = round(outlet - inlet, 1)

    print(f"Outdoor: {outdoor}F ({classify(outdoor, shutdown, design)}) weather={weather.get('state')}")
    print(f"Curve: design={design} WWSD={shutdown} supply={smin}->{smax} ratio={ratio:.2f}")
    print(
        f"Supply: calc={calc} sensor_target={supply_target} "
        f"commanded={wh.get('attributes', {}).get('temperature')} "
        f"outlet={outlet} inlet={inlet} reset={supply_reset}"
    )
    print(
        f"Closed loop: dT={dT}F (sensor={dT_sensor}, target~{TARGET_DELTA_T}) "
        f"flow={flow} GPM  heat_output={heat_out} BTU/h  gpm_per_loop={gpm_per}"
    )
    print(f"Drive gain (must be 0): {drive_gain}")

    active = []
    total_loops = 0
    active_loops = 0
    print("Zones (shared closed loop — valves isolate loops):")
    for name, climate_id, floor_id, room_id, target_id, valves_id in ZONES:
        cl = get(climate_id)
        a = cl.get("attributes", {})
        floor = get(floor_id)["state"]
        room = get(room_id)["state"] if room_id else "-"
        target = get(target_id)["state"]
        loops = fnum(get(valves_id)["state"]) or 0
        total_loops += int(loops)
        calling = is_calling(cl)
        if calling:
            active.append(name)
            active_loops += int(loops)
        flag = "CALL" if calling else "idle"
        print(
            f"  {name}: {flag} loops={int(loops)} sp={a.get('temperature')} "
            f"floor={floor} room={room} target={target} action={a.get('hvac_action')}"
        )
        try:
            if a.get("temperature") is not None and float(a["temperature"]) > float(target) + 2.5:
                print(f"    WARN: setpoint {a['temperature']} > floor target {target}+2 — pull down")
            if floor not in ("unavailable", "unknown", "-") and float(floor) >= 80:
                print("    WARN: slab at/above safety cap — turn zone off")
        except (TypeError, ValueError):
            pass

    est_flow = round(active_loops * gpm_per, 2)
    print(
        f"Active: {len(active)}/{len(ZONES)} zones ({', '.join(active) or 'none'}) "
        f"loops={active_loops}/{total_loops} est_flow≈{est_flow} GPM"
    )
    if dT is not None:
        if not active:
            print("  Note: dT with no zone CALL may be residual / DHW share / sensor lag")
        elif dT < TARGET_DELTA_T - 3:
            print(
                "  Hint: low dT — water too hot for load, short loops, or many zones open; "
                "consider lower supply (still ≥80)"
            )
        elif dT > TARGET_DELTA_T + 5:
            print(
                "  Hint: high dT — load large vs flow (few loops open) or supply too cool; "
                "raise supply toward curve or wait for more valves"
            )
        else:
            print("  Hint: dT near target for current open-loop count")
    return 0


if __name__ == "__main__":
    sys.exit(main())
