# Outdoor Reset Curve Reference

## Theory (source guidance)

An outdoor reset curve is a programmatic rule that automatically adjusts a hydronic boiler's supply water temperature based on the changing outdoor air temperature.
Instead of firing water to a fixed, high temperature (like 180°F) regardless of the weather, an outdoor reset control scales water temperatures down during mild weather and pushes them up only on the coldest days. This matches the home's heat loss, eliminates room temperature overshoots, prevents short-cycling, and unlocks maximum efficiency for condensing boilers by keeping return water cool enough to sustain flue gas condensation.

### The 4 Core Variables of a Reset Curve

To build or program a heating curve, the controller charts a linear relationship using four primary input values:

1. **Outdoor Design Temperature (Outdoor Low):** The absolute coldest expected outdoor temperature for your specific geographic region (e.g., 0°F or 5°F).
2. **Boiler Supply Design Temperature (Setpoint Low):** The highest water temperature your heat emitters (radiators, baseboards, floor loops) require to keep the house warm on that coldest day (e.g., 180°F for fin-tube baseboard, 110°F for radiant concrete).
3. **Outdoor Warm Weather Shutoff (Outdoor High):** The warm ambient outdoor temperature at which the building no longer requires space heating (typically defaulted between 65°F and 70°F).
4. **Warm Weather Supply Temperature (Setpoint High):** The lowest water temperature the system will circulate when the outdoor temperature hits the warm weather shutdown threshold (typically matching the desired indoor ambient temperature, around 70°F).

### Matching Emitter Types to the Curve

The slope of your reset curve is entirely dictated by how effectively your heating terminal units emit heat. High-mass systems can warm a room with tepid water, whereas thin fin-tube copper lines require aggressive heat to radiate successfully.

| Heat Emitter Type | Supply Temp at Design Cold (e.g., 0°F Outside) | Supply Temp at Warm Threshold (e.g., 65°F Outside) | Reset Curve Profile |
|-------------------|-----------------------------------------------|----------------------------------------------------|---------------------|
| High-Mass Radiant Floor (In-slab concrete) | 100°F – 115°F | 70°F – 75°F | Shallow / Flat (Low Reset Ratio) |
| Low-Mass Radiant Floor (Under-subfloor plates) | 120°F – 140°F | 70°F – 80°F | Moderate |
| Cast-Iron Radiators (Traditional large radiators) | 140°F – 160°F | 80°F – 90°F | Moderate-Steep |
| Fin-Tube Copper Baseboards / Convectors | 170°F – 180°F | 90°F – 110°F | Steep (High Reset Ratio) |
| Fan Coils / Air Handlers | 140°F – 180°F | 110°F – 120°F | Steep (Requires higher minimums) |

### How to Calculate the Reset Ratio

Many standalone or aftermarket controllers (like Tekmar or Honeywell AquaReset modules) require you to input a numerical Reset Ratio rather than choosing points on a graph. The ratio represents the change in supply water divided by the change in outdoor temperature:

\[\text{Reset Ratio}=\frac{\Delta T\text{ Supply Water}}{\Delta T\text{ Outdoor Air}}\]

Example Calculation (Standard Fin-Tube Baseboard System):

- Maximum required water is 180°F; minimum is 110°F. (ΔT Supply = 180 - 110 = 70)
- Outdoor design cold day is 0°F; warm weather cutoff is 70°F. (ΔT Outdoor = 70 - 0 = 70)
- Ratio: \(70 \div 70 = \mathbf{1.0}\). For every 1° drop outside, the boiler water climbs 1°.

Example Calculation (Radiant Floor Slab System):

- Maximum water needed is 105°F; minimum is 75°F. (ΔT Supply = 105 - 75 = 30)
- Outdoor design cold day is 10°F; warm weather cutoff is 70°F. (ΔT Outdoor = 70 - 10 = 60)
- Ratio: \(30 \div 60 = \mathbf{0.5}\). The boiler adjustments are highly dampened.

### Critical System Safety Constraints

- **Non-Condensing (Cast-Iron) Boilers:** If you have a traditional atmospheric or power-vent boiler, you must set a minimum boiler temperature protection limit (typically 130°F–140°F). Allowing the return water to fall lower will cause the flue gases to condense inside the iron heat exchanger, creating highly corrosive acidic moisture that will quickly rot the boiler and vent stack.
- **Sensor Placement:** The outdoor sensor must be mounted on a North or Northeast wall. It must be shielded entirely from direct morning/afternoon sunlight and away from secondary heat sources like dryer vents or exhaust pipes to prevent giving false warm readings to the control board.

---

## Forest Home mapping (live config)

| Curve variable | This home | Entity / notes |
|----------------|-----------|----------------|
| Outdoor design (Outdoor Low) | **25°F** | `number.sensorlinx_outdoor_reset_heating_curve_design_outdoor` |
| Supply at design (Setpoint at cold) | **140°F** | `number.sensorlinx_outdoor_reset_supply_water_max_temp_cold` |
| WWSD (Outdoor High) | **65°F** | `number.sensorlinx_outdoor_reset_heating_curve_shutdown_temp` |
| Supply at mild (Setpoint at warm) | **80°F** | `number.sensorlinx_outdoor_reset_supply_water_min_temp_mild` |
| Emitter class | Low-mass radiant floors | Tankless span 80→140 over outdoor 65→25 (ratio 1.5) |
| Heat source | Optimal Tankless **80–140°F** | `water_heater.main_water_heater` (electric; clamp to hardware) |
| Local weather | Quail Creek / Ames Lake | `weather.quail_creek_ames_lake_279th_ct_ne` |

### Floor targets + loop counts (zone comfort — not supply water)

Closed loop: all zones share tankless supply/return; valves open loops.

| Zone | Floor target | Loops | Cold lag boost |
|------|--------------|-------|----------------|
| Living room | 69°F | **3** | +0…2°F if outdoor cold **and** room air lagging |
| Main area | 72°F | **5** | same |
| Main office | 69°F | **1** | same |
| Laundry | 72°F | **1** (single loop) | same |
| Hard cap (wood safety) | 80°F | — | turn zone off if slab ≥ cap — never use as setpoint |

Hydronic telemetry: outlet/inlet → ΔT; `number.sensorlinx_outdoor_reset_valve_count_*` = loops per zone.

### Integration code

- Supply lerp: `OutdoorResetController._compute_supply_water_temp` in `custom_components/sensorlinx/outdoor_reset.py`
- Zone floor SP: `_compute_floor_mode_setpoint` / `_cold_weather_floor_boost` (max +2°F, lag-gated)
- Drive gain: unused; keep `number.outside_front_sensorlinx_outdoor_reset_floor_control_drive_gain` at **0**

### HA access

- Base URL: `http://172.16.255.250:8123`
- Token: same long-lived token used by `scripts/deploy_update.py` / `_system_status.py`
