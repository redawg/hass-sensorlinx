#!/usr/bin/env python3
"""Daily HVAC & Radiant Floor Performance Report.

Analyzes thermal data like a hydronic floor heating specialist:
- Heating curve efficiency and tuning
- Zone balance and thermal response
- Cycle analysis (short-cycling detection)
- Thermal lag characterization
- Floor temp safety margins
- Main HVAC interaction
- Operational efficiency scoring
- Daily recommendations
"""
import requests
import json
from datetime import datetime, timedelta
from collections import defaultdict
import math

import os
from pathlib import Path as _Path
BASE = os.environ.get(
    "HA_HOST",
    "http://127.0.0.1:8123" if _Path("/config").exists() else "http://172.16.255.250:8123",
)
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
headers = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

ZONES = ["laundry", "living_room", "main_area", "main_office"]
ZONE_LABELS = {"laundry": "Laundry", "living_room": "Living Room", "main_area": "Main Area", "main_office": "Main Office"}

SUPPLY_ENTITIES = {
    "min_temp": "number.sensorlinx_outdoor_reset_supply_water_min_temp_mild",
    "max_temp": "number.sensorlinx_outdoor_reset_supply_water_max_temp_cold",
    "reset_enabled": "switch.sensorlinx_outdoor_reset_supply_water_reset_enabled",
    "target_sensor": "sensor.sensorlinx_outdoor_reset_supply_water_target",
    "water_heater": "water_heater.main_water_heater",
    "delta_t": "sensor.sensorlinx_outdoor_reset_hydronic_delta_t",
    "btu_output": "sensor.sensorlinx_outdoor_reset_hydronic_heat_output",
}

ADJUST_STEP = 5.0
IDEAL_DELTA_T = (10.0, 20.0)
TANKLESS_MAX_TEMP = 140.0
DEFAULT_ELECTRICITY_COST = 0.105  # $/kWh (10.5 cents)
COST_ENTITY = "number.sensorlinx_outdoor_reset_electricity_cost_per_kwh"
POWER_DRAW_ENTITY = "sensor.main_water_heater_power_draw"
MAX_POWER_GAP_MINUTES = 15


def fetch_data():
    month = datetime.now().strftime("%Y-%m")
    log_url = f"{BASE}/local/sensorlinx_thermal_log/thermal_{month}.jsonl"
    r = requests.get(log_url, headers=headers)
    if r.status_code != 200:
        print(f"ERROR: Could not fetch thermal log ({r.status_code})")
        return []
    return [json.loads(l) for l in r.text.strip().split("\n") if l.strip()]


def get_current_state():
    r = requests.get(f"{BASE}/api/states", headers=headers)
    return {s["entity_id"]: s for s in r.json()}


def analyze_zone_cycles(zone_samples):
    """Detect heating cycles and characterize thermal response."""
    cycles = []
    in_heat = False
    cycle_start = None
    start_temp = None

    for s in zone_samples:
        action = s.get("hvac_action")
        temp = s.get("room_temp")
        if temp is None:
            continue

        if action == "heating" and not in_heat:
            in_heat = True
            cycle_start = s["ts"]
            start_temp = float(temp)
        elif action != "heating" and in_heat:
            in_heat = False
            end_temp = float(temp)
            cycles.append({
                "start": cycle_start,
                "end": s["ts"],
                "start_temp": start_temp,
                "end_temp": end_temp,
                "gain": end_temp - start_temp,
                "duration_min": len([x for x in zone_samples
                                     if cycle_start <= x.get("ts", "") <= s["ts"]
                                     and x.get("hvac_action") == "heating"]) * 5,
            })
    return cycles


def compute_efficiency_score(zone_data, zone_name):
    """Score 0-100 based on how well the zone maintains target."""
    if not zone_data:
        return 0, "No data"

    rooms = [float(s["room_temp"]) for s in zone_data if s.get("room_temp") is not None]
    targets = [float(s["commanded_setpoint"]) for s in zone_data if s.get("commanded_setpoint") is not None]

    if not rooms or not targets:
        return 0, "Missing data"

    avg_target = sum(targets) / len(targets)
    deviations = [abs(r - avg_target) for r in rooms]
    avg_dev = sum(deviations) / len(deviations)
    max_dev = max(deviations)

    # Scoring: 0 deviation = 100, 1F avg dev = 85, 2F = 70, 3F+ = 50
    score = max(0, min(100, 100 - (avg_dev * 15)))

    # Penalize large swings
    temp_range = max(rooms) - min(rooms)
    if temp_range > 6:
        score -= 10
    elif temp_range > 4:
        score -= 5

    reasons = []
    if avg_dev > 1.5:
        reasons.append(f"avg {avg_dev:.1f}F off target")
    if max_dev > 4:
        reasons.append(f"max {max_dev:.1f}F swing")
    if temp_range > 5:
        reasons.append(f"{temp_range:.1f}F total range")

    return round(score), ", ".join(reasons) if reasons else "well controlled"


def detect_short_cycling(cycles):
    """Short cycles < 15 min are inefficient for hydronic systems."""
    if not cycles:
        return 0, 0
    short = [c for c in cycles if c["duration_min"] < 15]
    return len(short), len(cycles)


def _avg_field(samples, field):
    vals = [float(s[field]) for s in samples if s.get(field) is not None]
    return sum(vals) / len(vals) if vals else None


def get_electricity_cost_per_kwh(states):
    """Read configured electricity rate from SensorLinx setup."""
    entity = states.get(COST_ENTITY, {})
    state = entity.get("state")
    try:
        return float(state)
    except (TypeError, ValueError):
        return DEFAULT_ELECTRICITY_COST


def compute_tankless_energy(recent, max_gap_minutes=MAX_POWER_GAP_MINUTES):
    """Integrate tankless power draw from thermal log samples (dedupe by timestamp)."""
    by_ts = {}
    for s in recent:
        ts = s.get("ts")
        pw = s.get("wh_power_kw")
        if not ts or pw is None:
            continue
        pw = float(pw)
        if pw < 0.01:
            continue
        by_ts[ts] = max(by_ts.get(ts, 0.0), pw)

    powers = list(by_ts.values())
    if len(by_ts) < 2:
        return {
            "energy_kwh": 0.0,
            "avg_power_kw": sum(powers) / len(powers) if powers else None,
            "peak_power_kw": max(powers) if powers else None,
            "active_hours": 0.0,
            "sample_points": len(by_ts),
        }

    points = sorted(by_ts.items())
    total_kwh = 0.0
    active_seconds = 0.0
    for i in range(len(points) - 1):
        t0 = datetime.fromisoformat(points[i][0])
        t1 = datetime.fromisoformat(points[i + 1][0])
        gap_sec = (t1 - t0).total_seconds()
        if gap_sec / 60 > max_gap_minutes:
            continue
        hours = gap_sec / 3600
        total_kwh += points[i][1] * hours
        active_seconds += gap_sec

    return {
        "energy_kwh": total_kwh,
        "avg_power_kw": sum(powers) / len(powers) if powers else None,
        "peak_power_kw": max(powers) if powers else None,
        "active_hours": active_seconds / 3600,
        "sample_points": len(points),
    }


def analyze_hydronic_performance(recent, zone_data, states):
    """Evaluate supply water and hydronic loop efficiency from thermal log data."""
    heating_samples = [
        s for s in recent
        if s.get("hvac_action") == "heating"
        or s.get("wh_heating") is True
        or (s.get("wh_power_kw") or 0) > 0.1
    ]

    outdoor_temps = [
        float(s["outdoor_temp"]) for s in recent if s.get("outdoor_temp") is not None
    ]
    outdoor_now = outdoor_temps[-1] if outdoor_temps else None

    underheating, overheating = [], []
    for z in ZONES:
        data = zone_data[z]
        if not data:
            continue
        rooms = [float(s["room_temp"]) for s in data if s.get("room_temp") is not None]
        targets = [float(s["commanded_setpoint"]) for s in data if s.get("commanded_setpoint") is not None]
        if not rooms or not targets:
            continue
        avg_room = sum(rooms) / len(rooms)
        avg_target = sum(targets) / len(targets)
        if avg_room < avg_target - 1.5:
            underheating.append(ZONE_LABELS[z])
        if max(rooms) > avg_target + 3:
            overheating.append(ZONE_LABELS[z])

    avg_supply = _avg_field(heating_samples, "wh_supply_temp")
    avg_target = _avg_field(heating_samples, "wh_target_temp")
    avg_delta_t = _avg_field(heating_samples, "wh_delta_t")
    avg_power = _avg_field(heating_samples, "wh_power_kw")
    avg_btu = _avg_field(heating_samples, "wh_btu_hr")
    avg_flow = _avg_field(heating_samples, "wh_flow_rate_gpm")

    target_actual_gap = None
    if avg_target is not None and avg_supply is not None:
        target_actual_gap = avg_target - avg_supply

    btu_per_kw = None
    if avg_btu and avg_power and avg_power > 0.05:
        btu_per_kw = avg_btu / (avg_power * 3412)

    supply_min = float(states.get(SUPPLY_ENTITIES["min_temp"], {}).get("state", 100))
    supply_max = float(states.get(SUPPLY_ENTITIES["max_temp"], {}).get("state", 140))
    target_sensor = states.get(SUPPLY_ENTITIES["target_sensor"], {})
    curve_target = float(target_sensor["state"]) if target_sensor.get("state") not in (None, "unavailable", "unknown") else None
    reset_enabled = states.get(SUPPLY_ENTITIES["reset_enabled"], {}).get("state") == "on"
    wh = states.get(SUPPLY_ENTITIES["water_heater"], {})
    wh_target = wh.get("attributes", {}).get("temperature")
    wh_actual = wh.get("attributes", {}).get("current_temperature")

    cost_per_kwh = get_electricity_cost_per_kwh(states)
    energy = compute_tankless_energy(recent)
    daily_cost = energy["energy_kwh"] * cost_per_kwh
    cost_per_1000_btu = None
    if avg_btu and energy["energy_kwh"] > 0.01:
        total_btu = avg_btu * energy["active_hours"]
        if total_btu > 0:
            cost_per_1000_btu = daily_cost / (total_btu / 1000)

    live_power = None
    power_state = states.get(POWER_DRAW_ENTITY, {})
    if power_state.get("state") not in (None, "unavailable", "unknown"):
        try:
            live_power = float(power_state["state"])
        except (TypeError, ValueError):
            pass

    return {
        "heating_sample_count": len(heating_samples),
        "outdoor_now": outdoor_now,
        "avg_delta_t": avg_delta_t,
        "avg_supply": avg_supply,
        "avg_return": _avg_field(heating_samples, "wh_return_temp"),
        "avg_flow": avg_flow,
        "avg_power": avg_power,
        "avg_btu": avg_btu,
        "avg_wh_target": avg_target,
        "target_actual_gap": target_actual_gap,
        "btu_per_kw": btu_per_kw,
        "underheating_zones": underheating,
        "overheating_zones": overheating,
        "supply_min": supply_min,
        "supply_max": supply_max,
        "curve_target": curve_target,
        "reset_enabled": reset_enabled,
        "wh_target": wh_target,
        "wh_actual": wh_actual,
        "cost_per_kwh": cost_per_kwh,
        "energy_kwh": energy["energy_kwh"],
        "daily_cost": daily_cost,
        "peak_power_kw": energy["peak_power_kw"],
        "tankless_active_hours": energy["active_hours"],
        "live_power_kw": live_power,
        "cost_per_1000_btu": cost_per_1000_btu,
    }


def compute_hydronic_efficiency_score(metrics):
    """Score 0-100 for supply water / hydronic delivery efficiency."""
    if metrics["heating_sample_count"] == 0:
        return 50, "No active heating samples (mild weather or idle)"

    score = 100
    reasons = []
    dt = metrics.get("avg_delta_t")
    gap = metrics.get("target_actual_gap")

    if dt is not None:
        if dt < 5:
            score -= 30
            reasons.append(f"low delta-T ({dt:.1f}F)")
        elif dt < IDEAL_DELTA_T[0]:
            score -= 15
            reasons.append(f"delta-T below ideal ({dt:.1f}F)")
        elif dt > 25:
            score -= 20
            reasons.append(f"delta-T too high ({dt:.1f}F)")

    if gap is not None and gap > 15:
        score -= 25
        reasons.append(f"setpoint gap {gap:.0f}F (target vs actual)")
    elif gap is not None and gap > 8:
        score -= 10
        reasons.append(f"setpoint gap {gap:.0f}F")

    if not metrics["reset_enabled"]:
        score -= 15
        reasons.append("supply reset disabled")

    if metrics.get("btu_per_kw") and metrics["btu_per_kw"] < 0.5:
        score -= 10
        reasons.append("low heat delivery per kW")

    if metrics.get("cost_per_1000_btu") and metrics["cost_per_1000_btu"] > 0.08:
        score -= 10
        reasons.append(f"high energy cost (${metrics['cost_per_1000_btu']:.3f}/1000 BTU)")

    assessment = ", ".join(reasons) if reasons else "optimal hydronic delivery"
    return max(0, min(100, round(score))), assessment


def compute_supply_adjustments(metrics):
    """Recommend supply water parameter changes based on performance."""
    adjustments = []
    supply_min = metrics["supply_min"]
    supply_max = metrics["supply_max"]

    if not metrics["reset_enabled"]:
        adjustments.append({
            "entity": SUPPLY_ENTITIES["reset_enabled"],
            "action": "turn_on",
            "reason": "Enable automatic supply water reset for outdoor-temp curve control",
            "old": "off",
            "new": "on",
        })

    gap = metrics.get("target_actual_gap")
    curve_target = metrics.get("curve_target")
    wh_target = metrics.get("wh_target")
    rounded_curve = round(curve_target) if curve_target else None

    if rounded_curve and wh_target is not None and abs(wh_target - rounded_curve) > 3:
        reason = f"Align tankless setpoint to curve target for {metrics['outdoor_now']:.0f}F outdoor"
        if gap and gap > 10 and metrics.get("avg_supply") is not None:
            reason = (
                f"Tankless target {wh_target}F but loop delivering ~{metrics['avg_supply']:.0f}F - "
                f"set to outdoor-reset curve target"
            )
        adjustments.append({
            "entity": SUPPLY_ENTITIES["water_heater"],
            "action": "set_temperature",
            "value": rounded_curve,
            "reason": reason,
            "old": wh_target,
            "new": rounded_curve,
        })

    if metrics["underheating_zones"] and metrics.get("avg_delta_t", 99) < 8:
        new_max = min(TANKLESS_MAX_TEMP, supply_max + ADJUST_STEP)
        if new_max > supply_max:
            adjustments.append({
                "entity": SUPPLY_ENTITIES["max_temp"],
                "action": "set_value",
                "value": new_max,
                "reason": (
                    f"Zones under-heating ({', '.join(metrics['underheating_zones'])}) "
                    f"with low delta-T - raise cold-weather supply ceiling"
                ),
                "old": supply_max,
                "new": new_max,
            })

    if metrics["underheating_zones"] and metrics.get("avg_supply", 999) < 95:
        new_min = min(130, supply_min + ADJUST_STEP)
        if new_min > supply_min:
            adjustments.append({
                "entity": SUPPLY_ENTITIES["min_temp"],
                "action": "set_value",
                "value": new_min,
                "reason": (
                    f"Actual supply ~{metrics['avg_supply']:.0f}F during heating - "
                    f"raise mild-weather supply floor"
                ),
                "old": supply_min,
                "new": new_min,
            })

    if metrics["overheating_zones"] and metrics.get("avg_delta_t", 0) > 18:
        new_min = max(80, supply_min - ADJUST_STEP)
        if new_min < supply_min:
            adjustments.append({
                "entity": SUPPLY_ENTITIES["min_temp"],
                "action": "set_value",
                "value": new_min,
                "reason": (
                    f"Zones overshooting ({', '.join(metrics['overheating_zones'])}) "
                    f"with high delta-T - lower mild-weather supply floor"
                ),
                "old": supply_min,
                "new": new_min,
            })

    outdoor = metrics.get("outdoor_now")
    if outdoor and outdoor > 58 and wh_target and wh_target > 115:
        new_max = max(110, supply_max - ADJUST_STEP)
        if new_max < supply_max:
            adjustments.append({
                "entity": SUPPLY_ENTITIES["max_temp"],
                "action": "set_value",
                "value": new_max,
                "reason": f"Mild outdoor ({outdoor:.0f}F) but high supply ceiling - reduce max for efficiency",
                "old": supply_max,
                "new": new_max,
            })

    # Deduplicate: keep last adjustment per entity
    seen = {}
    for adj in adjustments:
        seen[adj["entity"]] = adj
    return list(seen.values())


def apply_adjustments(adjustments):
    """Apply supply water adjustments via Home Assistant API."""
    applied = []
    for adj in adjustments:
        entity = adj["entity"]
        action = adj["action"]
        ok = False
        if action == "turn_on":
            r = requests.post(
                f"{BASE}/api/services/switch/turn_on",
                headers=headers,
                json={"entity_id": entity},
            )
            ok = r.status_code == 200
        elif action == "set_value":
            r = requests.post(
                f"{BASE}/api/services/number/set_value",
                headers=headers,
                json={"entity_id": entity, "value": adj["value"]},
            )
            ok = r.status_code == 200
        elif action == "set_temperature":
            temp = round(float(adj["value"]))
            r = requests.post(
                f"{BASE}/api/services/water_heater/set_temperature",
                headers=headers,
                json={"entity_id": entity, "temperature": temp},
            )
            adj = {**adj, "new": temp}
            ok = r.status_code == 200
        applied.append({**adj, "applied": ok})
    return applied


def print_hydronic_sections(metrics, adjustments, applied=None):
    """Print supply water performance analysis and agent actions."""
    score, assessment = compute_hydronic_efficiency_score(metrics)

    print("-" * 78)
    print("  9. SUPPLY WATER & HYDRONIC EFFICIENCY")
    print("-" * 78)
    print()
    print(f"  Hydronic Efficiency Score: {score}/100 - {assessment}")
    print()
    print(f"  Supply reset enabled: {'Yes' if metrics['reset_enabled'] else 'No'}")
    print(f"  Supply curve range: {metrics['supply_min']:.0f}F (mild) -> {metrics['supply_max']:.0f}F (cold)")
    if metrics["curve_target"]:
        print(f"  Curve target (now): {metrics['curve_target']:.1f}F")
    if metrics["wh_target"]:
        print(f"  Tankless setpoint: {metrics['wh_target']}F  actual: {metrics['wh_actual']}F")
    print()

    if metrics["heating_sample_count"] > 0:
        print(f"  Active heating samples (24h): {metrics['heating_sample_count']}")
        if metrics["avg_supply"] is not None:
            print(f"  Avg supply temp: {metrics['avg_supply']:.1f}F  return: {metrics['avg_return']:.1f}F")
        if metrics["avg_delta_t"] is not None:
            print(f"  Avg delta-T: {metrics['avg_delta_t']:.1f}F  (ideal: {IDEAL_DELTA_T[0]:.0f}-{IDEAL_DELTA_T[1]:.0f}F)")
        if metrics["avg_flow"] is not None:
            print(f"  Avg flow rate: {metrics['avg_flow']:.2f} GPM")
        if metrics["avg_power"] is not None:
            print(f"  Avg power draw: {metrics['avg_power']:.2f} kW")
        if metrics.get("peak_power_kw") is not None:
            print(f"  Peak power draw (24h): {metrics['peak_power_kw']:.2f} kW")
        if metrics.get("live_power_kw") is not None:
            print(f"  Current power draw: {metrics['live_power_kw']:.2f} kW")
        if metrics.get("energy_kwh", 0) > 0:
            print(f"  Tankless energy (24h): {metrics['energy_kwh']:.2f} kWh")
            print(f"  Electricity rate: ${metrics['cost_per_kwh']:.3f}/kWh")
            print(f"  Estimated daily cost: ${metrics['daily_cost']:.2f}")
            if metrics.get("tankless_active_hours", 0) > 0:
                print(f"  Tankless runtime (integrated): {metrics['tankless_active_hours']:.1f} hr")
            if metrics.get("cost_per_1000_btu") is not None:
                print(f"  Cost per 1000 BTU delivered: ${metrics['cost_per_1000_btu']:.3f}")
        if metrics["avg_btu"] is not None:
            print(f"  Avg heat output: {metrics['avg_btu']:.0f} BTU/hr")
        if metrics["btu_per_kw"] is not None:
            print(f"  Heat delivery ratio: {metrics['btu_per_kw']:.2f} BTU per kW input")
        if metrics["target_actual_gap"] is not None:
            print(f"  Setpoint vs actual gap: {metrics['target_actual_gap']:.1f}F")
    else:
        print("  No active heating in last 24h - hydronic metrics unavailable")
    print()

    print("-" * 78)
    print("  10. SUPPLY WATER AGENT ACTIONS")
    print("-" * 78)
    print()

    if not adjustments:
        print("  No adjustments needed - supply water parameters are optimal.")
        print()
        return

    for i, adj in enumerate(adjustments, 1):
        status = ""
        if applied:
            match = next((a for a in applied if a["entity"] == adj["entity"]), None)
            if match:
                status = " [APPLIED]" if match["applied"] else " [FAILED]"
        if adj["action"] == "turn_on":
            change = f"{adj['old']} -> {adj['new']}"
        else:
            change = f"{adj['old']} -> {adj['new']}F"
        entity_short = adj["entity"].split(".")[-1].replace("sensorlinx_outdoor_reset_", "")
        print(f"  {i}. {entity_short}: {change}{status}")
        print(f"     Reason: {adj['reason']}")
        print()


def main(apply_supply=False):
    print("=" * 78)
    print("  DAILY RADIANT FLOOR SYSTEM REPORT")
    print(f"  Generated: {datetime.now().strftime('%A, %B %d %Y at %I:%M %p')}")
    print("=" * 78)
    print()

    samples = fetch_data()
    if not samples:
        return

    states = get_current_state()

    # Last 24h of data
    cutoff = datetime.now() - timedelta(hours=24)
    recent = [s for s in samples if s.get("ts", "") >= cutoff.isoformat()]

    # Group by zone
    zone_data = defaultdict(list)
    for s in recent:
        z = s.get("zone")
        if z:
            zone_data[z].append(s)

    # --- SECTION 1: SYSTEM OVERVIEW ---
    print("-" * 78)
    print("  1. SYSTEM OVERVIEW")
    print("-" * 78)

    # Outdoor conditions
    outdoor_temps = [float(s["outdoor_temp"]) for s in zone_data.get("laundry", [])
                     if s.get("outdoor_temp") is not None]
    if outdoor_temps:
        print(f"  Outdoor temp (24h): Low {min(outdoor_temps):.0f}F / High {max(outdoor_temps):.0f}F / Now {outdoor_temps[-1]:.0f}F")
        # Heating degree calculation (base 65)
        hdd = sum(max(0, 65 - t) for t in outdoor_temps) / (len(outdoor_temps) / 12)  # normalize to day
        print(f"  Heating Degree Hours (base 65): {hdd:.1f}")
    print()

    # Current curve params
    curve_target = states.get("sensor.sensorlinx_outdoor_reset_heating_curve_target", {})
    if curve_target:
        attrs = curve_target.get("attributes", {})
        print(f"  Heating Curve: base={attrs.get('base')}F  overshoot={attrs.get('overshoot')}F  shutdown={attrs.get('shutdown')}F")
        print(f"  Current curve output: {curve_target.get('state')}F")
    print()

    # --- SECTION 2: ZONE PERFORMANCE ---
    print("-" * 78)
    print("  2. ZONE PERFORMANCE SCORECARD")
    print("-" * 78)
    print()
    print(f"  {'Zone':<14} {'Score':<8} {'Room Now':<10} {'Target':<9} {'Heat%':<8} {'Assessment'}")
    print(f"  {'----':<14} {'-----':<8} {'--------':<10} {'------':<9} {'-----':<8} {'----------'}")

    zone_scores = {}
    for z in ZONES:
        data = zone_data[z]
        if not data:
            print(f"  {ZONE_LABELS[z]:<14} {'--':<8} {'N/A':<10} {'N/A':<9} {'N/A':<8} No data")
            continue

        score, reason = compute_efficiency_score(data, z)
        zone_scores[z] = score

        rooms = [float(s["room_temp"]) for s in data if s.get("room_temp") is not None]
        heating = sum(1 for s in data if s.get("hvac_action") == "heating")
        total = len(data)
        target = data[-1].get("commanded_setpoint", "?")
        heat_pct = f"{heating/total*100:.0f}%" if total > 0 else "?"

        current_room = f"{rooms[-1]:.1f}F" if rooms else "?"
        target_str = f"{target}F" if target != "?" else "?"

        # Grade
        if score >= 90:
            grade = "Excellent"
        elif score >= 75:
            grade = "Good"
        elif score >= 60:
            grade = "Fair"
        else:
            grade = "Needs attention"

        print(f"  {ZONE_LABELS[z]:<14} {score:<8} {current_room:<10} {target_str:<9} {heat_pct:<8} {grade} - {reason}")

    overall = sum(zone_scores.values()) / len(zone_scores) if zone_scores else 0
    print()
    print(f"  Overall System Score: {overall:.0f}/100")
    print()

    # --- SECTION 3: THERMAL LAG & CYCLE ANALYSIS ---
    print("-" * 78)
    print("  3. THERMAL LAG & CYCLE ANALYSIS")
    print("-" * 78)
    print()
    print(f"  {'Zone':<14} {'Cycles':<9} {'Avg Dur':<10} {'Avg Gain':<10} {'Short Cyc':<11} {'Assessment'}")
    print(f"  {'----':<14} {'------':<9} {'-------':<10} {'--------':<10} {'---------':<11} {'----------'}")

    for z in ZONES:
        data = zone_data[z]
        if not data:
            continue

        cycles = analyze_zone_cycles(data)
        short, total_cyc = detect_short_cycling(cycles)

        if cycles:
            avg_dur = sum(c["duration_min"] for c in cycles) / len(cycles)
            avg_gain = sum(c["gain"] for c in cycles) / len(cycles)
            n_cycles = len(cycles)

            # Assessment
            issues = []
            if short > 0:
                issues.append(f"{short} short cycles (<15min)")
            if avg_dur < 20:
                issues.append("cycles too short for hydronic")
            if avg_dur > 90:
                issues.append("very long runs (undersized?)")
            if avg_gain > 4:
                issues.append("large swings (overshooting)")
            if avg_gain < 0.5:
                issues.append("minimal gain (temp already met)")

            assessment = "; ".join(issues) if issues else "Healthy cycling"

            print(f"  {ZONE_LABELS[z]:<14} {n_cycles:<9} {avg_dur:.0f} min   {avg_gain:+.1f}F     {short}/{total_cyc:<9} {assessment}")
        else:
            print(f"  {ZONE_LABELS[z]:<14} {'0':<9} {'--':<10} {'--':<10} {'--':<11} No heating activity")

    print()

    # --- SECTION 4: FLOOR TEMPERATURE SAFETY ---
    print("-" * 78)
    print("  4. FLOOR TEMPERATURE SAFETY ANALYSIS")
    print("-" * 78)
    print()

    floor_max_entity = states.get("number.sensorlinx_outdoor_reset_max_floor_temp_safety_cap", {})
    floor_max = float(floor_max_entity.get("state", 80))
    print(f"  Safety cap: {floor_max:.0f}F (wood floor maximum)")
    print()
    print(f"  {'Zone':<14} {'Floor Now':<11} {'Floor Max':<11} {'Margin':<9} {'Status'}")
    print(f"  {'----':<14} {'---------':<11} {'---------':<11} {'------':<9} {'------'}")

    for z in ZONES:
        data = zone_data[z]
        if not data:
            continue
        floors = [float(s["floor_temp"]) for s in data if s.get("floor_temp") is not None]
        if floors:
            current = floors[-1]
            peak = max(floors)
            margin = floor_max - peak
            if margin < 2:
                status = "WARNING - close to limit"
            elif margin < 5:
                status = "Monitor"
            else:
                status = "Safe"
            print(f"  {ZONE_LABELS[z]:<14} {current:.1f}F     {peak:.1f}F     {margin:.1f}F    {status}")

    print()

    # --- SECTION 5: MAIN HVAC INTERACTION ---
    print("-" * 78)
    print("  5. MAIN HVAC (FORCED AIR) INTERACTION")
    print("-" * 78)
    print()

    ecobee_data = [s for s in zone_data.get("laundry", []) if s.get("ecobee_action")]
    if ecobee_data:
        ec_heating = sum(1 for s in ecobee_data if s["ecobee_action"] == "heating")
        ec_cooling = sum(1 for s in ecobee_data if s["ecobee_action"] == "cooling")
        ec_fan = sum(1 for s in ecobee_data if s["ecobee_action"] == "fan")
        ec_idle = sum(1 for s in ecobee_data if s["ecobee_action"] == "idle")
        total_ec = len(ecobee_data)

        print(f"  Ecobee mode: {ecobee_data[-1].get('ecobee_mode')}  setpoint: {ecobee_data[-1].get('ecobee_setpoint')}F")
        print(f"  Activity breakdown:")
        print(f"    Heating: {ec_heating/total_ec*100:.1f}%  ({ec_heating * 5} min)")
        print(f"    Cooling: {ec_cooling/total_ec*100:.1f}%  ({ec_cooling * 5} min)")
        print(f"    Fan only: {ec_fan/total_ec*100:.1f}%  ({ec_fan * 5} min)")
        print(f"    Idle: {ec_idle/total_ec*100:.1f}%  ({ec_idle * 5} min)")
        print()

        if ec_heating == 0 and ec_cooling == 0:
            print("  Assessment: Radiant floor system is fully handling the thermal load.")
            print("  The forced air system has not needed to supplement.")
        elif ec_heating > 0:
            print(f"  Assessment: Forced air supplemented with {ec_heating * 5} min of heating.")
            print("  Consider whether radiant floor setpoints should be raised to eliminate")
            print("  forced air heating (more efficient for radiant to carry the load).")
        elif ec_cooling > 0:
            print(f"  Assessment: Cooling was active for {ec_cooling * 5} min.")
            print("  If floor temps are also high, consider lowering heating curve output.")

        # Sensor readings
        last_sensors = ecobee_data[-1].get("ecobee_sensors", {})
        if last_sensors:
            print()
            print(f"  Remote sensor readings:")
            for name, temp in sorted(last_sensors.items()):
                print(f"    {name}: {temp:.1f}F")
    print()

    # --- SECTION 6: ENERGY EFFICIENCY METRICS ---
    print("-" * 78)
    print("  6. ENERGY EFFICIENCY METRICS")
    print("-" * 78)
    print()

    total_heating_samples = 0
    total_samples = 0
    for z in ZONES:
        data = zone_data[z]
        total_heating_samples += sum(1 for s in data if s.get("hvac_action") == "heating")
        total_samples += len(data)

    if total_samples > 0 and outdoor_temps:
        system_duty = total_heating_samples / total_samples * 100
        avg_outdoor = sum(outdoor_temps) / len(outdoor_temps)

        print(f"  System-wide heating duty cycle: {system_duty:.1f}%")
        print(f"  Average outdoor temp: {avg_outdoor:.1f}F")

        # Efficiency ratio: lower duty at higher outdoor = better insulation/curve tuning
        # Ideal: duty < (65 - outdoor) * 2.5% for well-insulated radiant
        expected_duty = max(0, (65 - avg_outdoor) * 2.5)
        if expected_duty > 0:
            efficiency_ratio = expected_duty / max(system_duty, 1)
            if efficiency_ratio > 1.2:
                print(f"  Efficiency rating: EXCELLENT (duty {system_duty:.0f}% vs expected ~{expected_duty:.0f}%)")
            elif efficiency_ratio > 0.8:
                print(f"  Efficiency rating: GOOD (duty {system_duty:.0f}% vs expected ~{expected_duty:.0f}%)")
            else:
                print(f"  Efficiency rating: REVIEW (duty {system_duty:.0f}% vs expected ~{expected_duty:.0f}%)")
                print(f"  System is running more than expected. Check insulation or curve tuning.")
        print()

        # Comfort consistency (standard deviation of room temps)
        all_rooms = []
        for z in ZONES:
            all_rooms.extend([float(s["room_temp"]) for s in zone_data[z] if s.get("room_temp")])
        if all_rooms:
            mean = sum(all_rooms) / len(all_rooms)
            variance = sum((t - mean) ** 2 for t in all_rooms) / len(all_rooms)
            std = math.sqrt(variance)
            print(f"  Comfort consistency (cross-zone std dev): {std:.1f}F")
            if std < 1.5:
                print(f"  Rating: EXCELLENT - zones are well balanced")
            elif std < 2.5:
                print(f"  Rating: GOOD - minor zone imbalance")
            else:
                print(f"  Rating: FAIR - significant variation between zones")

        cost_per_kwh = get_electricity_cost_per_kwh(states)
        energy = compute_tankless_energy(recent)
        daily_cost = energy["energy_kwh"] * cost_per_kwh
        print()
        print(f"  Tankless water heater (24h):")
        if energy["energy_kwh"] > 0:
            print(f"    Energy consumed: {energy['energy_kwh']:.2f} kWh")
            print(f"    Electricity rate: ${cost_per_kwh:.3f}/kWh")
            print(f"    Estimated cost: ${daily_cost:.2f}")
            if energy["avg_power_kw"] is not None:
                print(f"    Avg power while firing: {energy['avg_power_kw']:.2f} kW")
            if energy["peak_power_kw"] is not None:
                print(f"    Peak power draw: {energy['peak_power_kw']:.2f} kW")
            if energy["active_hours"] > 0:
                print(f"    Integrated firing time: {energy['active_hours']:.1f} hr")
                print(f"    Avg cost while firing: ${daily_cost / energy['active_hours']:.2f}/hr")
        else:
            print("    No significant tankless power draw logged in last 24h")
        power_state = states.get(POWER_DRAW_ENTITY, {})
        if power_state.get("state") not in (None, "unavailable", "unknown"):
            try:
                live_kw = float(power_state["state"])
                print(f"    Current draw: {live_kw:.2f} kW (${live_kw * cost_per_kwh:.3f}/hr at current rate)")
            except (TypeError, ValueError):
                pass

    print()

    # --- SECTION 7: SPECIALIST RECOMMENDATIONS ---
    print("-" * 78)
    print("  7. SPECIALIST RECOMMENDATIONS")
    print("-" * 78)
    print()

    recommendations = []
    priority = 1

    # Check each zone for issues
    for z in ZONES:
        data = zone_data[z]
        if not data:
            continue

        rooms = [float(s["room_temp"]) for s in data if s.get("room_temp") is not None]
        floors = [float(s["floor_temp"]) for s in data if s.get("floor_temp") is not None]
        targets = [float(s["commanded_setpoint"]) for s in data if s.get("commanded_setpoint") is not None]

        if not rooms or not targets:
            continue

        avg_room = sum(rooms) / len(rooms)
        avg_target = sum(targets) / len(targets)
        current_room = rooms[-1]
        peak_floor = max(floors) if floors else 0
        room_range = max(rooms) - min(rooms)

        # Under-heating
        if avg_room < avg_target - 1.5:
            recommendations.append(
                f"[{ZONE_LABELS[z]}] Consistently under-heating (avg {avg_room:.1f}F vs target {avg_target:.1f}F). "
                f"Increase zone offset by +1-2F or check for air infiltration/drafts."
            )

        # Over-heating / overshoot
        if max(rooms) > avg_target + 5:
            recommendations.append(
                f"[{ZONE_LABELS[z]}] Overshooting to {max(rooms):.1f}F (target {avg_target:.1f}F). "
                f"Radiant thermal lag causing overshoot. Consider reducing zone offset or "
                f"lowering overshoot parameter during mild outdoor conditions."
            )

        # Large swings
        if room_range > 6:
            recommendations.append(
                f"[{ZONE_LABELS[z]}] Large temperature swing ({room_range:.1f}F range). "
                f"For hydronic radiant, aim for <4F swing. May need shorter update interval "
                f"or reduced overshoot to dampen oscillation."
            )

        # Floor near safety limit
        if peak_floor > floor_max - 3:
            recommendations.append(
                f"[{ZONE_LABELS[z]}] Floor peaked at {peak_floor:.1f}F, only {floor_max - peak_floor:.1f}F "
                f"from safety cap. Consider reducing max output for this zone."
            )

        # Cycles
        cycles = analyze_zone_cycles(data)
        short, total_cyc = detect_short_cycling(cycles)
        if short > 2:
            recommendations.append(
                f"[{ZONE_LABELS[z]}] {short} short heating cycles detected (<15 min). "
                f"Short-cycling reduces hydronic efficiency and increases pump wear. "
                f"Consider increasing the deadband or update interval."
            )

    # System-level recommendations
    if outdoor_temps:
        avg_out = sum(outdoor_temps) / len(outdoor_temps)
        # If outdoor is consistently above shutdown but system was heating
        shutdown_entity = states.get("number.sensorlinx_outdoor_reset_heating_curve_shutdown_temp", {})
        shutdown_temp = float(shutdown_entity.get("state", 65))

        if avg_out > shutdown_temp - 3 and total_heating_samples > total_samples * 0.1:
            recommendations.append(
                f"[System] Outdoor avg {avg_out:.0f}F is near shutdown threshold {shutdown_temp:.0f}F "
                f"but system is still heating {total_heating_samples/total_samples*100:.0f}% of the time. "
                f"Consider raising shutdown temp to {shutdown_temp + 3:.0f}F to save energy in mild weather."
            )

    # Ecobee coordination
    if ecobee_data:
        ec_temp = float(ecobee_data[-1].get("ecobee_temp", 0))
        ec_setpoint = float(ecobee_data[-1].get("ecobee_setpoint", 0))
        if ec_temp > ec_setpoint + 5:
            recommendations.append(
                f"[Ecobee] Main thermostat reads {ec_temp:.0f}F, well above its {ec_setpoint:.0f}F setpoint. "
                f"The radiant floor is over-conditioning the house relative to the forced air setpoint. "
                f"This is fine if intentional (radiant comfort), but could lower the Ecobee setpoint further "
                f"or match it to the floor curve target to avoid confusion."
            )

    if not recommendations:
        recommendations.append("No issues detected. System is operating within optimal parameters.")

    for i, rec in enumerate(recommendations, 1):
        print(f"  {i}. {rec}")
        print()

    # --- SECTION 8: TOMORROW'S OUTLOOK ---
    print("-" * 78)
    print("  8. OPERATIONAL OUTLOOK")
    print("-" * 78)
    print()

    if outdoor_temps:
        # Trend: is outdoor dropping or rising?
        first_half = outdoor_temps[:len(outdoor_temps)//2]
        second_half = outdoor_temps[len(outdoor_temps)//2:]
        trend = sum(second_half)/len(second_half) - sum(first_half)/len(first_half)

        if trend > 3:
            print("  Outdoor trend: WARMING (+{:.0f}F over 24h)".format(trend))
            print("  Expect reduced heating demand. System should coast more.")
        elif trend < -3:
            print("  Outdoor trend: COOLING ({:.0f}F over 24h)".format(trend))
            print("  Expect increased heating demand. Monitor for zones falling behind.")
        else:
            print("  Outdoor trend: STABLE ({:+.1f}F change)".format(trend))
            print("  Heating demand should remain consistent with current patterns.")

        print()
        current_out = outdoor_temps[-1]
        curve_out = float(curve_target.get("state", 70)) if curve_target else 70
        print(f"  Current operating point: {current_out:.0f}F outdoor -> {curve_out}F curve target")
        print(f"  System capacity headroom: {floor_max - max(max(floors) for z in ZONES for floors in [[float(s['floor_temp']) for s in zone_data[z] if s.get('floor_temp')]] if floors):.0f}F to safety cap")

    # --- SECTIONS 9-10: Supply water agent ---
    hydronic_metrics = analyze_hydronic_performance(recent, zone_data, states)
    supply_adjustments = compute_supply_adjustments(hydronic_metrics)
    applied = apply_adjustments(supply_adjustments) if apply_supply and supply_adjustments else None
    print_hydronic_sections(hydronic_metrics, supply_adjustments, applied)

    print("=" * 78)
    print("  END OF REPORT")
    print("=" * 78)

    return {
        "overall_score": overall,
        "hydronic_score": compute_hydronic_efficiency_score(hydronic_metrics)[0],
        "adjustments": supply_adjustments,
        "applied": applied,
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Daily HVAC performance report")
    parser.add_argument(
        "--apply", action="store_true",
        help="Apply supply water adjustments (use daily_report_agent.py for agent mode)",
    )
    args = parser.parse_args()
    main(apply_supply=args.apply)
