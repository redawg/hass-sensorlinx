#!/usr/bin/env python3
"""Deploy open-contact / thermostat-off → radiant floor disable on Forest Home.

Uses HA REST /api/config/automation/config/{id} (works when WS config APIs are unavailable).
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

HA_URL = os.environ.get("HA_URL", "http://172.16.255.250:8123").rstrip("/")
AUTO_ID = "sensorlinx_disable_floor_on_openings"

CONTACTS = [
    "binary_sensor.wldj_contact",
    "binary_sensor.wpxp_contact",
    "binary_sensor.s92n_contact",
    "binary_sensor.wk2n_contact",
    "binary_sensor.basement_door_contact",
    "binary_sensor.tb56_contact",  # Ecobee front door
]


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
    open_tpl = (
        "{{ ["
        + ", ".join(f"states('{c}')" for c in CONTACTS)
        + "] | select('eq', 'on') | list | length > 0 }}"
    )
    closed_tpl = (
        "{{ ["
        + ", ".join(f"states('{c}')" for c in CONTACTS)
        + "] | select('eq', 'on') | list | length == 0 }}"
    )
    return {
        "alias": "SensorLinx: Disable floor when openings open or thermostat off",
        "description": (
            "Turns off switch.radiant_floor_contoller when any ecobee door/window "
            "contact is open, or when climate.main_floor is off. Restores when "
            "thermostat is active and all contacts are closed."
        ),
        "mode": "single",
        "triggers": [
            {"platform": "state", "entity_id": CONTACTS + ["climate.main_floor"]},
        ],
        "conditions": [],
        "actions": [
            {
                "choose": [
                    {
                        "conditions": [
                            {
                                "condition": "or",
                                "conditions": [
                                    {
                                        "condition": "state",
                                        "entity_id": "climate.main_floor",
                                        "state": "off",
                                    },
                                    {
                                        "condition": "template",
                                        "value_template": open_tpl,
                                    },
                                ],
                            },
                            {
                                "condition": "state",
                                "entity_id": "switch.radiant_floor_contoller",
                                "state": "on",
                            },
                        ],
                        "sequence": [
                            {
                                "action": "switch.turn_off",
                                "target": {"entity_id": "switch.radiant_floor_contoller"},
                            }
                        ],
                    },
                    {
                        "conditions": [
                            {
                                "condition": "template",
                                "value_template": (
                                    "{{ states('climate.main_floor') in "
                                    "['heat','cool','heat_cool','auto'] }}"
                                ),
                            },
                            {
                                "condition": "template",
                                "value_template": closed_tpl,
                            },
                            {
                                "condition": "state",
                                "entity_id": "switch.radiant_floor_contoller",
                                "state": "off",
                            },
                        ],
                        "sequence": [
                            {
                                "action": "switch.turn_on",
                                "target": {"entity_id": "switch.radiant_floor_contoller"},
                            }
                        ],
                    },
                ]
            }
        ],
    }


def apply_now(token: str) -> None:
    def state(eid: str) -> str | None:
        try:
            _, data = ha_request("GET", f"/api/states/{eid}", token)
            return data.get("state")
        except Exception:
            return None

    thermostat = state("climate.main_floor")
    radiant = state("switch.radiant_floor_contoller")
    open_contacts = [c for c in CONTACTS if state(c) == "on"]
    should_off = thermostat == "off" or bool(open_contacts)
    print(
        f"apply_now: thermostat={thermostat} radiant={radiant} "
        f"open={open_contacts} should_off={should_off}"
    )
    if should_off and radiant == "on":
        ha_request(
            "POST",
            "/api/services/switch/turn_off",
            token,
            {"entity_id": "switch.radiant_floor_contoller"},
        )
        print("turned radiant floor controller OFF")
    elif (not should_off) and radiant == "off" and thermostat in (
        "heat",
        "cool",
        "heat_cool",
        "auto",
    ):
        ha_request(
            "POST",
            "/api/services/switch/turn_on",
            token,
            {"entity_id": "switch.radiant_floor_contoller"},
        )
        print("turned radiant floor controller ON")


def main() -> None:
    token = load_token()
    body = automation_body()
    status, result = ha_request(
        "POST", f"/api/config/automation/config/{AUTO_ID}", token, body
    )
    print(f"upsert automation {AUTO_ID}: HTTP {status} {result}")
    ha_request("POST", "/api/services/automation/reload", token, {})
    print("automation.reload OK")
    apply_now(token)


if __name__ == "__main__":
    main()
