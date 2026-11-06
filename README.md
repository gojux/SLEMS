<p align="center"><img src="assets/logo.png" alt="SLEMS" height="96"></p>

**English** | [Deutsch](README.de.md)

# SLEMS

SLEMS is a Home Assistant integration that manages home batteries and
controllable consumers. Its goal is to operate the batteries in a grid
friendly way, e.g. by shifting the charging time so that the daily feed-in
peak is absorbed, based on forecasts of consumption and PV production.

The name is a combination of *Slug* (our "favourite pets" and a funny word for
a whole range of [very interesting
animals](https://en.wikipedia.org/wiki/Sea_slug)) and *EMS* (energy management
system).

![SLEMS overview: energy flow, key figures and the forecast and plan chart of the day](docs/images/dashboard_overview_en.png)

> **Status: beta.** SLEMS has been running in production without problems
> for quite some time; new features and improvements keep coming often. In the operating mode **Active** it sends
> commands to batteries and switches consumers. Use it at your own risk: check
> the limits of your batteries and the fuses of your circuits. Please report
> faults as an [issue](https://github.com/gojux/SLEMS/issues) with the
> diagnostics (see [Quick start](#quick-start)).

## Does SLEMS fit your installation?

| Battery | Support |
|---|---|
| AC battery (its own inverter, at a socket or in the distribution board) | Marstek Venus E 3.0 directly over Modbus TCP; other models through a [Modbus profile](docs/guide/battery-modbus-profile.md) or the entities of their Home Assistant integration (control experimental) |
| Plug-in battery with PV modules (balcony solar with a battery, e.g. Marstek B2500, Zendure SolarFlow, Anker Solarbank) | through the entities of its integration with **Own PV modules at the battery** (experimental, testers and feedback welcome) |
| Hybrid inverter (PV and battery at one inverter) | its battery through the entities of its integration (experimental, testers and feedback welcome) |
| No battery | PV surplus into consumers only ([without batteries](#without-batteries)) |

If you would like to use SLEMS with another battery, please
[open an issue](https://github.com/gojux/SLEMS/issues) with the model and how
it is connected (Modbus, local API, Home Assistant integration), and whether
you could test it.

| Tariff templates | |
|---|---|
| Austria | 23 supply tariffs and 2 PV feed-in tariffs of 11 suppliers (Burgenland Energie, Energie AG Oberösterreich, Energie Steiermark, EVN, IKB, illwerke vkw, Kelag, LINZ AG, Salzburg AG, TIWAG, Wien Energie), a general dynamic tariff, the grid fees of all 14 grid areas, the federal levies |
| Germany | a general dynamic tariff, the EEG feed-in credit (up to 10 kWp, 8 commissioning periods), the grid fees of 19 grid operators, the federal levies |
| Switzerland | the tariff of your municipality from the ElCom open data |
| All countries | any tariff without a template (also in Austria, Germany and Switzerland): entered as on the bill, or as a YAML file an AI makes from your bill with the prompt provided (see [tariffs](#tariffs-optional)) |

## Contents

- [Does SLEMS fit your installation?](#does-slems-fit-your-installation)
- [Quick start](#quick-start)
- [Why SLEMS?](#why-slems)
- [Features](#features)
- [Installation](#installation)
  - [HACS (custom repository)](#hacs-custom-repository)
  - [Manual](#manual)
- [Setup](#setup)
  - [Smart meter over Modbus (optional)](#smart-meter-over-modbus-optional)
  - [Tariffs (optional)](#tariffs-optional)
  - [PV forecast](#pv-forecast)
  - [Weather (optional)](#weather-optional)
  - [Consumption forecast](#consumption-forecast)
- [Without batteries](#without-batteries)
- [Batteries](#batteries)
- [Consumers](#consumers)
- [Dashboard](#dashboard)
  - [Overview](#overview)
  - [Batteries tab](#batteries-tab)
  - [Consumers tab](#consumers-tab)
  - [Simulation](#simulation)
  - [Settings tab](#settings-tab)
  - [Using the dashboard](#using-the-dashboard)
- [Operation and control](#operation-and-control)
  - [Operating mode](#operating-mode)
  - [Bad weather mode](#bad-weather-mode)
  - [How the control works](#how-the-control-works)
  - [Which option when?](#which-option-when)
  - [Grid friendly charging](#grid-friendly-charging)
  - [Feed-in cap](#feed-in-cap)
  - [Import peak shaving and power price](#import-peak-shaving-and-power-price)
  - [Learned values](#learned-values)
  - [Further settings (entities)](#further-settings-entities)
  - [Problems and notifications](#problems-and-notifications)
- [Language](#language)
- [Roadmap](#roadmap)
- [Development](#development)
- [License](#license)

## Quick start

What you need:

- Home Assistant 2026.9 or later and HACS.
- A smart meter in Home Assistant that reports the grid power (import and
  export) continuously, e.g. a Shelly Pro 3EM. The more often it reports, the
  more precisely SLEMS controls (e.g. every second).
- Optional: a PV forecast (e.g. Forecast.Solar), batteries and consumers that
  SLEMS should control.

In five steps:

1. Install SLEMS through HACS and restart Home Assistant
   ([Installation](#installation)).
2. Add the integration, select the smart meter and the PV systems and check
   the sign of the grid power ([Setup](#setup)).
3. Add batteries and consumers ([Batteries](#batteries),
   [Consumers](#consumers)). Most batteries allow only one Modbus
   connection: disconnect them from other integrations first.
4. Let SLEMS run in the operating mode **Observe only** for a while (the
   default), so obvious problems show up early. The dashboard shows the
   forecast and the plan; nothing is controlled yet.
5. Turn off other controls of the same batteries and consumers (automations,
   other integrations) and set the [operating mode](#operating-mode) to
   **Active**. New batteries first run their
   [self-test](docs/guide/batteries.md#self-test-of-a-battery) (about 5–10 minutes), then SLEMS
   takes over.

The defaults are fine to start with; SLEMS learns losses, response times and
forecast buffers by itself (see [Learned values](#learned-values)). Hints on
problems are under [Problems and notifications](#problems-and-notifications).
For a fault report, attach the diagnostics: **Settings → Devices & services →
SLEMS → ⋮ → Download diagnostics**. Addresses and serial numbers of the
batteries are redacted in them.

## Why SLEMS?

Most home battery controls react to the moment: they keep the grid power at
zero and charge whenever there is surplus. The battery is then full by late
morning, and the midday peak goes into the grid anyway. SLEMS plans ahead and
controls precisely:

- **Grid friendly instead of full by 10 o'clock.** From the PV and consumption
  forecasts SLEMS calculates a feed-in limit and charges the batteries with
  the surplus above it, so they absorb the feed-in peak and are still full in
  the evening. The limit is recalculated continuously and drops by itself when
  the day turns out worse than forecast.
- **Its own consumption forecast.** Learned from the long-term statistics of
  your house, with the weather and a separate heat pump model; vacation mode,
  night discharge to a forecast based reserve and a projection of the state of
  charge for today and tomorrow.
- **Batteries and consumers in one plan.** The surplus is distributed between
  batteries and controllable consumers (heating rod, heat pump, …) with
  priorities, battery priority until the charge is secured, minimum runtimes,
  external blocking and thermostat pauses. Daily targets (runtime, energy or
  temperature by a time of day) are met from the surplus first and only if
  needed from the batteries or the grid; per consumer you decide whether the
  batteries may supply it.
- **Several batteries at their efficient point.** SLEMS learns the conversion
  losses of every battery, runs as few batteries as useful and switches
  between them with a smooth transition.
- **Feed-in cap.** If you may feed in only part of the PV power (e.g. 60 % or
  nothing at all), SLEMS stores the energy above the limit and makes room in
  the batteries in time instead of curtailing PV.
- **Import peaks shaved economically.** With power based grid fees (a power
  price for the highest quarter hour of the month) SLEMS picks the import
  limit and the reserve with the lowest expected cost, keeps energy in the
  batteries for the peaks, lets forced consumers give way and tops the
  batteries up below the limit before the peaks.
- **Prices in view.** Tariffs as on the bill, with templates for Austria,
  Germany and Switzerland; day-ahead prices per quarter hour only with your
  consent, estimated until the next day's prices are published. The price
  aware control keeps the stored energy for expensive hours and can
  optionally charge cheaply from the grid or feed in when it pays; a monthly
  tariff comparison shows what another tariff would have cost.
- **Precise control without oscillation.** Event driven on every report of the
  smart meter; the learned response time of the batteries (also of starts out
  of standby and reversals) is compensated and
  the control gain adapts itself.
- **Battery care and safety.** State of charge window, power limits (e.g. 800
  W), temperature charge limit, cell delta monitoring with active cell
  balancing, detection of batteries that do not respond, repair issues and
  notifications.
- **Regular full charge without losing surplus.** LFP batteries recalibrate
  their state of charge only when full. SLEMS charges the battery whose last
  full charge is the oldest first, one at a time and only with the surplus it
  would store anyway, and spares it when discharging so it starts the next day
  higher. It also recognises a full charge the BMS ends slightly below 100 %,
  and it measures the cell delta at the top.
- **Learns instead of asking.** Besides the consumption forecast, SLEMS learns
  the conversion losses and the response time of the batteries, the smart
  meter interval, the control gain and the consumption peaks; on request also
  the forecast buffers, the grid surplus targets, the control timing, the
  usable capacity of every battery and the power and thermostat behaviour of
  consumers (see **Learned values**). Every learned value can be switched back
  to a fixed one.
- **Transparent and local.** A dashboard with energy flow, forecast chart and
  all settings. Control, forecasts and learning run locally in Home Assistant,
  without a cloud account. SLEMS goes to the internet only for market prices
  and monthly market values (only with your consent), for Swiss tariffs from
  the open data of ElCom and once to suggest the region for public holidays
  (Nominatim, see [Consumption forecast](#consumption-forecast)). The
  operating mode **Observe only** shows what SLEMS would do before it
  controls anything, also next to an existing battery integration.
- **Try before you change.** The simulation in the dashboard calculates the
  day plan with other settings (grid friendly charging, night discharge,
  feed-in cap, battery limits, forecasts ±%) and compares feed-in, grid import
  and state of charge with the current plan, without changing anything.
- **Also without batteries.** SLEMS can direct the PV surplus into consumers
  alone (heating rod, wallbox, dehumidifier, …), with daily targets, tariffs
  and forecasts (see [without batteries](#without-batteries)).

**Other batteries and electric cars:** batteries that SLEMS does not yet
support directly or through a
[Modbus profile](docs/guide/battery-modbus-profile.md) can still be
controlled if there is an integration for them in Home Assistant: SLEMS then
controls them through its entities (experimental). SLEMS controls a wallbox
with the surplus, with **Charge now** and with a daily target by the car's
state of charge. evcc specialises in EV charging and complements SLEMS well
(see the [evcc guide](docs/wallbox-evcc.md)).

## Features

| Setup | Status |
|---|---|
| Any number of batteries, added/edited/removed at any time | ✅ |
| Smart meter, PV power and weather entity freely selectable | ✅ |
| Setup from the devices of smart meter and PV systems and from the energy dashboard; several PV systems summed up | ✅ |
| Smart meter read directly over Modbus as well (SunSpec), with the sensor as fallback | ✅ |
| Operating mode **Off / Observe only / Active** | ✅ |
| Vacation switch and bad weather mode | ✅ |
| Repair issues, notifications and diagnostics | ✅ |

| Forecasts | Status |
|---|---|
| PV forecast from any solar forecast integration (Forecast.Solar, Solcast, …) | ✅ |
| Consumption forecast today/tomorrow (history + weather, heat pump temperature dependent, public holidays of the own state or canton), raised at once after a sudden rise | ✅ |
| Correction of the PV forecast during the day and forecast accuracy of PV and consumption | ✅ |

| Batteries | Status |
|---|---|
| Marstek Venus E 3.0 via Modbus TCP | ✅ |
| Battery from existing entities: read-only or controlled by set point, charge/discharge power or script, entities suggested from the device | ✅ (control experimental) |
| Battery from a Modbus profile: an own profile made from the manual with an AI prompt, values read and write preview before anything is written, a trial with the self-test before it is controlled, observed after a firmware update; shipped (unverified): Marstek Venus E Gen 2 | ✅ (experimental) |
| Battery efficiency (battery counters, learned or manual), learned usable capacity | ✅ |
| Wear costs per battery (from purchase price and cycles) in the planning | ✅ |
| Battery menu: pause the communication for firmware updates, disable a battery, open its device | ✅ |
| Distribution between batteries by efficiency, the fastest battery takes quick changes first, rotation with smooth transition | ✅ |
| Battery limits: minimum/maximum SoC, charge/discharge power limit (e.g. 800 W), temperature charge limit, an early discharge stop of the BMS near the minimum detected | ✅ |
| Detection of batteries that do not deliver the commanded power, confirmation of set points | ✅ |
| Cell delta and active cell balancing (Marstek Venus E 3.0) | ✅ |
| Self-test of a battery: effect at the smart meter, sign and unit of the power, energy counters, state of charge, response time, release checked at the smart meter, stops at once on a wrong direction | ✅ |
| Dead time and phase per battery measured with a short probe step (for a new battery, after changes of battery, firmware, smart meter or integration, every 90 days, by hand) | ✅ |
| Regular full charge for the SoC calibration of LFP cells (one battery at a time, from the surplus, spared when discharging) | ✅ |

| Consumers | Status |
|---|---|
| Consumers with own power/energy sensors, inside or outside the smart meter | ✅ |
| Consumer setup from its device: devices ranked by fit (e.g. heat pump, heating rod, wallbox), entities, control and its range suggested | ✅ |
| Phase of consumers learned from the smart meter's phases (starting with the history), of batteries in the self-test, change reported, learn again; energy counters that do not net the phases detected | ✅ (control per phase not yet) |
| Heat pump as consumer type (weather dependent forecast), optionally a release of its hot water charge (operating mode, learned hot water power, [guide](docs/heat-pump-hot-water.md)) | ✅ |
| Control of consumers: on/off, power set point or release for an automation of your own (with latest start), priority, minimum runtime/pause, avoid short runs, external blocking, control switchable per consumer | ✅ |
| Consumers with their own thermostat: pauses recognised, their power goes to the batteries meanwhile | ✅ |
| Temperature sensors of a storage: learned energy per degree, remaining capacity, learned cooling per hour | ✅ |
| Role of a consumer in the feed-in cap (supporting, normal, never) | ✅ |
| Daily targets: runtime, enabled time, energy, temperature or the car's state of charge, with earliest start, deadline and source (surplus, batteries, grid) | ✅ |
| Battery support per consumer (always, automatic with the energy the batteries can spare, never) | ✅ |
| Wallbox / evcc loadpoint: current set point in A with phases, start/stop ([guide](docs/wallbox-evcc.md)) | ✅ (not yet tested with a real wallbox) |
| Car at the wallbox: state of charge in the energy flow, without a plugged in car no control and no planning, **Charge now** at full power | ✅ (not yet tested with a real wallbox) |

| Planning and control | Status |
|---|---|
| Distribution of surplus between batteries and consumers (priorities, split, minimum runtime/pause) | ✅ |
| Real-time control of batteries and consumers (operating mode **active**) | ✅ |
| Averaged grid surplus (0–300 s) | ✅ |
| Grid surplus targets for charging/discharging, maximum export while discharging | ✅ |
| Grid friendly charging: absorb PV feed-in peaks | ✅ |
| Feed-in cap: store the PV energy above a feed-in limit, make room in time | ✅ |
| Night discharge to a forecast based reserve | ✅ |
| Import peak shaving: an import limit and a state of charge threshold below which the batteries keep their energy for the peaks (the cheapest pair with a power price, one of them calculated from the consumption statistics, or both set; manually enabled); with a power price in the tariff the limit holds on average over the quarter hour, forced consumers give way, the batteries are topped up from the grid below the limit before the peaks, the overview shows the month's peak and its extra cost, and the tariff comparison estimates what peak shaving would have saved | ✅ |

| Dashboard | Status |
|---|---|
| Dashboard (sidebar panel): energy flow, key figures, daily forecast and plan chart, batteries, consumers, settings | ✅ |
| Simulation in the dashboard: day plan with other settings compared with the current one (today or tomorrow), with the price chart | ✅ |

| Tariffs and prices | Status |
|---|---|
| Tariffs as on the bill (time windows, VAT per group, percent levies) with a check against a bill | ✅ |
| Tariff templates: Austria (grid fees of all grid areas, levies, suppliers incl. options such as a green upgrade), Germany (levies, EEG feed-in credit, grid fees of 19 grid operators incl. § 14a modules and metering), Switzerland (query of the ElCom open data); a general template for dynamic tariffs (market price plus the markup of the contract) | ✅ |
| Template updates: new prices, successor tariffs and corrections as a repair issue, reset to the template values | ✅ |
| Tariffs as YAML (import, export, own templates) with a prompt to create one from a bill with an AI | ✅ |
| Day-ahead prices per quarter hour (APG, SMARD, Energy-Charts) and official monthly market values (E-Control) only with consent; no feed-in credit at negative prices where the tariff says so (EEG) | ✅ |
| Monthly tariff comparison: import costs and feed-in credit per tariff, tariffs shown or hidden, estimated saving of the price aware control | ✅ |
| Price aware control: stored energy kept for the expensive hours, grid part of daily targets in the cheapest window, optional charging from the grid and feeding in from the batteries | ✅ |
| Estimated day-ahead prices until the next day's prices are published (profile of the last days, weekends and public holidays) | ✅ |

## Installation

### HACS (custom repository)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=gojux&repository=SLEMS&category=integration)

The button opens SLEMS in HACS of your Home Assistant instance and adds the
custom repository; then continue with step 2. Or by hand:

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/gojux/SLEMS`,
   type **Integration**.
2. Install **SLEMS** and restart Home Assistant.

### Manual

Copy `custom_components/slems` into the `custom_components` folder of your
Home Assistant configuration and restart Home Assistant.

Requires Home Assistant 2026.9 or newer.

## Setup

1. **Settings → Devices & services → Add integration → SLEMS**.
2. Select the devices of your smart meter and your PV systems (optional,
   preselected from the energy dashboard of Home Assistant; the devices that
   fit best come first, e.g. a Shelly Pro 3EM or the meter of an inverter).
   SLEMS suggests their sensors and fills in what the energy dashboard
   already knows, including the sign of its grid power, the PV forecast
   entries and the weather entity. Check the grid power entity (positive =
   import, negative = export; the form shows its current value; enable
   **invert** if your meter uses the opposite sign), and optionally PV power,
   PV forecast and weather. Several PV systems (e.g. roof and balcony) are
   summed up.
3. On the SLEMS integration page choose **Add battery** for every battery and
   **Add consumer** for every consumer you want to measure or control.

**Phases** (optional): if your smart meter measures each phase, you can
select its **Grid power L1**, **L2** and **L3** in the SLEMS settings. SLEMS
then learns which phase each device is on and shows it; this does not
affect the control yet (see
[Phase of batteries and consumers](#phase-of-batteries-and-consumers)).

### Smart meter over Modbus (optional)

The entity of the smart meter is updated once per polling cycle of its
integration, often only about once per second, and then shows only a part of
the meter's values. With **Also read the smart meter over Modbus** SLEMS reads
the grid power directly from a SunSpec meter every 0.5 seconds (0.2 to
5 s). The control sees every value
sooner; the gain is largest with fast batteries.

- SLEMS uses the shared Modbus connection of Home Assistant. As long as the
  inverter integration keeps the inverter's only Modbus TCP connection to
  itself, a Modbus proxy has to be set up, e.g. the app [Modbus
  Proxy](https://github.com/TCzerny/ha-modbusproxy).
- SLEMS finds the meters in the SunSpec model chain; with several meters
  (e.g. export+import and consumption) you choose the one at the grid
  connection point, the current power of each helps to recognise it.
- The sign is found automatically by comparing a few values with the grid
  power entity; this needs some import or export (at least 100 W). Otherwise
  choose it by hand.
- The entity stays required: it is the history, the basis of the forecasts
  and the fallback. While no Modbus value is newer than 3 intervals (at least
  5 seconds), SLEMS uses the entity; after 5 minutes without Modbus a repair
  issue appears. The sensor **Smart meter update interval** shows the source as
  attribute `source`.
- When the source is switched, the learned battery response times start
  again, since they contain the delay of the old source.

### Tariffs (optional)

With a tariff SLEMS works in money: it checks your electricity bill, shows
every month what another tariff would have cost and can control the batteries
by the prices.

- **Enter a tariff** as on the bill, line by line, from **templates** with
  public list prices (Austria, Germany, Switzerland through ElCom) or as YAML,
  also created by an AI from a bill.
- **Dynamic tariffs** with day-ahead prices per quarter hour (fetched only when
  you switch it on) and **power prices** for the monthly peak.
- **Price aware control** (optional): keeps the batteries' energy for expensive
  hours, on request charges cheaply from the grid or feeds in when it pays
  well, and measures what that saved.

All settings and details: [Tariffs and prices](docs/guide/tariffs.md).

### PV forecast

Every integration that provides a solar forecast for the Home Assistant energy
dashboard can be selected, e.g. Forecast.Solar or Solcast. Several entries
(e.g. one Forecast.Solar entry per roof plane) are summed up. SLEMS uses the
finest resolution the provider delivers (e.g. 15 or 30 minutes).
Forecast.Solar marks each period by its end; SLEMS takes this into account.

**Recommendation: [Helios Forecast](https://github.com/ReikanYsora/Helios-Forecast)**
(open source, no account or API key, computed locally with weather data from
Open-Meteo). It learns from the production of your own PV system, including
shading by trees or mountains, soiling and an orientation that is a few
degrees off, and uses the existing long-term statistics of your energy sensor
right away. It provides a forecast for the energy dashboard in 15 minute
steps, so it can be selected in SLEMS directly.

### Weather (optional)

The weather entity improves the consumption forecast, especially for a heat
pump. The forecast of the national weather service fits best:

| Country | Recommendation | Why |
|---|---|---|
| Germany | [DWD Weather](https://github.com/FL550/dwd_weather) (HACS) | forecast of the German weather service for a station near you, hourly |
| Austria | [GeoSphere Austria Plus](https://github.com/coding-pagro/GeoSphere-Austria-Plus) or [GeoSphere Austria Next](https://github.com/slettmayer/ha-geosphere-next) (HACS) | high resolution AROME model of GeoSphere Austria, well suited for alpine valleys; the built-in *GeoSphere Austria* integration only provides measurements, no forecast |
| Switzerland | [Swiss Weather](https://github.com/izacus/hass-swissweather) (HACS) | local forecast of MeteoSwiss for your postcode, hourly |
| Liechtenstein | [Swiss Weather](https://github.com/izacus/hass-swissweather) (HACS) | MeteoSwiss covers Liechtenstein as well |
| Anywhere | **Open-Meteo** (built-in integration) | no account needed, combines several weather models; a good choice without an extra integration |

**Met.no**, the Home Assistant default, works everywhere but is less detailed
in alpine terrain.

### Consumption forecast

SLEMS learns your house's consumption from the long-term statistics of Home
Assistant and forecasts today and tomorrow by the hour: heat pumps by the
outdoor temperature, public holidays like weekends. When the consumption rises
suddenly (e.g. at the start of the heating season), SLEMS raises the forecast
at once. The card **Forecast accuracy** shows how well the consumption and PV
forecasts hit. When switching from another battery integration, choose its
house consumption sensor as **House consumption history**, then SLEMS learns
from the whole history at once.

Details: [Consumption forecast](docs/guide/consumption-forecast.md).

## Without batteries

SLEMS also works without any battery: add the consumers only.

- **Works**: directing the PV surplus into controlled consumers (heating rod,
  wallbox also with evcc, dehumidifier, heat pump through a switch input) with
  priorities, minimum runtime and pause, **Avoid short runs** and external
  blocking; daily targets with the sources **surplus only** and **+ grid** (also
  in the cheapest window with price aware control); tariffs, bill check,
  market prices, tariff comparison and price chart; energy flow, consumption
  and PV forecast with their accuracy.
- **Not without batteries**: everything that charges, discharges or plans a
  battery – grid friendly charging, night discharge, import peak shaving,
  price hold, charging from the grid, feeding in from the batteries – and
  currently the feed-in cap (it needs a battery even with supporting
  consumers).

## Batteries

SLEMS controls one or more home batteries: it distributes the power between
them, plans their use over the day (grid friendly charging, night discharge,
prices, import peaks) and protects them along the way. A battery can be added
in three ways:

- **Marstek Venus E 3.0** directly over Modbus TCP: the fastest way, with the
  most data (cell voltages, counters, firmware). SLEMS searches the network for
  it. The Venus allows only one Modbus connection.
- **Existing Home Assistant entities** (control experimental): any battery
  that another integration brings in, e.g. through Omnibattery, also a balcony
  storage or hybrid inverter with its own PV modules.
- **Battery from a Modbus profile** (experimental): a battery with Modbus TCP,
  described by a profile an AI creates from its manual.

What SLEMS brings for every battery:

- A **self-test** when it is added, checking signs, values and response.
- **Learned values**: response time, phase and efficiency. With several
  batteries only as many run as is efficient, with a smooth switch.
- **Limits and protection**: state of charge, charge and discharge power,
  minimum power; gentle charging near full, cell delta and cell balancing, a
  pause for firmware updates.
- Optional **wear costs**, so price aware charging from the grid only happens
  when it earns more.

All settings and details: [Batteries](docs/guide/batteries.md) and
[Battery from a Modbus profile](docs/guide/battery-modbus-profile.md).

## Consumers

SLEMS steers the PV surplus into controllable consumers and plans them
together with the batteries. Add the consumers SLEMS controls (heating rod,
wallbox, dehumidifier, …) and heat pumps, whose consumption it forecasts from
the weather. Each needs its own power and energy sensor; SLEMS suggests them
from the device.

- **Control**: on/off, a power set point in W, a current set point in A
  (wallbox, also with evcc) or a **release** for an automation of your own,
  e.g. for the hot water charge of a heat pump.
- **Priorities**, minimum runtime and pause, **avoid short runs** for devices
  with a compressor and an external block; SLEMS recognises a device's own
  thermostat by itself.
- **Battery support**: per consumer, whether the batteries may supply it.
- **Daily targets**: runtime, energy, temperature or the car's state of charge
  by a time of day, from the surplus where possible and only if needed from
  the batteries or the grid.
- **Feed-in cap**: consumers help take the surplus above the limit.

All settings and details: [Consumers](docs/guide/consumers.md), plus the
guides to the [hot water of a heat pump](docs/heat-pump-hot-water.md) and the
[wallbox with evcc](docs/wallbox-evcc.md).

## Dashboard

SLEMS adds the entry **SLEMS** to the Home Assistant sidebar.

### Overview

- **Energy flow** between grid, PV, house, every battery (with its state of
  charge) and the consumers; the animated dots run in the direction of the
  flow, faster and on a thicker line the higher the power. The SLEMS logo in
  the middle opens a menu: the [bad weather mode](#bad-weather-mode) and the
  **control details** (status, control gain, control interval and averaging
  window, source and update interval of the smart meter, with Modbus its
  round trip, errors and the time on the entity today, and the battery
  response times).
  With a battery that has its own PV (hybrid inverter, balcony solar with a
  battery) you can choose **Summed** or **By device** at the top right; the
  browser remembers the choice. **Summed** shows all PV in one box and the
  battery with the power its inverter takes including its PV. **By device**
  shows every PV system on its own and such a battery the way the energy
  flows: its inverter with the power at its connection (**Output** into the
  house or **Input** from the house), below it its PV and the battery with
  its power on the DC side. What is missing between them are the losses of
  the inverter.
- **Key figures**: status (problems first – smart meter without values,
  battery unreadable or not responding – otherwise the operating mode),
  strategy (a change shows once it lasted 30 seconds; the control switches
  at once), state of charge, feed-in limit, stored energy and capacity,
  forecasts, and the **expected export today**: the export measured since
  midnight plus the export the plan still expects (the blue line of the chart).
- **Forecast chart** (mean power per half hour in kW):
  - **Today** shows PV and consumption forecast (dashed), the measured values so
    far (solid), the expected and the measured feed-in into the grid (blue;
    the expected one from the plan, including night discharge, at most the
    feed-in cap), the expected and the measured grid import (blue, below the
    zero line; only where there is any), the planned battery charging (light
    bars) and the measured
    charging (solid bars), the planned load of the consumers' daily targets
    (pink, dashed; part of the consumption forecast), plus the projected total
    state of charge (dashed) and the measured one (solid) with their scale in %
    on the right.
  - **Limit lines**, each in the colour of the series it limits, greyed and
    with its own dash: the **PV limit of the feed-in cap** (orange grey), the
    **Limit of grid friendly charging** (green grey, today from now on while
    PV is forecast: the batteries charge above it) and the **Peak shaving
    limit** (blue grey, below the zero line). While more than one of them is
    active they are all hidden until switched on in the legend; a single one
    is shown. The browser keeps the choice made in the legend. The simulation
    draws the limits with its own settings.
  - Charging from the grid is blue: in the planned charging the part from the
    grid on top of the part from PV. The measured charging is green when it
    came almost only from PV, blue when it came almost only from the grid
    (charged while importing), and striped green and blue when both charged
    at once. The grid part is estimated from 5 minute means and overstated
    while the power fluctuates; below 20 % or below 50 Wh per half hour the
    charging therefore counts as PV.
  - **Tomorrow** shows the forecasts, planned charging and state of charge of
    the next day, continued from today's projection.
  - The projection follows the planning: charging only with the planned
    surplus, deficits covered by the batteries, import peak shaving and night
    discharge if enabled.
  - Hovering (on a phone: tapping, a tap elsewhere closes it) shows the values
    of a half hour; selecting an entry of the legend hides or shows that
    series (the scale follows the series shown; the browser keeps the choice;
    **PV above the limit** of the feed-in cap is hidden until switched on);
    **Show table** switches to a table.
- **Price chart** (only with a [tariff](#tariffs-optional) whose prices change
  over the day, with market prices or with price aware control), below the
  forecast chart on the same time axis: per quarter hour the price of an
  imported kWh with the current tariff and the feed-in credit (ct/kWh incl.
  VAT, with time windows and dynamic prices, without fixed fees) and the
  market price if market prices are fetched, with its source.

### Batteries tab

![Batteries tab: one card per battery with state of charge, power, set point and cell delta](docs/images/dashboard_batteries_en.png)

State of charge, stored energy and capacity (kWh), power on the grid side (AC)
with its direction (an idle battery is **Full** or **Empty** when SLEMS
charges or discharges it no further because of its state of charge or its
BMS, otherwise **Idle**), the SLEMS set point, efficiency, state and the **Enabled**
switch of every battery (disabling asks for confirmation), the cell delta with
its balance status, a recommendation for active cell balancing and the phase
of a running one.

### Consumers tab

![Consumers tab: heat pump measured only, heating rod with its settings and daily target](docs/images/dashboard_consumers_en.png)

One card per consumer with the important values: current power, temperature,
**Time window** (the planned window, otherwise the set time), **Energy needed**
(what is still missing, with a reached target that of the coming period), state
of the daily target and the release; for a wallbox the car. A heat pump shows
its **Status** and today's forecast at the top (with controlled hot water
**Heating today**: without the hot water energy, only with a single heat pump)
and the values of the hot water in the section **Hot water control**. **Show
settings** expands the card (the browser keeps the choice).

The menu (⋮) next to the name, which a consumer's box in the energy flow opens
as well, switches the control, starts or stops **Charge now**, learns the phase
again and opens the **History** and the device page. Its **Details** show the
planned power, the phase, the learned response times for switching on and off
(own sensor and at the meter; switching on includes a start delay of the device,
e.g. a compressor), the whole state and plan of the daily target and the battery
support.

### Simulation

![Simulation: day plan without the feed-in cap compared with the current settings](docs/images/dashboard_simulation_en.png)

Try other settings without changing anything.

- **What can be changed**: next to the day chart (today from now on or
  tomorrow) grid friendly charging, night discharge, import peak shaving and
  the feed-in cap can be switched on and off; their values appear below each
  switch as in the settings. The batteries (capacity, minimum/maximum state of
  charge, charge and discharge power; the default is the power of the
  batteries without the momentary limits of their state of charge), the night
  discharge buffer, the maximum
  grid export while discharging and the PV and consumption forecasts (±%) can
  be changed too. With comparison [tariffs](#tariffs-optional) the group
  **Tariff** at the top chooses another **Tariff for import** and **Tariff
  for feed-in** (e.g. a dynamic tariff, one with a reduced grid price at noon
  or one with a power price). Everything that uses prices plans with it: the
  price aware control, the runs of the daily targets in the cheapest window
  and import peak shaving with a power price; the price chart then shows
  their prices.
- **Result**: SLEMS calculates the day plans like the real ones, from the
  current state of charge and the current forecasts; the plan with the current
  settings is shown grey dotted for comparison, key figures (feed-in, grid
  import, highest import and feed-in, curtailed energy, state of charge at
  midnight; with a tariff also the import costs, the feed-in credit and both
  together, from the per kWh prices incl. VAT without yearly fees; with a
  power price the highest planned hour and an estimate of the billed power
  and the power price of the month, as the plan works in hours) side by side
  below.
- **Nothing is saved** or used for the control; every visit starts with the
  current settings.
- **Today** is simulated from now on (the values before are measured); in the
  afternoon a note suggests **Tomorrow** for a whole simulated day.
- The daily targets of the consumers are planned anew with the simulated
  settings and tariff (e.g. a run in the cheaper window of another tariff);
  otherwise consumers controlled by SLEMS and the battery priority (which acts
  in the real-time distribution) are not simulated.

**Tariff comparison**: with [tariffs](#tariffs-optional) the simulation tab
shows per month of the last year (and the current month to date) the recorded
grid import and feed-in and, per tariff including VAT, the costs of the
import and the credit for the feed-in (less its fees; a switch shows both
summed), below each comparison tariff its difference to the current one
(green better, red worse). The comparison tariffs can be shown or
hidden by selecting their names (remembered in the browser). It is a passive comparison: the recorded energy priced
differently, not what SLEMS would have done with another tariff (e.g. charging
the batteries in cheap hours). Dynamic tariffs use the stored market prices;
energy without a market price is marked. With batteries a line below the
costs of a tariff adds an estimate of what the [price aware control](#tariffs-optional) would
have saved: the recorded hourly house consumption and PV are played through a
simple battery model, once as usual and once planned like the price aware
control (with charging from the grid if it is switched on). The plan knows
consumption and PV exactly, so the estimate is an upper bound. Tariffs where
the control saves nothing (a fixed price) have no such line.

### Settings tab

All settings grouped and editable directly; while a value is learned (switch
**… automatic**), the learned value is shown instead. The **Control** card also
shows the smart meter update interval and the battery response time. Settings
that have no effect right now (no tariff, no controllable battery, fewer than
two for the change of batteries) follow at the end under **Currently not
usable**: greyed out, locked and with what they need.

### Using the dashboard

Selecting a value (tile, box in the energy flow except a consumer's, which opens
its menu, row of a card) opens the
Home Assistant dialog of its entity with history and settings.

The dashboard follows the language and the light/dark theme of Home Assistant
and works on phones (with little space the batteries in the energy flow are
shown one below the other).

## Operation and control

### Operating mode

The entity **SLEMS Operating mode** switches between:

- **Off** – nothing is planned or sent.
- **Observe only** – forecasts and plans are computed and shown, but no command
  is sent to batteries or consumers. This is the default.
- **Active** – plans are executed: SLEMS sends set points to the batteries and
  switches/sets the consumers. It reacts to every change of the smart meter.
  The entity **Control status** shows whether the control is active or paused
  because the smart meter did not report for a while (60 s or ten update
  intervals; the batteries then follow their own logic until the meter is
  back).

### Bad weather mode

When bad weather is coming, the switch **Bad weather mode** (also in the menu
of the SLEMS logo in the energy flow) stores as much of today's surplus as
possible and keeps it over the next night:

- The batteries charge every surplus at once instead of charging grid
  friendly, and the night discharge is off.
- The target grid surplus, the feed-in cap and the battery limits stay.
- It ends by itself in the morning after the next evening: switched on during
  the day the next morning, switched on at night (after the evening or before
  the morning) the morning after next.
- The evening is the end of the last hour in which the PV forecast is above
  the consumption forecast (without such an hour the sunset). The morning is
  the start of the first hour after it in which the PV forecast is above the
  consumption forecast again, as the night discharge plans until then.
- Without such an hour in the forecast (several dull days) it goes on until
  the sunrise after the end of the forecast. The end follows every new
  forecast; the attribute `until` shows it.
- While it is on, the logo carries a rain cloud; the forecast chart and the
  expected export follow it.

### How the control works

In operating mode **active** SLEMS reacts to every new value of the smart meter:

1. From the energy balance it calculates which battery power would bring the
   grid power exactly to the target (e.g. 100 W export while charging).
2. It only counts battery commands that the smart meter can already show. A
   new command needs some time until it appears in the meter value; SLEMS
   learns this **response time** and does not count a command twice. The same
   applies to the controlled consumers: until a command reaches the meter
   (**response time at the meter**, learned per consumer) the power before it
   counts, afterwards the measured power, or the commanded one while the
   consumer's own sensor has not caught up yet. So meter value, batteries and
   consumers always describe the same moment.
3. It does not jump to the calculated value at once but moves a share of the
   way per cycle, the **control gain**. With 0.5, half of the remaining
   deviation is corrected per cycle. A high gain reacts faster, a too high
   gain overshoots and makes the grid power swing back and forth.

**Automatic control gain** (switch, on by default): the set **Control gain**
is the upper limit; SLEMS only lowers it when the control swings:

- SLEMS watches the smart meter: if the grid power swings from import to
  export and back around the target three times in a row, each time shortly
  after the control changed its direction, and the swings do not get
  smaller, the control swings: the gain is lowered at once (× 0.8, down to
  0.2).
- After every 5 minutes without swinging it rises again in small steps
  (+ 0.05) up to the set control gain.
- After a reduction SLEMS waits 30 seconds to see its effect. Normal load
  changes (a kettle, a cloud, a cycling stove, the short pulses of a coffee
  machine) are not mistaken for swinging: there the meter jumps by more than
  the batteries can change it, or the meter moves first and the control
  follows. The learned value is kept across restarts.

A higher gain does not make the batteries faster, as their response time is
already taken into account. It only makes the control react harder to errors,
e.g. to a wrongly learned response time. With a fast smart meter (e.g. over
Modbus every 0.5 s) 0.5 is a good value; if it reports only every few seconds,
it may be higher (e.g. 0.7).

The sensor **Current control gain** shows the gain used right now. Switched
off, **Control gain** is used as a fixed value; switched on again, the
adjustment goes on from the gain it learned last, at most the set one. **Control interval**
(default 1 s) limits how often SLEMS sends commands.

Diagnostic sensors show what SLEMS has learned:

| Sensor | Meaning |
|---|---|
| **Current control gain** | gain used right now |
| **Smart meter update interval** | how often the smart meter reports; attribute `source`: Modbus or entity |
| **Battery response time** | time from a battery command until the smart meter shows it, for all batteries together; per battery (from steps it makes mostly alone) in **Details** of the battery, used by the control once learned |
| **Planned power** of a consumer, attribute `response_time_s` | time from a command until the consumer's own power sensor reacts |

Switch the automatic adjustment off only if the gain keeps changing
noticeably, e.g. because the smart meter reports very irregularly; then set a
fixed value (0.3–0.5 is a good start).

### Which option when?

All options are optional and can be combined. The charts show the same example
with and without each option: 8 kWp PV on a sunny day, a household with
morning and evening peaks, a 10 kWh battery, from 18:00 until midnight of the
next day. They are calculated with the SLEMS state of charge projection
(hourly means), so they show how SLEMS plans; the real curves depend on your
forecasts. Top: PV production and consumption (the same with and without the
option); middle: total state of charge; bottom: grid power (+ import / −
export); grey dashed without, coloured with the option.

**Grid friendly charging** (on by default)

![Grid friendly charging](docs/images/grid_friendly_en.svg)

Without it the batteries are full before noon and the whole midday peak goes
to the grid (here 5.7 kW). With it they charge with the surplus above a
feed-in limit and are still full in the afternoon; the peak drops to about 4.4
kW. Useful whenever many PV systems feed in at the same time (grid, energy
community). Switch it off if the batteries should be full as early as
possible, e.g. for backup power.

**Night discharge**

![Night discharge](docs/images/night_discharge_en.svg)

Energy still in the batteries in the morning is fed in over night, down to a
reserve that the PV forecast can refill the next day (here **Morning reserve**
10 %: about 2.9 kWh are fed in over night, and 3.2 kWh less at noon,
because the batteries have more room for the PV). Useful if the batteries are
still well charged in the morning (large battery, low night consumption,
summer):

- In an **energy community** your energy is more likely to find a buyer at
  night: during the day most members produce themselves, at night they
  consume. Night discharge shifts part of your surplus from noon to the night.
- It works well together with the **feed-in cap**: the batteries start the day
  with room for the energy above the limit, so SLEMS rarely has to feed in
  shortly before the peak.

The energy fed in at night passes the battery twice (charge and discharge
losses, about 10 %), and less energy is left for a power outage until the next
charge. Switch it off if the batteries are meant as a backup supply.

**Import peak shaving** (useful on days with little PV, e.g. in winter)

![Import peak shaving](docs/images/peak_shaving_en.svg)

Without it the battery covers everything until it is empty in the evening; the
morning peak then comes from the grid in full (here 2.1 kW). With it, below
the state of charge threshold the battery only covers the power above the
import limit: the base load comes from the grid, the battery keeps its energy
for the peaks (here 1.5 kW at most). Some peaks become higher than without
(here at 19:00, 1.5 instead of 0.4 kW, because the battery no longer covers
everything), but none goes above the import limit, so the highest peak drops.
Useful with a power based tariff or grid fee, or if the grid connection is
weak. Somewhat more energy comes from the grid (here 1 kWh), but it is still
in the battery at the end: the peaks are lower, the energy balance stays about
the same.

**Feed-in cap** (if the feed-in is limited, e.g. to 60 % of the peak power)

![Feed-in cap](docs/images/feed_in_cap_en.svg)

Without it the batteries are full at noon and the inverter curtails everything
above the limit (red, here 7.2 kWh at a limit of 45 %). With it SLEMS keeps
enough room free; here the night discharge makes the room and the batteries
absorb the energy above the limit. Needed whenever your grid operator limits
the feed-in; it takes precedence over the other options.

### Grid friendly charging

Charging as soon as there is surplus fills the batteries in the morning, and
the PV feed-in peak around noon then goes to the grid in full. With **Grid
friendly charging** (switch, on by default) SLEMS shifts charging into the
peak:

- From the PV and consumption forecasts it calculates a **Feed-in limit**: the
  highest grid export at which the surplus above it still fills the batteries
  by the end of the day (charge losses, maximum charge power and **Grid
  friendly charging buffer** included; default 1 kWh, a larger buffer lowers
  the limit and makes the batteries full earlier and more reliably when the
  forecast is too optimistic). Without a limit the overview tile shows the
  reason: **none – charge at once** (the surplus is not enough), **off (bad
  weather mode)** or **no forecast**; with grid friendly charging switched off
  the tile is hidden. The sensor then has no value and the reason in its
  attribute `reason`.
- The batteries only charge with the surplus above this limit; below it the
  power goes to the consumers or to the grid. The highest hours of the day are
  cut, wherever the clouds put them.
- The limit is recalculated continuously from the current state of charge and
  the remaining forecast. If charging falls behind (e.g. more clouds than
  forecast), the limit drops by itself.
- The PV forecast is corrected with today's actual production (diagnostic
  sensor **PV forecast correction**: the ratio of the production to the forecast
  until now, 50–120 %). A morning says little about the whole day (fog, a hill
  shading the first hours), so the ratio is applied with a weight: the current
  hour at 80 %, fading out over two hours, and the rest of the day only once
  15 % of the day's forecast energy has passed, fully from 50 % on (attributes
  of the sensor). After a restart SLEMS takes the production so far from the
  statistics of the PV sensor, so nothing is lost.
- As long as the charge is not secured (state of charge below **Battery
  priority below state of charge** or the forecast is not sufficient), SLEMS
  charges at once as before.

How much of the peak can be absorbed depends on the battery size compared to
the day's surplus: on a clear summer day with a large surplus a 10 kWh battery
takes about the top kilowatt of the peak, on days with less surplus a much
larger share.

### Feed-in cap

Some grid operators or regulations only allow a PV system to feed in a share
of its peak power (e.g. 60 %); the inverter curtails everything above it. With
**Feed-in cap** (switch, off by default) SLEMS stores that energy in the
batteries instead of losing it. The limit is **PV peak power** (kWp) × **Feed-in
cap limit** (%), measured at the grid connection point (grid export, after the
house consumption).

- **Planning**: from the PV forecast in its finest resolution (15/30/60 min,
  so short peaks are not averaged away) and the consumption forecast, SLEMS
  calculates until the end of tomorrow how much energy lies above the limit
  and how much free space the batteries need for it. Several peaks per day
  (clouds in between) and peaks today and tomorrow are covered: a cloud dip or
  the night in between, in which the batteries supply the house, makes room
  again; a surplus below the limit does not.
- **Buffer**: **Feed-in cap buffer** (default +20 %, negative values plan with
  less) is added to the energy to absorb. With **Automatic feed-in cap buffer**
  SLEMS uses the recorded PV forecast errors instead (see **Forecast
  accuracy**): of the days with more PV than forecast, the underestimation not
  exceeded on 80 % of them raises the PV forecast. This needs 14 recorded
  days; until then the fixed buffer applies. In addition a fixed minimum
  buffer of 5 % of the PV peak power as energy of one hour (0.5 kWh at 10 kWp)
  stays free per peak in any case: it covers errors in the timing and height
  of a peak that the daily forecast errors do not show, and small peaks for
  which the percentage buffer is tiny. The settings show the limit, the buffer
  in use and the minimum buffer.
- **Making room**: charging with surplus below the limit only happens as long
  as the space needed later stays free; above the limit the batteries always
  charge. The night discharge (if enabled) stops early enough to leave the
  space. If the house consumption and the night discharge are not enough,
  SLEMS feeds battery energy into the grid before the peak, as late as
  possible: it is planned to be finished one hour before the peak with 70 % of
  the possible power, never above the limit. For this it may exceed **Maximum
  grid export while discharging**.
- **Order during the peak**: the surplus above the limit goes to the batteries
  (regardless of battery priority, battery share and grid friendly charging),
  then to the consumers with the role **Supporting**, then to those with
  **Normal**, and only the rest is curtailed. If the forecast shows that a peak
  does not fit into the batteries (too small, or too late to make room, with
  the buffers), SLEMS plans the supporting consumers from the start of the
  peak for the part that does not fit: a consumer with little power would
  otherwise only start when the batteries are full and could then take far
  less than the surplus. With temperature sensors it plans them only as far as
  their storage can still take energy. The feed-in cap takes precedence over
  grid friendly charging (its feed-in limit never lies above the cap), night
  discharge and battery priority; the peak shaving threshold stays a floor for
  feeding in.
- **Overview**: the tile **Feed-in cap** shows the next peak, the energy the
  batteries have to absorb and, if needed, the energy to feed in before it and
  by when. The day chart shows the **PV limit of the feed-in cap** as an orange
  grey dashed line: expected consumption + limit, the PV power above which the
  export would exceed the limit (the limit applies after the house
  consumption, so the line follows the consumption forecast). The energy above
  it is shown as a bar on that line; in red the part that is still lost
  according to the plan, because the batteries are full or charge too slowly
  (the reason is shown in the tooltip; consumers set to take surplus above the
  limit are taken into account). The key figure **Curtailed** in the simulation
  is the same energy.
- **Warnings** (overview and notifications): batteries too small for the space
  needed, not enough time or power left to feed in before the peak, charge
  power too low for the surplus above the limit, and feed-in above the limit
  for more than 5 minutes. **Batteries too small** means the forecast itself
  does not fit; if only the safety buffer does not fit completely, the
  overview shows the note **forecast within the buffer zone** instead (no
  notification). Energy that does not fit into the batteries (too small, or
  too late to make room) does not count as a problem as far as the supporting
  and normal consumers can take it during the peak; the overview then shows
  the note **consumers take the rest** with the energy and the consumers.
  Batteries left out of the planning (cell balancing, communication paused,
  disabled, not responding) are named in the texts.

Sensors: **Feed-in cap energy to absorb** (on the day of the next peak, with the
peaks, the space needed, the export plan and the buffer as attributes) and
**Feed-in cap export before the peak**.

### Import peak shaving and power price

**Import peak shaving** (switch, off by default) keeps energy in the batteries
for consumption peaks: with a tariff with a power price (e.g. the Austrian
grid tariffs from 2027, which bill the highest quarter hour of the month) or
with a weak grid connection.

**Principle.** Two values decide:

- **Peak shaving grid import limit**: import up to this power comes from the
  grid, the batteries cover what is above.
- **Peak shaving state of charge threshold**: from this state of charge (of
  all batteries together) down, the batteries *only* cover the import above
  the limit. The energy between the minimum state of charge (e.g. 12 % on the
  Venus) and the threshold is reserved for peaks. Above it the batteries work
  as usual and cover the import above the limit also when they would not
  discharge otherwise (price plan, grid charging, consumers without battery
  support). Grid charging stays below the limit.

**Peak shaving mode** chooses what SLEMS calculates:

| Mode | You set | SLEMS calculates |
|---|---|---|
| **Economic** (default, only with a power price) | – | limit and threshold with the lowest expected cost |
| **Automatic limit** | threshold | the lowest limit the energy below the threshold lasts for |
| **Automatic threshold** | limit | the threshold whose energy covers the peaks above the limit |
| **Manual** | limit and threshold | – |

Without a power price in the current tariff **Economic** is greyed out in the
settings and works as **Automatic limit**. The settings show calculated
values read only with "(automatic)", the sensor **Peak shaving import limit
in effect** the limit used.

**Expected peaks.** The hourly forecast averages short peaks (oven, kettle)
away. SLEMS therefore takes the 5 minute statistics of the house consumption
of the last about 10 days: per hour of the day, how much energy was above a
power. Without a power price the mean of the days counts, with one the second
worst day (a single missed quarter hour costs the whole month). The energy
has to last until the refill: until the forecast PV surplus fills the
batteries from the minimum state of charge back to the threshold (a little
surplus on a rainy day does not count), at most 36 hours, counting only the
hours without surplus. **Peak shaving safety reserve** (default 20 %) keeps a
share on top, against more consumption than in the last days. Limit and
threshold are recalculated continuously; above the threshold as if it were
reached, during the day for the coming evening and night.

**With a power price** (a tariff line "per kW and month", see
[Tariffs](#tariffs-optional)) SLEMS works with what the bill counts:

- **Quarter hour budget**: the limit holds on average over the quarter hour
  (or hour) the tariff measures, on the wall clock as the smart meter counts.
  A short peak, e.g. while the batteries are at their power limit (a plug-in
  battery at 800 W), is made up for by importing less in the rest of the
  quarter hour; while budget is left a short peak may go above the limit.
  Feed-in does not count against import, so SLEMS never feeds in to make up.
- **What is paid already**: the limit never lies below what this month's
  peak already costs (the power billed so far, at least the minimum power,
  rounded up as in the tariff, minus a 200 W buffer against the difference
  between your meter and the grid operator's smart meter). A lower import
  saves nothing until the month ends.
- **Time window**: if the power price has one, quarter hours outside it do
  not count; the batteries keep their energy for the window.
- **Consumers give way**: when the batteries cannot cover the import above
  the limit, consumers running for a daily target are throttled, the lowest
  priority first: power controlled ones down to their minimum power, then
  those outside their minimum runtime switch off. The daily target catches up
  later.

**Charge from the grid for peak shaving** (switch, on by default, shown only
with a power price): import below the limit costs no power price. Below the
threshold the batteries are therefore topped up from the grid before the next
expected peak, up to the threshold: in the cheapest quarter hours (with a
dynamic tariff), with a fixed price as late as possible (home storage ages
mostly with time at a high state of charge), and only as far as the forecast
import stays below the limit; a PV surplus before counts. The energy for
peaks then only has to last until the end of the next block of peaks (two
hours without an expected peak end it) instead of until PV refills the
batteries. The strategy then shows **Peak shaving: charging from the grid**.

**Economic calculation** (mode **Economic**): SLEMS tries limits in 100 W
steps, from what the month already costs up to the highest expected peak,
each with its threshold, and adds up the expected costs until the end of the
month:

- the **power price** of the month for the limit (with levies and VAT),
- the **energy held back** that is still there at the refill (reserve,
  losses): it takes room from the PV surplus, which is then fed in instead of
  replacing import later, per remaining day at the import price minus the
  feed-in credit,
- with charging from the grid, the **conversion losses** and the **wear** of
  the peak energy charged back every day.

The limit with the lowest cost wins. Limits whose highest expected peak is
above the discharge power of the batteries, or whose threshold would reach the
maximum state of charge, are left out. Example: one kW less peak saves at
2.46 € per kW and month net (2.95 € with 20 % VAT) almost 3 € a month;
charging back 2 kWh a day for the evening peaks costs at 25 ct/kWh and 90 %
efficiency each way about 12 ct a day, a little over 3.50 € a month (without
wear). It pays when the peak drops by more than a little over 1 kW.

**Display**:

- The tile **Peak power this month** in the overview (only with a power
  price) shows the power billed so far, when the highest quarter hour was,
  up to which power a quarter hour costs nothing more, the extra cost of the
  month above the minimum power and the running quarter hour with the import
  still allowed for its rest.
- The **tariff comparison** estimates per month for tariffs with a power
  price what peak shaving would have saved and at which limit, from the
  recorded quarter hours (the lowest limit the batteries could have held with
  their discharge power and their usable energy per day, minus losses and
  wear). With a comparison tariff this shows before a power price applies
  what it would cost and what peak shaving brings.
- The settings note when the threshold reaches the maximum state of charge
  (the limit can probably not be held) or the month's peak raises the set
  limit.
- The diagnostics contain the month's peak, the budget of the running
  quarter hour, the limits the economic calculation tried and the charging
  plan; the debug log names every new economic limit and every planned
  charge.

### Learned values

Each of these values has a switch **… automatic** (or **Use learned …**). SLEMS
always learns, whatever the switch; it only decides what is used. Off, the
value you set applies; on, SLEMS uses the learned value as soon as there is
enough data, until then still yours. The switches are on by default (an
existing installation keeps its switches as they are). The settings show the
learned value (**learned**) or the note that there is not enough data yet; the
diagnostic sensor **Learned values in use** has all learned values as
attributes.

| Value | Learned from | Needs |
|---|---|---|
| Grid friendly charging buffer | recorded PV forecasts: the share by which the PV forecast was too high, not exceeded for 85 % of the forecast energy of all days (a day with at least the forecast PV counts with 0, every day weighted by its forecast: dull days with a small forecast so do not set the buffer of a clear day), applied to the PV still expected today; plus, once learned, the share of the consumption forecast being too low as for the safety buffer, applied to the consumption while PV is still produced today | 14 recorded days |
| Charge secured safety buffer (also the night discharge target) | the same for the PV plus the consumption forecast being too low (backtest: of the days with a too low forecast the error not exceeded on 85 % of them), applied to the rest of the day or the next 24 hours; while only one part is learned, the larger of it and the set buffer | 14 PV days, 7 consumption days |
| Grid surplus targets while charging and discharging | how far the grid power swings towards import while the batteries control it; charging: the target keeps the grid on the export side 90 % of the time (20–1000 W); discharging: the grid swings around the target, half of the time each side (−100 to 300 W, with **No negative grid surplus target** 0 to 300 W), as a short import costs little there but a permanent export gives battery energy away | some controlling in operating mode **active** |
| Control interval and surplus averaging window | the learned report interval of the smart meter (0.8 × and 3 ×) | a few meter reports |
| Usable capacity (per battery, **Use learned capacity**) | charge and discharge legs over at least 20 % state of charge: DC energy / change of the state of charge (legs with a jump of the state of charge are discarded), median of the last ten | three legs |
| Power of an on/off consumer by the outdoor temperature | the mean power and outdoor temperature (own sensor, else the weather entity) of each run of 10 minutes or more, the last 60 runs; a straight line, used only with a span of at least 8 K and a change of the power of at least 10 % over it (a ground source heat pump or a heating rod shows none, then it has no effect); attributes of the sensor **Learned power** | 6 runs |
| Morning reserve (**Morning reserve coverage** sets how cautious; shown only while the reserve is automatic) | the morning gap: energy the house needed from battery or grid between the planned takeover of PV (end of the night discharge) and the real one (PV covering the consumption for 15 minutes), in % of the day's forecast consumption; the reserve covers the chosen share of the mornings (90 % = 9 of 10), above 100 % the largest gap times the value (110 % = 10 % more than the worst morning) | 14 measured mornings (also measured while the night discharge is off) |
| Power and thermostat of a consumer (per consumer, **Use learned values**) | the power while switched on (on/off consumers: replaces the nominal power; power controlled consumers: the typical power at full load, measured while commanded at ≥ 90 % of the maximum power for at least 15 s or twice its response time, used for planning: forecast, daily target, feed-in cap; it is still commanded up to its maximum power) and pauses of its own thermostat while it is commanded, lasting 30 s to 10 min (two within 30 days: its own thermostat cycles, see **Own thermostat**; applies also without **Use learned values**; longer pauses, e.g. a dehumidifier at its target humidity, do not count) | 30 samples, two pauses |

#### Phase of batteries and consumers

If your smart meter measures each phase, select its **Grid power L1**, **L2**
and **L3** in the SLEMS settings (suggested from the smart meter's device, same
sign as the grid power). SLEMS then learns the phase of every consumer behind
the smart meter, and of every battery in its self-test and its probe step:

- **Consumers**: SLEMS compares the power of all batteries, consumers and the
  PV system with the smart meter's phases, averaged over two minutes each. A
  change of a consumer's power shows up on one phase with about the same size,
  or a third on each phase for a three phase device, even while the batteries
  compensate it at the same time. SLEMS starts with the last two days of the
  Home Assistant history and then goes on with the live values, so a consumer
  that switched a few times in this period gets its phase right away.
  Sensors that report up to a minute late are no problem.
- **Batteries**: the control moves the batteries together and against every
  other load, so their phase cannot be told from normal operation. The
  [self-test](docs/guide/batteries.md#self-test-of-a-battery) shows it: the other batteries and the
  consumers are held meanwhile. So does the
  [probe step](docs/guide/batteries.md#measuring-response-time-and-phase) if the smart meter reports
  its phases as fast as the total power.

The sensor **Phase** of the device shows the result (**L1**, **L2**, **L3** or
**Three phases**), its attribute *shares* the measured share per phase. For a
wallbox whose active phases come from an entity, only the one phase it uses
while charging on one phase is learned. The phase appears in **Details** of the
battery or consumer. It is shown and checked only; the control
does not use it yet.

- **Change**: if a consumer clearly shows up on another phase (e.g. a heater
  moved to another socket; older values fade over a few days), or a self-test
  measures another phase, **Settings → Repairs** asks which phase it is
  connected to; the measured one is preselected.
- **Learn phase again** (button of the device, **Learn again** next to the
  phase in **Details** or in the menu (⋮) of a consumer, after a confirmation): forgets the phase and learns it anew from the history; for a
  battery run a self-test afterwards. If the same phase comes out again,
  Repairs asks whether it is right or which phase to take instead. A phase you
  chose other than the measured one is kept; a change is still reported.

### Further settings (entities)

- **Vacation** (switch) – the household is away; switch manually or by an
  automation. Days on which the switch was on at some time are left out of the
  learned consumption entirely (consumption forecast including the heat pump,
  safety buffer, import peaks, reserve of the night discharge, usual daily
  energy of the consumers), so the forecast is not too low for weeks after the
  return. With fewer than 7 days left within the last 56 days, all days count.
- **Battery priority below state of charge** (default 30 %), **Charge secured
  safety buffer** (default 1 kWh) and **Battery share when charge is secured**
  (default 75 %) – the batteries get all surplus until their charge is
  secured: the state of charge is above the threshold and the expected PV
  surplus of the day covers the energy to fill them plus the safety buffer.
  Afterwards the surplus is split, the consumers' share is distributed by
  priority; a running on/off consumer keeps its power before power controlled
  consumers of higher priority (they take less instead, so the on/off one is
  not switched off and on again). Power one side cannot use goes to the other.
  The share only applies without **Grid friendly charging** (and is only shown
  then): with it the batteries take the surplus above its feed-in limit, the
  consumers the surplus below it.
- **Grid surplus target while charging** (0–5000 W, default 100 W) – the
  batteries only charge from the surplus above this value.
- **Grid surplus target while discharging** (−1000…+1000 W, default 50 W;
  positive = export, negative = import) – the grid power the discharging
  batteries aim at. Between the two targets the batteries stay idle.
- **Maximum grid export while discharging** (0 up to the sum of the maximum
  discharge power of all batteries, default 5000 W) – discharging never causes
  more export than this. 0 W means never feeding battery energy into the grid,
  the maximum switches the limit off. Below the **grid surplus target while
  discharging** it wins (the dashboard shows a note).
- **Morning reserve** (default 25 % of tomorrow's forecast consumption): energy
  that should remain above the minimum state of charge in the morning, for
  mornings when PV takes over later than forecast; used by the night
  discharge and the [battery support](docs/guide/consumers.md#battery-support).
- **Night discharge** (switch, off by default) – over night the batteries
  discharge evenly down to the reserve (usable energy above the minimum state
  of charge of the batteries) until PV production exceeds the consumption
  again, ignoring the discharge grid target (the maximum grid export still
  applies). If tomorrow's PV forecast cannot refill the batteries from the
  reserve, a higher reserve is kept; the refill counts per hour at most the
  charge power of the batteries, and the surplus that daily targets of
  consumers are expected to take that day must fit besides it. With price
  aware control it leaves the energy the price plan still needs for
  expensive hours (e.g. the next evening after a dull day). Requires the
  consumption forecast.
- **Surplus averaging window** (0–300 s, default 5 s, 0 = off) – what the
  house and the PV do without the batteries is averaged; the less favourable
  of average and current value is used, so the control does not overshoot
  with fluctuating PV. A rise of the consumption (or a PV drop) only counts
  once it has lasted twice the response time of the batteries (at most 2 s):
  short load pulses, e.g. of a coffee machine keeping its heater warm, are
  over before a battery could follow them.
- **Import peak shaving** (switch, off by default) with **Peak shaving
  mode**, **Peak shaving grid import limit**, **Peak shaving state of charge
  threshold**, **Peak shaving safety reserve** and **Charge from the grid for
  peak shaving**: see
  [Import peak shaving and power price](#import-peak-shaving-and-power-price).

### Problems and notifications

Ongoing problems appear under **Settings → Repairs** and disappear by themselves
when they are solved:

- a battery does not respond (excluded, see above),
- a battery could not be read for more than 5 minutes,
- a disabled battery in release state **Automatic** could not be read for more
  than 5 minutes (it counts with 0 W, so the house consumption is inaccurate),
- a battery could not be handed back to its own logic for more than 5
  minutes (it may still run with the last set point; SLEMS keeps trying,
  every release is confirmed by reading the battery back),
- the smart meter does not report while SLEMS is in operating mode **active**,
- the smart meter could not be read over Modbus for more than 5 minutes
  (SLEMS uses the entity meanwhile),
- the consumers behind the smart meter report clearly more power together
  than the whole house for more than 15 minutes (a consumer measures wrong or
  is not behind the meter; base load and consumption forecast are wrong
  meanwhile),
- the current tariff follows the market price while Home Assistant is set to
  another currency than euro (see market prices),
- a battery or consumer seems to be on another phase, or learning its phase
  anew gave the same one (with a dialog to answer, see **Phases**),
- the energy counters of the smart meter do not net the phases: in several
  quarter hours the import and the export counter both ran by about the same
  amount more than the grid power shows (e.g. a Shelly Pro 3EM, which counts
  per phase, while utility meters in Germany and Austria net the phases).
  Tariffs and bills would count energy that never crossed the meter. For a
  Shelly Pro 3EM, [Shelly Phase Netting](https://github.com/gojux/shelly_phase_netting)
  provides netted counters; otherwise split the grid power with template
  sensors and **Integral** helpers, or remove the counters (SLEMS then uses its
  own netted values). Ignore it if your utility meter counts per phase too.

The dashboard also shows them: a red note on the battery card (**cannot be
read**, **not responding**), in the energy flow and, for the smart meter, at the
top of the overview. The end of an active cell balancing run (in the normal
range, without further progress, after 24 hours or with an error) creates a notification with
the cell delta before and after and the duration; cancelling it yourself does
not.

For a problem report, **Settings → Devices & services → SLEMS → ⋮ → Download
diagnostics** (or the same on a battery device) saves the configuration and the
internal state as a file: measurements, plans, battery limits, the last
register values, the state of the cell delta measurement and of a balancing
run. IP and MAC addresses are removed. The warnings of the feed-in cap
(batteries too small, not enough time to make room, charge power too low,
feed-in above the limit, see **Feed-in cap**) are notifications too; they
disappear by themselves when the problem is gone.

## Language

SLEMS is available in English and German. Home Assistant uses two different
language settings:

- **Entity names** (e.g. **House consumption**, **Operating mode**, also the key
  figures in the dashboard) follow the **server language** under **Settings →
  System → General**. They are set when the integration loads, so reload SLEMS
  after changing it.
- Dialogs, menus, states (e.g. **Observe only**) and the texts of the
  dashboard follow the language in the **user profile**.

If names stay in the wrong language after an update of SLEMS, restart Home
Assistant (translations are only read at startup) and reload the browser
without cache. Entity IDs such as `sensor.slems_house_consumption` keep the
language of their creation; only the displayed names change.

## Roadmap

Possible extensions, grouped by topic. Wanted: feedback from users with a real
wallbox on the [evcc guide](docs/wallbox-evcc.md).

**Electric car**

- Targets that apply from plugging in the car ("80 % in 3 hours").
- Batteries with an availability (e.g. "car plugged in") and a reserve until
  a time of day: the basis for a car supplying the house (V2H).
- Learned presence of the car for the planning.

**Planning and prices**

- One planning for all battery goals: charging, discharging and feeding in
  per quarter hour by costs over the next 36–48 hours (prices, night
  discharge, feed-in cap, grid friendly charging), instead of separate rules
  each with its own horizon. Part of it:
  - the daily targets of the consumers planned together with the batteries,
  - a more stable plan of the price aware control (changed only when clearly
    better).
- Consumers preferred at negative prices (e.g. the heating rod).
- Estimated prices from the weather: the forecast of wind, solar and load of
  the bidding zone (Energy-Charts) moves the estimate; with them known
  exactly, the deviation of the last year would have dropped by about a
  third.

**Tariffs**

- Prices from a Home Assistant entity of the supplier (e.g. the Tibber
  integration, Octopus Germany, Ostrom): a line type with adapters for the
  common forecast formats, stating whether the price already includes the
  grid fees and levies.
- Index tariffs (e.g. on the Austrian electricity price index ÖSPI): a line
  type with the supplier's formula, the monthly index values fetched (with
  consent) from the Austrian Energy Agency; templates for index tariffs too.
- Feeding in from the batteries also in the estimate of the tariff
  comparison.
- Tariff comparison with other hardware: what a larger battery, more charge
  power, another maximum state of charge or more PV would have brought in the
  recorded year (the battery model of the estimate with the settings of the
  simulation).

**Forecast**

- Own forecast for vacations: a base load profile learned from whole vacation
  days (all vacations together) and a factor for the heat pump from the same
  days; until about three vacation days are learned the estimate of today.
- Program devices (washing machine, dishwasher, dryer): learn the course of a
  program and add the rest of a running program to the forecast of the next
  hours (helps the evening and night planning); later possibly starting them
  with surplus if the device allows a remote start.

**Consumers**

- SG Ready / smart grid of the heat pump: raise the set temperature with PV
  surplus or at cheap prices (building, buffer tank, hot water as heat
  storage), through the SG Ready states or an offset entity; heating and
  cooling ahead as a release with limits (highest temperature, offset,
  duration), not as a daily target. The forecast knows that the heat drawn
  ahead is missing later.
- Temperature targets with a safety net: if the temperature drops below the
  minimum outside the target's window (e.g. hot water after showering in the
  evening), the minimum is reached again within a set time instead of only
  by the next day's deadline.

**Dashboard**

- A clearer **Settings** tab: grouped by their role in the control (grid
  connection, charging from PV, reserves, discharging, prices, batteries), the
  expert settings collapsed, sub-settings hidden while their feature is off,
  and only the settings that have an effect in your installation (e.g. no
  battery settings without batteries). Also fewer settings where SLEMS can
  learn the value itself.

**Batteries and installations**

- Battery groups with a shared fuse: several batteries on one circuit (e.g.
  two Venus behind one circuit breaker) get a highest power together, which
  the active batteries of the group share, instead of a fixed half for each.
- Batteries with their own PV: the first real devices (Marstek B2500,
  Zendure SolarFlow) and hybrid inverters through their integrations or
  through Modbus profiles (candidates to test are available), a
  shared highest output for several units at one inverter, and the check
  for PV counted twice also in the system settings.
- Further kinds of installations: batteries with their own meter or a backup
  output with loads behind it, and batteries serving several inverters.
- Ongoing check of the batteries in normal operation: clean set point changes
  (one battery, no consumer switching, a quiet smart meter) compared with the
  smart meter and the reported power; after many consistent mismatches (e.g. a
  sign turned by an update of the integration) a repair issue suggests the
  self-test. First only counted in the diagnostics to set the limits with real
  data.
- Further battery models: shipped Modbus profiles from own profiles and
  [candidates](docs/battery-profiles/README.md) that passed their trial with
  users of these batteries; a driver of its own only for models that do not
  fit a profile.

## Development

See [developers.md](developers.md).

## License

GPL-3.0, see [LICENSE](LICENSE). The Marstek Venus register map and control
sequence are based on [Omnibattery](https://github.com/ffunes/Omnibattery)
(GPL-3.0).
