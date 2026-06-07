#!/usr/bin/env python3
"""Check SensorLinx cloud for current thermostat targets (raw JSON)."""
import asyncio
import json
import os
import logging
from pysensorlinx import Sensorlinx

logging.disable(logging.CRITICAL)

async def check():
    api = Sensorlinx()
    await api.login(os.environ["SENSORLINX_EMAIL"], os.environ["SENSORLINX_PASSWORD"])
    buildings = await api.get_buildings()
    bid = buildings[0]["_id"]
    devices = await api.get_devices(bid)
    for d in devices:
        dtype = d.get("deviceType", "")
        name = d.get("name", "?")
        if dtype == "THM":
            fields = {
                "cngOvr": d.get("cngOvr"),
                "offMode": d.get("offMode"),
                "mode": d.get("mode"),
                "away": d.get("away"),
                "rmT": d.get("rmT"),
                "target": d.get("target"),
                "changeSource": d.get("changeSource"),
                "connected": d.get("connected"),
                "connectedAt": d.get("connectedAt"),
            }
            co = d.get("changeover", [])
            active = [x.get("key") for x in co if isinstance(x, dict) and x.get("activated")]
            fields["changeover_active"] = active
            print(f"[{name}] {json.dumps(fields, default=str)}")
        elif dtype == "ZON":
            print(f"[{name}] type=ZON")
    await api.close()

asyncio.run(check())
