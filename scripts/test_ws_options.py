#!/usr/bin/env python3
"""Test options persistence via WebSocket config entry update."""
import asyncio
import json
import aiohttp

BASE = "ws://172.16.255.250:8123/api/websocket"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
ENTRY_ID = "01KTFBBKK8DSSRWFADF208M6TY"


async def main():
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(BASE) as ws:
            # Auth
            msg = await ws.receive_json()
            print(f"1. {msg['type']}")
            await ws.send_json({"type": "auth", "access_token": TOKEN})
            msg = await ws.receive_json()
            print(f"2. {msg['type']}")

            # Try updating config entry options directly via WS
            await ws.send_json({
                "id": 3,
                "type": "config_entries/update",
                "entry_id": ENTRY_ID,
                "options": {
                    "radiant_floor_switch_entity_id": "switch.test_persistence",
                    "zone_valves_laundry": 2,
                    "zone_valves_living_room": 3,
                    "zone_valves_main_area": 2,
                    "zone_valves_main_office": 1,
                    "supply_temp_sensor": "sensor.main_water_heater_outlet_temperature",
                    "return_temp_sensor": "sensor.main_water_heater_inlet_temperature",
                    "flow_rate_sensor": "sensor.main_water_heater_flow_rate",
                },
            })
            msg = await ws.receive_json()
            print(f"3. config_entries/update response: {json.dumps(msg, indent=2)}")

            # Now read back the entry
            await ws.send_json({
                "id": 4,
                "type": "config_entries/get_single",
                "entry_id": ENTRY_ID,
            })
            msg = await ws.receive_json()
            print(f"\n4. Read back entry:")
            if msg.get("success"):
                entry = msg.get("result", {})
                print(f"   options: {json.dumps(entry.get('options', {}), indent=2)}")
            else:
                print(f"   Error: {msg}")

asyncio.run(main())
