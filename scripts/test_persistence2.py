#!/usr/bin/env python3
"""Test persistence with more detail - check integration state and try options flow."""
import requests
import time
import json
import asyncio
import aiohttp

BASE_HTTP = "http://172.16.255.250:8123"
BASE_WS = "ws://172.16.255.250:8123/api/websocket"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJlNDM2OWE2YTVmYjk0ODIzOTFmNDA3OTdiM2NiZmFiYyIsImlhdCI6MTc3ODU0NzMyNCwiZXhwIjoyMDkzOTA3MzI0fQ.Kh_2jOBqDJnevRqvrEGnZ1E849jrRK0_-SOdr6lr2Fs"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
ENTRY_ID = "01KTFBBKK8DSSRWFADF208M6TY"


def get_entry():
    r = requests.get(f"{BASE_HTTP}/api/config/config_entries/entry", headers=HEADERS, timeout=10)
    for e in r.json():
        if e.get("domain") == "sensorlinx":
            return e
    return None


async def test_options_flow_via_ws():
    """Start and complete an options flow via WebSocket to test proper persistence."""
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(BASE_WS) as ws:
            msg = await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": TOKEN})
            msg = await ws.receive_json()
            print(f"  Auth: {msg['type']}")

            # Step 1: Start the options flow
            await ws.send_json({
                "id": 10,
                "type": "config_entries/options/flow",
                "handler": ENTRY_ID,
            })
            msg = await ws.receive_json()
            print(f"\n  Start flow response:")
            if not msg.get("success"):
                print(f"    ERROR: {msg.get('error')}")
                return
            flow_id = msg["result"]["flow_id"]
            step = msg["result"]["step_id"]
            print(f"    flow_id: {flow_id}")
            print(f"    step_id: {step}")
            print(f"    schema keys: {[s.get('name','?') for s in msg['result'].get('data_schema', [])]}")

            # Step 2: Submit init step with radiant floor switch
            await ws.send_json({
                "id": 11,
                "type": "config_entries/options/flow",
                "flow_id": flow_id,
                "handler": ENTRY_ID,
            })
            # Actually need to use the proper submit endpoint
            await ws.send_json({
                "id": 12,
                "type": "options_flow/submit",
                "flow_id": flow_id,
                "user_input": {
                    "hot_water_switch_entity_id": "",
                    "radiant_floor_switch_entity_id": "switch.radiant_floor",
                },
            })
            msg = await ws.receive_json()
            # Might get response to id 11 first
            if msg["id"] == 11:
                msg = await ws.receive_json()
            print(f"\n  Submit step 1 (init):")
            print(f"    success: {msg.get('success')}")
            if msg.get("success"):
                result = msg.get("result", {})
                step = result.get("step_id", "done")
                print(f"    next step: {step}")
                if step == "heating_source":
                    # Submit step 2 with empty (skip)
                    await ws.send_json({
                        "id": 13,
                        "type": "options_flow/submit",
                        "flow_id": flow_id,
                        "user_input": {},
                    })
                    msg = await ws.receive_json()
                    print(f"\n  Submit step 2 (heating_source):")
                    print(f"    success: {msg.get('success')}")
                    if msg.get("success"):
                        result = msg.get("result", {})
                        step = result.get("step_id", "done")
                        print(f"    next step: {step}")
                        if step == "zone_valves":
                            # Submit zone valves
                            await ws.send_json({
                                "id": 14,
                                "type": "options_flow/submit",
                                "flow_id": flow_id,
                                "user_input": {
                                    "zone_valves_laundry": 2,
                                    "zone_valves_living_room": 3,
                                    "zone_valves_main_area": 2,
                                    "zone_valves_main_office": 1,
                                },
                            })
                            msg = await ws.receive_json()
                            print(f"\n  Submit step 3 (zone_valves):")
                            print(f"    success: {msg.get('success')}")
                            if msg.get("success"):
                                result = msg.get("result", {})
                                print(f"    type: {result.get('type')}")
                                print(f"    Flow completed!")
                        elif step == "create_entry" or result.get("type") == "create_entry":
                            print(f"    Flow completed (no zone step)!")
            else:
                print(f"    ERROR: {msg.get('error')}")


# Step 1: Check current state
print("=" * 60)
print("STEP 1: Current config entry state")
print("=" * 60)
entry = get_entry()
if entry:
    print(f"  state: {entry['state']}")
    print(f"  options: {json.dumps(entry.get('options', {}))}")
else:
    print("  ERROR: No sensorlinx entry found!")
    exit(1)

# Step 2: Try via options flow (WebSocket)
print("\n" + "=" * 60)
print("STEP 2: Testing options flow via WebSocket")
print("=" * 60)
asyncio.run(test_options_flow_via_ws())

# Step 3: Wait for reload and check
print("\n" + "=" * 60)
print("STEP 3: Waiting 20s for reload...")
print("=" * 60)
time.sleep(20)

entry = get_entry()
if entry:
    print(f"  state: {entry['state']}")
    opts = entry.get("options", {})
    print(f"  options ({len(opts)} keys): {json.dumps(opts, indent=2)}")
    if opts:
        print("\n  SUCCESS! Options flow persistence works!")
    else:
        print("\n  STILL EMPTY - options flow also not persisting!")
