#!/usr/bin/env python3
"""Patch all THMs back to heat mode (cngOvr=1, offMode=0)."""
import asyncio
import os
import logging
from pysensorlinx import Sensorlinx

logging.disable(logging.CRITICAL)

DEVICES = ["ATHM-8008", "ATHM-7979", "ATHM-8014", "ATHM-7969"]

async def main():
    api = Sensorlinx()
    await api.login(os.environ["SENSORLINX_EMAIL"], os.environ["SENSORLINX_PASSWORD"])
    buildings = await api.get_buildings()
    bid = buildings[0]["_id"]

    for dev_id in DEVICES:
        await api.patch_device(bid, dev_id, cngOvr=1, offMode=0)
        print(f"Patched {dev_id} cngOvr=1 offMode=0 (heat)")

    await api.close()

asyncio.run(main())
