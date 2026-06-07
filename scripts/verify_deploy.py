#!/usr/bin/env python3
"""Verify what climate.py is deployed on Forest Home via HA WebSocket."""
import asyncio
import json
import aiohttp

BASE = "ws://172.16.255.250:8123/api/websocket"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"


async def main():
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(BASE) as ws:
            msg = await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": TOKEN})
            msg = await ws.receive_json()
            if msg.get("type") != "auth_ok":
                print(f"Auth failed: {msg}")
                return

            # Check HACS repo status
            await ws.send_json({
                "id": 2,
                "type": "hacs/repositories/list",
            })
            msg = await ws.receive_json()
            repos = msg.get("result", [])
            for r in repos:
                if "sensorlinx" in str(r.get("full_name", "")).lower():
                    print(f"HACS repo: {json.dumps(r, indent=2)[:800]}")
                    break
            else:
                print(f"sensorlinx not found in {len(repos)} HACS repos")

            # Check integration status
            await ws.send_json({
                "id": 3,
                "type": "config_entries/get",
            })
            msg = await ws.receive_json()
            entries = msg.get("result", [])
            for e in entries:
                if e.get("domain") == "sensorlinx":
                    print(f"\nConfig entry: {json.dumps(e, indent=2)[:500]}")
                    break

            # Try reading the climate.py to check for offMode
            await ws.send_json({
                "id": 4,
                "type": "execute_script",
                "sequence": [{"service": "shell_command.check_file"}],
            })


asyncio.run(main())
