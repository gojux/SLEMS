# SLEMS – developer notes

## Conventions

- All code, identifiers and comments are in English. User facing texts live in
  `strings.json` / `translations/*.json` (English and German).
- Both READMEs (`README.md`, `README.de.md`) are kept in sync.
- Code comments contain only lasting information (what, conventions, hardware
  facts, reasons for non-obvious decisions), never development history or
  status notes; those belong here or in commit messages.
- Sign conventions used everywhere:
  - grid power: **+ import / − export**
  - battery power: **+ charge / − discharge**
  - house power = grid + PV − battery (everything behind the smart meter)
  - base load = house power − consumers inside the smart meter
  - total consumption = house power + consumers outside the smart meter
- License: GPL-3.0 (compatible with Omnibattery, from which the Marstek
  register map and control sequence are taken).

## Development environment

Everything runs in Docker Compose; no local Python setup is needed.

```bash
docker compose up -d                  # Home Assistant + two simulated batteries
docker compose logs -f homeassistant  # follow logs
docker compose restart homeassistant  # after code changes
docker compose --profile tests run --rm tests   # run the test suite
docker compose down
```

| Service | Purpose |
|---|---|
| `homeassistant` | HA 2026.9.3 (same version as production) on <http://localhost:8123>. `custom_components/slems` is mounted read-only. |
| `venus-sim-1`, `venus-sim-2` | Modbus TCP simulators of a Venus E 3.0 (`dev/venus_sim/simulator.py`), reachable as host `venus-sim-1` / `venus-sim-2`, port 502. |
| `tests` | pytest in an image based on the HA image, so Python and HA versions match production. |

`dev/config/configuration.yaml` provides simulated measurements driven by
`input_number` sliders. The smart meter is a closed loop: house load + heat
pump + heating rod − PV + battery power of "Venus 1" and "Venus 2", updated
every 2 s, so the controller sees the effect of its commands (set the settle
time to ~9 s in the dev instance). Further: PV, a heat pump (power + energy) and a
heating rod controlled via `input_number.sim_heating_rod_setpoint` that can be
blocked via `input_boolean.sim_heating_rod_blocked`. For a PV forecast add the
Forecast.Solar integration in the dev instance (no account needed).

`dev/seed_statistics.py` imports 60 days of synthetic hourly statistics (see
its docstring) so the consumption forecast has history to learn from. An
access token can be created in the user profile of the dev instance. Everything else in
`dev/config` is created by Home Assistant and ignored by git. To start from
scratch, stop the stack and delete everything in `dev/config` except
`configuration.yaml`.

The simulator deliberately has no dependencies (plain asyncio Modbus TCP) so it
is independent of pymodbus API changes. Like the real device it accepts only a
single TCP connection.

## Architecture

```
custom_components/slems/
  __init__.py        setup/unload, builds batteries and consumers from subentries
  config_flow.py     main entry (system), options flow, battery and consumer subentry flows
  coordinator.py     SlemsCoordinator → SystemSnapshot; ControlSettings (runtime settings)
  consumers.py       ConsumerConfig (from subentry), ConsumerState (measured)
  pv_forecast.py     PV forecast via the HA energy platform (provider independent)
  forecast/          consumption forecast (history, models, forecaster)
  night_discharge.py night discharge planning
  battery_distribution.py  split of the battery power between batteries (rotation, ramps)
  controller.py      real-time controller (active mode): commands to batteries and consumers
  allocation.py      distribution of available power between batteries and consumers
  grid_filter.py     conservative moving average of the grid power
  efficiency.py      round trip efficiency (battery counters / learned / manual)
  entity.py          base classes (system device, battery devices)
  sensor.py          system and battery sensors
  select.py          operating mode (off / simulation / active)
  switch.py          vacation, import peak shaving
  number.py          numeric runtime settings (averaging window, allocation, peak shaving)
  binary_sensor.py   battery charge secured
  util.py            unit conversion of HA states
  drivers/
    base.py          BatteryDriver contract (read_telemetry, apply_power, release_control)
    marstek_venus_e3.py
    modbus_client.py Modbus TCP link with Venus firmware quirks
    ha_entities.py   read-only battery backed by HA entities
```

Runtime settings (operating mode, vacation, peak shaving) live in
`ControlSettings` on the coordinator. They are changed via entities and
restored after a restart by those entities (`RestoreEntity` / `RestoreNumber`).

### Allocation

Every coordinator update (operating mode *simulation* or *active*) computes an
`Allocation` (`allocation.py`); in simulation mode it is only shown via
sensors. Inputs:

- **Available power** = −(filtered grid power) + AC battery power + power of
  the controllable, unblocked consumers behind the meter. It is what SLEMS
  could distribute if it controlled everything it is allowed to.
- **Grid filter**: time weighted average over 0–300 s (0 = off); the larger
  import (= smaller surplus) of average and current value is used, so PV drops
  are followed immediately and PV rises only after the window.
- **Charge secured**: SoC ≥ *battery priority SoC* and expected PV surplus
  today ≥ energy to full (divided by the charge efficiency) + *safety buffer* (kWh).
  The expected surplus is the remaining PV forecast today minus the current
  uncontrolled load until the end of PV production (persistence forecast; the
  consumption forecast will replace it).
- **Grid targets** (surplus, +export / −import): charging only uses the power
  above the *charge target* (0…5000 W), so at least that much is exported;
  discharging aims at the *discharge target* (−1000…+1000 W), capped by the
  *maximum grid export while discharging* (range 0 … sum of the maximum
  discharge power of all batteries, default 5000 W). Between the two targets the
  batteries stay idle. The real-time controller also uses the maximum export
  as hard limit: if the measured export exceeds it while discharging, the
  discharge power is reduced immediately.
- **Order**: consumers within their minimum runtime keep their power; not
  secured → battery first; secured → *battery share* to the batteries, the
  rest to consumers by priority (1 first). Unused power of one side goes to
  the other. Deficit → batteries discharge (self consumption), with import
  peak shaving only the import above the limit.
- **Disabled batteries** (switch per battery): still read and part of the
  energy balance (their power is treated like an uncontrolled load/source),
  but excluded from the battery group, total SoC, available power, planning
  and control. Disabling one in active mode hands it back to its internal
  logic immediately.
- **Minimum runtime / pause** are tracked on the state commanded by SLEMS
  (`RuntimeTracker`), in simulation mode on the virtual state.

### Distribution between batteries

`battery_distribution.py` splits the planned battery power (AC) between the
enabled batteries; result per battery in the sensor *Planned power*.

- **How many**: each battery has a loss model
  `loss(P) = fixed + linear·P + quadratic·P²`. The number of batteries with
  the lowest total loss is used (current number kept within 5 %). The model is
  learned per battery from AC and DC power (`LossCurveLearner`, 250 W bins,
  moving average, quadratic fit once 3 bins have 20 samples); until then the
  default 15 W + 4 % + 1e-5·P² applies (sharing pays off above ~1.7 kW). It
  requires that the driver reports both AC and DC power; the Venus registers
  30006 (AC) and 30001 (DC) are assumed to be exactly that, to be verified.
- **Which**: discharging highest SoC first, charging lowest first. An active
  battery is replaced when it is more than the *rotation threshold* (default
  5 %) worse than the best inactive one, at most once per *minimum interval*
  (default 15 min).
- **Smooth transition**: weights ramp between 0 and 1 within the *ramp time*
  (default 30 s); the power is split by weight × maximum power, the sum always
  matches the plan. If the weighted batteries cannot deliver it, the others
  help immediately.
- Learned loss curves are stored with the efficiency data in `.storage`.

Efficiency: `EfficiencyTracker` per battery. *Battery counters* uses the
lifetime counters (Venus: registers 33000/33002), *learned* integrates the AC
battery power (persisted in `.storage/slems.<entry_id>.efficiency`), *manual*
uses the configured value, which is also the start value of the other modes
until 3 full cycles of throughput exist. Charge and discharge efficiency are
each √RTE.

### Night discharge

`night_discharge.py`, optional (switch, off by default). Without it the
batteries only cover the house consumption at night.

- **Until**: start of the first hour in which the PV forecast exceeds the
  consumption forecast (batteries would start charging again).
- **Target**: *reserve* = % of tomorrow's forecast daily consumption. If
  tomorrow's PV surplus (from the crossover until the end of the day, minus the
  safety buffer, times the charge efficiency) cannot refill the batteries from
  there, the target is raised to the level from which it can.
- **Power**: (stored energy − target) × discharge efficiency / hours until the
  crossover, recomputed every cycle. The batteries deliver at least this power;
  the discharge grid target is ignored, the maximum grid export while
  discharging still applies. Larger house loads are still covered.
- Not active during a surplus and while import peak shaving is active.
- Needs the consumption forecast (`SystemSnapshot.consumption_forecast`,
  hourly Wh of the whole house); without it no plan is made.

### Real-time controller

`controller.py`, only in operating mode *active*; the only component that
sends commands.

- **Trigger**: every grid meter state change and every coordinator update,
  at most every 2 s. It plans with the current HA states and the last
  commanded battery powers (`SlemsCoordinator.fast_snapshot`), without waiting
  for the battery polling, and publishes the result to the entities without
  rescheduling the polling.
- **Settle time** (setting, default 5 s): after a command the next correction
  waits until the meter can show the effect. Too short makes the loop
  oscillate (the new command is assumed delivered while the meter still shows
  the old state); too long makes it slow. Rule of thumb: meter update interval
  + battery reaction (~1 s) + margin. The dev environment needs ~9 s because
  its meter is composed of the 5 s battery polling.
- **Batteries**: new set point only when it changes by ≥ 25 W or changes
  direction; repeated every 60 s as keep-alive (also re-enables RS485 control).
- **Consumers**: switch → `turn_on`/`turn_off`, power → `set_value` (clamped to
  the entity's min/max/step, dead band 50 W); at most one command per consumer
  every 10 s. Blocked consumers are left alone.
- **Saturation**: a consumer drawing < 10 % of its command for 120 s (own
  thermostat) counts as saturated for 15 min and is planned like an
  uncontrolled load; its last command stays.
- **Grid meter stale** (no update within 60 s, based on `last_reported`): all
  batteries are handed back to their internal logic until the meter reports
  again (status *grid meter stale*).
- **Maximum export while discharging** is applied as hard limit on the
  current, unfiltered grid power (`limit_discharge_export`).
- Leaving *active* hands the batteries back immediately; consumers keep their
  last state.

Not implemented yet:

- **Grid friendly charging**: shift charging into the PV feed-in peak instead
  of charging as early as possible.
- **Dashboard**: custom sidebar panel (web component served by the integration).

### Configuration model

- One config entry (`single_config_entry`) holds the system settings in `data`;
  the options flow stores the complete settings in `options`, which then fully
  replace `data` (so clearing an optional field works).
- Every battery is a **config subentry** of type `battery`. Adding, editing or
  removing a subentry triggers the update listener, which reloads the entry.
  The listener is registered at the very start of `async_setup_entry`, so
  subentries added while a reload is still running are not lost.
- Every consumer is a config subentry of type `consumer`: power and energy
  sensor (required), type, *included in smart meter*, control mode and, for
  controlled consumers, control entity, power range, block entity and priority.
- Battery entities are registered with `config_subentry_id`, so removing a
  battery also removes its device and entities.
- The PV forecast is configured as a list of config entry ids of solar forecast
  providers (`async_get_solar_forecast` of their `energy` platform, as used by
  the energy dashboard). Any provider works; several entries are summed.

### Adding a battery model

1. Implement `BatteryDriver` in `drivers/<model>.py`.
2. Add the model to `BatteryModel` in `const.py` and to `create_driver()`.
3. Add a subentry step `async_step_<model>` in `config_flow.py` plus
   translations (`strings.json`, `translations/en.json`, `translations/de.json`,
   selector option in `selector.model`).
4. Report optional values via `BatteryTelemetry.extra` and list their keys in
   `extra_telemetry_keys`; matching sensors in `BATTERY_EXTRA_SENSORS` are then
   created automatically.

## Consumption forecast

`forecast/` – horizon today and tomorrow, hourly. The forecast covers the
consumption behind the smart meter that SLEMS does not control: base load plus
heat pumps (controllable consumers are scheduled by the allocation). It is
refitted every hour (minute 5), after a restart and when the vacation switch
changes; fitting runs in the executor.

Training data are the hourly means of the recorder long-term statistics
(`statistics_during_period`, up to 365 days):

- **House consumption**, per hour from the first available source: the SLEMS
  sensor *House consumption*, the optional *House consumption history* entity
  (e.g. from Omnibattery, for the time before SLEMS existed), otherwise
  grid + PV − SLEMS battery power.
- **Base load** = house − heat pumps − controllable consumers (behind the meter).
- **Outdoor temperature**: the optional temperature sensor, otherwise the
  SLEMS sensor *Outdoor temperature* that mirrors the weather entity (weather
  entities have no long-term statistics).
- **Forecast temperature**: `weather.get_forecasts` (hourly, else daily);
  measured hours of today override forecast hours; without weather the mean of
  the last 3 days is used.

Sensors *Consumption forecast today/tomorrow* carry the hourly values (total,
base, heat pump) in the attribute `hourly` (not recorded). The allocation
uses the forecast for the expected PV surplus (sum of hourly PV above
consumption) and the night discharge.

Model details (`forecast/models.py`):

The forecast is split into components that react differently to the weather:

| Component | Model |
|---|---|
| Base load (house − measured consumers) | Profile per hour of day and day type (workday / weekend + holiday / vacation), recency weighted |
| Heat pump (space heating + hot water) | Daily energy from outdoor temperature, distributed over the day with the recent hourly shape |
| Heating rod and other controlled consumers | Not forecast as load; the planner schedules them |
| PV | From the configured forecast provider |

Requirements and how the models meet them:

- **React quickly to weather changes** (heating season interrupted by warm days
  in spring/autumn): the heat pump model uses the forecast temperature of the
  day as input, not the season. Daily energy
  `E = a + b · max(0, T_base − T_mean)` (hot water share `a`, heating slope `b`,
  heating limit `T_base`) is fitted by weighted least squares with exponentially
  decaying weights (half-life of a few weeks), so a warm spell immediately
  predicts little heating energy and changed behaviour is learned within weeks.
- **Short-term correction**: the residual of the last days shifts the forecast
  (bias correction), which absorbs effects the model does not know.
- **Improve with more data**: the models use all available history (weighted
  by age). Planned: more features (global radiation, wind, previous day
  temperature) chosen by the error on the most recent weeks.
- **Vacation**: with the vacation switch on, the base load is limited to its
  night level (mean of 01:00–05:00).
- **Without weather entity** the mean temperature of the last days is used as
  persistence forecast.

Hot water in winter is part of the heat pump energy; in summer the heating rod
is used, both may run at the same time. The heat pump model therefore keeps
the hot water share `a` independent of the temperature term.

Parameters: base load half-life 14 days over 8 weeks, correction from the
last 72 h limited to 0.8…1.25; heat pump half-life 21 days over 365 days,
heating limit chosen from 10…20 °C, correction from the last 3 days limited to
±30 %. Only numpy (bundled with Home Assistant) is used. Holidays are treated
as workdays (no holiday calendar yet).

## Marstek Venus E 3.0

Source: Omnibattery `const/registers_v3.py`, `drivers/marstek.py`,
`infra/modbus_client.py`.

| Register | Key | Type | Scale | Notes |
|---|---|---|---|---|
| 37005 | battery_soc | uint16 | 1 % | see open point below |
| 30001 | battery_power | int16 | 1 W | + charge / − discharge |
| 30006 | ac_power | int16 | 1 W | + discharge / − charge |
| 30100 | battery_voltage | uint16 | 0.01 V | |
| 35000 | internal_temperature | int16 | 0.1 °C | |
| 35100 | inverter_state | uint16 | | 0 sleep, 1 standby, 2 charge, 3 discharge, 4 backup, 5 OTA, 6 bypass |
| 33000 | total_charging_energy | uint32 | 0.01 kWh | |
| 33002 | total_discharging_energy | int32 | 0.01 kWh | |
| 42000 | rs485_control | uint16 | | write 0x55AA = enable, 0x55BB = disable |
| 42010 | force_mode | uint16 | | 0 none, 1 charge, 2 discharge |
| 42020 | set_charge_power | uint16 | W | max 2500 |
| 42021 | set_discharge_power | uint16 | W | max 2500 |

Control sequence (`apply_power`): enable RS485 control → discharge set point →
charge set point → force mode (last). `release_control` zeroes the set points,
sets force mode none and disables RS485 control.

Firmware quirks handled in `modbus_client.py`:

- exception responses carry a wrong MBAP length byte (patched via `trace_packet`),
- ≥150 ms between frames,
- a single TCP connection slot that is released slowly → wait 1 s before reconnecting,
  never reconnect per request,
- retries inside pymodbus (same transaction id) so late replies still match.

Open points to verify on the real device:

- **SoC register**: Omnibattery's code uses 37005 for v3, its register table
  (`site-docs/reference/registers.md`) lists 34002 for `e_v3`. SLEMS uses 37005.
  Compare both with `tools/read_registers.py` (see below).
- Omnibattery marks the v3 map as partly untested.
- The device holds only one connection: while Omnibattery is running, a Modbus
  battery in SLEMS cannot connect. Use the read-only *HA entities* battery
  during the transition.

## Tools

`tools/read_registers.py` reads holding registers of a Venus repeatedly and
shows whether they are identical (standard library only, Python ≥ 3.10):

```bash
python3 tools/read_registers.py <battery-ip>                       # 37005 vs 34002
python3 tools/read_registers.py <battery-ip> --registers 37005 34002 32104 --samples 20 --interval 5
```

The battery allows one Modbus connection only: disable other integrations
using it (e.g. Omnibattery) while the script runs.

## Decision log

| Date | Decision |
|---|---|
| 2026-09-23 | Custom integration (not an app/add-on): direct entity access, config flow, works on every HA installation type. |
| 2026-09-23 | Batteries as config subentries; count can change at any time. |
| 2026-09-23 | Planner: rule based first, exchangeable for an LP optimisation later. |
| 2026-09-23 | Power distribution between batteries: efficiency optimum. |
| 2026-09-23 | Configuration via config flow and dashboard; own dashboard panel similar to Omnibattery. |
| 2026-09-23 | Default operating mode *simulation*; nothing is sent unless *active*. |
| 2026-09-23 | License GPL-3.0. |
| 2026-09-23 | Consumers as config subentries; each needs its own power and energy sensor and a flag whether it is included in the smart meter. |
| 2026-09-23 | Heat pump is a consumer type (not a separate system setting), so it shares measurement and optional control with all consumers. |
| 2026-09-23 | Grid friendly target: absorb PV feed-in peaks (usually around noon, but depending on clouds at any time). |
| 2026-09-23 | Import peak shaving at low SoC is optional and off by default. |
| 2026-09-23 | PV forecast via the HA energy platform instead of a sensor, so the provider can be exchanged. |
| 2026-09-23 | Vacation as switch entity (manual or by automation). |
| 2026-09-23 | Weather entity optional. |
| 2026-09-23 | Priority between battery and consumers: battery first until charge is secured (SoC threshold + PV forecast), then configurable split; settings as number entities. |
| 2026-09-23 | Battery efficiency from battery counters, learned or manual (per battery). |
| 2026-09-23 | Grid power averaging window 0–300 s, the less favourable of average and current value is used. |
| 2026-09-23 | Minimum runtime and pause are optional per consumer; power set points only in W. |
| 2026-09-23 | Heating rod has its own thermostat; SLEMS needs no tank temperature. |
| 2026-09-23 | Charge secured uses a safety buffer in kWh instead of a percentage margin. |
| 2026-09-23 | Grid surplus targets for charging (0…5 kW) and discharging (−1…+1 kW) plus a maximum grid export while discharging. |
| 2026-09-23 | Batteries can be disabled temporarily via a switch; they stay measured. |
| 2026-09-23 | Real-time controller event driven on the grid meter with settle time after commands; batteries released when the meter is stale. |
| 2026-09-23 | Distribution between batteries by minimal conversion losses (learned per battery), rotation by SoC threshold with minimum interval and ramped transition. |
| 2026-09-23 | Consumption forecast from long-term statistics; optional history entity for the house consumption and optional outdoor temperature sensor, otherwise SLEMS records the weather temperature itself. |
| 2026-09-23 | Night discharge: evenly spread until PV exceeds consumption, target = reserve raised to what tomorrow's PV can refill; grid target ignored, maximum export respected. |
