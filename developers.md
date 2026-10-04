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
| `evcc` (profile `evcc`) | evcc 0.316 for the [evcc guide](docs/wallbox-evcc.md), host network, configured in its web UI (data in `dev/evcc`, not committed); start with `docker compose --profile evcc up -d evcc`. Home Assistant reaches it at the bridge gateway (e.g. `http://10.240.6.1:7070`), the browser at the host IP, which evcc also uses for the Home Assistant login. [ha-evcc](https://github.com/marq24/ha-evcc) is copied into `dev/config/custom_components` by hand. |
| `sunspec-meter` | SunSpec meter in the layout of a SolarEdge inverter with two meters (`dev/sunspec_meter/meter_sim.py`), host `sunspec-meter`, port 502, export positive. An automation of the dev instance writes the simulated grid power through port 5021 at every change of `sensor.smart_meter_power`. |
| `tests` | pytest in an image based on the HA image, so Python and HA versions match production. It runs as root on the mounted repository, so it writes no bytecode and no pytest cache (they would be owned by root). |

`dev/config/configuration.yaml` provides simulated measurements driven by
`input_number` sliders. The smart meter is a closed loop: house load + heat
pump + heating rod + wallbox − PV − AC power of the simulated batteries, updated every
second. The AC power is read by the HA Modbus integration from a read-only
port of the simulators (5020) every second, so the loop behaves like a real
installation with a fast meter. Further: PV, a heat pump (power + energy) and a
heating rod controlled via `input_number.sim_heating_rod_setpoint` that can be
blocked via `input_boolean.sim_heating_rod_blocked`, and a wallbox
(`sensor.wallbox_power` = `input_number.sim_wallbox_current` × 230 V ×
`input_number.sim_wallbox_phases` while `input_boolean.sim_wallbox_enabled`
and `input_boolean.sim_car_connected` are on) for a consumer with current
control (start/stop entity: the enable switch). For a PV forecast add the
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
  feed_in_cap.py     feed-in cap planning (space needed, export before the peak)
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
  diagnostics.py     diagnostics download (entry and battery devices), incl. internal states
  simulation.py      websocket command slems/simulate for the simulation tab
  learning.py        learned values: forecast buffers, capacity, grid targets, timing, consumers
  select.py          operating mode (off / simulation / active)
  switch.py          vacation, import peak shaving
  number.py          numeric runtime settings (averaging window, allocation, peak shaving)
  binary_sensor.py   battery charge secured
  util.py            unit conversion of HA states, clamping to number entities
  entity_match.py    suggests the entities of a battery device per role (config flow)
  drivers/
    base.py          BatteryDriver contract (read_telemetry, apply_power, release_control)
    marstek_venus_e3.py
    modbus_client.py Modbus TCP link with Venus firmware quirks
    ha_entities.py   battery backed by HA entities (read-only, set point, split, script)
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
  30006 (AC) and 30001 (DC) are exactly that (verified, see *Marstek Venus E
  3.0*).
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

### Battery support

`battery_support.py`, per consumer behind the meter (select `battery_support`,
default *always*). `SlemsCoordinator._update_unsupported` collects the
consumers the batteries must not cover right now (*never*; *automatic* while
`SupportBudget` is used up; not a forced run with the source *battery*):
controllable ones by id (their planned power), the others by measured power.
`forecast_plan` computes the budget (`_support_budget`, only with a consumer
on *automatic*) and passes the forced runs with the source *grid* of
*never* / *automatic* consumers to the projection (`grid_load`,
`budget_load`).

### Tariffs

`tariff.py`: a tariff (config subentry type `tariff`) is a list of bill lines
(side, group, unit ct/kWh or €/year per day, optional months / weekdays / time
window, `valid_from`) with VAT per side and group. Per hour the most specific
matching line of a name counts, so a window line replaces the general line of
the same name in its window. `compute_bill` prices hourly import / export
(`energy_history.async_grid_energy`: changes of the energy counters, else the
hourly mean of the grid power). The subentry flow is an assistant: lines are
added from a menu, and *check* compares a bill period with the recorded energy.

### Market prices

`market_prices.py`: day-ahead prices (€/MWh per quarter hour, hourly prices
repeated per quarter) from APG (one local day per request; rows by position,
so the clock change needs no parsing of "2A"/"2B"), SMARD (weekly files from
`index_quarterhour.json`) or Energy-Charts (`bzn` AT / DE-LU, 28 days per
request). Nothing is fetched while the switch `market_prices` is off; the
select `price_source` defaults by `hass.config.country`. `MarketPrices` keeps
the last 365 days in a store (dense array from the first quarter hour),
fetches the missing days newest first (first request only today and
tomorrow) and checks every hour at :17, the next day from 13:00. Dynamic
tariff units `spot` and `market_month` use the hourly means; the monthly
market price is the mean weighted by the export unless entered per month.

### Tariff comparison

`tariff_comparison.py` (websocket `slems/tariff_comparison`): the hourly
import / export of the last 12 months (`energy_history`) split per local month
and priced with each tariff (`compute_bill` in the executor, dynamic items with
the stored hourly market prices). The result is cached for 10 minutes on the
coordinator; a changed tariff clears it (`tariffs_changed`).

Backtest (`price_backtest.play`): hourly house consumption (first house
statistic with data, as the forecast) and PV through a battery model (current
battery group: capacity, SoC window, power, efficiency, wear), once as usual
and once with `plan_grid_charge` per deficit run with the tariff's hourly
import prices (perfect forecast). Both priced with `compare`; the difference
per month and tariff is `savings`. Runs in the executor (about half a second
for a year), cached with the comparison (30 minutes).

### Recorded quarter hours

`grid_quarters.py`: every grid value (entity or Modbus, via
`_integrate_export`) counts until the next one, at most 300 s, split at the
quarter hour boundaries of the wall clock into import and export. A quarter
counts with 90 % coverage. Kept for 400 days in its own store
(`slems.<entry>.grid_quarters`, dense arrays), saved every 15 minutes and at
unload (`Store.async_delay_save` would restart its timer with every value).
`energy_history.async_grid_energy` uses an hour per quarter when all four are
complete, scaled to the hourly change of the energy counters if configured;
otherwise the hour from the counters or the hourly mean of the grid power.
`GridEnergy.lengths` says the length of every period, so
`MarketPrices.period_means` gives the matching market price.

### Price hold

`price_hold.py`: until the next refill (`battery_support.next_refill`) the
usable energy (above the minimum SoC, AC) against the hourly deficits
(consumption incl. target load minus PV minus the grid load of battery
support). If it does not last, the most expensive hours are covered (greedy
by import price; `price_chart.hourly_import_prices` of the first current
tariff, cached per quarter hour), the hours cheaper by the minimum gain are
held (limit 0 W), and the partly covered hour gets its allotted mean power if
a more expensive covered hour follows. `forecast_plan` adds the limits as
grid load to the projection; `allocate(discharge_limit_w=…)` caps the deficit
cover (strategy `price_hold`), the night discharge is off in such an hour,
peak shaving stays. Without a price for every hour until the refill: no hold.

Daily targets with the source "grid": `SlemsCoordinator._price_window` moves
`TargetState.latest_start` to `price_hold.cheapest_start` (quarter hour
candidates from now to the latest start, run length = deadline − margin −
latest start, mean of the hourly prices) when the forecast surplus is short
for the target plus filling the batteries, and marks `price_window`. The mode
becomes forced from that start; `forced_load` and the plans follow it.

### Grid charging

`grid_charge.py`: dynamic programming over the stored energy (1 % steps of
the capacity, at least 25 Wh) per hour from now (only in a deficit) until the
refill (`next_refill`) or the last known price (no refill within 36 h: winter).
Options per hour: cover the whole deficit (exact; the cost to go is
interpolated between the levels, so rounding does not favour a part), a part
in steps, hold, or charge in steps. Costs: price × import, per kWh charged
wear + minimum gain + a tiny early-charging cost (later wins), per kWh kept the
minimum gain; energy left at the end is worth nothing at a refill, else the
lowest price × efficiency. Limits: grid charge SoC (also below the space of
the feed-in cap at the horizon), charge power, import limit of peak shaving.
The plan replaces the price hold when grid charging is on; `forecast_plan`
passes its charging to `project_soc(grid_charge=…)` and its limits as grid
load, `allocate(grid_charge_w=…)` charges outside a surplus (strategy
`grid_charge`).

Feeding in from the batteries (`battery_export`): an extra option per hour
after the whole deficit is covered, in steps, valued with the hour's export
credit (`hourly_import_prices(..., Side.EXPORT)`) minus the minimum gain; not
below `export_floor_wh` (minimum SoC + morning reserve of tomorrow's
consumption), at most `discharge_max_grid_export_w` and the cap limit.
`allocate(battery_export_w=…)` covers the deficit and feeds in the planned
power on top (strategy `battery_export`); `project_soc(battery_export=…)`.
`battery_export_effective`: the current tariff has a spot feed-in item.

### Measured saving

`price_savings.PriceSavings` follows runs: from the first cycle with a price
plan (hold, grid charging, feed-in) until it is gone (PV takeover) or 24 h,
with the stored energy at the start; `acted` once the strategy was
`price_hold`, `grid_charge` or `battery_export` in active mode. Finished runs
with an action wait until their hourly statistics exist (evaluated at :40);
`price_backtest.measured_saving` compares the recorded import / export
(`async_grid_energy`, quarter hours summed per hour) priced with the current
tariff against `play(..., prices=None)` from the start energy. The monthly
total is kept in the coordinator store (`price_savings`), sensor
`price_saving`.

### Price chart

`price_chart.py` (websocket `slems/price_chart`, `day` today / tomorrow): per
quarter hour of the local day (iterated in UTC, so clock changes give 92 / 100
slots) `tariff.kwh_price` of the first current tariff for import and export
(incl. VAT, without yearly items) and the stored market price. The panel draws
it below the day chart with the margins of the day chart
(`_chartGeometry`), so the hours line up.

### Night discharge

`night_discharge.py`, optional (switch, off by default). Without it the
batteries only cover the house consumption at night.

- **Until**: start of the first hour in which the PV forecast exceeds the
  consumption forecast (batteries would start charging again).
- **Target**: *reserve* = % of tomorrow's forecast daily consumption, on top
  of the energy below the minimum SoC of the batteries (not usable). If
  tomorrow's PV surplus (from the crossover until the end of the day, minus the
  safety buffer, times the charge efficiency) cannot refill the batteries from
  there up to their maximum SoC, the target is raised to the level from which
  it can.
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
  change until the meter moved 60 % of it (smaller corrections the same way
  keep the measurement, one the other way ends it; stored with the gain); consumer response times
  (`DirectionalResponse`) from commands with ≥ 100 W change until the
  consumer's own power sensor moved 60 % (at the meter ≥ 300 W), switching on
  and off learned apart, each measured up to 5 min (`CONSUMER_MAX_RESPONSE_S`):
  switching on includes the start delay of a device (compressor), switching
  off is mostly the reporting delay; a command the other way drops a pending
  measurement. `seen_consumer_power` uses the times of the command's
  direction; until the meter time is learned, the consumer's own time (the
  start delay is seen by the meter too), then the battery response. Shown as
  attributes of *Planned power* (`response_on_s`, `response_off_s`,
  `grid_response_on_s`, `grid_response_off_s`).
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
  5 × its response time of switching off, and at least 1.5 × its learned
  start time (30–600 s; own thermostat, or the device did not start), counts
  as saturated for 15 min and is planned like an uncontrolled load; its last
  command stays on the device.
- **Control active** (`ConsumerControlSwitch`, RestoreEntity): the ids of
  switched-off consumers are in `SlemsCoordinator.consumer_control_disabled`
  and `SystemSnapshot.control_disabled`; `is_controllable_now` is false for
  them. Switching off calls `async_release_consumer` (0 W clamped to the
  entity / `turn_off`, only in active mode).
- **Resting** (consumers with `thermostat_cycles`, instead of saturation):
  < 10 % of the command for longer than 2 × response time of switching off
  (10–60 s) →
  `RealTimeController.resting`; the consumer stays in the allocation and keeps
  its command, `plan` adds its unused power (allocated − measured) to the
  battery power (up to the maximum charge power). In the controller cycle a
  resting consumer counts at its command (`consumer_power_seen`), because its
  own sensor shows a restart seconds after the grid meter; the command is
  taken off the battery power again, since it is not in the grid power. Ends
  with the first sample with power. A water heater as block entity blocks in operation mode `off`;
  its temperatures are not used (e.g. my-PV measures at the element and
  cycles).
- **Grid power over Modbus** (`grid_meter.py`, optional): polls a SunSpec
  meter (total power and scale factor in one request) through
  `homeassistant.components.modbus.async_get_unit`, each read limited to 1 s.
  Every value goes the way of an entity change (meter cadence, grid filter,
  response learners, `request()`); while a Modbus value is fresh
  (max(3 × interval, 5 s)) the entity's reports are ignored, otherwise the
  entity is the source (`SlemsCoordinator.grid_power_w`, `grid_age_s`). After
  3 failed reads in a row the polling backs off from 5 to 60 s. The config
  flow walks the SunSpec model chain (`find_meters`) and finds the sign by
  comparing with the entity (`detect_inversion`), both over
  `async_get_temporary_unit`. The response times are stored with the source
  they were learned with and not restored for another one.
- **Bad weather mode** (`bad_weather.py`, switch `bad_weather`): until
  `BadWeatherMode.until` the feed-in limit is off (reason `bad_weather`) and
  no night discharge is planned; the projection gets the same end
  (`ProjectionSettings.bad_weather_until`). The end is the evening of the day
  chosen when switching on, recomputed from every forecast as long as it still
  has a surplus hour of that day; stored in the control data.
- **Grid meter stale** (no report within max(60 s, 10 × meter interval),
  based on `last_reported`, or of the Modbus value while it is the source): all batteries are handed back to their internal logic until the meter reports
  again (status *grid meter stale*).
- **Maximum export while discharging** is applied as hard limit on the
  current, unfiltered grid power (`limit_discharge_export`).
- Leaving *active* hands the batteries back immediately; consumers keep their
  last state.

### Battery limits and delivery monitoring

`battery_limits.py` (pure): `SocWindow` (block discharge at ≤ min SoC and
charge at ≥ max SoC below 100 %, release 2 % away), `temperature_factor`
(high limit after Omnibattery's `TemperatureChargeLimitManager`, plus a low
limit with a 5 °C ramp), `TopTaper` (after Omnibattery's full charge
voltage taper: 200 W charge from a highest cell ≥ 3.48 V until < 3.44 V,
always on, controllable batteries with cell voltages only) and
`power_limits` (capability → user power limit → temperature → top taper →
SoC window, with the limiting reason). The coordinator updates
the window and `BatteryRuntime.power_limits` with every poll; the limits
replace the capabilities in `BatteryUnit` (distribution) and `BatteryGroup`
(allocation). `BatteryGroup` carries the capacity weighted `min_soc_pct` and
`full_soc_pct`: `energy_to_full_wh` and `is_full` use the maximum SoC, the
night discharge target (`min_wh`) and the SoC projection the minimum.
The group is full only when every battery is (`all_full`, `battery_full`),
and a full battery adds nothing to `max_charge_w`.
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
stored in the balancer at the start. The feed-in cap problems
(`CapPlan.problems` and the export above the limit) are notifications too,
one id per problem, created when a problem appears and dismissed when it is
gone; they come from the forecast and are not repairs the user can make.
Issues with their ids are deleted on the first update.

Simulator: while `SIM_FAULT_FILE` (default `/tmp/venus_fault`) exists in the
container, the simulated Venus accepts commands but delivers 0 W
(`docker compose exec venus-sim-2 touch /tmp/venus_fault`).

### Communication pause (firmware update)

`BatteryRuntime.paused_until` (wall clock, stored) and `pause_reason`
(`manual` / `firmware_update`). `async_pause_communication` releases the
battery first for a manual pause (not for a detected update: no more traffic),
closes the driver connection and sets the time from
`communication_pause_min`. The poll skips paused batteries (no read, no
`unreadable_since`) and resumes them when the time is over;
`participating`/`plannable` are false meanwhile and a balancing run pauses. The
automatic pause triggers when the telemetry reports `inverter_state`
`ota_upgrade` (register 35100 = 5). Only drivers with `has_connection` get the
switch. Simulator: `SIM_OTA_FILE` (default `/tmp/venus_ota`) makes it report
the update. Device information (`read_device_info`) is read after connecting
and every 6 h and shown in the dashboard's details dialog.

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
- **Last full charge** (`CellMonitor.observe_full`, every poll, also during a
  balancing run and for batteries without cell voltages): the wall clock time
  the battery reached the top (highest cell ≥ 3.60 V or SoC ≥ 99.5 %, or the
  BMS ended the charge near the top: commanded ≥ 100 W, power < 25 W for
  120 s from SoC ≥ 98 % or a cell ≥ 3.45 V, as Omnibattery's BMS cut-off
  detector; that also arms a top delta measurement), without waiting for a
  rest. A new one counts after leaving the top (highest cell < 3.40 V, below
  the BMS end voltage, or SoC < 97 % without cell voltages); after a start while full the
  stored time stays (`_at_top` unknown). Stored under `cell_monitor.last_full`;
  sensor *Last full charge* (timestamp); shown in the battery details only.
- **Regular full charge** (`full_charge.py`, pure): `due_battery` picks one
  due battery (last full charge older than `full_charge_interval_days` or
  unknown; oldest first, then name) among the plannable controllable ones,
  every poll (`_update_full_charge`, `SlemsCoordinator.full_charge_battery`).
  It may exceed its maximum SoC (`power_limits(full_charge=True)`, group
  `full_soc_pct` 100 for it); `BatteryDistributor.distribute(full_charge=…)`
  gives it the charge power first (the rest to the others as usual) and
  leaves it out when discharging while all others are above
  `SPARE_OTHERS_MIN_SOC_PCT` (50 %) and can deliver the power. When its
  `last_full` changes it rests `REST_S` (90 s, zero limits in
  `_battery_units`) so the monitor measures the top delta. Sensor *Last full
  charge* has `due` and `preferred`; the card shows a chip, the overview a
  note after `FULL_CHARGE_NOTE_DAYS` (14, frontend).
- **Run** (`CellBalancer`, one step per battery poll, 5 s): phases
  `pre_top_charge → charge → wait_measure → discharge → charge … →
  final_discharge → done`. Constants at the top of the module (Omnibattery
  defaults). `charge` detects a BMS refusal (3 samples ≤ 10 W after 10 s
  grace) and retries from 3.49 V, or 10 mV below the refusal voltage
  (≥ 3.40 V); refusals do not lower it further.
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

### PV forecast periods

`pv_forecast.py`: SLEMS keys every forecast period by its start. Forecast.Solar
keys it by its end (API documentation: "the value is always for the period
from last timestamp to the timestamp in the key"; in the data the first energy
of a day comes at the first full hour after sunrise, and the last hour before
sunset ends a few minutes before the sunset timestamp). `keyed_by_start`
re-keys the providers in `PERIOD_END_DOMAINS` when the forecast is read: a
period starts at the previous timestamp if that is at most one hour earlier,
otherwise one hour before its end. Other providers are taken as period starts
(not verified for Solcast). `power_lookup` gives the mean power of the period
a moment falls in (period length = gap to the next timestamp, at most 1 h; the
last one gets the median gap); `mean_power` averages it over any interval
(5 minute samples).

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

PV correction (`grid_friendly.py`): the ratio of today's measured PV energy
to the forecast until now, limited to 0.5–1.2, used once the forecast until
now exceeds 1 kWh. A morning says little about the whole day (fog, a hill
shading the first hours), so the ratio is applied per forecast period with a
weight:

```
ratio   = clamp(produced today / forecast until now, 0.5, 1.2)   # pv_correction
elapsed = forecast until now / forecast of the whole day           # pv_elapsed_share
day     = clamp((elapsed - 0.15) / (0.50 - 0.15), 0, 1)            # rest of the day
near    = 0.8 · max(0, 1 - hours_ahead / 2)                        # current and next hour
weight  = max(day, near)                                           # correction_weight
period  = forecast · (1 + (ratio - 1) · weight)                    # corrected_forecast
```

`hours_ahead` is the time from now to the start of the period (0 for the
current and past periods). Example: fog until 8:00, ratio 0.5, 3 % of the day
passed: the current hour is lowered by 40 %, the next by 20 %, noon keeps the
forecast. The constants are `CORRECTION_START_SHARE`,
`CORRECTION_FULL_SHARE`, `NEAR_WEIGHT` and `NEAR_HOURS`. All users of the
corrected forecast (planning, feed-in limit, feed-in cap, night discharge,
projection, day chart, simulation) go through `SlemsCoordinator._pv_native`;
tomorrow is never corrected. The diagnostic sensor *PV forecast correction*
shows the ratio, its attributes the elapsed share and the weights for now and
the rest of the day. Idea for
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

### Feed-in cap

`feed_in_cap.py` (pure), switch *Feed-in cap* (default off), limit =
*PV peak power* × *Feed-in cap limit* at the grid connection point. Every plan
calls `plan_cap` (coordinator `_feed_in_cap`), stored in
`SystemSnapshot.feed_in_cap`.

Steps of 15 min from now until the end of tomorrow. The PV power of a step is
the mean power of the native forecast period it falls in (period length =
gap to the next timestamp, at most 1 h; the last period gets the median gap,
because providers add irregular sunrise/sunset timestamps); today's periods
are multiplied by the PV correction. Consumption is the hourly forecast.

```
excess     = max(0, PV − consumption − limit)
over       = max(0, excess − early)              early: supporting consumers planned from the block start
absorbed   = min(over, charge power without SoC window)
taken(x)   = what the supporting consumers (power left after early, capacity) and then
             the normal ones take of x in order; on/off consumers only with their full power
curtailed  = (over − absorbed) − taken(over − absorbed)
spare      = taken(over) − taken(over − absorbed)
drained    = min(max(0, consumption − PV), discharge power)
need(t)    = min(usable, max(0, need(t+1) + absorbed·eff·(1+buffer) [+ min buffer at a block end] − drained/eff))
```

Blocks are runs of steps with excess, gaps ≤ 1 h merged. `need` is the free
stored space required at each step; the need without buffers above the
usable space (max − min SoC) is energy that does not fit (see below for
`battery_too_small`); if only the buffered need exceeds it, `buffer_short`
(a note in the overview, no notification). `hold_charging` = free space now − need(now) ≤
50 Wh. `export_needed = max(0, need(now) − free now) · eff`. Export capacity
per step before the next block = min(discharge power − deficit, limit − 100 W
− PV surplus); `export_possible` sums it at full power; the latest start
collects 70 % of it backwards from one hour before the block; after the start
the export power is needed / remaining time.

What does not fit into the batteries is planned in two passes. The first
pass has no early consumer power; per block its *shortfall* is the buffered
stored energy clipped at `usable` / eff (AC), for the next block plus the
export that cannot be done in time, `(needed − possible) / eff²`. With
supporting consumers and a shortfall > 100 Wh, `_assign_early` gives each
block's shortfall to them from the block start (step by step up to the
excess, in order of priority, each up to its power and learned capacity,
an on/off consumer only where its full power fits,
`CapConsumer.power_after`; the capacity is available again each day), and
the second pass plans with that. Sized to the shortfall, the early power
leaves the space to make unchanged: the clipped need drops back to `usable`.
The first step's early power is `CapPlan.support_w`, which `CapControl`
passes to the allocation. In the final pass the stored energy still clipped
in a block (/ eff) is covered by that block's `spare` first; the rest is
`battery_too_small` (the buffered overflow likewise decides `buffer_short`);
the late export is covered by the next block's remaining spare, the rest >
100 Wh is `too_late`. The early energy plus the covered energy is
`takeover_wh` (overview note with `takeover_consumers`). Problems: `battery_too_small`,
`too_late`, `charge_power_too_low` (curtailed > 100 Wh); the coordinator tracks `cap_exceeded_since` (export > limit) for
the runtime problem after 5 minutes. These problems are persistent
notifications (see *Battery limits and delivery monitoring*), not repair
issues.

Automatic buffer (`auto_buffer`): the recorded days of `PvAccuracyTracker`
with forecast and production; with ≥ 14 of them the 80 % quantile of
actual/forecast − 1 over the days with actual > forecast is applied as a PV
factor instead of the percentage buffer.

Minimum buffer: fixed `FEED_IN_CAP_MIN_BUFFER_PCT` (5 % of the kWp as energy
of one hour) per block, not a setting; the cap sensor reports it as
`min_buffer_kwh` for the settings note. Settings entities that no longer
exist (`REMOVED_SETTINGS`) are removed from the entity registry at setup.
Learning it would need the forecast vs. real energy above the limit per
peak, which only peaks with an active cap provide.

Integration:

- Allocation (`CapControl`): surplus above `limit − margin` → the planned
  `support_w` to `CapMode.SUPPORT` consumers → batteries (charge power) →
  `SUPPORT` consumers → `NORMAL` consumers; the rest below the limit as usual
  without the `SUPPORT` consumers, batteries without charging while
  `hold_charging`. The export branch discharges max(normal, night, cap
  export), bounded by `max_discharge_export_w` (cap export: limit − margin,
  otherwise min(max export setting, limit − margin)); also used for
  `limit_discharge_export` in the coordinator. Peak shaving active → no export.
- Grid friendly feed-in limit capped at the cap limit.
- Night discharge: `max_target(crossover) = full − need(crossover)`.
- SoC projection: charging below the limit only up to full − need(hour end),
  at least the absorbed energy of the hour; from the export start stored
  energy is reduced to full − need.
- Day plan rows get `cap_line_wh` (consumption + limit), `cap_excess_wh`,
  `cap_curtailed_wh` from the fine steps, and `cap_lost_wh` /
  `cap_lost_reason` from the projection: export above the limit minus the
  consumers taking surplus (`CapPlan.consumers_w`), "full" if the projected
  SoC is at the maximum, otherwise "charge_power". The chart's red part and
  the simulation's *curtailed* key figure use `cap_lost_wh`.
- The projection plans the charging every hour again from the projected SoC
  (like the controller), so charging held back before a peak is made up
  afterwards; a plan made once per day left the batteries short after the
  peak.

Role of each consumer: select *Role with feed-in cap* per controllable
consumer (`ConsumerCapModeSelect`, restored, stored values of earlier versions
mapped by `LEGACY_CAP_MODES`; `coordinator.consumer_cap_modes`, default
`normal`), shown on the consumer card under *Feed-in cap* while the cap is on.
While the cap is on, `allocate` leaves `support` consumers out of the normal
surplus distribution; `normal` and `never` consumers get the normal surplus as
usual. The planning does not count on consumers to make room: planned
consumer energy is only what does not fit, so an unavailable consumer (a hot
boiler) never costs battery space.

Daily targets (`consumer_targets.py`, pure): `TargetSettings` per consumer
(entities on its card: selects `target_type`, `target_source`, numbers
`target_hours`, `target_energy`, `target_min_temperature`,
`target_max_temperature` (only with temperature sensors), switch
`target_earliest_enabled` and time `target_earliest` (earliest start,
`window_start`: the last one before the deadline; mode *waiting* keeps the
consumer off, the latest start is never before it; a temperature target
always has midnight as its window start, so it waits from the deadline until
midnight, and `_count_target` ignores its temperatures in that time),
select
`target_sensor` (only with two sensors: `TargetSensor` mean / first /
second, `target_temperature`; the latest start uses the energy per kelvin
learned for that sensor, `ThermalLearner.sensor_energy_per_k`, the mean's
until it is learned), switch
`target_priority`, time `target_deadline`; `coordinator.consumer_targets`),
`TargetProgress` per period from deadline to deadline (stored under
`targets`; runtime from ≥ 50 W, enabled time from the device command, energy
from the power, each sample's state until the next, gaps > 120 s skipped;
`min_reached` / `done` for temperatures). `_count_target` runs with every
poll and notifies a missed target once (`problems.async_notify_target_missed`).
`evaluate` in `plan()` (`_target_states`): *done* (met, or the target
temperature reached: off until the next period, `must_stay_off` after the
minimum runtime), *forced* from the latest start (deadline − remaining time
× 1.2 − 10 min; temperature: missing K × Wh/K of `ThermalLearner` / power, or
2 h) if the source allows (batteries: they deliver an on/off consumer's full
power, a power controlled one at least its minimum; it is then limited to
their discharge power, `forced_max_w`), *boost* (surplus before the batteries:
below the minimum temperature always; otherwise with `priority` when the
forecast surplus until the deadline, `remaining_surplus_by_hour`, is below
the missing energy plus `energy_to_full_wh`), else *surplus*. In `allocate`
forced consumers get their power up front (the batteries cover the deficit
like any load), boost consumers the surplus before the battery budget.
Sensor *Planned power* has the `target_*` attributes for the card. The
target temperature counts as reached for the value it was reached with
(`TargetProgress.done_target_c`): a higher one set later heats on.

Planning: `_target_states` runs in `plan()` before `forecast_plan` and sums
`forced_load` of every consumer with a source beyond the surplus into
`coordinator.target_load` (local hour -> Wh): the rest of the target at the
forced power (batteries only: at most their discharge power) from the
latest start until the deadline, the worst case without further surplus;
heat pumps are left out (their consumption is in the heat pump model;
controllable consumers are subtracted from the base load, so nothing is
counted twice). `_with_target_load` adds it to the hourly consumption for
the SoC projection, the night discharge (real plan and projection) and the
day plan.

`energy_to_target` is the energy the target still needs (energy target:
exact; runtime/enabled time: remaining time × full power; temperature: (target
temperature − current) × energy per kelvin, None until learned); attribute
`target_energy_wh` for the card. `surplus_demand` is the part the forced run
does not cover, from now (or the earliest start) until the deadline at the
full power; `coordinator.target_demands` holds them in order of priority
(not for heat pumps, not for supporting consumers while the feed-in cap is
on: the cap plan counts them). `project_soc(..., demands=...)` lets them take,
per hour, the surplus left after the planned charging (`_take_surplus`,
`SocProjection.consumer_w`), which lowers the expected grid export; the SoC
is unchanged, the batteries charge first. Day plan rows: `consumption_wh`
includes both, `consumer_wh` is the planned consumer part (chart series
*Consumers (planned)*). The feed-in cap plan keeps its own consumer model
(`CapConsumer`).

Supporting consumers with temperature sensors (`CONF_TEMPERATURE_ENTITY`,
`CONF_TEMPERATURE_2_ENTITY`, their mean in `ConsumerState.temperature_c`):
`coordinator.cap_consumer` builds the `CapConsumer` from
`ThermalLearner.capacity` × `THERMAL_SAFETY` (0.8) and the learned cycling
power; without learned values only the power limits it.

### Learned values

`learning.py` (pure) with the learners; the coordinator decides per value
whether the learned or the set value applies (switch on and learned value
known):

- `grid_friendly_buffer_wh(settings, pv_remaining_wh)`: `pv_overestimate`
  share × PV still expected today; used for the feed-in limit and the
  projection's charge buffer.
- `secured_buffer_wh(settings, pv_wh, consumption_wh)`: PV share × PV +
  `consumption_underestimate` share (from the backtest days of
  `ConsumptionAccuracy`) × consumption; rest of the day for *charge secured*
  (allocation), next 24 h for the night discharge buffer (real plan and
  projection).
- `grid_target_w(settings, charging)`: `GridTargetLearner` samples
  `grid + target` in `plan()` during controller cycles (active mode,
  control status active, strategy charging or self consumption, battery power
  not near its limit), from 300 of the last 3000 samples. Charging: 90 %
  quantile of `max(0, grid + target)`, 20–1000 W. Discharging: median,
  −100 to 300 W. Not stored (relearned within an hour).
- `control_interval_s` / `average_window_s`: `auto_timing` of
  `controller.meter.interval_s`; used by the controller and the grid filter.
- `BatteryRuntime.capacity_wh`: learned capacity if `learn_capacity` and
  three estimates; replaces `capabilities.capacity_wh` in the battery group,
  balancing energy and the stored energy sensors. `CapacityLearner.update`
  runs with every poll on the DC power (legs ≥ 20 % SoC, idle > 10 min or a
  direction change ends a leg, a SoC jump > 3 % or a gap > 60 s discards,
  plausible 50–130 % of the configured capacity). Stored per battery.
- `effective_consumer(consumer)`: learned `nominal_power_w` (on/off
  consumers; power controlled consumers: for planning only via
  `_full_power_w`, never below `min_power_w`, `max_power_w` unchanged) and
  (only these with the consumer's learning switch on) and `thermostat_cycles`
  from two pauses within `PAUSE_MEMORY_S` (always; the former setting
  `thermostat_cycles` of a consumer still counts); used in the
  allocation requests, the feed-in cap and the controller.
  `ConsumerLearner.update` runs with every poll on the command last sent by
  the controller (kept while saturated) and the measured power. The power is
  sampled only at a full command (≥ `FULL_COMMAND_SHARE`, 90 %, of the
  configured full power): the median of throttled set points of a power
  controlled consumer would be meaningless. Its sensor is named *Learned
  maximum power* there (translation key `learned_max_power`, same unique
  id). Stored with the marker `full_command`; powers of a power controlled
  consumer stored without it are dropped on load. A pause counts as a thermostat cycle only from 30 s to 10 min
  (`PAUSE_MAX_S`): a device with its own long control, such as a dehumidifier
  at its target humidity, is better treated as saturated (its surplus goes to
  the other consumers) than as resting. No power right after switching on is
  the start delay, not a pause: only pauses after the consumer ran count (the
  thermal learner takes a pause before the first run only after
  `START_DELAY_MAX_S`, 5 min, as a warm storage).
- Thermal storage (`ThermalLearner`, consumers with temperature sensors,
  always learning, stored under `thermal`): with every poll on the command,
  the measured power and the mean temperature of the sensors. A run lasts
  while the consumer is commanded on (a gap > 120 s ends it). Learned per run:
  Wh per K of the mean temperature (rise ≥ 3 K, ≥ 300 Wh) and of every
  sensor (`sensor_wh_per_k`, same rules; a sensor at the heating element
  rises much faster than one higher up), the mean
  temperature 30 s into the first thermostat pause (power < 50 W while
  commanded), the mean power from that pause to the end of the run (≥ 30 min
  of cycling) and the mean temperature at which a 30 min window of the
  cycling phase stays below 15 % of the learned power (full). Cycling power
  and full detection are skipped when the run was commanded below 90 % of the
  full power (a throttled consumer is not a full storage). Medians of the last
  20; the energy per K from 3 runs, the temperatures from 2 marks. Capacity
  at a temperature: until it cycles (pause temperature − T) · Wh/K, until
  full the same with the full temperature (until it is learned: the energy
  until it cycles). Sensor *Storage capacity left* with the learned values.
  The mean of the sensors needs neither the role nor the position of a
  sensor: with a sensor at the heating element and one higher up, the rise
  per energy and the cycling mark are consistent from run to run.

Night discharge reserve (`night_reserve_pct(settings)`): `MorningGapLearner`.
With every poll (any operating mode, also without night discharge) the
coordinator keeps the planned PV takeover of the coming morning
(`night_discharge.pv_takeover`, the first hour PV exceeds consumption; updated
until it is reached) with the forecast consumption of that day. From the
takeover on it integrates max(0, total consumption − PV) until PV has covered
the consumption for 15 minutes (or 12:00) and stores the gap in % of the
forecast consumption (60 days). The reserve is the `night_reserve_coverage_pct`
quantile of the gaps, above 100 % the largest gap times the coverage; from 14
mornings. Used by the real night discharge plan and the projection.

### Simulation

`simulation.py`, websocket command `slems/simulate` (registered once per HA
run in `async_setup_entry`). The request carries `settings` (fields of
`ControlSettings` listed in `SETTINGS`, the ones that change the plans),
`battery` (total capacity, min/max SoC, charge/discharge power of the group)
and `pv_pct` / `consumption_pct`. It builds a `ControlSettings` copy, a
modified `BatteryGroup` (the SoC in % stays) and a snapshot copy with scaled
forecasts, and calls `SlemsCoordinator.forecast_plan` – the same method the
real `plan()` uses for the feed-in limit, feed-in cap, peak shaving limit, SoC
projection and day plans. The reply has the day plans, key figures per day
from `grid_w` of the rows (`_metrics`: export/import energy of the projected
part, peaks, energy above the cap limit, last SoC) for the simulation and the
real plan, and the starting values (`base`). Nothing is stored; the panel
starts from `base` on every visit (`_sim` state, 250 ms debounce). The chart
reuses `_chartRows(plans, compare)` / `_renderDayChart(options)`; the real
plan appears as `socCompare` / `exportCompare` (grey dotted, drawn first).

### Dashboard

`panel.py` registers the sidebar panel (`panel_custom`, URL `/slems`) and
serves `frontend/` under `/slems_static` (registered once per HA run; the
module URL carries the file's modification time to bust the browser cache).
The panel config passes what the frontend cannot find itself: system device
id, batteries and consumers with their device ids, the consumers' power
entities and whether they are shown in the energy flow (`show_in_flow`, a
field of the consumer subentry, default on). The panel is removed on unload and re-registered on every setup.

`frontend/slems-panel.js` is a plain custom element without build step:

- Entities are found via `hass.entities` (platform `slems`, translation key,
  device id), so renamed entity ids do not matter. Values are formatted with
  `hass.formatEntityState` (translated states, units, locale).
- Rendering is split into sections whose markup is only replaced when it
  changed (`_setSection`), so inputs and the chart hover survive the frequent
  `hass` updates. When a section is replaced, the focused control is found
  again by its data attributes (`focusSelector`) and focused, with a value
  being typed kept. Events are delegated on the content element.
- Consumer cards are collapsed to measured / planned power and the target
  progress; *Show settings* expands the rest (`_expandedConsumers`,
  localStorage `slems-expanded-consumers`).
- Keyboard: the battery ⋮ menu moves the focus to its first item when opened,
  arrows move between the items, Escape closes it and returns to its button.
  Every input, switch and select carries an `aria-label` with its name.
- Entity names come from the integration's translations in the frontend
  language (`hass.loadBackendTranslation("entity", "slems")`,
  `component.slems.entity.<domain>.<key>.name`), not from the friendly name,
  which is in the server language; entities renamed by the user (full registry,
  `config/entity_registry/list`, `name` set) keep their name.
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
- A consumer box shows the temperature of its storage below the power
  (attribute `temperature_c` of *Planned power*: the sensor chosen for the
  daily target, `target_temperature`, the mean by default).
- The crossing of the flow lines shows the SLEMS icon:
  `frontend/slems-icon.svg` is a copy of `assets/icon.svg` (HACS installs only
  `custom_components/slems`); update both together.
- Day chart: data from the attribute `day_plan` of the sensor *Feed-in limit*
  (24 rows: corrected PV forecast, consumption forecast, planned charging,
  projected total SoC at the end of the hour, plus per half hour the mean PV
  power from the native forecast periods `pv_half_w` and the feed-in cap
  energies; `day_plan_tomorrow` for the *Tomorrow* view with the uncorrected
  PV forecast) plus today's 5 minute means from the recorder
  (`recorder/statistics_during_period`, period `5minute`, for the SLEMS PV,
  house, total SoC, total battery power and grid power sensors, refreshed
  every 5 min), averaged per half hour; the measured charging is the mean of
  the positive battery power, the measured feed-in the mean of the negative
  grid power. The expected feed-in comes from `grid_w` of the day plan (the
  SoC projection: consumption − PV + battery AC power, the battery power from
  the change of the stored energy), limited to the feed-in cap if it is on. 48 half hour slots, all series as mean power (W; hourly
  values fill both halves: consumption forecast and planned charging come
  from the hourly models). Drawn as SVG in real pixels (redrawn on resize):
  mean power on the left axis, the total SoC drawn on top with its own scale
  on the right (0–100 %, labels only, the grid lines belong to the power axis;
  the user's choice over a separate panel); forecast dashed, measured solid,
  planned charging as light bars on the left half of a slot, measured
  charging as solid bars on the right half;
  the SoC forecast starts at the current total SoC (tomorrow: at today's last
  projected value); crosshair tooltip per half hour (touch: `pointerdown` shows and
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
  identity. Accent text (active tab, active segment, links) uses
  `--slems-accent`: the theme's primary colour in dark mode, mixed with 38 %
  black in light mode (the plain primary colour reaches only about 3:1 on
  light backgrounds); active tabs and segments are also underlined.
- Phones (≤ 600 px): the tab row scrolls sideways instead of wrapping; on a
  tab change only the row scrolls to the active tab. Flow box titles take up
  to two lines. The simulation shows its inputs above chart and key figures
  in one column (≤ 900 px).
- Forecast accuracy: accuracy and the expected deviation of tomorrow only
  from `MIN_ACCURACY_DAYS` (7) compared days, before that *collecting data*.
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
consumers = controllable consumers as the meter shows them (seen_consumer_power)
available = -grid_filtered + seen + consumers
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
  The controllable consumers are treated the same way
  (`seen_consumer_power`): until a command reached the grid meter (the
  consumer's grid response, learned per consumer like the battery response;
  until then the battery response) the power before it counts, afterwards the
  measured power, or the commanded one while the consumer's own sensor
  (response learned separately) has not caught up. Outside the controller
  cycles (poll, simulation mode) the measured power is used.
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

### Finding batteries

`discovery.py`: `async_find_batteries` takes the IPv4 networks of the enabled
network adapters of Home Assistant (`network.async_get_adapters`; networks
larger than 1022 hosts are reduced to the /24 around the address), opens TCP
port 502 on every host (0.6 s timeout, 64 in parallel) and reads the SoC
register once where it is open (`MarstekVenusE3Driver.read_soc`, also used by
the connection test). The battery subentry flow runs it as step `search`
before the Venus form (not when reconfiguring); configured batteries are
excluded by host and by the IPv4 addresses their host names resolve to.

Idea for later – automatic discovery. The Venus LAN port gets its address
by DHCP with the host name `CH395` (the WCH CH395 Ethernet chip, seen on three
devices; their MAC prefixes differ, so they are no criterion, and the chip is
used in other devices too, so a device must be checked by reading the SoC).
Subentry flows do not support discovery, and a `dhcp` matcher in the manifest
starts a config flow that Home Assistant aborts at once for a
`single_config_entry` integration with an entry (checked in HA 2026.9;
removing `single_config_entry` would work but is not wanted). The way without
that side effect, after the core integration `mitsubishi_comfort`:

- read the DHCP sightings with the public
  `homeassistant.components.dhcp.async_discovered_service_info(hass)` (IP,
  host name, MAC) at startup and periodically, no manifest matcher;
- for an unknown `ch395*` device that answers the SoC register, create a
  fixable repair issue ("Marstek Venus found at …"); its `RepairsFlow` asks
  for name, capacity and limits and adds the battery with
  `hass.config_entries.async_add_subentry`;
- store the MAC of a battery (device registry connection) and update its host
  when its MAC shows up with another IP;
- use the sightings in the search step first (instant), the port scan as
  fallback.

Drawbacks: the battery appears under *Repairs* instead of *Discovered*, and
only after Home Assistant saw a DHCP request (at the latest with the next
lease renewal after a restart of Home Assistant). A DHCPREQUEST with host name
CH395 can be sent from the simulator container to test it.

### Adding a battery model

1. Implement `BatteryDriver` in `drivers/<model>.py`.
2. Add the model to `BatteryModel` in `const.py` and to `create_driver()`.
3. Add a subentry step `async_step_<model>` in `config_flow.py` plus
   translations (`strings.json`, `translations/en.json`, `translations/de.json`,
   selector option in `selector.model`).
4. Report optional values via `BatteryTelemetry.extra` and list their keys in
   `extra_telemetry_keys`; matching sensors in `BATTERY_EXTRA_SENSORS` are then
   created automatically.

### Batteries from Home Assistant entities

`drivers/ha_entities.py`, configured by `EntityBatteryConfig.from_data`
(subentry keys in `const.py`, control type `BatteryControl`):

- **Telemetry**: SoC (required), power (required when controlled, because it
  is part of `available_power_w`), optional temperature, highest/lowest cell
  voltage (mV converted to V) and energy counters (kWh) as `extra` keys of the
  Venus (`internal_temperature`, `max_cell_voltage`, ...), so the temperature
  limit, top taper, cell delta/balancing and the counter efficiency work
  unchanged.
- **Commands**: `setpoint` writes one number (optionally inverted, kW
  converted, `clamp_to_entity`); `split` writes the stopping direction to 0
  first, then the other number, then the mode option; `script` calls
  `script.turn_on` with `variables: {power_w}`. A remote control entity
  (switch or select with on/off options) is switched on before commands.
  Unchanged values are not written again unless `refresh`. Service calls are
  not blocking; an unavailable entity fails the command (`BatteryDriverError`).
- **Timing**: `BatteryDriver.min_command_interval_s` (the controller keeps
  the previous set point until it passed) and `keepalive_s` (replaces
  `BATTERY_KEEPALIVE_S` for that battery).
- **Release** (`ReleaseState`, also for the Venus): `auto` hands the battery
  to its own logic (Venus: RS485 control off; entities: mode option
  automatic, remote control off or release script), `standby` leaves it at
  0 W (Venus: RS485 control stays on). The config flow only offers `auto`
  when `can_release_to_auto`. Removing a battery reloads the entry;
  `async_shutdown` releases the batteries of the old state in active mode,
  so a removed battery gets its release state too (HA's delete dialog of a
  subentry cannot ask).
- **Config flow**: `ha_device` (optional device) → `_suggest_from_device`
  (`entity_match.match_battery_entities`, scores per role on domain,
  device class, unit, EN/DE words; capacity and power limits from the capacity
  sensor and the number min/max) → `ha_entities` → `ha_setpoint` /
  `ha_split` / `ha_script` → `ha_options` (select options, suggested) →
  `ha_limits`. `_entity_battery_data` drops the keys of other control types.
- **Dev instance**: `venus-sim-3` is controlled only through Modbus and
  template entities (`dev/config/configuration.yaml`: template numbers for
  charge/discharge power, template select for the force mode, Modbus switch
  for RS485 control), like a Venus behind another integration.

### Other batteries (Omnibattery drivers)

Omnibattery (GPL-3.0 like SLEMS, so its code may be used with attribution)
supports more batteries than SLEMS. Its drivers cannot be copied as they are:
they are built for Omnibattery's coordinator (read groups, register keys,
control helpers), while a SLEMS driver only connects, reads telemetry, sets a
net power, releases control and reads device information. What carries over
is the device knowledge (registers, sign conventions, control sequences,
quirks); the wrapper has to be written for SLEMS, a few hundred lines per
brand. Assessment of the drivers in `Omnibattery/custom_components/omnibattery/drivers/`:

| Battery | Transport | Effort | Notes |
|---|---|---|---|
| Marstek Venus v2 / vA / vD | Modbus TCP | low | register maps in `const/registers_v2/va/vd.py`; the SLEMS Venus driver fits |
| Marstek behind a LilyGo RS485 bridge (`esphome.py`) | HA entities (ESPHome) | low to medium | a Venus v2; close to `drivers/ha_entities.py` |
| Zendure SolarFlow AC models | local HTTP | medium | AC coupled; HEMS must be off in the app, otherwise the device overrides the set points |
| Sessy | local HTTP | low to medium | AC coupled, simple signed power set point |
| Anker Solarbank | Modbus TCP | high | DC coupled |
| Hoymiles | MQTT (HA broker) | high | DC coupled |
| Huawei LUNA2000 | Modbus + `huawei_solar` | high | split transport (telemetry direct, commands via the integration or a direct write sequence), usually behind a Modbus proxy |

Open points before other brands:

- DC coupled batteries (PV on the battery's own DC bus): SLEMS assumes a
  separately measured PV and a battery that charges and discharges on the grid
  side. Planning, delivery monitoring and the feed-in cap would have to take
  PV "inside" the battery into account (Omnibattery: `dc_coupled`,
  `ac_delivered_power`).
- Cell balancing, the communication pause for firmware updates and the
  efficiency from the battery counters are Venus specific and must be switched
  off through the driver capabilities for other brands.
- The dev environment only simulates the Venus; every other brand needs a
  tester with the device.
- Order if it is done: Venus variants first (most benefit, least effort), then
  Zendure and Sessy (AC coupled), DC coupled devices only with a tester and
  after the model is extended.

Until then any battery with SoC and power entities in Home Assistant can be
added through its entities (`ha_entities`), read-only or controlled.

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
limit per hour is a binary search. `hours_until_refill` collects the hours
without PV surplus from now until the forecast surplus adds up to the AC
energy that charges the batteries from the minimum SoC back to the threshold
(surplus hours themselves are skipped, so a small surplus on a rainy day does
not end the period); during a surplus right now it starts with the following
period without surplus (evening and night); at most 36 h ahead. `auto_limit` bisects the lowest
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
| 34003 | cycle_count | uint16 | 1 | charge cycles counted by the battery (as in Omnibattery; plausible on the device, the app no longer shows the count) |
| 31000 | device_name | char × 10 | | device information, read after connecting and every 6 h |
| 30200 / 30202 / 30204 | ems / vms / bms_version | uint16 | | |
| 30350 | comm_module_firmware | char × 6 | | |
| 30304 | mac_address | char × 6 | | |
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

Verified with `tools/set_power.py` (2026-09-26, SoC 55 %): charging with a
set point of 1000 W gave 30006 = −996 W (AC) and 30001 = +940…+971 W (DC),
about 3 % loss; discharging with 800 W gave 30006 = +797 W and 30001 =
−862…−877 W, about 8–9 % loss. The Marstek app showed the AC value (−996 W
charging, 797 W discharging). The set points are delivered within a few watts;
34003 reported 13 charge cycles; the app no longer shows the cycle count
(nor the DC power), so it cannot be compared.

Note:

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

`tools/set_power.py` charges or discharges a Venus with a fixed power (same
control registers as the driver: RS485 control, set points, force mode, set
point written again every 30 s) and shows DC/AC power, SoC, inverter state and
the set point registers meanwhile; at the end or on Ctrl+C it hands the
battery back. Pause the battery in SLEMS first (battery menu ⋮).

```bash
python3 tools/set_power.py <battery-ip> --charge 1000 --duration 180
python3 tools/set_power.py <battery-ip> --discharge 800
python3 tools/set_power.py <battery-ip> --release
```

`tools/at_grid_templates.py` writes the Austrian tariff templates for the grid
fees of every grid area (network level 7, for households and interruptible
supply) and the federal levies from a table of the published values (SNE-V,
Erneuerbaren-Förderbeitrag, Elektrizitätsabgabe). For a new year add the new
values with their date and run it again; earlier price levels stay.

```bash
python3 tools/at_grid_templates.py
```

`dev/readme_charts.py` creates the charts of the README section *Which option
when?* (`docs/images/<scenario>_<language>.svg`) from the SLEMS state of charge
projection with example data; after changes to the planning run it again
(two steps: simulate in the tests container, render on the host; see its
docstring).

## Continuous integration

`.github/workflows/validate.yml` runs the HACS validation (`hacs/action`,
category integration) and `hassfest` on every push, on pull requests and
daily; `tests.yml` runs the test suite in the same container as locally
(`docker compose --profile tests run --rm tests`) and the end-to-end test of
the setup.

The end-to-end test (`dev/e2e`, a Compose project of its own, so a running
development instance is not touched) starts a fresh Home Assistant with its
configuration in memory, the development `configuration.yaml` and its own
simulators, onboards it and runs the config flows through the REST and
websocket API of the frontend (`run.py`, standard library only): setup,
batteries (Modbus simulator, entities), consumers of every control mode,
tariffs (by hand, YAML, Austrian and German templates, EEG), the options with
the smart meter over Modbus, every subentry reconfigured, and the dashboard
commands. A form gets its defaults and suggested values like in the frontend
plus the values of its step; after every entry the integration must be
loaded and the log free of errors. Only made-up data, no secrets, no
internet services (market prices stay off). It takes a few minutes, so it
runs on GitHub after a push; locally when a change touches the setup:

```bash
docker compose -f dev/e2e/docker-compose.yml run --rm e2e
docker compose -f dev/e2e/docker-compose.yml down
```

`hassfest` can be run locally before a push:

```bash
docker run --rm -v "$PWD/custom_components:/github/workspace/custom_components:ro" ghcr.io/home-assistant/hassfest
```

The HACS check also looks at the GitHub repository itself: it needs a
description and topics, and a brand icon in the `home-assistant/brands`
repository (otherwise its *brands* check fails).

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
| 2026-09-26 | Communication pause per battery for firmware updates (manual, 20 min default, automatic on a reported OTA update); battery card actions in a ⋮ menu with a details dialog (firmware, counters). |
| 2026-09-26 | Feed-in cap at the grid connection point (kWp × %): space for the energy above the limit planned backwards over all peaks until the end of tomorrow with the native PV periods, percentage buffer plus minimum buffer (% of kWp), optional automatic buffer from the recorded PV errors; making room by holding charging, a lower night discharge target and feeding in as late as possible (1 h before, 70 % power, may exceed the maximum export while discharging, never the limit); consumers per option counted / emergency / never; precedence over grid friendly charging, night discharge and battery priority. |
| 2026-09-26 | Feed-in cap warnings as notifications (created on appearance, dismissed when gone) instead of repair issues: they come from the forecast. The part of each consumer in the cap is a select on the consumer card instead of a config flow field. |
| 2026-09-26 | Forecast.Solar periods are re-keyed from their end to their start when read (before, its forecast was used one hour late everywhere). Day chart in half hours with mean power, measured charging from the 5 minute statistics next to the planned one. |
| 2026-09-26 | Batteries can be searched in the network when adding a Venus (port 502 + SoC read). No automatic discovery for now; the way that keeps `single_config_entry` is noted under *Finding batteries*. |
| 2026-09-27 | A Venus draws about 13 W DC at standby: resting is up to 25 W, a refused balancing charge is below 30 W (instead of ±10 W, which kept a run in its charge leg). The top cell delta is measured once per charge (re-armed below 3.49 V, not armed after a start), because a battery standing full relaxes and the value kept falling. Diagnostics download with the internal states. |
| 2026-09-27 | Simulation tab: day plans with other settings, batteries and forecasts, calculated by the backend with the real planning code (`forecast_plan`), compared with the real plan; nothing stored. The SoC projection now also respects the maximum grid export while discharging. |
| 2026-09-27 | Learned values with a switch each (the set value applies when off or without enough data): grid friendly buffer and safety buffer from the forecast errors, grid targets from the grid deviation, control timing from the meter interval, usable capacity per battery, consumer power and thermostat pauses. The night discharge reserve is left for a later decision. |
| 2026-09-27 | Night discharge reserve learnable from the morning gap (planned vs. real PV takeover) in % of the forecast consumption, with a coverage setting (quantile, above 100 % a margin over the worst morning); no price based optimisation, because whether the energy community takes the energy at night is not known live. |
| 2026-09-28 | PV correction weighted: the rest of the day only from 15 % of the day's forecast energy on (fully from 50 %), the current and next hour more strongly; a foggy morning halved the whole day's forecast before. |
| 2026-09-28 | Feed-in cap mode *Instead of curtailing* means only against curtailment: such consumers get no normal surplus while the cap is on (a heating rod ran on normal surplus although the batteries could take the peak). Default mode changed to *Plan with*. |
| 2026-09-28 | Feed-in cap roles *Supporting* / *Normal* (default) / *Never* instead of counted / instead of curtailing / never. Batteries first; supporting consumers get no normal surplus while the cap is on, and for energy that does not fit into the batteries they are planned from the start of the peak, because a consumer with less power than the surplus only takes a fraction of it once the batteries are full. Nothing is counted on to make room (a hot boiler would cost battery space in the peak); problems count the consumers' spare power, a note names them. Optional temperature sensors per consumer: the storage capacity is learned from the mean of the sensors (energy per K, cycling and full temperature, cycling power) without knowing which sensor switches the thermostat, and limits the planned energy (80 %). |
| 2026-09-29 | Feed-in cap minimum buffer fixed at 5 % of the kWp instead of a setting: it covers errors in the timing and height of a peak that the learned buffer (from daily totals) does not show, and its right value depends on the forecasts rather than the house, so a user cannot judge it. The night discharge reserve coverage is only shown while the reserve is automatic. |
| 2026-09-29 | Other batteries from Omnibattery: not ported for now. Its drivers are tied to its coordinator; the device knowledge can be taken over (both GPL-3.0). Order if done: Venus v2/vA/vD, then AC coupled Zendure and Sessy, DC coupled devices only after the model is extended and with a tester. Users who want another battery are asked to open an issue. |
| 2026-09-29 | Regular full charge: one due battery at a time (older than 7 days or unknown; oldest, then name) is charged first and may exceed its maximum SoC once; spared when discharging while all others are above 50 % (fixed, not a setting); rests 90 s when full for a top delta measurement. The split between batteries and consumers is unchanged. |
| 2026-09-29 | Daily targets per consumer (runtime, enabled time, energy, temperature) from deadline to deadline, set on the card; sources staged surplus only (default) / + batteries / + batteries + grid, forced as late as possible; priority over the batteries optional and only when the forecast is short; the minimum temperature always before the batteries, the target temperature ends the day. Forced runs are not yet part of the day chart and the SoC projection. |
| 2026-09-29 | Forced runs of daily targets are an extra load in the day chart, the SoC projection and the night discharge, planned from the latest start as the worst case (shrinks as the surplus fills the target). A target temperature raised later in the period is no longer reached. |
| 2026-09-29 | Gentle charging near the top without a setting (200 W from 3.48 V, released below 3.44 V, as Omnibattery's default): the BMS balances passively before the highest cell ends the charge; it costs only the last one or two percent. |
| 2026-09-29 | Energy per kelvin learned per temperature sensor too, so a temperature target of one sensor estimates its latest start with that sensor's value (the mean's until learned). |
| 2026-09-29 | A full charge also when the BMS ends the charge near the top (commanded ≥ 100 W, nothing taken for 2 min from 98 % / 3.45 V): a battery the BMS stops at 99 % would otherwise stay due for its full charge forever. The top is left only below 3.40 V. |
| 2026-09-29 | The learned power of a power controlled consumer is its highest power, sampled only while commanded at ≥ 90 % of its maximum power (the median of throttled set points said nothing); with learning on it caps the maximum power, so no power is planned that the device does not take. The thermostat pauses are not part of it; the mean power while cycling is the storage's own learned value. |
| 2026-09-29 | A temperature target applies to the calendar day: from midnight to the deadline, then the consumer waits (off, also with surplus) until midnight; temperatures in that time count for no day. Otherwise a storage that reached its target before the deadline heated again right after it, when the next period began. |
| 2026-09-29 | The battery group is full only when every battery is full, and full batteries add no charge power: with the mean SoC one battery at 98.4 % among full ones switched the group between full and not full with each SoC step, and the heating rod between its allocation and a quarter of it. |
| 2026-09-29 | A resting consumer (thermostat pause) counts at its command in the controller cycle: its restart appeared at the grid meter up to 5 s before its own sensor, as new house load, and SLEMS cut it back to almost nothing. |
| 2026-09-30 | Batteries from HA entities can be controlled (experimental): set point, separate charge/discharge power with optional mode select, or a script with `power_w`; entities are suggested from the chosen device and confirmed in the flow. It reaches every battery with an HA integration without a driver per brand; direct drivers stay for batteries where precision matters (Venus). |
| 2026-09-30 | State when released per controllable battery (automatic or standby), also for the Venus; default automatic. It is a setting, because HA's delete dialog of a subentry cannot ask, and it applies to every release (mode off, meter failure, removal). |
| 2026-09-30 | Releases from outside the control cycle (operating mode, *Enabled* switch, manual communication pause, unload) go through `RealTimeController.async_release` and wait for the controller lock: a cycle still running (e.g. a Venus confirming its set point) otherwise sent a set point right after the release. |
| 2026-09-30 | Daily targets in the planning also with their surplus part: after the planned charging each target takes the surplus left in its window, so the expected feed-in no longer contains energy the consumers will take; shown summed as *Consumers (planned)*. Batteries first, because until the charge is secured the allocation gives the surplus to them; with a secured charge the consumers get a share earlier, but the batteries end up full either way. The card shows the energy still needed; for temperature targets only with the learned energy per kelvin. |
| 2026-09-30 | Temperature target: below the minimum a reached target is open again (`TargetProgress.track_temperature`): up to the minimum with priority, then with the surplus up to the target. Only reheating to the minimum would keep the consumer off above it and export the afternoon surplus; the minimum as reset threshold still keeps it from starting at every small loss. The temperature flags belong to the sensor choice (`TargetProgress.temperature_sensor`); another choice starts the target open. A storage that reached its target and then lost the hot water stayed off for the rest of the day. |
| 2026-09-30 | Battery response time: smaller corrections the same way no longer end a pending measurement, and the learned value is stored. The damped control follows every step with a smaller one within a second, so with a smart meter reporting every second the value was hardly ever learned and it was lost with every restart (3 s default meanwhile). |
| 2026-10-01 | SoC projection: the controller's feed-in limit only in the current hour; later hours of today compute it from the projected state of charge (with the feed-in cap as ceiling), as the controller will. The limit from midnight (before the night discharge) planned too little charging, and the chart showed batteries not getting full. Night discharge: the refill counts per hour at most the charge power, and the surplus part of the daily targets must fit besides it (`min(chargeable, surplus − demands)`). |
| 2026-10-01 | Cell balancing ends at a top delta of at most 190 mV (`TARGET_DELTA_V`), when it did not fall by 2 mV for 6 hours (`STALL_S`) or after 24 hours (`end_reason` target / no_progress / max_time); 24 hours is a normal end with the final discharge, an error only if that does not finish within 2 more hours. A refused charge leg gives no measurement. On a real Venus E 3.0 the delta fell from 168 to 164 mV in 16 hours: the BMS bleeds only a few mV per day, the former 30 mV target was unreachable, and refused legs measured at a lower voltage showed dips of about 110 mV. The start dialog says when the last measurement is already in the normal range. |
| 2026-10-01 | Battery response time also per battery (`BatteryResponses`), learned at the grid meter from steps one battery carries for at least 80 % of the moved power; a larger shared step ends all pending measurements. The controller uses a battery's own value for which of its commands the meter already shows, the joint value until learned. The batteries' own power is read only every 5 s, too coarse for response times of about 1 s, and the joint value hides a slow battery as long as the others bring 60 % of a step. |
| 2026-10-01 | Grid power optionally read directly from a SunSpec meter over Modbus (`grid_meter.py`), through the shared connection of the HA Modbus backend (`async_get_unit`) instead of an own one. SolarEdge Modbus Multi updates the entity about once per second, so it misses most of the meter's changes (measured for an hour with a diagnosis integration over a Modbus proxy at 0.2 s: entity 2495 changes missed, direct read 478; mean delay behind the fastest path 547 ms against 150 ms). The HA backend is as fast as an own connection (round trip and median delay equal) and needs no rework once the inverter integration shares its connection; until then it points to the proxy. HA 2026.9 is the minimum version anyway. Each read is limited to 1 s, since the shared connection has a timeout of 10 s. The entity stays required (history, forecasts, fallback). |
| 2026-10-01 | Bad weather mode: grid friendly charging and night discharge off until the evening, the target grid surplus stays (it is the margin of the control, not a planning choice). The evening comes from the forecast (end of the last hour with PV above consumption), not from the measured power: with passing clouds the PV falls below the consumption many times a day. Switched on after the evening it covers the next day, since the user switches it on the evening before a rainy day. The end follows new forecasts only through a surplus hour, so a forecast without the past hours does not stretch it. |
| 2026-10-01 | Cell balancing: a refused charge no longer lowers the retry voltage step by step; the next leg starts again from the top window (3.49 V, or 10 mV below the refusal voltage). On the real Venus the retry voltage had sunk to 3.40 V: every leg then discharged out of the window and charged back with 95 W for hours, so the run measured only a few times a day, while the BMS only bleeds within the window. |
| 2026-10-01 | Battery support per consumer (always / automatic / never, `battery_support.py`): in a deficit the batteries leave the power of unsupported consumers to the grid (`allocate(unsupported=…)`), the house stays covered; peak shaving and the night discharge power are unchanged. The budget of *automatic* is the lowest projected stored energy until the next charge from PV, from a projection without night discharge and without the loads it is for, minus minimum SoC, morning reserve and safety buffer (and the peak shaving threshold). Without night discharge in that projection, energy the night discharge would export may go to the consumers; the night discharge then plans from the lower state of charge. The budget is recomputed every update instead of being counted down. The night discharge reserve was renamed *morning reserve*: it covers mornings with a late PV takeover and now serves both. First step towards a wallbox (an EV charging from the batteries only with what they can spare). |
| 2026-10-01 | Current control (A) for wallboxes and evcc loadpoints, consumer type *wallbox*. Planned in watts (current range × voltage × active phases, from a phases entity if given), sent as whole amperes rounded down. Start/stop through a switch or a select with on/off options, since the maximum current of an evcc loadpoint cannot go below its minimum current (stopping is its charge mode); without one, the lowest current the entity takes, and evcc decides in its PV mode. Checked against evcc 0.316 and ha-evcc 2026.9 (number entities for min/max current, select for mode and phases, sensor for active phases). The minimum pause of a consumer now only counts after SLEMS really switched it off: after every start the first "off" started the pause, so a wallbox (5 min pause) waited five minutes after each restart. |
| 2026-10-01 | evcc guide (`docs/wallbox-evcc.md`, German `.de.md`) with screenshots from the dev instance and a demo charger. evcc reads grid, PV and battery from SLEMS through its Home Assistant meter template; it expects the battery power positive while discharging and cannot invert it, so SLEMS has the sensor *Battery power total (discharge positive)*. ha-evcc exposes the maximum current of a loadpoint as a select with ampere options, so current control also drives selects (highest option not above the planned current). evcc's battery control scripts stay empty: SLEMS controls the batteries. |
| 2026-10-01 | Release and service calls hardened after an external code review. A release is confirmed (Venus: every write checked and the force mode / RS485 register read back; entity batteries: blocking calls) and stays outstanding until it succeeds (`BatteryRuntime.release_pending_since`, retried every update while SLEMS does not control the battery, three attempts at unload, repair issue after 5 minutes). Before, a failed release went unnoticed and was not tried again while the grid meter stayed stale, so a battery could keep its last set point. All service calls to other integrations wait for the result with a 10 s timeout (`util.async_call_service`): before, errors were never seen and with separate charge/discharge entities the stopping direction was not guaranteed to stop first. Scripts are called directly so SLEMS waits for them. The ramp timer is kept and cancelled at shutdown, and no cycle starts after it. `clamp_to_entity` stays within min/max when they are not on the step grid; NaN and infinite states count as unknown. |
| 2026-10-01 | Tariffs as bill lines (`tariff.py`, subentry type `tariff`) instead of a fixed price per kWh: Austrian grid tariffs now have reduced prices in time windows (noon in summer), yearly items are charged per day, and VAT differs between the sides of a bill (e.g. none on feed-in energy). Entered like the bill and checked against it with the recorded hourly energy, so the user does not have to derive effective prices. Values only live in the user's Home Assistant; tests and docs use made-up tariffs. First part of dynamic tariffs (next: market prices with the user's consent, tariff comparison in the simulation, price chart). |
| 2026-10-01 | Day-ahead prices only with the user's consent (switch, off by default): SLEMS must not contact services on the internet on its own. APG for Austria (official, data since 2023), SMARD for Germany (official, CC BY 4.0) and Energy-Charts for both as an alternative; no automatic fallback to another service than the one chosen. Prices are cached for a year, so comparisons and the check need no requests. The feed-in "monthly market price" is approximated by the export-weighted mean (as published market prices of PV feed-in are, but only on websites without an API); published values can be entered per month. |
| 2026-10-01 | Tariff comparison as a passive comparison in the simulation tab: the recorded energy priced with each tariff per month. Simulating what the control would have done with another tariff would need price-aware planning, which does not exist yet; the label says so, so a dynamic tariff is not judged by a comparison that leaves out its main advantage. |
| 2026-10-02 | SLEMS records grid import and export per quarter hour from its own grid values instead of relying on statistics only: the hourly mean of the grid power nets import and export within an hour, the counters' long-term statistics are hourly, while bills with dynamic prices are per quarter hour. With counters the quarters only give the split within the hour and the counter the total, so a less exact sampling of the grid power does not change the billed energy. |
| 2026-10-02 | Price aware control starts with moving the grid import that happens anyway into cheaper hours (price hold): no extra import, no export, no extra cycles, so it needs no wear costs and cannot make things worse than the forecast error. A greedy cover of the most expensive hours is optimal for a fixed amount of energy and linear prices; a full optimisation (dynamic programming) comes with charging from the grid. The minimum gain absorbs forecast errors; any time dependent import price of the tariff counts, with or without market prices. |
| 2026-10-02 | Daily targets with the source "grid" start their forced run in the cheapest window only when the forecast surplus is short for them anyway: otherwise an early grid run could take what the surplus would have covered later for free. One contiguous run instead of single cheap hours, because the forced mode runs the consumer at full power until the target is met (and minimum runtimes apply). |
| 2026-10-02 | Battery wear costs from optional purchase price and rated cycles, otherwise a low estimate (1 ct/kWh) shown as such; a price of 0 means none. Field measurements of home storage systems (RWTH, Nature Energy 2024: 2–3 percentage points capacity per year, mainly loss of lithium inventory, faster at high and low SoC) show calendar aging dominating at a few hundred cycles per year, so an extra cycle costs little; a high assumed value would silently block grid charging, a low one leaves the safeguarding to the minimum gain and the efficiency losses. The relevant cost of grid charging is rather time at high SoC, so charging is planned late, just before the energy is needed. |
| 2026-10-02 | Grid charging planned with dynamic programming instead of pairing cheap and expensive hours: the stored energy, the charge and discharge limits, the SoC limits and holding interact over the night, a greedy pairing gets them wrong. Planned only in a deficit, because the plan does not model charging from PV (that stays with the existing rules). Ties go to the later charge (calendar aging at high SoC). The horizon may end at midnight before the prices of the next day are known, with the remaining energy valued at the lowest price of the plan, so that it also works in winter without a PV refill. |
| 2026-10-02 | The benefit of the price aware control is estimated by replaying the recorded consumption and PV through a battery model with and without it, instead of comparing recorded costs before and after switching it on: months differ in weather and consumption, so a before/after comparison would mostly measure those. The model run is optimistic (perfect forecast) and labelled as an upper bound. |
| 2026-10-02 | Feeding in from the batteries is part of the same plan as grid charging instead of a separate rule: whether a kWh is worth more fed in now or kept for later depends on the same stored energy and prices. Effective only with an hourly market price credit (shown in the dashboard), because with a fixed or monthly credit the time of feeding in does not change the credit. Not below the morning reserve, so the house is not left on the grid in the morning for a small gain. |
| 2026-10-02 | The measured saving compares each run in which the price aware control acted with the same run played as usual by the battery model from the measured start energy, and counts runs without an action as 0: the counterfactual cannot be measured, and a before/after comparison of months would mostly show weather and consumption. Restricting it to runs with an action keeps the model's error from adding up over ordinary days. |
| 2026-10-02 | Fast batteries first: with clearly different learned response times the efficient split is made for a settled total following the total with the slowest response time, the fastest battery takes the difference, bounded so no battery works against the direction of the total. The 3 s minimum gap ignores the learning noise of similar batteries (identical simulators learned 0.7 s and 2.2 s). |
| 2026-10-02 | Battery steps that reverse the direction are learned apart (per battery and for the total), for the diagnostics only: whether a direction change is slower than a power change decides whether giving the batteries fixed roles around 0 W (one takes small surpluses, the other small deficits, never both at once) is worth it. The control keeps using the normal response time. |
| 2026-10-02 | A consumer with a daily target that draws no power for 3 days although switched on (`consumer_targets.NoPowerWatch`) is only shown on its card and in the energy flow, not as a notification or repair issue: a device with its own thermostat may legitimately need nothing for days, and the hint goes away by itself. Days it was switched on for less than 30 minutes (or the target / its window if shorter) do not count either way. |
| 2026-10-02 | A running on/off consumer keeps its power before power controlled consumers of higher priority. Seen in a real installation: a heating rod (priority 5, power controlled, own thermostat pausing) took the surplus back after each thermostat pause and pushed a dehumidifier (priority 6, on/off) off after its 1 minute minimum runtime, so the dehumidifier cycled every few minutes. A power controlled consumer can simply take less; switching costs a cycle. The priority still decides who is switched on first. |
| 2026-10-02 | "Avoid short runs" uses the minimum runtime as the length of a run instead of a separate field: it already is the shortest run the device should make. Starting is gated by the forecast surplus of that run (`consumers.cycling_holds`), dips are bridged for 5 minutes; no block planning in the day chart yet, the gate and the bridge already keep the runs long, and the daily targets' forced runs are contiguous anyway. |
| 2026-10-02 | Several current tariffs form one contract and a comparison tariff takes a side it lacks from the current contract (`tariff.combine_tariffs`, used by `configured_tariffs`): import and feed-in are often separate contracts, and a comparison of a new supply tariff should keep the existing feed-in instead of asking to enter it twice. The bill check stays per tariff. |
| 2026-10-02 | Negative prices: the price plan covers the surplus hours (options absorb all, take part and feed in the rest, absorb and charge from the grid on top) only when a negative import price or credit is within the known prices; otherwise the existing rules (grid friendly charging, feed-in cap) keep handling the surplus unchanged. Energy left at the end of a plan is worth at least 0, a negative price in the plan says nothing about later hours. A separate *highest grid import* setting caps every charging from the grid (main fuse), independent of peak shaving. |
| 2026-10-02 | The planned consumption of consumers uses their measured daily energy of the last 7 days: a temperature target needs at least that per day minus what it already got (the learned energy per degree only covers the way to the target temperature, not water draws and heat losses: a real heating rod showed 0.2 kWh planned against about 8 kWh a day), the following period's target is planned too (so *Tomorrow* shows it), and a controllable consumer without a target is expected to take its daily energy from the surplus (its power is left out of the house forecast, so it was missing from the plan). Heat pumps and wallboxes are left out: the heat pump has its own forecast model, a wallbox charges when a car is there. |
| 2026-10-02 | The price plans (`grid_charge`, `price_hold`, `cheapest_start`) work in quarter hours: market prices change every quarter hour and their hourly means hid cheap or expensive quarters. Consumption is only forecast per hour and is spread evenly over its quarters, PV comes from the forecast's own periods. The SoC projection stays hourly and gets the plan as hourly means; the backtest stays hourly because its recordings are. The quarter hour plan costs about 1.5 times the hourly one, so it is made again only for a new quarter hour, a 1 % step of the stored energy, other settings or after a minute instead of every control cycle. |
| 2026-10-02 | Tariffs as YAML (`tariff_yaml`, format `slems-tariff`) with a format version: older files are migrated step by step (`_MIGRATIONS`), newer ones refused, unknown fields are errors (an AI's typo such as `preis` must not be dropped silently). Import by pasting text, because the answer of an AI is copied anyway and a file upload would need another dependency. Supplier, validity from/to and source are kept with the tariff (`meta`) and exported again, but do not change the prices yet. Templates are planned in parts (energy of a supplier, grid fees of a grid operator, levies of a country), because a supplier's tariff alone fits every customer only in its energy part. |
| 2026-10-03 | Tariff templates in parts (`tariff_templates`): a template covers energy, grid and/or levies and several are combined into one tariff, each bringing the VAT of its parts. Shipped templates live in `templates/tariffs/<country>/` and contain only public list prices from price sheets with their source; a test validates every file. Own templates are read from `slems_tariff_templates` in the HA configuration (private ones, and testing without made-up files in the project). Data of the E-Control tariff calculator is not used: its terms of use forbid changing the data, combining it with other services and passing it on as files. A `percent` item covers levies that are a share of other items (e.g. a municipal levy on energy and grid). |
| 2026-10-03 | Updating tariffs made from templates (`tariff_updates`): a tariff remembers the family and price level of each template; newer levels and successors are offered, the old items end the day before (`valid_to` per item) instead of being replaced, so bill checks and the tariff comparison of past months keep their prices. Templates have a readable fixed `id` (country/provider/tariff, not a UUID: reviewable and derivable from a price sheet) that survives renaming; a successor names its predecessors in `replaces`, because old price levels stay unchanged once published. Several successors are possible, the user chooses (or declines until a newer level comes). Reminders are repair issues for current tariffs only. Own templates may reuse a shipped id on purpose to add a price level before an update of SLEMS. |
| 2026-10-03 | A tariff from templates keeps a snapshot of the template items it took over (per template, current price level). A change of the template at the same price level is a correction (detected by comparison, no revision number to forget): it changes the values in place and keeps the dates, so past bills use the right price, but leaves items the user changed. The snapshot also tells own changes apart exactly and allows resetting to the template values. |
| 2026-10-03 | Energy templates: list prices that apply to all customers are preferred (their price changes are updates for everyone); offers for new contracts (a price fixed from the contract start, new offer every month) are marked `offer: true` and only get corrections, never a newer offer as update. Tariffs following a published index that SLEMS cannot fetch (e.g. the Austrian ÖSPI) are left out; tariffs on the hourly market price are fine. Austrian grid templates are listed by grid area, which everyone knows, before the grid operator. |
| 2026-10-03 | Options of a price sheet (upgrades, bonuses with conditions, the metering fee of the feed-in direction) are optional template items chosen in an extra step and remembered with the tariff, so corrections and newer price levels keep the choice; one template per variant would double the list and a fixed item would be wrong for most customers. Feed-in tariffs are templates of their own (only export items) without the grid and levies step, because they are separate contracts and SLEMS already combines all current tariffs. |
| 2026-10-03 | Official monthly market values (Austria: Referenzmarktwert per § 13 EAG from the E-Control Excel file) are fetched with the market prices under the same consent instead of being written into templates: a new month in a template would be reported as a correction every month and would only arrive with an update of SLEMS. A `market_month` item names the value it follows (`market`); entered month values come first, the official value second, the day-ahead mean weighted by the own feed-in until the month is published. Only the values a configured tariff uses are fetched, at most twice a day. The E-Control tariff calculator terms do not apply; this is the statutory publication, attributed as "E-Control". |
| 2026-10-03 | The currency of the tariffs is the one set in Home Assistant (`hass.config.currency`), not a field of each tariff: one installation has one currency, and tariff prices are plain numbers (hundredths per kWh, whole units per year). Only the shown symbols follow it (`currency.py`, the panel). Market prices come from European exchanges in €/MWh and stay in euro; spot items in another currency would need an exchange rate, not done yet. |
| 2026-10-03 | Tariff files name their currency (`currency`, required for shipped templates and checked against the country): the template selection only offers templates in the currency of Home Assistant and a pasted file in another currency is refused, because prices are plain numbers and no exchange rate is applied. A file without a currency is taken to be in the currency of Home Assistant, the export always names it. |
| 2026-10-03 | Swiss tariffs are fetched from the ElCom open data (LINDAS SPARQL) on the user's action instead of shipped as files: a household cannot choose its supplier, energy, grid and levies depend on the municipality, so files would be more than 2000 per year and a list too long to choose from. Each published year becomes a price level of a template family (`elcom.template_of`), so the September publication of the next year uses the existing update workflow. Values are excluding VAT (dimension "Total exkl. MWST"); the metering tariff in CHF/year is the fixed part of the published total. Category means hide high and low tariff times; the machine-readable tariffs some operators publish (`urltr`) could add them later. |
| 2026-10-03 | A change of tariffs does not reload the integration: tariffs are read with every computation, only the price caches, the wanted reference values and the update check are refreshed (`coordinator.tariffs_changed`). Any other change (options, batteries, consumers) still reloads, decided by comparing the configuration without the tariffs (`_setup_key`). A reload would interrupt the control and the battery communication for a few seconds. |
| 2026-10-03 | German templates start with what is the same everywhere: the federal levies (one template per concession levy class of § 2 KAV, at its maximum, as most municipalities charge it) and the EEG feed-in credit (`tools/de_templates.py`). There is no open data set of the about 900 grid operators' fees like ElCom or the Austrian ordinance, so grid templates follow per operator from their price sheets (`GRID`). Several operators block automated downloads (the E.ON grid companies), their sheets are downloaded by hand. The § 14a EnWG variants are templates of their own (module 1, module 1 + 3) rather than options, because module 3 replaces the working price in windows (items of the same name) and only applies together with module 1; module 2 prices a separate meter of the device only and is left out. Each EEG commissioning period is a family of its own (`id` with the period): its rate is fixed for 20 years, a later period is no update for an existing plant. The no-credit rule at negative prices (§ 51 EEG, Solarspitzengesetz) is an item flag `zero_when_negative` instead of a separate unit, so it works with any unit; a tariff with it counts as dynamic, as it needs the market prices, and a period without a market price is paid. |
| 2026-10-03 | Thermostat cycling of a consumer is observed only, no longer a setting of the consumer: the pauses are learned all the time, and an observed property of the device needs no consent like a learned power that caps the planning. Pauses count for 30 days, so a replaced device or thermostat is relearned. Before two pauses the consumer is treated as saturated, which ends as soon as it draws again, so the difference to the former setting is small. |
| 2026-10-03 | The learned power of a power controlled consumer no longer caps its maximum power (replaces the decision of 2026-09-29): the power of a heating element varies with the water temperature and the grid voltage, the median sat below what it took at times, and the cap kept SLEMS from ever seeing more. It is its nominal power now, used for planning only (`_full_power_w`: forecast, daily target, feed-in cap); what the device does not take, the batteries balance on the grid meter. Samples count once the command is `CONSUMER_SETTLE_S` (15 s) or twice the learned response time old, as some devices take seconds to follow. |
| 2026-10-03 | Prices after the last known day-ahead price are estimated for the price aware control (`market_prices.estimate_prices`): the median of the same local quarter hour over the last 14 days of the same kind (working day or weekend; all days if a kind has fewer than 3), the swing around the mean of that day profile halved. The median resists single spikes, the halving is the safety margin: an estimated spread must be twice as large to move energy as a known one. Holding, charging from the grid, feeding in and the cheapest window of daily targets use them; the plan for negative prices starts only on known negative prices, as estimates are means of past days. Without consent there are no stored prices and so no estimates. |
| 2026-10-03 | Metering in the German grid templates is offered as options at the legal maximum (§ 30, § 32 MsbG: modern meter, smart meter by consumption, control box) rather than per operator: it is billed by the metering operator, who may be a third party, and most charge the maximum. Price sheets published only as preliminary (Stromnetz Berlin, Westfalen Weser Netz) are taken with "vorläufig" in the source; a later final sheet comes as a correction of the template. |
| 2026-10-03 | The setup is tested end to end against a real, fresh Home Assistant through the frontend API (`dev/e2e`) rather than with `pytest-homeassistant-custom-component`, which pins its own Home Assistant version against the one of the image. It found two faults no unit test saw: the battery form failing on the currency symbol, and reconfiguring a Marstek battery with an unchanged address failing its connection test (the running battery holds the only Modbus connection; the test is now skipped then). Too slow for every change, it runs on GitHub after a push. |
| 2026-10-03 | No templates of German suppliers of dynamic tariffs: Tibber, Octopus, Ostrom and Rabot publish their markup and fee only per postcode or not at all (Rabot takes a share of the saving, no item can express it), and the comparison sites contradict each other. Instead a general template per country (energy at the day-ahead price, markup and fee 0, to be entered from the contract) gives the right structure, and the AI prompt warns not to take the mean price of a dynamic bill as a fixed price. |
| 2026-10-03 | No official monthly market value for Germany (MW Solar): the API of netztransparenz.de needs a personal registration (OAuth client credentials), the website loads the values through an internal, undocumented service. A `market_month` item without an official value already uses the day-ahead mean weighted by the own feed-in, which approximates MW Solar (weighted by the German PV generation); published values can be entered by hand (`month_prices`). It concerns few households anyway: small plants get the fixed EEG credit. |
| 2026-10-04 | How often the price plans change their decision for the batteries (normal, hold, limit, charge, export, room) is counted per day for the diagnostics only (`PlanChanges`, `price_plan_changes`): back and forth within a quarter hour, changes at a new quarter hour, quarter hours with a plan. Whether a steadier plan (keep the plan unless the new one is better by a margin) is worth it is decided from these counts. A hysteresis would not change the grid import of a hold (it only moves in time), and would cost at most its margin with charging from the grid. |
