#!/usr/bin/env python3
"""Patch offMode=1 for Laundry, Living Room, Main Area on SensorLinx cloud."""
import asyncio
import os
import logging
from pysensorlinx import Sensorlinx

logging.disable(logging.CRITICAL)

DEVICES = ["ATHM-8008", "ATHM-7979", "ATHM-8014"]  # Laundry, Living Room, Main Area

async def main():
    api = Sensorlinx()
    await api.login(os.environ["SENSORLINX_EMAIL"], os.environ["SENSORLINX_PASSWORD"])
    buildings = await api.get_buildings()
    bid = buildings[0]["_id"]

    for dev_id in DEVICES:
        await api.patch_device(bid, dev_id, offMode=1)
        print(f"Patched {dev_id} offMode=1")

    await api.close()

asyncio.run(main())
