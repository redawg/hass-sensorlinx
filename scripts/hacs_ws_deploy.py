#!/usr/bin/env python3
"""Deploy via HACS using WebSocket API (HACS 2.0+)."""
import asyncio
import json
import aiohttp

BASE = "ws://172.16.255.250:8123/api/websocket"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
REPO = "redawg/hbx-sensorlinx-ha"


async def main():
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(BASE) as ws:
            # Wait for auth_required
            msg = await ws.receive_json()
            print(f"1. {msg.get('type')}")

            # Authenticate
            await ws.send_json({"type": "auth", "access_token": TOKEN})
            msg = await ws.receive_json()
            print(f"2. auth: {msg.get('type')}")
            if msg.get("type") != "auth_ok":
                print(f"   Auth failed: {msg}")
                return

            msg_id = 1

            # Step 1: Add custom repository
            msg_id += 1
            await ws.send_json({
                "id": msg_id,
                "type": "hacs/repositories/add",
                "repository": REPO,
                "category": "integration",
            })
            msg = await ws.receive_json()
            print(f"3. add repo: success={msg.get('success')} result={str(msg.get('result',''))[:200]}")
            if not msg.get("success"):
                print(f"   error: {msg.get('error')}")

            # Step 2: List repos to find the ID
            msg_id += 1
            await ws.send_json({
                "id": msg_id,
                "type": "hacs/repositories/list",
            })
            msg = await ws.receive_json()
            repos = msg.get("result", [])
            target = None
            for r in repos:
                if r.get("full_name") == REPO or REPO in str(r.get("full_name", "")):
                    target = r
                    break
            if target:
                print(f"4. found repo: id={target.get('id')} full_name={target.get('full_name')} installed={target.get('installed')}")
            else:
                print(f"4. repo not found in {len(repos)} repos")
                # Try searching
                for r in repos:
                    if "sensorlinx" in str(r).lower():
                        target = r
                        print(f"   found via search: {r.get('full_name')} id={r.get('id')}")
                        break

            # Step 3: Download
            if target:
                repo_id = target.get("id")
                msg_id += 1
                await ws.send_json({
                    "id": msg_id,
                    "type": "hacs/repository/download",
                    "repository": str(repo_id),
                })
                msg = await ws.receive_json()
                print(f"5. download: success={msg.get('success')}")
                if not msg.get("success"):
                    print(f"   error: {msg.get('error')}")
                else:
                    print("   Downloaded! Restarting HA...")
                    msg_id += 1
                    await ws.send_json({
                        "id": msg_id,
                        "type": "call_service",
                        "domain": "homeassistant",
                        "service": "restart",
                        "service_data": {},
                    })
                    print("6. restart sent")


asyncio.run(main())
