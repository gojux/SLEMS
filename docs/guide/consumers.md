# Consumers

[Back to the README](../../README.md#consumers) · [Deutsch](consumers.de.md)

<!-- toc -->

- [Settings](#settings)
- [Release for an automation of your own](#release-for-an-automation-of-your-own)
  - [Heat pump with release (hot water)](#heat-pump-with-release-hot-water)
- [Further settings and behaviour](#further-settings-and-behaviour)
- [Current control](#current-control)
- [Car at the wallbox](#car-at-the-wallbox)
- [Temperature sensors of the storage](#temperature-sensors-of-the-storage)
- [Battery support](#battery-support)
- [Role in the feed-in cap](#role-in-the-feed-in-cap)
- [Daily target](#daily-target)

<!-- /toc -->

Every consumer needs its own power **and** energy sensor.

Which consumers to add: the ones SLEMS controls (heating rod, wallbox,
dehumidifier, …) and heat pumps (their consumption is forecast from the
weather). Adding other devices only to measure them, e.g. a washing machine or
a dishwasher, currently does not improve the planning: their consumption is
already part of the house consumption, and its forecast (a profile per weekday
and hour) contains their average use. They only get their own card and box in
the energy flow.

## Settings

- **Device** (first step, optional): the consumer's device in Home Assistant,
  e.g. the heat pump, the heating rod or the wallbox. The devices that fit best
  come first (by their integration and the entities they have); devices
  whose entities SLEMS already uses are not listed. SLEMS suggests the entities of the next steps from it: power and
  energy (of a heat pump the electrical power, not the heat output), the
  control and its control entity with its range, the start/stop entity and
  active phases of a wallbox, the operating mode and the storage of a heat
  pump, the storage sensors of a heating rod and its water heater as external
  block. You check and correct them. **Meter device**: optional, if another
  device measures the consumer (e.g. a smart plug).
- **Heats a hot water storage** (heat pump): only then SLEMS asks for its
  operating mode (with **Release**) and the temperature of the storage.
- **Included in smart meter**: enable if the consumer is behind the smart
  meter (its consumption is already part of the grid power). Disable for
  consumers on a separate supply.
- **Show in the energy flow** (default on): off hides the consumer's box in
  the energy flow of the dashboard; its consumption still counts in the house,
  and the consumer card still shows it.
- **Type**: **heat pump** (heating and hot water, forecast from the weather),
  **heating rod** (e.g. hot water in summer), **wallbox** (electric vehicle) or
  **other**. Heat pump and heating rod may run at the same time. A wallbox is
  never part of the house consumption forecast (also when only measured);
  its defaults are current control, 6–16 A, 3 phases, 5 minutes minimum
  runtime and pause and battery support **automatic**.
- **Control**: none (measurement only), on/off via a switch, a power set
  point via a number entity in W, a [current set point](#current-control)
  in A, or a **Release** for an automation of your own. For controlled consumers an entity can be
  selected that blocks them externally (SLEMS leaves the consumer alone while
  a switch or binary sensor is on, or while a water heater is in operation
  mode **off**), a priority (1 = highest) and optionally a minimum runtime and a
  minimum pause. For on/off consumers a rough estimate of the power when on is
  enough if **Use learned values** is on; rather too low than too high, because
  SLEMS only learns while it runs the consumer.

## Release for an automation of your own

For devices an automation of your own starts, because it has to know more than
SLEMS (e.g. a washing machine with a start command, or the hot water charge of
a heat pump via SG Ready only while its compressor runs steadily). SLEMS
switches nothing itself, but plans the consumer like an on/off consumer with
the configured power and provides:

- **Release** (binary sensor): on while SLEMS would switch the consumer on
  (enough surplus, with **Avoid short runs** for a whole run, or from the
  latest start of its daily target on).
- **Forced run** (binary sensor): on while released from the latest start
  of the daily target on: then start at once, also without surplus.
- **Latest start** (sensor, timestamp): when the daily target has to start
  at the latest; empty once it is met.

On the consumer card under **Release**:
- **Release duration** (minutes, 0 = off): once opened, the release stays open at
  least this long, also without surplus, until the daily target is met or
  its deadline has passed. For an automation that waits for the right
  moment, e.g. a compressor run of the heat pump. With the source
  **Surplus only** this applies only while the consumer runs: until then the
  release closes without surplus and opens again with new surplus. Unlike the
  minimum runtime it does not count for **Avoid short runs**: SLEMS plans only
  the energy the consumer really takes.
- **Before the forced run** (with a daily target whose source goes beyond
  the surplus): the **Release duration** lies before the latest start. SLEMS releases the
  consumer from latest start − release duration on, also without surplus, but not
  forced; **Forced run** only from the latest start. The card shows when
  the release opens ("release 12:20–14:20 · forced from 14:20").
- **Surplus window** (with a daily target): from the PV and consumption
  forecast SLEMS plans the window, as long as the **Release duration**, at
  least as long as the run (from the earliest start until the forced run,
  the energy still needed at its learned power). As the automation can start
  anywhere in the window, all starts in it count: SLEMS takes the window that
  needs the least energy from the batteries or the grid, of equal ones the
  one with the most surplus. A run ends with the window, but goes on beyond
  it up to the minimum runtime, a hot water charge until it ends. **By surplus**: released
  as soon as there is enough surplus, then for the **Release duration** and
  afterwards while it lasts, no window planned. **By forecast**: the card
  (row **Release planned**, with the source **Surplus only** **Surplus
  window**) and the day chart show it, and it is also released for
  the planned window, also while the surplus dips, so the automation can wait
  for its moment; a part may then come from the batteries or the grid. With
  the source **Surplus only** the window is only shown. Once open, the window stays as
  planned until it closes.

While the automation has not started the device, it draws nothing: SLEMS
keeps the release on and gives the surplus to the batteries meanwhile (as
for a saturated consumer). With the source **Surplus only** it closes as soon
as the surplus no longer suffices.

The **Minimum runtime** of a release counts from the start of the device (from
50 W), not from the opening. Until the start the release stays open also without
surplus for up to 10 minutes, at most as long as the minimum runtime: this way a
short cloud does not take it back before the automation could start or the power
sensor shows the start.

### Heat pump with release (hot water)

With the type **Heat pump** the
release is for its hot water charge. It needs the **Operating mode** (e.g.
its sensor **Status**) and, in the next step, the states that mean hot
water; preselected is what sounds like hot water (e.g. `hot_water`, `dhw`,
"Warmwasser"). Only in these states its measured power counts as
controlled; the heating stays an uncontrolled load and its forecast
unchanged. The power when on is a starting value: with **Use learned
values** SLEMS learns it in hot water mode, so it follows the coefficient of
performance. With the boiler's sensors as temperature sensors a daily target
**Temperature** fits (e.g. minimum 42 °C, target 50 °C, deadline 16:00). The
entities of the daily target, the release and the control carry "hot water"
in their names (e.g. **Hot water release**, **Hot water forced run**,
**Hot water latest start**,
**Hot water control active**); the consumer card shows the control switch in its own section
**Hot water control** instead of in the menu (⋮). A
daily target **Energy** does not fit here, as the energy counter includes
the heating. The [guide to the hot water of a heat pump](../heat-pump-hot-water.md)
shows a detailed automation that waits for a good moment for the
compressor. Short example:

```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.heat_pump_hot_water_release
    to: "on"
  - trigger: state
    entity_id: binary_sensor.heat_pump_hot_water_forced_run
    to: "on"
  - trigger: state
    entity_id: binary_sensor.heat_pump_running  # your own condition
    to: "on"
conditions:
  - condition: state
    entity_id: binary_sensor.heat_pump_hot_water_release
    state: "on"
  - or:
      - condition: state
        entity_id: binary_sensor.heat_pump_hot_water_forced_run
        state: "on"
      - condition: state
        entity_id: binary_sensor.heat_pump_running
        state: "on"
actions:
  - action: number.set_value
    target:
      entity_id: number.heat_pump_smart_grid_offset
    data:
      value: 38
```

## Further settings and behaviour

- **Avoid short runs** (e.g. a device with a compressor): SLEMS switches the
  consumer on only when the forecast surplus lasts for a whole run (its
  minimum runtime, 15 minutes without one, or what its daily target still
  needs if less), and keeps it running through dips of the surplus for up to
  5 minutes (the batteries or the grid bridge them). Forced runs of a daily
  target are not affected. Set a minimum runtime and pause as well.
- **Own thermostat** (no setting, observed): a consumer that draws nothing
  although commanded counts as saturated for 15 minutes and keeps its last
  command (shown as **Planned** with "held"); once it draws half of that command
  again, SLEMS controls it again at once, as well as when its daily target
  is met or waits for its period (after its minimum runtime): then it is
  switched off, a release ends. If its own thermostat switches it
  off and on while it is commanded (e.g. a heating rod that measures at the
  element: two pauses of 30 s to 10 min within 30 days), SLEMS keeps
  controlling it instead: during a pause (no power for 2 response times,
  10–60 s) the batteries get its unused power, and as soon as it draws again
  it gets it back (shown as **thermostat pause**).
- **Switching off and turning down** (no setting): what a consumer draws
  beyond its command until switching off takes effect counts as house load;
  the batteries cover it according to its **Battery support** until it really
  draws less.
- **Runs on its own** (no setting, observed): a consumer SLEMS switched off
  (0 W, off or release closed) that keeps drawing 100 W or more runs on its
  own, e.g. a hot water charge started by hand that a closed release does not
  end. After 10 s and 1.5 times its response time for switching off (learned
  at the meter, else at its own sensor, else 10 s) SLEMS plans it like an
  uncontrolled consumer until it draws less again; then SLEMS controls it
  again.
- **Control active** (switch per controllable consumer, in the dashboard
  ⋮ → **Turn control on** or **Turn control off**): off means SLEMS only measures the consumer. Switching it off
  in operating mode **active** sets it to 0 W (or off) once; afterwards SLEMS
  leaves it alone and plans it like an uncontrolled load: its daily target is
  paused and nothing of it enters the forecast (the card shows **control off**);
  if it runs by itself, the control sees it live.

## Current control

For wallboxes and evcc loadpoints: SLEMS plans in watts and sends whole
amperes, rounded down so the charging stays within the planned power.

- **Minimum / maximum current** (default 6 / 16 A): below the minimum the
  consumer is stopped. Its power range is current × voltage × phases (3
  phases: 4.1–11 kW, 1 phase: 1.4–3.7 kW); one ampere is 230 W per phase.
- **Phases** fixed (1 or 3) or an **entity of the active phases** (1 or 3,
  e.g. of a wallbox or evcc loadpoint switching phases itself); the power
  range follows it. SLEMS does not switch phases itself.
- **Voltage** (default 230 V per phase).
- **Start/stop entity** (optional): a switch, or a select with the options
  for on and off (asked in the next step), e.g. the mode of an evcc
  loadpoint (**now** / **off**). Without it SLEMS stops by setting the lowest
  current the control entity takes (0 A if allowed).
- The control entity is a number entity in A or a select with ampere
  options.
- With evcc ([ha-evcc](https://github.com/marq24/ha-evcc)): the control
  entity is the maximum charging current of the loadpoint (a select), the
  phases entity its active phases. Step by step with screenshots:
  [Connecting a wallbox with evcc](../wallbox-evcc.md). Without a start/stop entity evcc decides itself when to
  start and stop (e.g. in its PV mode) and SLEMS only caps the current; with
  the charge mode as start/stop entity SLEMS controls the charging fully, so
  its battery support and forecasts decide. The card shows the planned power
  with amperes and phases, e.g. **4,140 W (6 A, 3 phases)**.

## Car at the wallbox

Optional, only for the consumer type **Wallbox**, in the step **Control**.
With evcc or a go-e SLEMS suggests the entities of the device.

- **State of charge of the car**: sensor with the car's state of charge in %.
  The wallbox itself does not know it when charging AC; it comes from evcc
  (the **Vehicle charge** of a loadpoint; evcc knows which car is plugged in) or
  from the car's integration.
- **Battery capacity of the car** (kWh): needed for a daily target **State of
  charge**.
- **Car plugged in**: a binary sensor (on = plugged in, e.g. **Connected** of an
  evcc loadpoint) or the car state of the wallbox, e.g. of a go-e. For a car
  state the next step asks which states mean plugged in, also while the car
  is not charging (waiting, complete); preselected is what sounds like it,
  missing states can be typed in. Without a car SLEMS does not control the
  wallbox and plans nothing for it: no forced run of a daily target, no share
  of the surplus in the forecast. Once a car is plugged in again, everything
  applies as before.
- The wallbox's box in the energy flow shows the state of charge with a car
  icon, **plugged in** without a state of charge sensor; without a car it is
  greyed out and shows **not plugged in**. The wallbox's card shows both in
  the row **Car**.
- **Charge now** (switch of every controlled wallbox, in the dashboard **Start
  charge now** in the menu (⋮) of its card or its box in the energy flow; with a
  state of charge sensor the dialog asks **Up to**): charges at
  full power, regardless of the surplus and the daily target. The batteries
  help according to the wallbox's [battery support](#battery-support), the
  grid delivers the rest. The switch turns off by itself when the car is
  unplugged or reaches the state of charge **Charge now up to** (default
  100 %, only with a state of charge sensor); switched on before a car is
  plugged in, it waits for one. The planning counts the charge up to that
  state of charge when the state of charge and the battery capacity are
  known. The box in the energy flow then shows **charging now**.

## Temperature sensors of the storage

Optional, up to two, e.g. the sensor of a boiler at the heating rod and one
higher up. A water heater (e.g. the hot water of a heat pump) counts with its
current temperature.

- SLEMS learns from the mean of the sensors (and of each sensor) how much
  energy the storage takes per degree, from which temperature the thermostat
  starts cycling, its mean power while cycling and when it is full. Which
  sensor switches the thermostat and where they sit does not matter. With
  other sensors selected, it learns anew.
- From three heating runs and two starts of the cycling on, the consumer card
  shows how much the storage can still take, and the feed-in cap plans the
  consumer only with 80 % of that.
- Its box in the energy flow shows the temperature of the sensor chosen for
  the daily target (the mean by default) with a boiler symbol.

## Battery support

Select per consumer behind the smart meter (also measured only ones), on its
card under **Show settings**: how far the batteries may cover it when there is
no PV surplus.

- **Always** (default): like any other load.
- **Never**: its power always comes from the grid; the batteries cover only
  the rest of the house.
- **Automatic**: the batteries cover it only with the energy they can spare,
  the **budget**: the lowest stored energy until the next charge from PV (from
  the forecast of house consumption and PV, without night discharge) minus
  the minimum state of charge, the **morning reserve** and the safety buffer;
  with import peak shaving also its SoC threshold. "Until the next charge"
  is the coming morning at night and the morning after the coming night
  during a surplus, on a day whose forecast PV surplus fills the batteries
  from the minimum to the maximum state of charge (a little surplus on a
  dull day does not count, the budget then looks to the next such day). The budget is computed anew from the current state of
  charge all the time and shrinks while the batteries cover the consumer;
  once used up the consumer runs from the grid until the budget is back at
  0.2 kWh. Several consumers on automatic share the budget. Energy the night
  discharge would feed in may go to them instead.

The PV surplus is not affected, import peak shaving still covers peaks, and a
forced run of a daily target with the source **battery** may use the batteries.
The forced runs of daily targets with the source **grid** follow the setting in
the forecast chart as well (on automatic until the next charge from PV;
later ones are planned like **always**); measured only consumers are part of the
consumption forecast, there the setting acts only in the control. The card
shows the setting with the budget and whether the batteries cover the
consumer right now; while it runs from the grid for this reason, its box in
the energy flow carries the badge **grid**. The budget is also the attribute
`support_budget_kwh` of **Stored energy total**.

Useful for loads that would empty the batteries in the evening: a sauna, an
instantaneous water heater, a heating rod with the source **grid**, later a
wallbox.

## Role in the feed-in cap

Select per controlled consumer, on its card in the dashboard under **Feed-in
cap** while the cap is on. Within a role the priority decides. Applies only
while **Control active** is on (greyed out otherwise). See **Feed-in cap**.

- **Supporting**: gets no other surplus while the feed-in cap is on, only the
  surplus above the limit the batteries cannot absorb; if the forecast shows
  that a peak does not fit into the batteries, it runs from the start of the
  peak so its power is used over the whole peak.
- **Normal** (default): surplus as without the feed-in cap; above the limit it
  takes what the batteries and the supporting consumers cannot, before it is
  curtailed.
- **Never**: surplus as without the feed-in cap, never the surplus above the
  limit.

## Daily target

Per controlled consumer, on its card under **Daily target**.

- **Kind**: **runtime** (time it draws power), **enabled time** (time SLEMS has it
  switched on, for devices with their own control such as a dehumidifier with
  a hygrostat), **energy** (kWh), **temperature** (with temperature sensors:
  minimum and target temperature of its storage; with two sensors **Sensor**
  chooses the mean, sensor 1 or sensor 2, in the order of the consumer's
  configuration) or **state of charge** (a wallbox with the car's state of
  charge and battery capacity, see [Car at the wallbox](#car-at-the-wallbox)).
- **Period**: counted from one deadline (**Until**, default 22:00, also across
  midnight) to the next and met from the surplus first.
- **Earliest start** (optional): before this time SLEMS
  does not switch the consumer on, not even with surplus (e.g. a dehumidifier
  only from 10:00); the latest start is never before it. If the hours (or the
  energy at full power) do not fit between the earliest start and the
  deadline, the target settings show a warning.
- **No power**: if a consumer with a daily target draws no power on 3 days in
  a row although SLEMS switched it on (at least 30 minutes a day, less if the
  target or its window is shorter), its card and its box in the energy flow
  show a hint (switched off, fuse or broken?) until it draws power again.
- **Source** decides what may cover the rest in time:
  - **surplus only** (default; the target may be missed, then a notification
    says so – not if SLEMS could not control the consumer at some time of the
    period: operating mode not **Active** or its **Control active** switched off –
    or if it declined power because its own thermostat was satisfied
    (saturated or resting); if it was blocked externally or no car was plugged
    in, the notification names how long),
  - **surplus + batteries** (from the latest start on the consumer runs
    regardless of the surplus as long as the batteries can deliver, a power
    controlled one at most with their discharge power),
  - **surplus + batteries + grid**.

  The latest start is the deadline minus the remaining time × 1.2 minus 10
  minutes.
- **Before the batteries when short**: the consumer gets the surplus before
  the batteries if the forecast surplus until the deadline is short for the
  rest of its target plus filling the batteries.
- **Temperature**: below the minimum the consumer always gets the surplus
  before the batteries (forced from the latest start, estimated with the
  learned energy per degree, otherwise 2 hours before the deadline); at the
  target temperature it is off for the rest of the day; if a higher target
  temperature is set later in the day, it heats on. A temperature target
  applies to the calendar day: after the deadline the consumer stays off until
  midnight (**waits until 00:00**), also with surplus; with an earliest start
  until then (e.g. hot water by a heat pump only from 11:00, when the air is
  warmer and there is surplus). If the temperature falls
  below the minimum again (e.g. after drawing hot water), the target is open
  again: up to the minimum with priority, then with the surplus up to the
  target. Choosing another sensor starts the target open again.
  SLEMS learns how the storage cools per hour of the day while it is not
  heated (heat losses and the usual water draws, about the last two weeks).
  Once it knows 12 hours of the day, it forecasts the temperature at the
  deadline: if the storage will fall below the minimum by then, the forced
  run (and a release before it) is planned for that temperature, also
  while it is still above the minimum and before the earliest start. The row
  **Plan** shows the forecast, e.g. **about 37 °C at 15:30 without heating**.
- **State of charge**: target state of charge of the car by the deadline
  (default 80 %). The energy still needed is the missing state of charge ×
  battery capacity, with 10 % charging losses (e.g. 40 % of 60 kWh: about
  26.7 kWh from the grid); the latest start follows from it as for an energy
  target. Without a known state of charge the wallbox charges from the
  surplus only. Once the target state of charge is reached, the wallbox stays
  off until the deadline (after its minimum runtime), also if the state of
  charge drops a little afterwards; a higher target state of charge is open
  again. The card shows e.g. **62 / 80 % · until 07:00**, without a car also
  **not plugged in**.
- **Display**: the card shows the progress, e.g. **1.5 / 4 h** (while the target
  waits for the next day, the row is called **Tomorrow** instead of **Today**), **Details**
  in full, e.g. **1.5 / 4 h · until 22:00 · about 2.3 kWh to go**, in the row
  **Plan** when SLEMS plans the rest, e.g.
  **release 12:20–14:20 · forced from 14:20 · takes about 1 h 5 min**, and the
  chips **priority** or
  **forced** while they apply; the consumer's box in the energy flow carries
  the same badge, and the strategy tile lists it (**Before the batteries:
  …**), since the batteries then only get what is left. The energy still needed is exact for an energy
  target; for a runtime or enabled time it is the remaining time at full power
  (less if the consumer's own thermostat stops it earlier); for a temperature
  target it is the way up to the target temperature with the learned energy
  per degree. That leaves out water draws and heat losses, so it is at least
  the mean daily energy of the consumer's last 7 days with consumption (at
  least 2) minus what it already got in the period (**estimated from the last
  days**); this estimate also applies while the energy per degree is not
  learned yet, without such days **energy still being learned**.
- **Planning**: with a source beyond the surplus the planning counts the
  forced run as extra consumption from the latest start on, as if the surplus
  covered nothing more; the planned run shrinks as the surplus fills the
  target. The rest of the target the planning expects from the surplus: hour
  by hour it takes what is left after charging the batteries, in order of
  priority, from now (or the earliest start) until the deadline and at most at
  the consumer's power; the expected feed-in drops by it. The target of the
  following period is planned the same way (a temperature target with the
  daily energy of the last days), so **Tomorrow** shows it too. A controllable
  consumer without a daily target is expected to take its mean daily energy
  of the last days from the surplus (today what is left of it). Both appear in the
  day chart as **Consumers (planned)** and in the consumption forecast, the state
  of charge forecast and the night discharge. Below the day chart, **Daily
  targets** shows a lane per consumer with a daily target: its period (faint),
  the release (the planned window and the release before the forced run, one
  span where they overlap), the planned window outlined in the PV colour, the
  forced run and the deadline (a mark); a legend below names what is shown.
  While a hot water charge runs, the release it started in stays, the
  release still to come and the forced run give way to the charge (hatched,
  until about the target temperature); if the charge stops early, for
  example for a defrost, they come back. A consumer with the role
  **Supporting** is left out of the surplus part while the feed-in cap is on:
  the feed-in cap plans it with the surplus above the limit.
