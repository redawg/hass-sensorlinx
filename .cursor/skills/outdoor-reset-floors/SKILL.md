---
name: outdoor-reset-floors
description: >-
  Evaluates outdoor temperature, weather forecast, tankless supply-water outdoor
  reset, closed-loop hydronic delta-T vs open zone valves/loops, and radiant
  floor targets for the Forest Home SensorLinx system. Use when the user asks
  about outdoor reset, floor setpoints, supply water, WWSD, loop counts, delta-T,
  cold-weather floor boost, why floors are too hot/cold, or today's hydronic plan.
---

# Outdoor Reset Floors (Forest Home)

Hydronic radiant floors heated by an Optimal Tankless. **Outdoor reset primarily sets supply water temperature.** Zone climates hold configured **floor targets** (not drive-gain overshoot). In cold weather, if room air lags comfort, floor target may rise by **at most +2°F**.

Read [reference.md](reference.md) for the reset-curve theory (verbatim) and live entity map.

## Control model (do not violate)

| Layer | What it controls | What it must not do |
|-------|------------------|---------------------|
| Supply-water outdoor reset | `water_heater.main_water_heater` setpoint from outdoor | Raise zone SP above floor target |
| Floor targets | Zone climate heat setpoints | Act as boiler water temp |
| Cold lag boost | Floor target +0…2°F when outdoor cold **and** room air lagging | Overshoot to safety cap (80°F) |
| Drive gain | Unused (keep 0) | Re-enable room SP overshoot |

## Evaluation workflow

Copy and track:

```
OR Floor Eval:
- [ ] 1. Snapshot outdoor + forecast
- [ ] 2. Compute expected supply water from curve
- [ ] 3. Closed loop: outlet−inlet dT + flow + which zones/loops are CALL
- [ ] 4. Check each zone floor vs target / safety
- [ ] 5. Decide: water adjust, floor-target nudge (≤+2), or leave alone
- [ ] 6. Apply only approved changes; re-read state
```

### 1. Snapshot outdoor + forecast

Prefer local weather (Ames Lake / Quail Creek), not remote sites:

- `weather.quail_creek_ames_lake_279th_ct_ne` — current outdoor
- HA `weather.get_forecasts` hourly/daily for that entity
- WWSD / design: `number.sensorlinx_outdoor_reset_heating_curve_shutdown_temp` (65°F), `..._design_outdoor` (25°F)

Classify day:

- **Above WWSD (≥65°F)** → heat off / preheat only if forecast drops
- **WWSD latch** → once off at ≥65°F, stays off until outdoor &lt; **61°F** (4°F deadband); latch persists across reloads so shoulder-season 64–66°F bounce cannot flap valves
- **Mild (WWSD → ~45°F)** → shallow supply; floors near base targets
- **Cold (→ design 25°F)** → supply toward max; allow lag boost ≤+2°F if rooms lag
- **At/below design** → supply at max (140°F cap)

### 2. Expected supply water

Linear reset (this home):

Heat source: Optimal Tankless, hardware range **80–140°F**.

```
outdoor ≥ shutdown (65)  → supply = supply_temp_min (80°F)
outdoor ≤ design (25)    → supply = supply_temp_max (140°F)
else                     → lerp(min, max) by outdoor fraction
```

Reset ratio for this home:

```
ΔT supply = 140 - 80 = 60
ΔT outdoor = 65 - 25 = 40
ratio = 60/40 = 1.5  (full tankless span; floors still use low water temps vs baseboard)
```

Live entities:

- Target: `sensor.sensorlinx_outdoor_reset_supply_water_target`
- Commanded: `water_heater.main_water_heater` `.temperature`
- Outlet / inlet: `sensor.main_water_heater_outlet_temperature`, `..._inlet_temperature`
- Enable: `switch.sensorlinx_outdoor_reset_supply_water_reset_enabled` **must be on**

### 3. Closed loop — ΔT, open zones, loop counts

Single closed loop from the tankless; zone **valves** open/close individual floor loops. Shared sensors:

- **ΔT** = outlet − inlet (`sensor.sensorlinx_outdoor_reset_hydronic_delta_t`, target ~10°F)
- **Flow** = `sensor.main_water_heater_flow_rate` (GPM)
- **Heat output** = `sensor.sensorlinx_outdoor_reset_hydronic_heat_output`
- **GPM per loop** (estimate) = `number.sensorlinx_outdoor_reset_hydronic_flow_rate_per_valve_gpm` (0.5)

| Zone | Climate | Floor target | Loops (valves) | Entity |
|------|---------|--------------|----------------|--------|
| Living | `climate.living_room_living_room` | 73 (day) | **3** | `number.sensorlinx_outdoor_reset_valve_count_living_room` |

**Living room floor probe bias −5°F** (`number.living_room_floor_sensor_bias_living_room`): sensor reads ~5°F **hot** (near supply/manifold). Always use **true slab ≈ raw − 5** for decisions; integration commands thermostat SP = **floor target + 5** (e.g. true **73°F** → climate set **78°F**). Room air: `sensor.living_room_temperature` (~71°F) is more representative of comfort than the floor probe.
| Main area | `climate.main_area_main_area` | 72 | **5** | `..._valve_count_main_area` |
| Main office | `climate.main_office_main_office` | 69 | **1** | `..._valve_count_main_office` |
| Laundry | `climate.laundry_laundry` | 72 | **1** (single loop) | `..._valve_count_laundry` |

**Always interpret ΔT with open-loop count**, not alone:

- 1 zone / few loops (e.g. laundry only = 1 loop) → lower flow, ΔT often **higher** for the same supply
- Many zones (e.g. living 3 + main 5 = 8 loops) → higher flow, ΔT often **lower**
- Est. open flow ≈ `(sum of valve_count on CALL zones) × gpm_per_loop`
- Low ΔT + many loops open → supply may be hotter than needed; ease supply toward curve mild end
- High ΔT + few loops → load heavy vs flow; raise supply toward curve or expect longer run
- ΔT with **no** zone CALL → residual / sensor lag; do not retune from that alone

Run: `python .cursor/skills/outdoor-reset-floors/scripts/evaluate_or_floors.py`

### 4. Floor targets vs slab / room

Also read:

- Floor sensors: `sensor.{zone}_floor_temperature`
- Room air (lag detect): `sensor.living_room_temperature`, `sensor.family_room_temperature`, `sensor.office_temperature`
- Safety cap: `number.sensorlinx_outdoor_reset_max_floor_temp_safety_cap` (80) — **turn zone off if floor ≥ cap**, never chase it as a setpoint
- Floor control mode switches must be **on** for floor-target control

**Lag boost rule:** if outdoor fraction toward design is high **and** room avg < ~71°F, floor target may be base + up to `floor_boost` (max **2°F**). If rooms are already comfortable or slab ≫ target, boost = 0 and SP should be at/below base target (idle).

### 5. Decision matrix

| Observation | Action |
|-------------|--------|
| Supply reset off / tankless ≪ computed target while zones call heat | Enable supply reset; set water to curve target |
| Zones at 75–80 while floor target ~69–72 | Pull climate SP to floor target; ensure drive gain 0; do not “drive harder” |
| Slab ≥ safety cap | `climate.turn_off` that zone |
| Forecast dropping through WWSD tonight | Rely on preheat + rising supply curve; do not pre-raise zone SP to 80 |
| Cold day, room air lagging &lt;71°F, slab near/below target | Allow floor target +≤2°F only |
| Mild day, rooms warm, slab hot | Hold base floor targets; supply at mild min |
| ΔT weird vs open loops | Adjust **supply water**, not zone SP overshoot; re-check after valves settle |

### 6. Apply changes (HA)

Base: `http://172.16.255.250:8123` with the project long-lived token (same pattern as `scripts/_system_status.py` / deploy scripts).

```python
# supply water (outdoor reset)
hass services: water_heater.set_temperature / number entities for min/max
# zone floor target as climate SP — never target + drive_gain
hass services: climate.set_temperature entity_id=climate.* temperature=<floor_target> hvac_mode=heat
# enable supply OR
hass services: switch.turn_on entity_id=switch.sensorlinx_outdoor_reset_supply_water_reset_enabled
```

Prefer fixing **code** in `custom_components/sensorlinx/outdoor_reset.py` when behavior is wrong (overshoot, boost >2, OR writing crazy zone SPs). Live number/climate tweaks are for immediate comfort.

### 7. Report format

Keep the user brief:

```
Outdoor: {T}°F ({class}) | Forecast: {next relevant swing}
Supply: target {X} / commanded {Y} / outlet {Z} / inlet {I} (reset {on|off})
Loop: dT={dT}F flow={gpm} active={n} zones / {loops} loops (e.g. laundry=1)
Floors: living {sp}/{floor} (target {t}, 3 loops) …
Action: {what changed or why no change}
```

## Emitter profile (this home)

Low-to-moderate mass radiant floors on tankless **80–140°F**. Curve uses the full tankless span (not 180°F baseboard). Electric tankless: cool returns are fine; no cast-iron min-boiler floor.

## Night / morning schedule

| Time | Action |
|------|--------|
| 11:00 PM | Evening setback → **70°F** all zones |
| midnight | Deep night → **66°F** (tunable night target numbers) |
| Dynamic pre-wake | Morning recovery → **72°F** when lead time says slabs won't hit wake by 8:00 AM |
| 8:00 AM | Day restore → outdoor-reset / configured floor targets |

Morning lead uses: floor deficit × thermal lag (min/°F), outdoor coldness, supply vs curve target, and loop ΔT (outlet−inlet).

Manual HA/app setpoint **raises** while outdoor is below WWSD bump that zone's floor target by +0.5°F (capped at default +2°F) and persist — cold-weather under-heat feedback.

## Anti-patterns

- Setting zone climate to 78–80 to “catch up” the slab
- Using heating-curve room overshoot (base+6) as a floor setpoint while floor mode is on
- Adjusting Desert Hot Springs / other non-local weather entities
- Treating floor target as the tankless setpoint (or vice versa)
- Ignoring open loop count when reading ΔT
