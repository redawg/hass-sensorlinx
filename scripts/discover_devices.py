#!/usr/bin/env python3
"""List buildings and devices from your SensorLinx account.

Usage:
  pip install pysensorlinx
  python scripts/discover_devices.py

Environment variables:
  SENSORLINX_EMAIL
  SENSORLINX_PASSWORD
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from pysensorlinx import Sensorlinx, device_for


async def main() -> int:
    email = os.environ.get("SENSORLINX_EMAIL")
    password = os.environ.get("SENSORLINX_PASSWORD")
    if not email or not password:
        print(
            "Set SENSORLINX_EMAIL and SENSORLINX_PASSWORD environment variables.",
            file=sys.stderr,
        )
        return 1

    api = Sensorlinx()
    try:
        await api.login(email, password)
        profile = await api.get_profile()
        buildings = await api.get_buildings()
        if isinstance(buildings, dict):
            buildings = [buildings]

        print(f"Logged in as: {profile.get('email') if profile else email}\n")

        for building in buildings:
            building_id = building.get("_id")
            building_name = building.get("name") or building_id
            print(f"Building: {building_name} ({building_id})")

            devices = await api.get_devices(building_id)
            if not isinstance(devices, list):
                print("  (no devices)")
                continue

            for raw in devices:
                device_id = raw.get("syncCode") or raw.get("id") or raw.get("_id")
                name = raw.get("name") or device_id
                dtype = raw.get("deviceType") or "unknown"
                helper = device_for(api, building_id, raw)

                print(f"  - {name}")
                print(f"      id: {device_id}")
                print(f"      type: {dtype}")

                if dtype == "THM":
                    room = await helper.get_room_temperature(raw)
                    floor = await helper.get_floor_temperature(raw)
                    target = await helper.get_target_temperature(raw)
                    mode = await helper.get_hvac_mode(raw)
                    away = await helper.get_away_mode(raw)
                    print(f"      room: {room}")
                    print(f"      floor: {floor}")
                    print(f"      target: {target}")
                    print(f"      mode: {mode}")
                    print(f"      away: {away.get('activated')}")
                elif dtype == "ECO":
                    state = await helper.get_system_state(raw)
                    print(
                        "      system_state keys:",
                        ", ".join(sorted(state.keys())),
                    )
                elif dtype == "ZON":
                    relays = await helper.get_relays(raw)
                    active = sum(1 for r in relays if r)
                    linked = await helper.get_thermostat_sync_codes(raw)
                    aux = await helper.get_aux_setpoint(raw)
                    print(f"      active relays: {active}/{len(relays)}")
                    print(f"      linked THMs: {', '.join(linked) or '(none)'}")
                    if isinstance(aux, dict) and aux.get("value") is not None:
                        print(f"      aux setpoint: {aux['value']} F")

                if os.environ.get("SENSORLINX_DUMP_JSON"):
                    print(
                        "      raw:",
                        json.dumps(raw, indent=2, default=str)[:500],
                        "...",
                    )
            print()
    finally:
        await api.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
