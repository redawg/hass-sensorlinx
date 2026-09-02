#!/usr/bin/env python3
"""Patch kitchen floor LED automations — indoor hallway lux (InvisOutlet).

Anti-flicker design:
- Motion automation: motion sensors only (no lux triggers).
- Ambient: bootstrap only (sunset / HA start / first dark entry).
- Night dim: baseline after motion clears + debounced lux tier updates.
- All turn_on actions skip when brightness already within ~8%.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS / "lib"))
from grey_day_lux import (  # noqa: E402
    BRIGHT_HOLD_MIN,
    FLOOR_LED_TRANSITION_SEC,
    INDOOR_BRIGHT_LX,
    INDOOR_DARK_LX,
    INDOOR_LUX_SENSOR,
    ambient_bright_template,
    ambient_dark_template,
    baseline_brightness_interior,
    brightness_pct_change_needed,
    lux_bootstrap_triggers,
    lux_tier_triggers_debounced,
    manual_override_condition_for,
    manual_override_detect_body,
    motion_all_clear_template,
    motion_brightness_interior,
    motion_gate_indoor_template,
    strip_illuminance_triggers,
    strip_lux_triggers,
)

FLOOR_TRANSITION = FLOOR_LED_TRANSITION_SEC

HA_URL = os.environ.get("HA_URL", "http://172.16.255.250:8123").rstrip("/")
FLOOR_LABEL = "kitchen_floor_led_stirps"
FLOOR_MONITOR_LIGHT = "light.kitchen_toe_kick_group"
KITCHEN_MANUAL_SENSOR = "sensor.kitchen_floor_manual_override_date"
KITCHEN_MANUAL_ENTITIES = [
    "light.kitchen_toe_kick_group",
    "switch.kitchen_toe_kick_led_switch",
    "switch.island_toe_kick_led_switch",
]
KITCHEN_MOTION_SENSORS = [
    "binary_sensor.main_floor_motion",
    "binary_sensor.invisoutlet_7f6c_motion",
]

MOTION_AUTO_ID = "1771056631680"
NIGHT_DIM_AUTO_ID = "1771059094655"
DAY_OFF_AUTO_ID = "1771347692348"
AMBIENT_AUTO_ID = "kitchen_floor_lux_ambient_on"
MANUAL_DETECT_AUTO_ID = "kitchen_floor_detect_manual_override"
AMBIENT_ENTITY_ID = "automation.kitchen_floor_ambient_lux_brightness"


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
        err = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"HTTP {exc.code}: {err or exc.reason}") from exc


def template_dark_condition() -> dict:
    return {
        "condition": "template",
        "value_template": ambient_dark_template(INDOOR_LUX_SENSOR, INDOOR_DARK_LX),
    }


def motion_clear_condition() -> dict:
    return {
        "condition": "template",
        "value_template": motion_all_clear_template(KITCHEN_MOTION_SENSORS),
    }


def standard_conditions(*, require_motion_clear: bool = False) -> list:
    conds = [template_dark_condition(), manual_override_condition_for(KITCHEN_MANUAL_SENSOR)]
    if require_motion_clear:
        conds.append(motion_clear_condition())
    return conds


def fix_dark_conditions(conditions: list) -> list:
    out: list = []
    for cond in conditions:
        if cond.get("condition") == "sun":
            out.append(template_dark_condition())
        elif cond.get("condition") == "template" and (
            "illuminance" in cond.get("value_template", "")
            or "sun.sun" in cond.get("value_template", "")
        ):
            out.append(template_dark_condition())
        elif (
            cond.get("condition") == "numeric_state"
            and cond.get("entity_id") == "light.island_toe_kick_1"
        ):
            out.append(
                {
                    "condition": "template",
                    "value_template": motion_gate_indoor_template(
                        INDOOR_LUX_SENSOR, INDOOR_DARK_LX
                    ),
                }
            )
        else:
            out.append(cond)
    return out


def ensure_manual_sensor(token: str) -> None:
    try:
        ha_request("GET", f"/api/states/{KITCHEN_MANUAL_SENSOR}", token)
    except RuntimeError:
        ha_request(
            "POST",
            f"/api/states/{KITCHEN_MANUAL_SENSOR}",
            token,
            {
                "state": "none",
                "attributes": {
                    "friendly_name": "Kitchen Floor Manual Override Date",
                },
            },
        )
        print(f"  created {KITCHEN_MANUAL_SENSOR}")


def patch_motion_automation(cfg: dict) -> dict:
    cfg = dict(cfg)
    cfg["alias"] = "Kitchen LED after Sunset Night lights"
    cfg["mode"] = "restart"
    cfg["description"] = (
        "Brighten kitchen floor LEDs on motion only when indoor dark "
        f"(<{INDOOR_DARK_LX} lx hallway or after sunset). No lux triggers."
    )
    cfg["triggers"] = strip_illuminance_triggers(
        strip_lux_triggers(list(cfg.get("triggers", [])), INDOOR_LUX_SENSOR)
    )
    cfg["conditions"] = fix_dark_conditions(cfg.get("conditions", []))
    cfg["conditions"].append(manual_override_condition_for(KITCHEN_MANUAL_SENSOR))
    cfg["actions"] = [
        {
            "variables": {
                "pct": motion_brightness_interior(INDOOR_LUX_SENSOR, floor_accent=True)
            }
        },
        brightness_pct_change_needed(FLOOR_MONITOR_LIGHT),
        {
            "action": "light.turn_on",
            "target": {"label_id": FLOOR_LABEL},
            "data": {
                "brightness_pct": "{{ pct | int(50) }}",
                "transition": FLOOR_TRANSITION,
            },
        },
    ]
    return cfg


def patch_night_dim_automation(cfg: dict) -> dict:
    cfg = dict(cfg)
    cfg["mode"] = "single"
    cfg["description"] = (
        "Kitchen floor LED baseline after motion clears or debounced lux tier change. "
        f"Sensor: {INDOOR_LUX_SENSOR}."
    )
    cfg["triggers"] = strip_lux_triggers(list(cfg.get("triggers", [])), INDOOR_LUX_SENSOR)
    for t in lux_tier_triggers_debounced(INDOOR_LUX_SENSOR, debounce_seconds=60):
        if t not in cfg["triggers"]:
            cfg["triggers"].append(t)
    cfg["conditions"] = fix_dark_conditions(cfg.get("conditions", []))
    cfg["conditions"].extend(
        [
            manual_override_condition_for(KITCHEN_MANUAL_SENSOR),
            motion_clear_condition(),
        ]
    )
    cfg["actions"] = [
        {
            "variables": {
                "pct": baseline_brightness_interior(INDOOR_LUX_SENSOR, floor_accent=True)
            }
        },
        brightness_pct_change_needed(FLOOR_MONITOR_LIGHT),
        {
            "action": "light.turn_on",
            "target": {"label_id": FLOOR_LABEL},
            "data": {
                "brightness_pct": "{{ pct | int(18) }}",
                "transition": FLOOR_TRANSITION,
            },
        },
    ]
    return cfg


def patch_day_off_automation(cfg: dict) -> dict:
    cfg = dict(cfg)
    cfg["mode"] = "single"
    cfg["description"] = (
        f"Turn off kitchen floor LEDs when indoor lux >= {INDOOR_BRIGHT_LX} lx sustained."
    )
    cfg["triggers"] = [
        {"trigger": "sun", "event": "sunrise", "offset": "1:30"},
        {
            "trigger": "numeric_state",
            "entity_id": INDOOR_LUX_SENSOR,
            "above": INDOOR_BRIGHT_LX,
            "for": {"hours": 0, "minutes": BRIGHT_HOLD_MIN, "seconds": 0},
        },
    ]
    cfg["conditions"] = [
        {
            "condition": "template",
            "value_template": ambient_bright_template(
                INDOOR_LUX_SENSOR, INDOOR_BRIGHT_LX
            ),
        },
        manual_override_condition_for(KITCHEN_MANUAL_SENSOR),
    ]
    cfg["actions"] = [
        {
            "action": "light.turn_on",
            "target": {"label_id": FLOOR_LABEL},
            "data": {"transition": FLOOR_TRANSITION, "brightness_pct": 0},
        }
    ]
    return cfg


def ambient_automation_body() -> dict:
    return {
        "alias": "Kitchen Floor: ambient lux brightness",
        "description": (
            f"Bootstrap kitchen floor LEDs when entering dark "
            f"(<{INDOOR_DARK_LX} lx or sunset). No per-tick lux updates — "
            "night-dim handles baseline after motion."
        ),
        "mode": "single",
        "triggers": lux_bootstrap_triggers(INDOOR_LUX_SENSOR, INDOOR_DARK_LX),
        "conditions": standard_conditions(require_motion_clear=True),
        "actions": [
            {
                "variables": {
                    "pct": baseline_brightness_interior(
                        INDOOR_LUX_SENSOR, floor_accent=True
                    )
                }
            },
            brightness_pct_change_needed(FLOOR_MONITOR_LIGHT),
            {
                "choose": [
                    {
                        "conditions": [
                            {
                                "condition": "template",
                                "value_template": "{{ pct | int(0) == 0 }}",
                            }
                        ],
                        "sequence": [
                            {
                                "action": "light.turn_off",
                                "target": {"label_id": FLOOR_LABEL},
                                "data": {"transition": FLOOR_TRANSITION},
                            }
                        ],
                    }
                ],
                "default": [
                    {
                        "action": "light.turn_on",
                        "target": {"label_id": FLOOR_LABEL},
                        "data": {
                            "brightness_pct": "{{ pct | int(18) }}",
                            "transition": FLOOR_TRANSITION,
                        },
                    }
                ],
            }
        ],
    }


def manual_detect_body() -> dict:
    return manual_override_detect_body(
        label="Kitchen Floor",
        entity_ids=KITCHEN_MANUAL_ENTITIES,
        lux_sensor=INDOOR_LUX_SENSOR,
        dark_below=INDOOR_DARK_LX,
        manual_sensor=KITCHEN_MANUAL_SENSOR,
    )


def upsert_automation(token: str, auto_id: str, body: dict) -> None:
    status, _ = ha_request(
        "POST", f"/api/config/automation/config/{auto_id}", token, body
    )
    print(f"  upsert {auto_id} ({body.get('alias', auto_id)}): HTTP {status}")


def main() -> None:
    token = load_token()
    _, indoor = ha_request("GET", f"/api/states/{INDOOR_LUX_SENSOR}", token)
    print(f"  indoor hallway lux: {indoor.get('state')} lx")

    ensure_manual_sensor(token)

    motion = ha_request("GET", f"/api/config/automation/config/{MOTION_AUTO_ID}", token)[1]
    night_dim = ha_request(
        "GET", f"/api/config/automation/config/{NIGHT_DIM_AUTO_ID}", token
    )[1]
    day_off = ha_request("GET", f"/api/config/automation/config/{DAY_OFF_AUTO_ID}", token)[1]

    upsert_automation(token, MOTION_AUTO_ID, patch_motion_automation(motion))
    upsert_automation(token, NIGHT_DIM_AUTO_ID, patch_night_dim_automation(night_dim))
    upsert_automation(token, DAY_OFF_AUTO_ID, patch_day_off_automation(day_off))
    upsert_automation(token, AMBIENT_AUTO_ID, ambient_automation_body())
    upsert_automation(token, MANUAL_DETECT_AUTO_ID, manual_detect_body())

    if not os.environ.get("HA_DEPLOY_SKIP_RELOAD"):
        ha_request("POST", "/api/services/automation/reload", token, {})
        print("  automation.reload OK")
        if not os.environ.get("HA_DEPLOY_SKIP_AMBIENT_TRIGGER"):
            try:
                ha_request(
                    "POST",
                    "/api/services/automation/trigger",
                    token,
                    {"entity_id": AMBIENT_ENTITY_ID},
                )
                print(f"  triggered {AMBIENT_ENTITY_ID}")
            except RuntimeError as exc:
                print(f"  ambient trigger skipped: {exc}")


if __name__ == "__main__":
    main()
