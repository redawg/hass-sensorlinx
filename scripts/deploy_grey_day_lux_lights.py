#!/usr/bin/env python3
"""Deploy grey-day lux automations for Forest Home lights.

Interior zones use hallway indoor illuminance (InvisOutlet).
Soffit uses outdoor weather-station lux.
Manual switch on/off during active auto hours pauses auto until 5:30 AM next day.

Also runs kitchen floor deploy (same indoor sensor).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS / "lib"))
from grey_day_lux import (  # noqa: E402
    BRIGHT_HOLD_MIN,
    INDOOR_BRIGHT_LX,
    INDOOR_DARK_LX,
    INDOOR_LUX_SENSOR,
    MANUAL_OVERRIDE_RESET_TIME,
    OUTDOOR_BRIGHT_LX,
    OUTDOOR_DARK_LX,
    OUTDOOR_LUX_SENSOR,
    ambient_bright_template,
    ambient_dark_template,
    baseline_brightness_exterior,
    baseline_brightness_interior,
    idle_brightness_interior,
    lux_triggers,
    manual_override_condition_for,
    manual_override_detect_body,
    motion_all_clear_template,
    motion_brightness_exterior,
    motion_brightness_interior,
    not_manual_override_template,
)

HA_URL = os.environ.get("HA_URL", "http://172.16.255.250:8123").rstrip("/")
RESET_TIME = MANUAL_OVERRIDE_RESET_TIME
SHARED_RESET_AUTO_ID = "grey_day_lux_reset_manual_override_530am"
KITCHEN_MANUAL_SENSOR = "sensor.kitchen_floor_manual_override_date"
PACKAGE = Path(__file__).resolve().parent.parent / "packages" / "grey_day_lux_lights.yaml"


@dataclass
class LuxZone:
    zone_id: str
    light_entity: str
    label: str
    manual_sensor: str
    lux_sensor: str
    dark_below: float
    bright_above: float
    interior: bool = True
    motion_sensors: list[str] = field(default_factory=list)
    idle_when_no_motion: bool = False
    motion_idle_minutes: int = 3


ZONES: list[LuxZone] = [
    LuxZone(
        zone_id="main_hallway",
        light_entity="light.main_hallway_lights",
        label="Main Hallway",
        manual_sensor="sensor.main_hallway_manual_off_date",
        lux_sensor=INDOOR_LUX_SENSOR,
        dark_below=INDOOR_DARK_LX,
        bright_above=INDOOR_BRIGHT_LX,
        motion_sensors=[
            "binary_sensor.invisoutlet_7f6c_motion",
            "binary_sensor.main_floor_motion",
        ],
        idle_when_no_motion=True,
        motion_idle_minutes=3,
    ),
    LuxZone(
        zone_id="family_wall",
        light_entity="light.family_wall",
        label="Family Wall",
        manual_sensor="sensor.family_wall_manual_off_date",
        lux_sensor=INDOOR_LUX_SENSOR,
        dark_below=INDOOR_DARK_LX,
        bright_above=INDOOR_BRIGHT_LX,
    ),
    LuxZone(
        zone_id="soffit",
        light_entity="light.soffit_lights",
        label="Soffit",
        manual_sensor="sensor.soffit_lights_manual_off_date",
        lux_sensor=OUTDOOR_LUX_SENSOR,
        dark_below=OUTDOOR_DARK_LX,
        bright_above=OUTDOOR_BRIGHT_LX,
        interior=False,
    ),
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
        err = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"HTTP {exc.code}: {err or exc.reason}") from exc


def zone_baseline(zone: LuxZone) -> str:
    if zone.idle_when_no_motion and zone.interior:
        return idle_brightness_interior(zone.lux_sensor)
    if zone.interior:
        return baseline_brightness_interior(zone.lux_sensor)
    return baseline_brightness_exterior(zone.lux_sensor, zone.bright_above)


def zone_motion_brightness(zone: LuxZone) -> str:
    if zone.interior:
        return motion_brightness_interior(zone.lux_sensor)
    return motion_brightness_exterior(zone.lux_sensor)


def ensure_manual_override_sensor(
    token: str, sensor: str, label: str
) -> None:
    try:
        ha_request("GET", f"/api/states/{sensor}", token)
    except RuntimeError:
        ha_request(
            "POST",
            f"/api/states/{sensor}",
            token,
            {
                "state": "none",
                "attributes": {
                    "friendly_name": f"{label} Manual Override Date",
                },
            },
        )
        print(f"  created {sensor}")


def ambient_body(zone: LuxZone) -> dict:
    lux_kind = "indoor hallway" if zone.interior else "outdoor"
    idle_note = (
        " Low idle baseline when no motion; motion automation brightens for walk-through."
        if zone.idle_when_no_motion
        else ""
    )
    return {
        "alias": f"{zone.label}: ambient lux brightness",
        "description": (
            f"Auto-adjust {zone.label.lower()} from {lux_kind} lux when dark "
            f"(<{zone.dark_below} lx or after sunset). "
            f"Manual override until {RESET_TIME} next day.{idle_note}"
        ),
        "mode": "single",
        "triggers": lux_triggers(zone.lux_sensor, zone.dark_below)
        + [
            {"trigger": "sun", "event": "sunset"},
            {"trigger": "homeassistant", "event": "start"},
        ],
        "conditions": [
            {
                "condition": "template",
                "value_template": ambient_dark_template(zone.lux_sensor, zone.dark_below),
            },
            manual_override_condition_for(zone.manual_sensor),
        ],
        "actions": [
            {"variables": {"pct": zone_baseline(zone)}},
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
                                "target": {"entity_id": zone.light_entity},
                                "data": {"transition": 15},
                            }
                        ],
                    }
                ],
                "default": [
                    {
                        "action": "light.turn_on",
                        "target": {"entity_id": zone.light_entity},
                        "data": {
                            "brightness_pct": "{{ pct | int(40) }}",
                            "transition": 15,
                        },
                    }
                ],
            },
        ],
    }


def day_off_body(zone: LuxZone) -> dict:
    return {
        "alias": f"{zone.label}: off when bright enough",
        "description": (
            f"Turn off {zone.label.lower()} when {zone.lux_sensor} "
            f">= {zone.bright_above} lx sustained (respects manual override)."
        ),
        "mode": "single",
        "triggers": [
            {"trigger": "sun", "event": "sunrise", "offset": "1:30"},
            {
                "trigger": "numeric_state",
                "entity_id": zone.lux_sensor,
                "above": zone.bright_above,
                "for": {"hours": 0, "minutes": BRIGHT_HOLD_MIN, "seconds": 0},
            },
        ],
        "conditions": [
            {
                "condition": "template",
                "value_template": ambient_bright_template(
                    zone.lux_sensor, zone.bright_above
                ),
            },
            manual_override_condition_for(zone.manual_sensor),
        ],
        "actions": [
            {
                "action": "light.turn_off",
                "target": {"entity_id": zone.light_entity},
                "data": {"transition": 30},
            }
        ],
    }


def motion_body(zone: LuxZone) -> dict:
    return {
        "alias": f"{zone.label}: motion brighten (grey day)",
        "description": (
            f"Brighten {zone.label.lower()} on motion when indoor/outdoor dark "
            "and not manually overridden today."
        ),
        "mode": "single",
        "triggers": [
            {
                "trigger": "state",
                "entity_id": zone.motion_sensors,
                "from": "off",
                "to": "on",
            },
        ],
        "conditions": [
            {
                "condition": "template",
                "value_template": ambient_dark_template(zone.lux_sensor, zone.dark_below),
            },
            manual_override_condition_for(zone.manual_sensor),
        ],
        "actions": [
            {"variables": {"pct": zone_motion_brightness(zone)}},
            {
                "action": "light.turn_on",
                "target": {"entity_id": zone.light_entity},
                "data": {
                    "brightness_pct": "{{ pct | int(70) }}",
                    "transition": 3,
                },
            }
        ],
    }


def motion_idle_body(zone: LuxZone) -> dict:
    """Dim to low idle baseline after motion clears (hallway energy saving)."""
    mins = zone.motion_idle_minutes
    return {
        "alias": f"{zone.label}: idle dim after no motion",
        "description": (
            f"Dim {zone.label.lower()} to low idle level after {mins} min with no motion "
            "on grey days and at night."
        ),
        "mode": "single",
        "triggers": [
            {
                "trigger": "template",
                "value_template": motion_all_clear_template(zone.motion_sensors),
                "for": {"hours": 0, "minutes": mins, "seconds": 0},
            },
            {"trigger": "sun", "event": "sunset"},
        ],
        "conditions": [
            {
                "condition": "template",
                "value_template": ambient_dark_template(zone.lux_sensor, zone.dark_below),
            },
            manual_override_condition_for(zone.manual_sensor),
            {
                "condition": "template",
                "value_template": motion_all_clear_template(zone.motion_sensors),
            },
        ],
        "actions": [
            {"variables": {"pct": idle_brightness_interior(zone.lux_sensor)}},
            {
                "action": "light.turn_on",
                "target": {"entity_id": zone.light_entity},
                "data": {
                    "brightness_pct": "{{ pct | int(15) }}",
                    "transition": 10,
                },
            }
        ],
    }


def all_manual_override_sensors() -> list[str]:
    return [z.manual_sensor for z in ZONES] + [KITCHEN_MANUAL_SENSOR]


def shared_reset_body() -> dict:
    return {
        "alias": "Grey day lux: reset manual override flags at 5:30 AM",
        "description": (
            "Clear manual override dates for grey-day auto lights at 5:30 AM."
        ),
        "mode": "single",
        "triggers": [{"trigger": "time", "at": RESET_TIME}],
        "actions": [
            {
                "action": "homeassistant.set_state",
                "data": {"entity_id": sensor, "state": "none"},
            }
            for sensor in all_manual_override_sensors()
        ],
    }


def upsert_automation(token: str, auto_id: str, body: dict) -> None:
    status, _ = ha_request(
        "POST", f"/api/config/automation/config/{auto_id}", token, body
    )
    print(f"  upsert {auto_id}: HTTP {status} — {body.get('alias')}")


def deploy_zone(token: str, zone: LuxZone) -> None:
    print(
        f"\n--- {zone.label} ({zone.light_entity}) "
        f"sensor={zone.lux_sensor} dark<{zone.dark_below} ---"
    )
    ensure_manual_override_sensor(token, zone.manual_sensor, zone.label)
    upsert_automation(token, f"{zone.zone_id}_lux_ambient_brightness", ambient_body(zone))
    upsert_automation(
        token,
        f"{zone.zone_id}_detect_manual_override",
        manual_override_detect_body(
            label=zone.label,
            entity_ids=[zone.light_entity],
            lux_sensor=zone.lux_sensor,
            dark_below=zone.dark_below,
            manual_sensor=zone.manual_sensor,
        ),
    )
    upsert_automation(token, f"{zone.zone_id}_lux_day_off", day_off_body(zone))
    if zone.motion_sensors:
        upsert_automation(token, f"{zone.zone_id}_motion_grey_day", motion_body(zone))
    if zone.idle_when_no_motion and zone.motion_sensors:
        upsert_automation(token, f"{zone.zone_id}_motion_idle_dim", motion_idle_body(zone))


def trigger_ambient_zones(token: str) -> None:
    for zone in ZONES:
        for entity in (
            f"automation.{zone.zone_id}_lux_ambient_brightness",
            f"automation.{zone.zone_id}_ambient_lux_brightness",
        ):
            try:
                ha_request(
                    "POST",
                    "/api/services/automation/trigger",
                    token,
                    {"entity_id": entity},
                )
                print(f"  triggered {entity}")
                break
            except RuntimeError:
                continue


def deploy_kitchen(token: str) -> None:
    kitchen_script = _SCRIPTS / "deploy_kitchen_floor_lux_grey_day.py"
    print("\n=== Kitchen floor (indoor lux) ===")
    env = {**os.environ, "HA_DEPLOY_SKIP_RELOAD": "1", "HA_DEPLOY_SKIP_AMBIENT_TRIGGER": "1"}
    result = subprocess.run(
        [sys.executable, str(kitchen_script)],
        env=env,
        capture_output=True,
        text=True,
    )
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr)
        raise SystemExit(result.returncode)


def main() -> None:
    token = load_token()
    print("=== Grey-day lux lights deploy (indoor retune) ===")
    _, indoor = ha_request("GET", f"/api/states/{INDOOR_LUX_SENSOR}", token)
    _, outdoor = ha_request("GET", f"/api/states/{OUTDOOR_LUX_SENSOR}", token)
    print(f"  indoor ({INDOOR_LUX_SENSOR}): {indoor.get('state')} lx")
    print(f"  outdoor ({OUTDOOR_LUX_SENSOR}): {outdoor.get('state')} lx")
    print(f"  interior dark if < {INDOOR_DARK_LX} lx · bright if >= {INDOOR_BRIGHT_LX} lx")

    for zone in ZONES:
        deploy_zone(token, zone)

    print("\n--- Shared 5:30 AM manual-override reset ---")
    upsert_automation(token, SHARED_RESET_AUTO_ID, shared_reset_body())

    deploy_kitchen(token)

    ha_request("POST", "/api/services/automation/reload", token, {})
    print("\n  automation.reload OK")

    trigger_ambient_zones(token)
    print("  (kitchen floor ambient not auto-triggered — avoids flicker on deploy)")

    print("\n=== Done ===")


if __name__ == "__main__":
    main()
