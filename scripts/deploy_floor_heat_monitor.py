#!/usr/bin/env python3
"""Deploy floor heat monitor (SensorLinx v0.1.56+) via HACS WebSocket + verify."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HA_HOST = os.environ.get("HA_HOST", "172.16.255.250")
HA_HTTP = f"http://{HA_HOST}:8123"
HA_WS = f"ws://{HA_HOST}:8123/api/websocket"
REPO = "redawg/hass-sensorlinx"
DOMAIN = "sensorlinx"

VERIFY_ENTITIES = [
    "sensor.sensorlinx_floor_heat_ecoflow_power_guarded",
    "sensor.sensorlinx_floor_heat_source_energy_today",
    "sensor.sensorlinx_floor_heat_ecoflow_energy_today",
    "sensor.sensorlinx_floor_heat_pumps_power",
    "binary_sensor.sensorlinx_floor_heat_ecoflow_power_stale",
]


def load_token() -> str:
    token = (os.environ.get("HA_TOKEN") or os.environ.get("API_ACCESS_TOKEN") or "").strip()
    if token:
        return token
    repos = Path(os.environ.get("REPOS_ROOT", "/home/aatom/repos"))
    path = repos / ".cursor/scripts/morning-ha-weather-forecast.py"
    spec = importlib.util.spec_from_file_location("morning_ha", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    _, token = mod.load_ha_config()
    return token


def ha_get(path: str, token: str) -> dict:
    req = urllib.request.Request(
        f"{HA_HTTP}{path}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def ha_post(path: str, token: str, data: dict | None = None) -> None:
    body = json.dumps(data or {}).encode()
    req = urllib.request.Request(
        f"{HA_HTTP}{path}",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        resp.read()


async def hacs_download_and_restart(token: str) -> bool:
    import aiohttp

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(HA_WS) as ws:
            msg = await ws.receive_json()
            if msg.get("type") != "auth_required":
                print(f"Unexpected WS message: {msg}")
                return False
            await ws.send_json({"type": "auth", "access_token": token})
            msg = await ws.receive_json()
            if msg.get("type") != "auth_ok":
                print(f"Auth failed: {msg}")
                return False

            msg_id = 1
            msg_id += 1
            await ws.send_json(
                {
                    "id": msg_id,
                    "type": "hacs/repositories/list",
                }
            )
            msg = await ws.receive_json()
            repos = msg.get("result", [])
            target = None
            for r in repos:
                if r.get("full_name") == REPO or "sensorlinx" in str(r.get("full_name", "")).lower():
                    target = r
                    break
            if not target:
                print(f"HACS repo not found in {len(repos)} entries")
                return False

            repo_id = target.get("id")
            print(f"HACS repo: {target.get('full_name')} id={repo_id}")

            msg_id += 1
            await ws.send_json(
                {
                    "id": msg_id,
                    "type": "hacs/repository/download",
                    "repository": str(repo_id),
                }
            )
            msg = await ws.receive_json()
            if not msg.get("success"):
                print(f"HACS download failed: {msg}")
                return False
            print("HACS download OK — restarting HA…")

            msg_id += 1
            await ws.send_json(
                {
                    "id": msg_id,
                    "type": "call_service",
                    "domain": "homeassistant",
                    "service": "restart",
                    "service_data": {},
                }
            )
            return True


def wait_for_ha(token: str, timeout_s: int = 180) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            ha_get("/api/", token)
            return True
        except (urllib.error.URLError, TimeoutError, urllib.error.HTTPError):
            time.sleep(5)
    return False


def find_config_entry_id(token: str) -> str:
    entries = ha_get("/api/config/config_entries/entry", token)
    for entry in entries:
        if entry.get("domain") == DOMAIN:
            return entry["entry_id"]
    raise RuntimeError(f"No config entry for {DOMAIN}")


def verify_entities(token: str) -> int:
    ok = 0
    for entity_id in VERIFY_ENTITIES:
        try:
            state = ha_get(f"/api/states/{entity_id}", token)
            print(f"  {entity_id}: {state.get('state')} {state.get('attributes', {}).get('unit_of_measurement', '')}")
            ok += 1
        except urllib.error.HTTPError as e:
            print(f"  {entity_id}: MISSING ({e.code})")
    return ok


def main() -> int:
    token = load_token()
    print(f"=== Deploy floor heat monitor → HACS @ {HA_HOST} ===\n")

    print("1. HACS download + HA restart")
    if not asyncio.run(hacs_download_and_restart(token)):
        return 1

    print("\n2. Wait for HA")
    if not wait_for_ha(token):
        print("   HA did not come back in time")
        return 1
    print("   HA is up")

    print("\n3. Reload SensorLinx config entry")
    entry_id = find_config_entry_id(token)
    ha_post(
        "/api/services/homeassistant/reload_config_entry",
        token,
        {"entry_id": entry_id},
    )
    print(f"   reload_config_entry {entry_id} OK")
    time.sleep(5)

    print("\n4. Verify floor heat entities")
    found = verify_entities(token)
    if found < len(VERIFY_ENTITIES):
        print(f"\nWARN: only {found}/{len(VERIFY_ENTITIES)} entities present")
        return 1

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
