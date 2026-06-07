#!/usr/bin/env python3
"""Restart Home Assistant via WebSocket."""
import asyncio
import aiohttp

BASE = "ws://172.16.255.250:8123/api/websocket"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"


async def main():
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(BASE) as ws:
            await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": TOKEN})
            msg = await ws.receive_json()
            if msg.get("type") != "auth_ok":
                print(f"Auth failed: {msg}")
                return
            await ws.send_json({
                "id": 2,
                "type": "call_service",
                "domain": "homeassistant",
                "service": "restart",
                "service_data": {},
            })
            print("Restart command sent.")

asyncio.run(main())
