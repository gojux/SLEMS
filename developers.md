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
`input_number` sliders: smart meter, PV, a heat pump (power + energy) and a
heating rod controlled via `input_number.sim_heating_rod_setpoint` that can be
blocked via `input_boolean.sim_heating_rod_blocked`. For a PV forecast add the
Forecast.Solar integration in the dev instance (no account needed). Everything else in
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
  today ≥ energy to full (divided by the charge efficiency) × *safety margin*.
  The expected surplus is the remaining PV forecast today minus the current
  uncontrolled load until the end of PV production (persistence forecast; the
  consumption forecast will replace it).
- **Order**: consumers within their minimum runtime keep their power; not
  secured → battery first; secured → *battery share* to the batteries, the
  rest to consumers by priority (1 first). Unused power of one side goes to
  the other. Deficit → batteries discharge (self consumption), with import
  peak shaving only the import above the limit.
- **Minimum runtime / pause** are tracked on the state commanded by SLEMS
  (`RuntimeTracker`), in simulation mode on the virtual state.

Efficiency: `EfficiencyTracker` per battery. *Battery counters* uses the
lifetime counters (Venus: registers 33000/33002), *learned* integrates the AC
battery power (persisted in `.storage/slems.<entry_id>.efficiency`), *manual*
uses the configured value, which is also the start value of the other modes
until 3 full cycles of throughput exist. Charge and discharge efficiency are
each √RTE.

Planned layers (not implemented yet):

- **Forecast**: see [Consumption forecast](#consumption-forecast).
- **Planner**: 15 minute slots, 24–48 h horizon; rule based first, interface
  prepared for a later LP optimisation.
- **Real-time controller**: follows the plan and corrects deviations from the
  grid set point in seconds; the only component that sends commands, and only
  in operating mode *active*. It must be triggered by grid meter state changes:
  the coordinator cycle (5 s + battery reads) reacts only within ~5–10 s.
  Consumers with their own thermostat (heating rod) may draw no power although
  switched on; the controller has to detect this and release their share.
- **Consumer manager**: switch and number entities, priorities, external block.
- **Import peak shaving** (only when switched on): at or below the SoC
  threshold the batteries stop regular discharging and only cover the grid
  import above the limit.
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

Concept (not implemented yet). Horizon: today and tomorrow, hourly, later
interpolated to the 15 minute planner slots.

Training data comes from the recorder long-term statistics (hourly, kept
indefinitely), read with `statistics_during_period`. More than a year of data
is available on the target system; the models must still work with a few weeks.

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
- **Improve with more data**: with more history more features are added
  (e.g. global radiation, wind, previous day temperature) and chosen by the
  error on the most recent weeks (rolling validation); with little data the
  model falls back to the simple profile.
- **Vacation**: the vacation switch selects the absence profile learned from
  earlier vacation periods; without such data the base load is scaled down to
  its night level.
- **Without weather entity** the mean temperature of the last days is used as
  persistence forecast.

Hot water in winter is part of the heat pump energy; in summer the heating rod
is used, both may run at the same time. The heat pump model therefore keeps
the hot water share `a` independent of the temperature term.

Only numpy (bundled with Home Assistant) is used; fitting runs in the executor
once a day and after a restart.

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
