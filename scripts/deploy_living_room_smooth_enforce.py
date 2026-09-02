#!/usr/bin/env python3
"""Deploy Living Room 3s smooth-transition enforcement automation on Forest Home."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

HA_URL = os.environ.get("HA_URL", "http://172.16.255.250:8123").rstrip("/")
AUTO_ID = "living_room_smooth_transition_enforce"
SMOOTH_ON = "number.living_room_smooth_on"
SMOOTH_OFF = "number.living_room_smooth_off"
TARGET_SECONDS = 3


def load_token() -> str:
    token = (os.environ.get("HA_TOKEN") or os.environ.get("API_ACCESS_TOKEN") or "").strip()
    if token:
        return token
    repos = Path(os.environ.get("REPOS_ROOT", "/home/aatom/repos"))
    sys.path.insert(0, str(repos / ".cursor/scripts"))
    import importlib.util

    path = repos / ".cursor/scripts/morning-ha-weather-forecast.py"
    spec = importlib.util.spec_from_file_location("morning_ha", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    _, token = mod.load_ha_config()
    return token


def ha_request(method: str, path: str, token: str, data: dict | None = None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(
        f"{HA_URL}{path}",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        err = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"HTTP {exc.code}: {err or exc.reason}") from exc


def automation_body() -> dict:
    drift_condition = (
        "{{ states('number.living_room_smooth_on') not in ['unavailable', 'unknown', 'none'] "
        "and states('number.living_room_smooth_off') not in ['unavailable', 'unknown', 'none'] "
        "and (states('number.living_room_smooth_on') | float(0) != 3 "
        "or states('number.living_room_smooth_off') | float(0) != 3) }}"
    )
    return {
        "alias": "Living Room: enforce 3s smooth on/off",
        "description": (
            "Re-applies 3 second smooth on/off when the Tapo S505D bulb reports "
            "a different fade duration (commonly 30s from firmware or Tapo app sync)."
        ),
        "mode": "single",
        "triggers": [
            {"platform": "state", "entity_id": [SMOOTH_ON, SMOOTH_OFF]},
            {"platform": "homeassistant", "event": "start"},
        ],
        "conditions": [
            {"condition": "template", "value_template": drift_condition},
        ],
        "actions": [
            {
                "action": "number.set_value",
                "target": {"entity_id": [SMOOTH_ON, SMOOTH_OFF]},
                "data": {"value": TARGET_SECONDS},
            }
        ],
    }


def read_smooth(token: str) -> tuple[str, str]:
    _, on = ha_request("GET", f"/api/states/{SMOOTH_ON}", token)
    _, off = ha_request("GET", f"/api/states/{SMOOTH_OFF}", token)
    return on.get("state", "?"), off.get("state", "?")


def main() -> None:
    token = load_token()
    before_on, before_off = read_smooth(token)
    print(f"before: smooth_on={before_on} smooth_off={before_off}")

    status, result = ha_request(
        "POST", f"/api/config/automation/config/{AUTO_ID}", token, automation_body()
    )
    print(f"upsert automation {AUTO_ID}: HTTP {status} {result}")

    ha_request("POST", "/api/services/automation/reload", token, {})
    print("automation.reload OK")

    # Ensure current values are 3s (in case already drifted).
    on, off = read_smooth(token)
    if on not in ("unavailable", "unknown", "3") or off not in ("unavailable", "unknown", "3"):
        ha_request(
            "POST",
            "/api/services/number/set_value",
            token,
            {"entity_id": [SMOOTH_ON, SMOOTH_OFF], "value": TARGET_SECONDS},
        )
        print("applied immediate correction to 3s")

    after_on, after_off = read_smooth(token)
    print(f"after: smooth_on={after_on} smooth_off={after_off}")


if __name__ == "__main__":
    main()
