"""Grey-day / indoor-dark lux templates for Forest Home HA automations."""
from __future__ import annotations

# Interior reference — main hallway InvisOutlet (proxy for kitchen / family / hallway)
INDOOR_LUX_SENSOR = "sensor.invisoutlet_7f6c_illuminance"
OUTDOOR_LUX_SENSOR = "sensor.outside_front_quail_creek_ames_lake_279th_ct_ne_illuminance"

INDOOR_DARK_LX = 150  # below → room feels dark, enable auto lights
INDOOR_BRIGHT_LX = 250  # above sustained → bright enough, auto off
OUTDOOR_DARK_LX = 7500
OUTDOOR_BRIGHT_LX = 7500

BRIGHT_HOLD_MIN = 10
MANUAL_OVERRIDE_RESET_TIME = "05:30:00"
FLOOR_LED_TRANSITION_SEC = 5  # kitchen toe-kick strips — max fade up/down


def not_manual_override_template(sensor: str) -> str:
    """True when auto may run (no manual override recorded for today)."""
    return (
        "{% set d = states('" + sensor + "') %}"
        "{{ d in ['unknown','unavailable','none',''] or d != now().date() | string }}"
    )


def is_manual_trigger_context_template() -> str:
    """Physical switch / local control — not an automation or UI user action."""
    return (
        "{{ trigger.context.parent_id is none "
        "and trigger.context.user_id is none }}"
    )


def manual_override_detect_body(
    *,
    label: str,
    entity_ids: list[str],
    lux_sensor: str,
    dark_below: float,
    manual_sensor: str,
    auto_id: str | None = None,
) -> dict:
    """Record manual on or off during active auto hours; blocks auto until 5:30 AM."""
    triggers = []
    for eid in entity_ids:
        triggers.append(
            {"trigger": "state", "entity_id": eid, "from": "on", "to": "off"}
        )
        triggers.append(
            {"trigger": "state", "entity_id": eid, "from": "off", "to": "on"}
        )
    body = {
        "alias": f"{label}: record manual override (grey day)",
        "description": (
            f"Record manual switch on/off for {label.lower()} while auto is active; "
            f"pauses all auto until {MANUAL_OVERRIDE_RESET_TIME} next day."
        ),
        "mode": "single",
        "triggers": triggers,
        "conditions": [
            {
                "condition": "template",
                "value_template": ambient_dark_template(lux_sensor, dark_below),
            },
            {
                "condition": "template",
                "value_template": is_manual_trigger_context_template(),
            },
        ],
        "actions": [
            {
                "action": "homeassistant.set_state",
                "data": {
                    "entity_id": manual_sensor,
                    "state": "{{ now().date() }}",
                },
            }
        ],
    }
    return body


def manual_override_condition_for(manual_sensor: str) -> dict:
    return {
        "condition": "template",
        "value_template": not_manual_override_template(manual_sensor),
    }


def ambient_dark_template(lux_sensor: str, dark_below: float) -> str:
    return (
        "{{ is_state('sun.sun', 'below_horizon') "
        f"or (states('{lux_sensor}') | float(99999) < {dark_below}) "
        "}}"
    )


def ambient_bright_template(lux_sensor: str, bright_above: float) -> str:
    return f"{{{{ states('{lux_sensor}') | float(0) >= {bright_above} }}}}"


def lux_triggers(lux_sensor: str, dark_below: float) -> list:
    return [
        {"trigger": "state", "entity_id": lux_sensor},
        {
            "trigger": "numeric_state",
            "entity_id": lux_sensor,
            "below": dark_below,
        },
    ]


def lux_bootstrap_triggers(lux_sensor: str, dark_below: float) -> list:
    """Enter dark mode — no per-tick lux state trigger (avoids sensor jitter flicker)."""
    return [
        {"trigger": "sun", "event": "sunset"},
        {"trigger": "homeassistant", "event": "start"},
        {
            "trigger": "numeric_state",
            "entity_id": lux_sensor,
            "below": dark_below,
            "for": {"hours": 0, "minutes": 2, "seconds": 0},
        },
    ]


def lux_tier_triggers_debounced(
    lux_sensor: str, *, debounce_seconds: int = 60
) -> list:
    """Slow lux tier updates when idle — debounced state change only."""
    return [
        {
            "trigger": "state",
            "entity_id": lux_sensor,
            "for": {"hours": 0, "minutes": 0, "seconds": debounce_seconds},
        },
    ]


def strip_illuminance_triggers(triggers: list) -> list:
    """Remove any illuminance sensor triggers (motion must be motion-only)."""
    out: list = []
    for t in triggers:
        eid = t.get("entity_id")
        tr = t.get("trigger") or t.get("platform")
        if tr in ("numeric_state", "state"):
            eids = eid if isinstance(eid, list) else [eid]
            if any(x and "illuminance" in str(x) for x in eids if x):
                continue
        out.append(t)
    return out


def strip_lux_triggers(triggers: list, lux_sensor: str) -> list:
    """Remove lux sensor triggers — motion automations must not react to lux ticks."""
    out: list = []
    for t in triggers:
        eid = t.get("entity_id")
        if eid == lux_sensor:
            continue
        if isinstance(eid, list) and lux_sensor in eid:
            filtered = [x for x in eid if x != lux_sensor]
            if not filtered:
                continue
            t = {**t, "entity_id": filtered}
            eid = filtered
        tr = t.get("trigger") or t.get("platform")
        if tr in ("numeric_state", "state") and (
            eid == lux_sensor
            or (isinstance(eid, list) and lux_sensor in eid)
        ):
            continue
        out.append(t)
    return out


def brightness_pct_change_needed(
    light_entity: str, pct_var: str = "pct", *, threshold: int = 8
) -> dict:
    """Skip turn_on when brightness is already close enough (stops fade fighting)."""
    return {
        "condition": "template",
        "value_template": (
            "{% set target = " + pct_var + " | int(0) %}"
            "{% set cur = ((state_attr('"
            + light_entity
            + "', 'brightness') | int(0)) / 2.55) | int(0) %}"
            "{% if target == 0 %}"
            "{{ states('"
            + light_entity
            + "') not in ['off','unavailable','unknown'] }}"
            "{% elif states('"
            + light_entity
            + "') in ['off','unavailable','unknown'] %}"
            "true"
            "{% else %}"
            "{{ (cur - target) | abs >= " + str(threshold) + " }}"
            "{% endif %}"
        ),
    }


def idle_brightness_interior(lux_sensor: str) -> str:
    """Low baseline when no motion — hallway path lighting, energy saving."""
    return (
        "{% set lux = states('" + lux_sensor + "') | float(99999) %}"
        "{% if lux >= 300 and is_state('sun.sun', 'above_horizon') %}"
        "0"
        "{% elif is_state('sun.sun', 'below_horizon') %}"
        "15"
        "{% elif lux >= 150 %}"
        "12"
        "{% elif lux >= 80 %}"
        "15"
        "{% elif lux >= 40 %}"
        "18"
        "{% elif lux >= 15 %}"
        "20"
        "{% else %}"
        "22"
        "{% endif %}"
    )


def motion_all_clear_template(motion_sensors: list[str]) -> str:
    checks = " and ".join(f"is_state('{s}', 'off')" for s in motion_sensors)
    return "{{ " + checks + " }}"


def baseline_brightness_interior(lux_sensor: str, *, floor_accent: bool = False) -> str:
    """Lower indoor lux → higher light %. floor_accent = kitchen toe-kick strips."""
    if floor_accent:
        return (
            "{% set lux = states('" + lux_sensor + "') | float(99999) %}"
            "{% if lux >= 300 and is_state('sun.sun', 'above_horizon') %}"
            "0"
            "{% elif is_state('sun.sun', 'below_horizon') %}"
            "1"
            "{% elif lux >= 150 %}"
            "10"
            "{% elif lux >= 80 %}"
            "18"
            "{% elif lux >= 40 %}"
            "28"
            "{% elif lux >= 15 %}"
            "40"
            "{% else %}"
            "50"
            "{% endif %}"
        )
    return (
        "{% set lux = states('" + lux_sensor + "') | float(99999) %}"
        "{% if lux >= 300 and is_state('sun.sun', 'above_horizon') %}"
        "0"
        "{% elif is_state('sun.sun', 'below_horizon') %}"
        "20"
        "{% elif lux >= 150 %}"
        "25"
        "{% elif lux >= 80 %}"
        "40"
        "{% elif lux >= 40 %}"
        "55"
        "{% elif lux >= 15 %}"
        "70"
        "{% else %}"
        "85"
        "{% endif %}"
    )


def baseline_brightness_exterior(lux_sensor: str, bright_above: float) -> str:
    return (
        "{% set lux = states('" + lux_sensor + "') | float(99999) %}"
        "{% if lux >= " + str(bright_above) + " and is_state('sun.sun', 'above_horizon') %}"
        "0"
        "{% elif is_state('sun.sun', 'below_horizon') %}"
        "30"
        "{% elif lux >= 5000 %}"
        "25"
        "{% elif lux >= 3000 %}"
        "40"
        "{% elif lux >= 1500 %}"
        "55"
        "{% elif lux >= 500 %}"
        "70"
        "{% else %}"
        "85"
        "{% endif %}"
    )


def motion_brightness_interior(lux_sensor: str, *, floor_accent: bool = False) -> str:
    base = baseline_brightness_interior(lux_sensor, floor_accent=floor_accent)
    boost = "35" if floor_accent else "25"
    cap = "85" if floor_accent else "100"
    return (
        "{% set lux = states('" + lux_sensor + "') | float(99999) %}"
        "{% set base = "
        + (
            "1 if is_state('sun.sun', 'below_horizon') else "
            "(10 if lux >= 150 else (18 if lux >= 80 else (28 if lux >= 40 else (40 if lux >= 15 else 50))))"
            if floor_accent
            else
            "20 if is_state('sun.sun', 'below_horizon') else "
            "(25 if lux >= 150 else (40 if lux >= 80 else (55 if lux >= 40 else (70 if lux >= 15 else 85))))"
        )
        + " %}"
        "{{ [base + " + boost + ", " + cap + "] | min }}"
    )


def motion_brightness_exterior(lux_sensor: str) -> str:
    return (
        "{% set lux = states('" + lux_sensor + "') | float(99999) %}"
        "{% set base = 30 if is_state('sun.sun', 'below_horizon') else "
        "(25 if lux >= 5000 else (40 if lux >= 3000 else (55 if lux >= 1500 else (70 if lux >= 500 else 85)))) %}"
        "{{ [base + 25, 100] | min }}"
    )


def motion_gate_indoor_template(lux_sensor: str, dark_below: float) -> str:
    """Kitchen motion: allow brighten when indoor dark; at night allow if toe-kick dim."""
    return (
        "{% set lux = states('" + lux_sensor + "') | float(99999) %}"
        "{% if lux < " + str(dark_below) + " and is_state('sun.sun', 'above_horizon') %}"
        "true"
        "{% else %}"
        "{% set b = state_attr('light.island_toe_kick_1', 'brightness') | int(0) %}"
        "{{ states('light.island_toe_kick_1') in ['unavailable','unknown'] or (b > 1 and b < 20) }}"
        "{% endif %}"
    )
