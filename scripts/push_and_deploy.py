#!/usr/bin/env python3
"""Push outdoor_reset.py to GitHub and deploy via HACS."""
import pathlib
import json
import asyncio
import aiohttp
import requests

# --- Push to GitHub ---
GITHUB_TOKEN_FILE = None  # We'll use the MCP approach via requests

# Read the file
file_path = pathlib.Path(r"C:\Users\andre\Projects\hbx-sensorlinx-ha\custom_components\sensorlinx\outdoor_reset.py")
content = file_path.read_text(encoding="utf-8")
print(f"Read {len(content)} chars from outdoor_reset.py")

# --- Deploy via HACS WebSocket ---
BASE_WS = "ws://172.16.255.250:8123/api/websocket"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"


async def deploy():
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(BASE_WS) as ws:
            msg = await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": TOKEN})
            msg = await ws.receive_json()
            if msg.get("type") != "auth_ok":
                print(f"Auth failed: {msg}")
                return

            msg_id = 1

            # Download latest from HACS
            msg_id += 1
            await ws.send_json({
                "id": msg_id,
                "type": "hacs/repository/download",
                "repository": "1261517229",
            })
            msg = await ws.receive_json()
            print(f"HACS download: success={msg.get('success')}")
            if not msg.get("success"):
                print(f"  error: {msg.get('error')}")
                return

            # Restart HA
            msg_id += 1
            await ws.send_json({
                "id": msg_id,
                "type": "call_service",
                "domain": "homeassistant",
                "service": "restart",
                "service_data": {},
            })
            print("HA restart sent")


asyncio.run(deploy())
