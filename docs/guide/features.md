# All features

[Back to the README](../../README.md#features-at-a-glance) · [Deutsch](features.de.md)

The complete list of what SLEMS can do. Without a note a feature is proven in
production; everything else is marked as *experimental* or *untested*.

<!-- toc -->

- [Setup](#setup)
- [Forecasts](#forecasts)
- [Batteries](#batteries)
- [Consumers](#consumers)
- [Planning and control](#planning-and-control)
- [What SLEMS learns by itself](#what-slems-learns-by-itself)
- [Dashboard](#dashboard)
- [Tariffs and prices](#tariffs-and-prices)
- [Without batteries](#without-batteries)
- [Local and transparent](#local-and-transparent)

<!-- /toc -->

## Setup

- Any number of batteries, added/edited/removed at any time.
- Smart meter, PV power and weather entity freely selectable.
- Setup from the devices of smart meter and PV systems and from the energy
  dashboard; several PV systems summed up.
- Smart meter read directly over Modbus as well (SunSpec), with the sensor as
  fallback.
- Operating mode **Off / Observe only / Active**.
- Vacation switch and bad weather mode.
- Repair issues, notifications and diagnostics.

Guide: [Installation and setup](setup.md).

## Forecasts

- PV forecast from any solar forecast integration (Forecast.Solar, Solcast,
  …).
- Consumption forecast today/tomorrow (history + weather, heat pump
  temperature dependent, public holidays of the own state or canton), raised
  at once after a sudden rise.
- Correction of the PV forecast during the day and forecast accuracy of PV
  and consumption.
- Projection of the state of charge for today and tomorrow.

Guide: [Consumption forecast](consumption-forecast.md).

## Batteries

- Marstek Venus E 3.0 via Modbus TCP.
- Battery from existing entities: read-only or controlled by set point,
  charge/discharge power or script, entities suggested from the device
  (*control experimental*).
- Battery from a Modbus profile: an own profile made from the manual with an
  AI prompt, values read and write preview before anything is written, a
  trial with the self-test before it is controlled, observed after a firmware
  update; shipped (unverified): Marstek Venus E Gen 2 (*experimental*).
- Battery efficiency (battery counters, learned or manual), learned usable
  capacity.
- Wear costs per battery (from purchase price and cycles) in the planning.
- Battery menu: pause the communication for firmware updates, disable a
  battery, open its device.
- Distribution between batteries by efficiency: only as many run as is most
  efficient for the current power, the others stay in standby as a reserve;
  the fastest battery takes quick changes first, rotation with smooth
  transition.
- Battery limits: minimum/maximum SoC, charge/discharge power limit (e.g.
  800 W), temperature charge limit, an early discharge stop of the BMS near
  the minimum detected.
- Detection of batteries that do not deliver the commanded power,
  confirmation of set points.
- Cell delta and active cell balancing (Marstek Venus E 3.0).
- Self-test of a battery: effect at the smart meter, sign and unit of the
  power, energy counters, state of charge, response time, release checked at
  the smart meter, stops at once on a wrong direction.
- Dead time and phase per battery measured with a short probe step (for a new
  battery, after changes of battery, firmware, smart meter or integration,
  every 90 days, by hand).
- Regular full charge for the SoC calibration of LFP cells: SLEMS charges the
  battery whose last full charge is the oldest first, one at a time and only
  with the surplus it would store anyway, and spares it when discharging so
  it starts the next day higher. It also recognises a full charge the BMS
  ends slightly below 100 %, and it measures the cell delta at the top.

Guides: [Batteries](batteries.md),
[Battery from a Modbus profile](battery-modbus-profile.md).

## Consumers

- Consumers with own power/energy sensors, inside or outside the smart meter.
- Consumer setup from its device: devices ranked by fit (e.g. heat pump,
  heating rod, wallbox), entities, control and its range suggested.
- Phase of consumers learned from the smart meter's phases (starting with the
  history), of batteries in the self-test, change reported, learn again;
  energy counters that do not net the phases detected (*control per phase not
  yet*).
- Heat pump as consumer type (weather dependent forecast), optionally a
  release of its hot water charge (operating mode, learned hot water power,
  [guide](../heat-pump-hot-water.md)).
- Control of consumers: on/off, power set point or release for an automation
  of your own (with latest start), priority, minimum runtime/pause, avoid
  short runs, external blocking, control switchable per consumer.
- Consumers with their own thermostat: pauses recognised, their power goes to
  the batteries meanwhile.
- Temperature sensors of a storage: learned energy per degree, remaining
  capacity, learned cooling per hour.
- Role of a consumer in the feed-in cap (supporting, normal, never).
- Daily targets: runtime, enabled time, energy, temperature or the car's
  state of charge, with earliest start, deadline and source (surplus,
  batteries, grid); temperature targets with a safety net (minimum again
  within a set time).
- Battery support per consumer (always, automatic with the energy the
  batteries can spare, never).
- Wallbox / evcc loadpoint: current set point in A with phases, start/stop
  ([guide](../wallbox-evcc.md)) (*not yet tested with a real wallbox*).
- Car at the wallbox: state of charge in the energy flow, without a plugged
  in car no control and no planning, **Charge now** at full power (*not yet
  tested with a real wallbox*).

Guide: [Consumers](consumers.md).

## Planning and control

- Distribution of surplus between batteries and consumers (priorities, split,
  minimum runtime/pause); battery priority until the charge is secured, set
  automatically from the coming night's consumption.
- Real-time control of batteries and consumers (operating mode **Active**):
  event driven on every report of the smart meter; the learned response time
  of the batteries (also of starts out of standby and reversals) is
  compensated and the control gain adapts itself.
- Averaged grid surplus (0–300 s).
- Grid surplus targets for charging/discharging, maximum export while
  discharging.
- Grid friendly charging: absorb PV feed-in peaks. The feed-in limit is
  recalculated continuously and drops by itself when the day turns out worse
  than forecast.
- Feed-in cap: store the PV energy above a feed-in limit, make room in time.
- Night discharge to a forecast based reserve.
- Import peak shaving: an import limit and a state of charge threshold below
  which the batteries keep their energy for the peaks (the cheapest pair with
  a power price, one of them calculated from the consumption statistics, or
  both set; manually enabled). With a power price in the tariff the limit
  holds on average over the quarter hour, forced consumers give way, the
  batteries are topped up from the grid below the limit before the peaks, the
  overview shows the month's peak and its extra cost, and the tariff
  comparison estimates what peak shaving would have saved.

Guide: [Operation and control](control.md).

## What SLEMS learns by itself

Besides the consumption forecast, SLEMS learns the conversion losses and the
response time of the batteries, the smart meter interval, the control gain
and the consumption peaks; on request also the forecast buffers, the grid
surplus targets, the control timing, the usable capacity of every battery and
the power and thermostat behaviour of consumers. Every learned value can be
switched back to a fixed one (see [Learned values](control.md#learned-values)).

## Dashboard

- Dashboard (sidebar panel): energy flow, key figures, daily forecast and
  plan chart, batteries, consumers, settings.
- Simulation in the dashboard: day plan with other settings (grid friendly
  charging, night discharge, feed-in cap, battery limits, forecasts ±%)
  compared with the current one (today or tomorrow), with the price chart;
  what other batteries, more PV or another consumption would have changed in
  the recorded year.

Guide: [Dashboard](dashboard.md).

## Tariffs and prices

- Tariffs as on the bill (time windows, VAT per group, percent levies) with a
  check against a bill.
- Tariff templates: Austria (grid fees of all grid areas, levies, suppliers
  incl. options such as a green upgrade), Germany (levies, EEG feed-in
  credit, grid fees of 19 grid operators incl. § 14a modules and metering),
  Switzerland (query of the ElCom open data); a general template for dynamic
  tariffs (market price plus the markup of the contract).
- Template updates: new prices, successor tariffs and corrections as a repair
  issue, reset to the template values.
- Tariffs as YAML (import, export, own templates) with a prompt to create one
  from a bill with an AI.
- Day-ahead prices per quarter hour (APG, SMARD, Energy-Charts) and official
  monthly market values (E-Control) only with consent; no feed-in credit at
  negative prices where the tariff says so (EEG).
- Monthly tariff comparison: import costs and feed-in credit per tariff,
  tariffs shown or hidden, estimated saving of the price aware control.
- Price aware control: stored energy kept for the expensive hours, grid part
  of daily targets in the cheapest window, optional charging from the grid
  and feeding in from the batteries.
- Estimated day-ahead prices until the next day's prices are published
  (profile of the last days, weekends and public holidays).
- Sensors for the energy dashboard of Home Assistant (disabled by default):
  the current import and feed-in price per kWh, and running import costs and
  feed-in compensation including base price and power price.

Guide: [Tariffs and prices](tariffs.md).

## Without batteries

SLEMS also works without any battery: add the consumers only.

- **Works**: directing the PV surplus into controlled consumers (heating rod,
  wallbox also with evcc, dehumidifier, heat pump through a switch input) with
  priorities, minimum runtime and pause, **Avoid short runs** and external
  blocking; daily targets with the sources **Surplus only** and **Surplus + batteries + grid** (also
  in the cheapest window with price aware control); tariffs, bill check,
  market prices, tariff comparison and price chart; energy flow, consumption
  and PV forecast with their accuracy.
- **Not without batteries**: everything that charges, discharges or plans a
  battery – grid friendly charging, night discharge, import peak shaving,
  price hold, charging from the grid, feeding in from the batteries – and
  currently the feed-in cap (it needs a battery even with supporting
  consumers).

## Local and transparent

Control, forecasts and learning run locally in Home Assistant, without a
cloud account. SLEMS goes to the internet only for market prices and monthly
market values (only with your consent), for Swiss tariffs from the open data
of ElCom and once to suggest the region for public holidays (Nominatim, see
[Public holidays](consumption-forecast.md#public-holidays)). The operating
mode **Observe only** shows what SLEMS would do before it controls anything,
also next to an existing battery integration.
