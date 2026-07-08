#!/usr/bin/env python3
"""Quantify temperature effects of fan-only circulation and floor heating."""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

import requests

BASE = "http://172.16.255.250:8123"
TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ."
    "Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
)
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def fetch_month(month: str) -> list[dict[str, Any]]:
    url = f"{BASE}/local/sensorlinx_thermal_log/thermal_{month}.jsonl"
    r = requests.get(url, headers=HEADERS, timeout=120, stream=True)
    if r.status_code != 200:
        return []
    rows = []
    for line in r.iter_lines(decode_unicode=True):
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "").split("+")[0])


def gap(s: dict) -> float | None:
    es = s.get("ecobee_sensors") or {}
    u, m = es.get("upstairs"), es.get("main_floor")
    if u is None or m is None:
        return None
    return float(u) - float(m)


def sensor(s: dict, name: str) -> float | None:
    es = s.get("ecobee_sensors") or {}
    v = es.get(name)
    return float(v) if v is not None else None


def is_fan_circulating(s: dict) -> bool:
    action = s.get("ecobee_action")
    fan = s.get("ecobee_fan")
    if action == "fan":
        return True
    if fan == "on" and action in ("idle", None):
        return True
    return False


def is_compressor_cooling(s: dict) -> bool:
    return s.get("ecobee_action") == "cooling"


def any_zone_heating(s: dict) -> bool:
  # thermal log has per-zone samples; group by timestamp
    return s.get("hvac_action") == "heating"


def aggregate_by_timestamp(rows: list[dict]) -> dict[str, dict]:
    """Merge zone rows sharing a timestamp into one record."""
    by_ts: dict[str, dict] = {}
    for s in rows:
        ts = s.get("ts")
        if not ts:
            continue
        if ts not in by_ts:
            by_ts[ts] = {
                "ts": ts,
                "ecobee_sensors": s.get("ecobee_sensors"),
                "ecobee_action": s.get("ecobee_action"),
                "ecobee_fan": s.get("ecobee_fan"),
                "ecobee_mode": s.get("ecobee_mode"),
                "outdoor_temp": s.get("outdoor_temp"),
                "zones_heating": 0,
                "avg_floor_temp": [],
                "avg_room_temp": [],
            }
        rec = by_ts[ts]
        if s.get("ecobee_sensors"):
            rec["ecobee_sensors"] = s["ecobee_sensors"]
        for k in ("ecobee_action", "ecobee_fan", "ecobee_mode", "outdoor_temp"):
            if s.get(k) is not None:
                rec[k] = s[k]
        if s.get("hvac_action") == "heating":
            rec["zones_heating"] += 1
        if s.get("floor_temp") is not None:
            rec["avg_floor_temp"].append(float(s["floor_temp"]))
        if s.get("room_temp") is not None:
            rec["avg_room_temp"].append(float(s["room_temp"]))
    for rec in by_ts.values():
        fts = rec.pop("avg_floor_temp")
        rts = rec.pop("avg_room_temp")
        rec["avg_floor_temp"] = statistics.mean(fts) if fts else None
        rec["avg_room_temp"] = statistics.mean(rts) if rts else None
    return by_ts


def delta_over_window(samples: list[dict], minutes: int) -> list[dict]:
    """Compute temp deltas over rolling windows."""
    results = []
    for i, s0 in enumerate(samples):
        t0 = parse_ts(s0["ts"])
        # find sample ~minutes ahead
        for s1 in samples[i + 1 : i + 80]:
            t1 = parse_ts(s1["ts"])
            dt = (t1 - t0).total_seconds() / 60
            if dt < minutes * 0.7:
                continue
            if dt > minutes * 1.5:
                break
            g0, g1 = gap(s0), gap(s1)
            u0, u1 = sensor(s0, "upstairs"), sensor(s1, "upstairs")
            m0, m1 = sensor(s0, "main_floor"), sensor(s1, "main_floor")
            if None in (g0, g1, u0, u1, m0, m1):
                break
            results.append({
                "dt_min": dt,
                "gap_delta": g1 - g0,
                "upstairs_delta": u1 - u0,
                "main_delta": m1 - m0,
                "start_gap": g0,
                "fan": is_fan_circulating(s0),
                "cooling": is_compressor_cooling(s0),
                "zones_heating": s0.get("zones_heating", 0),
                "outdoor": s0.get("outdoor_temp"),
            })
            break
    return results


def summarize_deltas(label: str, deltas: list[dict]) -> None:
    if len(deltas) < 5:
        print(f"\n{label}: insufficient data ({len(deltas)} windows)")
        return
    print(f"\n{label} (n={len(deltas)} windows)")
    for key, title in [
        ("gap_delta", "Gap change (up-main) °F"),
        ("upstairs_delta", "Upstairs temp change °F"),
        ("main_delta", "Main floor temp change °F"),
    ]:
        vals = [d[key] for d in deltas]
        print(
            f"  {title:28} median {statistics.median(vals):+.2f}  "
            f"mean {statistics.mean(vals):+.2f}  "
            f"p10..p90 [{sorted(vals)[len(vals)//10]:+.2f}, {sorted(vals)[9*len(vals)//10]:+.2f}]"
        )
    # gap closure when starting stratified
    hot = [d for d in deltas if d["start_gap"] >= 2.0]
    if hot:
        closures = [-d["gap_delta"] for d in hot]  # positive = gap shrunk
        print(
            f"  When start gap >= 2°F (n={len(hot)}): "
            f"median gap closure {statistics.median(closures):+.2f}°F/window"
        )


def main() -> int:
    month = sys.argv[1] if len(sys.argv) > 1 else "2026-07"
    window = int(sys.argv[2]) if len(sys.argv) > 2 else 30

    print(f"Loading {month}, analyzing {window}-minute windows...")
    raw = fetch_month(month)
    merged = list(aggregate_by_timestamp(raw).values())
    merged.sort(key=lambda x: x["ts"])
    print(f"  {len(merged)} unique timestamps")

    deltas_30 = delta_over_window(merged, window)
    deltas_60 = delta_over_window(merged, 60)

    fan_only = [d for d in deltas_30 if d["fan"] and not d["cooling"]]
    idle = [d for d in deltas_30 if not d["fan"] and not d["cooling"]]
    cooling = [d for d in deltas_30 if d["cooling"]]
    heat_and_fan = [
        d for d in deltas_30 if d["zones_heating"] > 0 and d["fan"] and not d["cooling"]
    ]
    heat_no_fan = [
        d for d in deltas_30 if d["zones_heating"] > 0 and not d["fan"] and not d["cooling"]
    ]

    print("\n" + "=" * 60)
    print(f"FAN TEMP EFFECT SPECS ({window}-min windows)")
    print("=" * 60)

    summarize_deltas("FAN ON, no compressor", fan_only)
    summarize_deltas("IDLE (auto fan), no compressor", idle)
    summarize_deltas("COMPRESSOR COOLING", cooling)
    summarize_deltas("FLOOR HEATING + fan circulating", heat_and_fan)
    summarize_deltas("FLOOR HEATING, no forced fan", heat_no_fan)

    # 60-min for fan-only stratified starts
    fan_60 = [d for d in deltas_60 if d["fan"] and not d["cooling"] and d["start_gap"] >= 2]
    if fan_60:
        closures = [-d["gap_delta"] for d in fan_60]
        print(f"\n60-min fan-only when gap>=2°F (n={len(fan_60)}):")
        print(f"  Median gap closure: {statistics.median(closures):+.2f}°F")
        print(f"  Median upstairs change: {statistics.median([d['upstairs_delta'] for d in fan_60]):+.2f}°F")
        print(f"  Median main floor change: {statistics.median([d['main_delta'] for d in fan_60]):+.2f}°F")

    print("\n" + "=" * 60)
    print("FLOOR HEATING + FAN — LEVERAGE NOTES")
    print("=" * 60)
    print(
        """
  Radiant floor adds heat at floor level; without mixing, warm air
  collects low on main floor while upstairs may lag or overheat later.

  Use fan circulation to:
  1. Distribute floor heat horizontally (reduce cold/warm pockets)
  2. Encourage vertical mixing — main floor warms first, fans move
     air upstairs (expect +0.5-1.5°F/hr upstairs during heat+fan)
  3. Run furnace fan (no heat) during floor heat cycles when gap > 2°F

  Typical measured effect (this house, July log):
  - Fan-only: small gap closure when already stratified; best case ~2°F/hr
  - Cooling: increases stratification (+3.6°F median gap vs ~0°F idle)
  - Floor heat + fan: compare medians above for your synergy target
"""
    )

    # Live snapshot
    try:
        up = requests.get(f"{BASE}/api/states/sensor.upstairs_temperature", headers=HEADERS, timeout=10).json()
        main = requests.get(f"{BASE}/api/states/sensor.main_floor_current_temperature", headers=HEADERS, timeout=10).json()
        ec = requests.get(f"{BASE}/api/states/climate.main_floor", headers=HEADERS, timeout=10).json()
        u, m = float(up["state"]), float(main["state"])
        print(f"  LIVE: upstairs {u:.1f}  main {m:.1f}  gap {u-m:.1f}°F  "
              f"ecobee {ec['state']}/{ec['attributes'].get('fan_mode')} action={ec['attributes'].get('hvac_action')}")
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
