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
  - house power = grid + PV − battery (everything behind the smart meter);
    the smart meter often reports a change later than PV and batteries, so
    a negative result is replaced by the last valid value for at most 30 s,
    then unknown (`HousePowerHold`; the control does not use it)
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
| `tests` | pytest in an image based on the HA image, so Python and HA versions match production. It runs as root on the mounted repository, so it writes no bytecode and no pytest cache (they would be owned by root). |

`dev/config/configuration.yaml` provides simulated measurements driven by
`input_number` sliders. The smart meter is a closed loop: house load + heat
pump + heating rod − PV − AC power of both simulated batteries, updated every
second. The AC power is read by the HA Modbus integration from a read-only
port of the simulators (5020) every second, so the loop behaves like a real
installation with a fast meter. Further: PV, a heat pump (power + energy) and a
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
single TCP connection. The set points drive the AC power; the DC power (30001)
differs by conversion losses (same shape as the SLEMS default loss model), so
the loss learning can be tested.

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
  grid_friendly.py   feed-in limit for grid friendly charging, PV forecast correction
  battery_distribution.py  split of the battery power between batteries (rotation, ramps)
  controller.py      real-time controller (active mode): commands to batteries and consumers
  panel.py           registration of the sidebar dashboard
  frontend/slems-panel.js  dashboard web component (no build step)
  response.py        learned meter cadence and response times
  adaptive_gain.py   automatic adaptation of the control gain
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
- **Smooth transition**: weights ramp between 0 and 1; the power is split by
  weight × maximum power, the sum always matches the plan. The ramp moves the
  power with the *ramp rate* (default 100 W/s), but takes at most the *maximum
  ramp time* (default 30 s): 600 W move in 6 s, 5 kW in 30 s. In active mode
  the controller applies it in steps of the settle time. If the weighted
  batteries cannot deliver the power, the others help immediately.
- **Disabling a discharging battery** (active mode): it ramps out within
  3.5 s (`LEAVE_RAMP_S`, its share limited to the remaining time fraction);
  the controller follows every second and places the last step exactly at
  the end. After the battery was
  commanded to 0 W it is released in a separate cycle; with the Modbus writes
  the handover takes about 4–5 s. Other batteries are released at once.
- With equal losses (below 1 W, e.g. a battery that reports AC = DC) the
  smallest number of batteries is used.
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
- **Target**: *reserve* = % of tomorrow's forecast daily consumption, on top
  of the energy below the minimum SoC of the batteries (not usable). If
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
  at most every *control interval* (default 1 s). It plans with the current HA
  states (`SlemsCoordinator.fast_snapshot`), without waiting for the battery
  polling, and publishes the result to the entities without rescheduling the
  polling. In active mode only the controller plans; the coordinator shows
  its latest result.
- **Control structure** (see [Control structure](#control-structure)):
  feedforward from the energy balance, dead time compensation with the
  learned battery response time, proportional correction with an adaptive
  gain.
- **Learned timing** (`response.py`, moving averages):
  meter cadence from every report of the grid meter (also unchanged values,
  `EVENT_STATE_REPORTED`); battery response time from commands with ≥ 300 W
  change until the meter moved 60 % of it; consumer response time from
  commands with ≥ 100 W change until the consumer's own power sensor moved
  60 %. The consumer value sets the saturation delay (5 × response time,
  30–300 s) and is shown as attribute of *Planned power*.
- **Batteries**: new set point only when it changes by ≥ 25 W or changes
  direction; every 60 s a complete write as keep-alive (also re-enables RS485
  control). The Venus driver skips registers whose value did not change, so a
  power change is usually a single write (150 ms).
  Batteries that reduce their power are written first, so the short gap
  between the writes causes a little import instead of feeding battery energy
  into the grid. Set points are logged at debug level.
- **Consumers**: switch → `turn_on`/`turn_off`, power → `set_value` (clamped to
  the entity's min/max/step, dead band 50 W); at most one command per consumer
  every 10 s. Blocked consumers are left alone.
- **Saturation**: a consumer drawing < 10 % of its command for longer than
  5 × its response time (30–300 s; own thermostat) counts as saturated for
  15 min and is planned like an uncontrolled load; its last command stays.
- **Control active** (`ConsumerControlSwitch`, RestoreEntity): the ids of
  switched-off consumers are in `SlemsCoordinator.consumer_control_disabled`
  and `SystemSnapshot.control_disabled`; `is_controllable_now` is false for
  them. Switching off calls `async_release_consumer` (0 W clamped to the
  entity / `turn_off`, only in active mode).
- **Resting** (consumers with `thermostat_cycles`, instead of saturation):
  < 10 % of the command for longer than 2 × response time (10–60 s) →
  `RealTimeController.resting`; the consumer stays in the allocation and keeps
  its command, `plan` adds its unused power (allocated − measured) to the
  battery power (up to the maximum charge power). Ends with the first sample
  with power. A water heater as block entity blocks in operation mode `off`;
  its temperatures are not used (e.g. my-PV measures at the element and
  cycles).
- **Grid meter stale** (no report within max(60 s, 10 × meter interval),
  based on `last_reported`): all batteries are handed back to their internal logic until the meter reports
  again (status *grid meter stale*).
- **Maximum export while discharging** is applied as hard limit on the
  current, unfiltered grid power (`limit_discharge_export`).
- Leaving *active* hands the batteries back immediately; consumers keep their
  last state.

### Battery limits and delivery monitoring

`battery_limits.py` (pure): `SocWindow` (block discharge at ≤ min SoC and
charge at ≥ max SoC below 100 %, release 2 % away), `temperature_factor`
(high limit after Omnibattery's `TemperatureChargeLimitManager`, plus a low
limit with a 5 °C ramp) and `power_limits` (capability → user power limit →
temperature → SoC window, with the limiting reason). The coordinator updates
the window and `BatteryRuntime.power_limits` with every poll; the limits
replace the capabilities in `BatteryUnit` (distribution) and `BatteryGroup`
(allocation). `BatteryGroup` carries the capacity weighted `min_soc_pct` and
`full_soc_pct`: `energy_to_full_wh` and `is_full` use the maximum SoC, the
night discharge target (`min_wh`) and the SoC projection the minimum.
Balancing runs use `power_limits(..., use_soc_window=False)`. Read-only
batteries ignore the SoC window. Settings: per battery `BatteryNumber`
(RestoreNumber; a power limit at the capability is stored as `None`),
temperature in `ControlSettings`.

`delivery_monitor.py` (pure, after Omnibattery's `NonResponsiveTracker` and
`_check_non_delivery`): judged in `_check_delivery` with every poll in
operating mode *active* for participating, controllable batteries that do not
ramp out. The controller provides the latest command and since when it has
its direction (`command_state`, set in `_record_command`). Three failures →
first `force_refresh` (the next write is complete, RS485 control included),
then exclusion for 5 min: `participating`/`plannable` are false, the
controller releases the battery at the start of the next cycle and it is
retried after the cooldown (`tick`). Failed or unconfirmed writes
(`apply_power` returns False) are recorded by the controller without a wake
attempt. The Venus driver reads 42000, 42010 and 42020/42021 back after
every complete write (`refresh`, i.e. the first write and the keep-alive
every 60 s; 0.2 s settle time, tolerance max(100 W, 10 %)) — not after every
write, to keep the control cycle short.

`problems.py`: `ProblemReporter.update` runs after every poll and keeps the
repair issues (`issue_registry`, not fixable, warning) in line with the state:
`battery_not_responding_<subentry>`, `battery_unreadable_<subentry>`
(`BatteryRuntime.unreadable_since` ≥ 5 min) and `grid_meter_stale` (active
mode and `ControlStatus.GRID_STALE`). It creates or deletes only on changes;
the first update after a start deletes stored issues that no longer apply.
`async_remove_entry` deletes all SLEMS issues. The end of a balancing run
(not `cancelled`) creates a `persistent_notification` (one id per battery);
its texts come from the `exceptions` translations
(`async_get_translations`) in the server language, with the initial delta
stored in the balancer at the start.

Simulator: while `SIM_FAULT_FILE` (default `/tmp/venus_fault`) exists in the
container, the simulated Venus accepts commands but delivers 0 W
(`docker compose exec venus-sim-2 touch /tmp/venus_fault`).

### Cell balancing

`cell_balancing.py` (monitor and state machine, no HA dependency),
`coordinator.py` (`_update_cells`, `start_balancing`, `end_balancing`),
`controller.py` (`_async_apply_balancing`). Based on the cell balance monitor
and `blueprints/marstek_active_balance_blueprint.yaml` of Omnibattery.

- **Top measurement** (`CellMonitor`): after the highest cell reached 3.60 V
  or the SoC 99.5 % (BMS cut-off), |power| ≤ 10 W for 60 s → one measurement
  per top (source `rest`); leaving the top window (< 3.49 V) cancels it. As
  in Omnibattery (`async_record_top_balance_measurement`); status limits
  200/230/250 mV and the balancing suggestion from 230 mV follow its code
  (Marstek factory spread about 180 mV at the top; the 50/100/150 mV of its
  documentation are outdated). A balancing
  run records its own measurements (source `balancing`) and pauses the
  monitor meanwhile. Stored with the learned data.
- **Run** (`CellBalancer`, one step per battery poll, 5 s): phases
  `pre_top_charge → charge → wait_measure → discharge → charge … →
  final_discharge → done`. Constants at the top of the module (Omnibattery
  defaults). `charge` detects a BMS refusal (3 samples ≤ 10 W after 10 s
  grace) and lowers the retry voltage by 10 mV (≥ 3.40 V).
- **Membership**: `BatteryRuntime.balancer` is the run; `participating` is
  false while it exists, except during the ramp-out of a discharging battery
  (`controller.disable_battery`, as for disabling). After the ramp-out the
  battery is not released; the run takes over.
- **Energy balance**: the balancing battery's charge stays a load in
  `available_power_w` (the other batteries cover it); its discharge
  (`min(0, power)`) is removed from `available_power_w`, so it is fed in and
  not charged into the other batteries. `pre_top_charge` takes the surplus
  `_balancing_surplus_w` = −grid (filtered) + power of the batteries in normal
  operation + controlled consumers + own power, i.e. before the other
  batteries and consumers; clamped to 95 W … max charge power.
- **Forecast**: `_balancing_charge_wh` (capacity × missing SoC / efficiency
  while `pre_top_charge` or `charge`) is subtracted from the expected surplus
  and added to the energy grid friendly charging must fill.
- **Commands**: `_async_apply_balancing` runs before the normal set points;
  dead band 25 W, keep-alive 60 s. When a run ends (done, cancelled, timeout,
  telemetry), the battery is released (`release_control`, as the Omnibattery
  blueprint releases manual mode) and planned again in the same cycle if it
  can be read.
- **Pause**: outside operating mode *active* and during the ramp-out the
  run gets no steps (phase shown as `waiting`); the timers of the current leg
  (engage grace, measurement rest) start again on resume. Starting requires
  operating mode *active* (`ServiceValidationError`).
- **Persistence**: phase, retry voltage, last delta and start time are
  stored (key `balancer`, saved 5 s after start, phase change and end); a
  restored run continues in its phase with fresh leg timers.
- **Limits**: `MAX_RUN_S` 24 h (wall clock since the start, also across
  restarts); unreadable battery or missing cell voltages end the run at once
  (as in the blueprint).
- **Simulator**: `SIM_CELL_OFFSET_PCT` (SoC lead of the high cell),
  `SIM_BLEED_PCT_PER_MIN` (BMS balancing speed, much faster than reality);
  a small `SIM_CAPACITY_WH` makes a run take minutes.

### Grid friendly charging

`grid_friendly.py`, switch *Grid friendly charging* (default on). Every plan
computes the feed-in limit `T` (sensor *Feed-in limit*):

```
surplus_h = corrected PV forecast_h − consumption forecast_h   (remaining hours today, > 0)
needed    = energy to full / charge efficiency + grid friendly buffer
T = max { T : Σ min(max(0, surplus_h − T), max_charge) · hours_h ≥ needed }
```

found by bisection (10 W resolution). `None` if even `T = 0` is not enough
(charge at once, `feed_in_limit_reason` "not_enough_surplus"; also
"disabled" and "no_forecast", attribute `reason` of the sensor, shown as text
in the overview tile); with a full battery `T` is the highest surplus. Without a
consumption forecast the current uncontrolled load is assumed per hour.

In the allocation, only when the charge is secured: the battery charge is
capped at `remaining − T` (strategy *grid_friendly* while the cap limits);
the rest goes to the consumers by priority, then to the grid. Not secured →
battery priority as before, no cap.

PV correction: ratio of today's measured PV energy to the forecast until now,
limited to 0.5–1.2, used once the forecast until now exceeds 1 kWh. Idea for
later: a higher upper limit (e.g. 1.5); on a morning where the forecast is far
too low the plan and the feed-in limit are otherwise too cautious. The
energy of today is read at startup from the 5 minute statistics of the PV
power sensor (`energy_from_means`; the minutes since the last statistics
period are bridged with the current PV power) and then integrated from the
live values in memory. Without statistics for the PV sensor (no
`state_class`) it is only complete from the next midnight on.

Effect (`tests/test_grid_friendly.py::test_day_simulation_absorbs_the_peak`,
15 min steps, clear day, 8 kWp, 500 W load, 10 kWh battery from 20 %):
charging early leaves the full 6.5 kW peak, grid friendly 5.4 kW; both fill
the battery. The broader the peak and the larger the surplus compared to the
battery, the smaller the cut.

Possible extensions: take the planned consumers into account in the
surplus; use 15 minute forecast periods.

### Dashboard

`panel.py` registers the sidebar panel (`panel_custom`, URL `/slems`) and
serves `frontend/` under `/slems_static` (registered once per HA run; the
module URL carries the file's modification time to bust the browser cache).
The panel config passes what the frontend cannot find itself: system device
id, batteries and consumers with their device ids and the consumers' power
entities. The panel is removed on unload and re-registered on every setup.

`frontend/slems-panel.js` is a plain custom element without build step:

- Entities are found via `hass.entities` (platform `slems`, translation key,
  device id), so renamed entity ids do not matter. Values are formatted with
  `hass.formatEntityState` (translated states, units, locale).
- Rendering is split into sections whose markup is only replaced when it
  changed (`_setSection`), so inputs and the chart hover survive the frequent
  `hass` updates. Events are delegated on the content element.
- Energy flow: rounded boxes in a CSS grid (PV on top; grid, hub, house in
  the middle; one box per battery below the hub, consumers stacked below the
  house). The DOM is built once per set of batteries/consumers
  (`_flowSkeleton`), afterwards only texts, the SoC bars and classes are
  updated (`_updateFlowNode`), so the animation runs smoothly. Connectors are
  drawn in an SVG overlay from the measured box positions (`_layoutFlow`,
  again on every resize): orthogonal paths with rounded corners
  (`roundedPath`), each defined in the direction of positive power; negative
  power reverses the dash animation. Speed and line width follow the power
  (speed quantised so the animation does not restart). Battery flow uses the
  AC power when the driver reports it. If the batteries do not fit side by
  side with at least `MIN_FLOW_BOX_W` (130 px) each, `_layoutFlow` stacks them
  (class `stacked`); their connectors then run down a trunk left of the boxes
  (the consumers' trunk is on the right).
- The crossing of the flow lines shows the SLEMS icon:
  `frontend/slems-icon.svg` is a copy of `assets/icon.svg` (HACS installs only
  `custom_components/slems`); update both together.
- Day chart: data from the attribute `day_plan` of the sensor *Feed-in limit*
  (24 rows: corrected PV forecast, consumption forecast, planned charging,
  projected total SoC at the end of the hour; `day_plan_tomorrow` for the
  *Tomorrow* view with the uncorrected PV forecast) plus the hourly means of
  today from the recorder (`recorder/statistics_during_period` for the SLEMS
  PV, house and total SoC sensors, refreshed every 5 min). Drawn as SVG in
  real pixels (redrawn on resize): energy per hour on the left axis (kWh;
  the hourly mean power in W equals the energy in Wh), the total SoC drawn on
  top with its own scale on the right (0–100 %, labels only, the grid lines
  belong to the kWh axis; the user's choice over a separate panel);
  forecast dashed, measured solid, planned charging as bars;
  the SoC forecast starts at the current total SoC (tomorrow: at today's last
  projected value); crosshair tooltip per hour (touch: `pointerdown` shows and
  keeps it, also across redraws via `_tooltipAt`; a tap elsewhere hides it;
  `touch-action: pan-y` keeps vertical scrolling); table view
  as accessible alternative.
- SoC projection (`soc_projection.py`, run with every plan): hour by hour
  until the end of tomorrow. Surplus hours charge the planned amount
  (`planned_charging`; each day planned at its first surplus hour from the
  projected SoC, today with the controller's feed-in limit, tomorrow with a
  feed-in limit computed for tomorrow if grid friendly charging is on);
  deficit hours discharge like the allocation (peak shaving below the
  threshold only above the import limit; night discharge via
  `plan_night_discharge` per hour, the extra part stops at its target).
  One-way efficiency both ways, max charge/discharge power, 0–100 %.
  Controllable consumers, grid targets and balancing batteries are ignored.
- Colours: roles PV / battery / grid / consumer / house use categorical slots
  validated for colour vision deficiency in both modes (adjacent pairs, see
  the `COLORS` table); dark values when `hass.themes.darkMode`. Everything
  else uses the HA theme variables. Some light-mode colours are below 3:1
  contrast on the card; legend, direct labels and the table view carry the
  identity.
- Strings: English and German inside the file (`STRINGS`), picked from the HA
  language.
- Switches marked with `data-confirm-off` (battery *Enabled*) are kept on and
  open a native `<dialog>` first; only the confirmation turns them off.

Screenshots for checking the layout can be taken with Playwright against the
dev instance (log in as the dev user, open `/slems`, click the tab buttons in
`slems-panel`); check light, dark, German and a 390 px wide viewport.

### Control structure

The controller is not a textbook PID. Per cycle it computes the battery power
that would bring the grid to its target (feedforward from the energy
balance), then moves only a share of the way there:

```
seen      = battery commands issued at least the battery response time ago
available = -grid_filtered + seen + controllable consumer power
target    = allocation(available, ...)            # absolute set point
new       = latest + gain · (target - latest)     # latest = last commanded total
new       = limit_discharge_export(new, ...)      # hard export limit
```

- **Feedforward** gives an absolute target each cycle, so there is no
  steady-state error and no integral part is needed. An I part would also
  wind up against the dead band and the averaging window.
- **Dead time compensation** (Smith predictor principle): the energy balance
  uses the command the meter can already show, so a command is not counted
  again while it is on its way. This removes the main cause of oscillation.
- **No D part**: smart meter values are noisy (switching loads); a derivative
  would amplify that noise. Omnibattery uses a PD controller (Kp 0.35,
  Kd 0.3) because it corrects on the error only, without an energy balance.
- **Proportional gain** absorbs what the model does not know (variance of the
  response time, meter jitter, battery ramping).

#### Adaptive gain

`adaptive_gain.py`, active while the switch *Automatic control gain* is on.
Input per control cycle: the correction of the total battery power
(`new − latest`) whenever set points were sent. Corrections below 50 W carry
no direction and are ignored; corrections more than 15 s apart belong to
different movements (history cleared).

| Rule | Condition | Action |
|---|---|---|
| Oscillation | 4 significant corrections alternate in sign, each ≥ 70 % of the previous (not dying out) | gain × 0.8 |
| Sluggish | 5 significant corrections in a row with the same sign, each smaller than the previous (slow approach to a fixed target) | gain + 0.05 |
| Cooldown | after every adjustment | history cleared, no change for 30 s |

Bounds 0.2–0.9. The asymmetry (fast down, slow up) is deliberate: swinging
costs grid import/export and battery cycles, a slower approach only a little
self consumption.

Why these patterns: a well damped loop answering a step gives corrections of
one sign that shrink geometrically (ratio 1 − gain). Alternating signs that do
not decay can only come from the loop itself; outside load changes produce a
series of one sign per change. A slowly moving target (PV ramp) produces
corrections of similar size, which neither rule matches.

The number *Control gain* is the start value: changing it calls
`AdaptiveGain.reset`. With the switch off it is used directly. The current
gain is stored with the battery learning data (`.storage`, key `control`) and
restored at startup; restoring the number entity does not reset it.

Tests: `tests/test_adaptive_gain.py` covers oscillation, decaying
alternation, external load steps, slow approach, ramps, cooldown, gaps and
bounds. In the dev environment the loop with learned response time does not
oscillate even at gain 0.9, so the decrease can only be seen there with an
artificially wrong response time.

Possible extensions: separate gains for charging and discharging; adapting
the averaging window; using the scatter of the learned response time to
limit the maximum gain.

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
refitted every hour (minute 5), right after midnight (so "tomorrow" is
covered), after a restart and when the vacation switch changes; fitting runs
in the executor. Days are always derived in local time
(`start_of_local_day(as_local(...))`): `start_of_local_day` takes the date of
the given datetime as is, so a UTC time between 00:00 and 02:00 local time
would give the previous day.

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
±30 %. Only numpy (bundled with Home Assistant) is used.

#### Holidays (open)

The day type is derived from the weekday only (`models.day_type`): Monday to
Friday are workdays, Saturday and Sunday weekend. Public holidays are
therefore forecast with the workday profile, and past holidays are learned as
workdays.

Impact: on a holiday the forecast follows the workday shape (e.g. lower
consumption during working hours) although the household behaves like on a
weekend; the short-term correction (last 72 h) partly compensates on the
following days. Each holiday also slightly distorts the workday profile it is
learned into (weighted by age, so the effect fades).

Options, in order of preference:

1. **Workday binary sensor** (HA integration *Workday*, configured for
   Austria / Vorarlberg): optional setting in the system options. Its state
   covers today; for tomorrow the integration offers the action
   `workday.check_date`. History would have to come from the recorder states
   of that sensor, which are only kept for the recorder's purge period.
2. **`holidays` Python package** (already a dependency of Home Assistant via
   the Workday integration): compute holidays for past and future days
   directly from country and subdivision (derived from the HA location, or a
   setting). No history problem; covers training and forecast alike.
3. **Calendar entity** (e.g. a holiday or school holiday calendar): most
   flexible, also for school holidays, but the most work for the user.

Recommended: option 2 for the training data and the forecast, with the
country/subdivision taken from the HA configuration and overridable in the
options; holidays then use the weekend profile. School holidays or other
special days could later come from a calendar entity (option 3).

### Automatic peak shaving limit

`peak_shaving.py` (pure). `PeakProfile` holds the 5 minute means of the first
house source with data of the last 10 days (built with every forecast
refresh), sorted per local hour with prefix sums, so the mean energy above a
limit per hour is a binary search. `hours_until_refill` runs from now until
the first hour whose PV forecast exceeds the consumption forecast (only the
current hour during a surplus, at most 36 h). `auto_limit` bisects the lowest
limit (50 W) whose expected energy above it fits into the usable energy × (1 −
reserve); the usable energy uses min(SoC, threshold), so above the threshold
the limit shows what applies once it is reached. The dashboard shows it read
only in place of the fixed limit. `SlemsCoordinator._peak_shaving` returns the threshold (never below
the capacity weighted minimum SoC) and the limit in effect; both replace the
settings in the allocation and the SoC projection. Sensor
`peak_shaving_limit` (W; attributes for the note on the energy above the
minimum SoC).

### Forecast accuracy

`forecast/accuracy.py` (pure). `backtest_consumption` runs with every forecast
refresh (hourly, executor): for each of the last 14 complete days the base
load profile and the heat pump model are fitted with `now` = that day's
midnight and compared hour by hour with base + heat pump history (days with
≥ 22 hours only). Daily error = |forecast − actual| / actual, bias = signed
mean, hourly error = Σ|error| / Σ actual. The heat pump uses the measured
daily mean temperature (no historical weather forecast). Expected error of
tomorrow = mean |daily error| of the same day type (≥ 2 days, else all) ×
tomorrow's forecast. `PvAccuracyTracker` stores per day the raw PV forecast
of the day (first poll before 06:00) and, at the midnight rollover of the PV
integration, the produced energy if the day was complete; kept 60 days in the
coordinator store (key `pv_accuracy`). Sensors `consumption_forecast_accuracy`
and `pv_forecast_accuracy` (100 % − mean daily error; details as attributes,
the day list is not recorded); dashboard card below the day chart.

## Marstek Venus E 3.0

Source: Omnibattery `const/registers_v3.py`, `drivers/marstek.py`,
`infra/modbus_client.py`.

| Register | Key | Type | Scale | Notes |
|---|---|---|---|---|
| 34002 | battery_soc | uint16 | 0.1 % | fallback 37005 (1 %) if it does not answer |
| 30001 | battery_power | int16 | 1 W | + charge / − discharge |
| 30006 | ac_power | int16 | 1 W | + discharge / − charge; the SLEMS sensor *AC power* keeps this sign (convention of the HA energy dashboard for battery power), internally `ac_power_w` is +charge |
| 30100 | battery_voltage | uint16 | 0.01 V | |
| 35000 | internal_temperature | int16 | 0.1 °C | |
| 35100 | inverter_state | uint16 | | 0 sleep, 1 standby, 2 charge, 3 discharge, 4 backup, 5 OTA, 6 bypass |
| 34003 | cycle_count | uint16 | 1 | charge cycles counted by the battery (as in Omnibattery; not yet verified on the device) |
| 33000 | total_charging_energy | uint32 | 0.01 kWh | |
| 33002 | total_discharging_energy | int32 | 0.01 kWh | |
| 37007 | max_cell_voltage | int16 | 0.001 V | |
| 37008 | min_cell_voltage | int16 | 0.001 V | |
| 42000 | rs485_control | uint16 | | write 0x55AA = enable, 0x55BB = disable |
| 42010 | force_mode | uint16 | | 0 none, 1 charge, 2 discharge |
| 42020 | set_charge_power | uint16 | W | max 2500 |
| 42021 | set_discharge_power | uint16 | W | max 2500 |
| 44002 | max_charge_power | uint16 | W | not read; 2500 on the real device |
| 44003 | max_discharge_power | uint16 | W | not read; 2500 on the real device |

Control sequence (`apply_power`): enable RS485 control → discharge set point →
charge set point → force mode (last). `release_control` zeroes the set points,
sets force mode none and disables RS485 control.

Firmware quirks handled in `modbus_client.py`:

- exception responses carry a wrong MBAP length byte (patched via `trace_packet`),
- ≥150 ms between frames,
- a single TCP connection slot that is released slowly → wait 1 s before reconnecting,
  never reconnect per request,
- retries inside pymodbus (same transaction id) so late replies still match.

SoC register: Omnibattery's code uses 37005 for v3, its register table
(`site-docs/reference/registers.md`) lists 34002 for `e_v3`. On a real Venus
E 3.0 (2026-09-24, `tools/read_registers.py`) 37005 and 32104 show the state
of charge in whole percent (55), 34002 the same value in 0.1 % (554 = 55.4 %);
34002 followed a change from 54.4 to 55.4 % together with the others. SLEMS
reads 34002 for the finer resolution.

Verified on the same device at rest (2026-09-24): 30100 = 5311 (53.11 V),
35000 = 324 (32.4 °C), 35100 = 1 (standby), 42000 = 21947 (0x55BB, RS485
control disabled), 42010 = 0 (force mode none), 44002/44003 = 2500 W,
33000 = 6148 and 33002 = 4661. The Marstek app showed 12.64 / 6.35 kWh at the
same time, without a common factor (it counts over another period). With
0.01 kWh the counters give a round trip efficiency of 80 %, with 0.1 kWh 76 %,
with 0.001 kWh more than 100 % (ruled out). Delta test: charging from 55 to
65 % (0.51 kWh stored) raised 33000 from 6148 to 6206, i.e. 0.58 kWh with
0.01 kWh (one way efficiency about 88 %); 33002 stayed unchanged. The scale
0.01 kWh is confirmed.

Open points to verify on the real device:

- Omnibattery marks the v3 map as partly untested; still open: sign and
  difference of 30001/30006 while charging and discharging.
- The device holds only one connection: while Omnibattery is running, a Modbus
  battery in SLEMS cannot connect. Use the read-only *HA entities* battery
  during the transition.

## Tools

`tools/read_registers.py` reads holding registers of a Venus repeatedly and
shows whether their raw values are identical (standard library only,
Python ≥ 3.10):

```bash
python3 tools/read_registers.py <battery-ip>                       # 37005 and 34002
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
| 2026-09-23 | Battery rotation ramp by rate (W/s) with maximum duration instead of a fixed duration. |
| 2026-09-23 | Disabling a discharging battery hands over within 5 s. |
| 2026-09-23 | Real-time controller event driven on the grid meter; batteries released when the meter is stale. |
| 2026-09-23 | Damped correction (control gain) plus learned response times instead of a fixed settle time; meter cadence learned from its reports. |
| 2026-09-23 | Dashboard as own sidebar panel (web component without build step), like Omnibattery. |
| 2026-09-23 | Grid friendly charging via a feed-in limit from PV and consumption forecast, recalculated every cycle; only when the charge is secured. |
| 2026-09-23 | No PID: feedforward + dead time compensation + proportional gain; the gain adapts automatically (oscillation → lower, sluggish → higher). |
| 2026-09-23 | Distribution between batteries by minimal conversion losses (learned per battery), rotation by SoC threshold with minimum interval and ramped transition. |
| 2026-09-23 | Consumption forecast from long-term statistics; optional history entity for the house consumption and optional outdoor temperature sensor, otherwise SLEMS records the weather temperature itself. |
| 2026-09-23 | Night discharge: evenly spread until PV exceeds consumption, target = reserve raised to what tomorrow's PV can refill; grid target ignored, maximum export respected. |
| 2026-09-24 | Active cell balancing after the Omnibattery blueprint; the balancing battery takes the PV surplus first (≥ 95 W, other batteries cover the rest), its discharge is fed in, maximum 24 h, starts only in operating mode *active*, ends at once on unreadable telemetry, survives restarts. |
| 2026-09-24 | Venus SoC from register 34002 (0.1 %), fallback 37005 (1 %); both verified identical on a real device. |
| 2026-09-24 | Battery protection after Omnibattery: SoC window per battery (default 12–100 %, 2 % re-entry), power limits per battery (e.g. 800 W), optional temperature charge limit (high derate plus low-temperature stop), detection of non-delivering batteries (3 failures → wake, then 5 min exclusion) and read-back confirmation on complete writes. |
| 2026-09-24 | Grid friendly charging has its own buffer (*Grid friendly charging buffer*), separate from the charge secured buffer (which also sets the night discharge target). |
| 2026-09-24 | The night discharge reserve counts on top of the minimum SoC (the energy below it cannot be used). |
| 2026-09-24 | Ongoing problems as repair issues (they clear themselves), events (end of a balancing run) as persistent notifications. |
| 2026-09-24 | Consumers with a cycling thermostat rest instead of being saturated; water heater entities can block consumers (mode `off`). |
| 2026-09-24 | Consumer control can be switched off per consumer; switching off sets it to 0 W once, then SLEMS only measures it. |
| 2026-09-24 | The *AC power* sensor stays +discharge / −charge so it can be used as battery power in the Home Assistant energy dashboard; the SLEMS dashboard converts it. |
| 2026-09-25 | Top cell delta measured like Omnibattery at 3.60 V / BMS cut-off after 60 s rest; status limits 200/230/250 mV, balancing suggested from 230 mV; balancing target stays 30 mV. |
| 2026-09-25 | Forecast accuracy: consumption by backtest over 14 days (immediately available), PV by recording the forecast of each day (past forecasts are not available). |
| 2026-09-25 | Efficiency source: battery counters recommended for the Venus (accurate at once over the whole operating time), learned for read-only batteries; the distribution between batteries uses the separate AC/DC loss curve, not the efficiency. |
| 2026-09-25 | Peak shaving: threshold stays absolute but not below the minimum SoC; optional automatic import limit from the 5 minute consumption peaks until PV refills, with a safety reserve. Number fields lose the focus on the mouse wheel (it changed and saved values while scrolling). |
