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
  - [Phases (optional)](#phases-optional)
  - [Smart meter over Modbus (optional)](#smart-meter-over-modbus-optional)
  - [Tariffs (optional)](#tariffs-optional)
  - [PV forecast](#pv-forecast)
  - [Weather (optional)](#weather-optional)
  - [Consumption forecast](#consumption-forecast)
- [Without batteries](#without-batteries)
- [Batteries](#batteries)
- [Consumers](#consumers)
  - [Current control](#current-control)
  - [Car at the wallbox](#car-at-the-wallbox)
  - [Temperature sensors of the storage](#temperature-sensors-of-the-storage)
  - [Battery support](#battery-support)
  - [Role in the feed-in cap](#role-in-the-feed-in-cap)
  - [Daily target](#daily-target)
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
  the conversion losses and the response time of the batteries, the phase of
  batteries and consumers (with a smart meter measuring each phase), the smart
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

### Phases (optional)

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

With **Add tariff** on the SLEMS integration page you enter your electricity
tariff the way your bill shows it; the values stay in your Home Assistant.
Prices are in the currency set in Home Assistant under **Settings → System →
General** (hundredths per kWh, e.g. ct or Rp., and whole amounts per year);
market prices come in euro.

1. Name, role (**current tariff** or **comparison tariff**) and VAT: for the
   consumption bill, for the feed-in energy (often 0 % for private PV) and for
   the other feed-in items. Separate contracts for import and feed-in: add
   both as **current tariff**, they count together as one. A comparison tariff
   with items for only one side (e.g. a dynamic supply tariff) takes the other
   side from the current tariffs, so the comparison always shows total costs.
2. The lines of the bill one by one: name, side (**consumption** or
   **feed-in**), group (**energy**, **grid**, **levies**) and the net price (without
   VAT; SLEMS adds the VAT of step 1) in ct/kWh or €/year (charged per day; a
   discount is negative). If the bill only shows gross prices, enter those and
   set the VAT to 0 %. Optionally only in some
   months, on some weekdays or in a time window of the day, e.g. a cheaper
   grid price at noon in summer: a line with a window replaces the line of the
   same name in its window. A price change is the same line again with
   **valid from**. On the feed-in side the energy price is your credit.
3. **Check against a bill**: enter its period and amounts; SLEMS computes the
   period with the tariff and your recorded grid import and export and shows
   both amounts and the deviation, per side and group.

**Import and export as YAML.** Instead of entering the lines, **Add tariff →
Paste YAML** takes a tariff file (format `slems-tariff` with a version
number; prices net, plus optional supplier, validity from/to and source).
**Export as YAML** in the menu of the lines shows a tariff as such a file, e.g.
to save it or to use it in another SLEMS. An AI can turn a bill into such a
file: [prompt and instructions](docs/tariff-prompt.md). A file of a newer
format version is refused with a hint to update SLEMS; older versions are
converted.

**Templates.** **Add tariff → From templates** offers tariffs with public list
prices, per country and part of the bill: the energy of a supplier, the grid
fees of a grid area, the levies. First choose the energy tariff; the grid
tariff usually combined with it (households) and the levies of the country
are then preselected, each with the prices in effect today, and SLEMS
combines them into one tariff (then compare it with a bill). The result is
an ordinary tariff you can edit; later changes of a template do not touch
it. Options of a price sheet (e.g. an upgrade to certified green electricity,
a bonus for an online bill, the metering of the feed-in direction) are chosen
in an extra step. A feed-in tariff of its own is a template of its own, added
as a separate current tariff (SLEMS counts all current tariffs as one
contract).

A tariff from templates remembers its templates: when an update of SLEMS (or an own
template) brings newer prices or a successor tariff, a repair issue tells
you, as it does when a current tariff is past its end. **Update to the new
prices** in the menu of the lines takes them over (with several successors you
choose one, or none): the current lines end the day before, the new ones
apply from then on, the old prices stay for the past and own lines stay as
they are. A corrected template (e.g. a misplaced decimal point, same dates)
is offered the same way: the values are corrected in place, also for the
past, lines you changed yourself stay. **Reset to the template values** undoes
your own changes of the template lines.

Own templates: YAML files in the folder `slems_tariff_templates` of your Home
Assistant configuration. Suggest a template for everyone as an
[issue](https://github.com/gojux/SLEMS/issues) with the source of the prices.

Switzerland: **Add tariff → From ElCom** queries the open data of
ElCom (ld.admin.ch) for the tariff of your municipality and consumption
category (energy, grid and levies, excluding VAT, as the mean of the
category; high and low tariff times are averaged); the next year, published
in September, is offered as new prices, in the background only with
**Fetch market prices** switched on.

Austria: the grid fees of every grid area (network level 7, from the grid fee
ordinance, with the reduced price from April to September 10–16 h)
and the federal levies; the metering fee is entered at its legal maximum
(your grid operator may charge less) and municipal levies (e.g. the
Gebrauchsabgabe in Vienna) are not included.

Austria and Germany: a general template **Dynamic tariff** (energy at the day-ahead price per quarter hour);
enter the markup and the monthly fee of your contract afterwards in its
lines, as suppliers of dynamic tariffs mostly publish them only per postcode.

Germany: the federal levies
(electricity tax, surcharges and the concession levy by the size of the
municipality, at its legal maximum), the grid fees of the largest grid
operators (Westnetz, Bayernwerk, E.DIS, Avacon, Netze BW, Schleswig-Holstein
Netz, Stromnetz Berlin, Hamburger Energienetze, EWE NETZ, N-ERGIE Netz, SWM
Infrastruktur, RheinNetz, enercity Netz, Thüringer Energienetze, Netze ODR,
LEW Verteilnetz, Westfalen Weser Netz, Syna, Mainnetz; also with § 14a module 1 and
module 1 + 3 with time variable prices for controllable devices; metering as
an option at its legal maximum by meter type and consumption) and
the EEG feed-in credit of PV plants up
to 10 kWp per commissioning period; for plants commissioned since 25 February
2025 the credit is 0 while the day-ahead price is negative (**Fetch market
prices** needed). A line can have **Zero at negative market prices** for this.

A line may also be a **percent of the group** (e.g. a municipal levy of 7 % on
the energy).

Where the energy comes from: SLEMS records grid import and feed-in per
quarter hour itself from every grid value (kept for 400 days), so import and
feed-in within an hour do not cancel out and dynamic prices apply per quarter
hour. For exact totals choose the energy counters of your smart meter
(**Grid import energy (counter)** / **Grid export energy (counter)**) in the SLEMS
options: the recorded quarter hours are then scaled to the counter of each
hour. Hours before the recording use the counters, without counters the
hourly mean of the grid power (less exact).

**Power price.** A line with **€ per kW and month (peak power)**
charges the peak import of each calendar month, as the Austrian grid tariffs
do from 2027: the mean power of the highest quarter hour (or hour) of the
month, or the mean of several highest ones, at least **Power billed at
least** (e.g. 2 kW) and rounded up to **Rounded up to** if the tariff does
so. **Charged from** / **Charged up to** split it into tiers with their own
price, e.g. a second line with the higher price above 10 kW. The peak comes
from the recorded quarter hours; hours before the recording only have their
hourly mean, which is lower than their highest quarter hour, so the
comparison marks months in which such an hour could hide a quarter hour
above the billed power (its mean × 4 is above it). How SLEMS keeps the peak low: see
[Import peak shaving and power price](#import-peak-shaving-and-power-price).

**Dynamic tariffs and market prices.** A line can also follow the day-ahead
market: **spot price (hourly)** or **monthly market price**, each as market price
+ share in % of its amount + price in ct/kWh, e.g. "spot + 5 % + 1.2 ct" or
"monthly market price − 0.5 ct" for the feed-in; a markup in % stays a markup
at negative market prices. **Market price at most** caps the market price
before the markups (e.g. 60 ct/kWh net at Salzburg AG FlexSpot). Published monthly market
prices can be entered per month (e.g. "2026-08: 7.1"); months without a value
use the mean of the spot prices weighted by your feed-in. If the price
follows an official monthly value, choose it as the line's **Monthly value**
(Austria: the reference market value for PV, wind or hydro published by
E-Control according to § 13 EAG); SLEMS then fetches it with the market
prices, the weighted mean stands in until it is published.

SLEMS only fetches market prices from the internet once you switch on
**Fetch market prices** (off by default). **Price source** selects where from:
APG (Austria), SMARD of the Bundesnetzagentur (Germany/Luxembourg) or
Energy-Charts of Fraunhofer ISE (Austria, Germany/Luxembourg and Switzerland);
the default follows the country set in Home Assistant. Swiss prices are in euro
like all exchange prices. At the moment SLEMS only supports market prices in
euro: with another currency set in Home Assistant, the tariff comparison and
**Check against a bill** leave out tariffs that follow the market price, and a
repair notice says so if the current tariff is one of them. SLEMS then loads the last 12 months once, stores them
locally and fetches the next day after the day-ahead auction (from 13:00).
The sensor **Market price** shows the price of the current quarter hour in
ct/kWh without fees and VAT, with the source as attribution.

**Price aware control** (switch, off by default; needs a tariff): if the
stored energy does not last for every hour until PV refills the batteries
(a day whose forecast PV surplus fills them from the minimum to the maximum
state of charge; a little surplus on a dull day does not count), they cover the hours with the highest import price of the current tariff and
keep their energy in hours that are cheaper by at least the **minimum gain**
(default 2 ct/kWh); there the house draws from the grid. The grid import
stays the same, it only moves to cheaper hours – no charging from the grid
and no feed-in from the batteries. Any time dependent price counts: market
prices or time windows of a fixed tariff. Import peak shaving still applies.
The strategy shows **Price hold** with the hours concerned, the day chart and
the simulation include it. The price plans work in quarter hours (market
prices change every quarter hour): the hourly consumption forecast is spread
evenly over its quarters, PV comes from the forecast; the day chart shows
their hourly means. Daily targets with the source **+ grid**: if the
forecast surplus is short for them anyway, the forced run starts in the
window with the lowest cost before the deadline (cheaper by at least the
minimum gain per kWh than at the latest start); the consumer card shows
**from … in the cheapest window**. The cost per quarter hour: what the
forecast surplus covers counts at the feed-in credit, the rest at the import
price. For an on/off consumer whose power clearly depends on the outdoor
temperature (e.g. the hot water charge of an air source heat pump) its
learned power at the forecast temperature of the hour counts: a warm
afternoon can so beat a cold morning with cheaper electricity.

**Estimated prices**: the day-ahead prices of the next day are published
around 13:00. For the quarter hours after the last known price the price aware
control plans with estimates: per time of day the median of the last 14 days
of the same kind (working day, or weekend and public holiday of the bidding
zone; in Switzerland the days off in at least half of the cantons), its swing around the mean of the day halved as a safety margin. So the night and a cloudy next day are planned
before the real prices are known; a new plan follows as soon as they are.
Only known negative prices start the plan for negative prices. The price
chart shows the estimates faint.

**Charge batteries from the grid** (switch, off by default; only with price
aware control): SLEMS plans until PV refills the batteries (as above; at most
until the last known price, after it with the estimated ones) which hours the
batteries cover, which they hold and in which they charge from the grid. A
smaller PV surplus before is part of the plan: the batteries take it, and
only what is still missing for the expensive hours is charged from the grid
(e.g. at a cheap noon before an expensive evening after a dull day). A charge must pay after the
charge and discharge losses, the [wear costs](docs/guide/batteries.md#battery-wear-costs) and the
minimum gain; of equal plans the later charge wins, as home storage ages
mostly with time at a high state of charge. Limits: **Highest state of charge
from the grid** (default 90 %), **Highest grid charge power** (0 = the charge
power of the batteries), the import limit of peak shaving and the space the
feed-in cap needs when PV takes over. Your own PV surplus is taken first. The plan includes the price hold; the strategy shows **Grid charging** with the energy, the
start and the expected saving, the day chart the planned charging.

**Surplus hours and negative prices**: with price aware control and a
feed-in credit that follows the market price per hour, or a negative import
price or credit within the known prices, the plan also covers the surplus
hours, without any further switch: the batteries keep room for the PV surplus
of the hours with a lower or negative credit (they take only part of the
surplus before and feed in the rest at the higher credit; strategy **Room for
cheaper hours**). With **Charge batteries from the grid** they also charge
from the grid when the import price including all fees is negative. Your own
PV surplus is taken first. **Highest grid import while charging from the
grid** (sum of all phases, 0 = no limit, e.g. for the agreed connection power;
it does not protect a single phase or the main fuse) and the import limit of
peak shaving cap the import. The plan then reaches the last known price (at
most 36 hours) and is computed in the background; until it is ready, the
previous one applies.

**Feed in from the batteries** (switch, off by default; only with price
aware control): the same plan may feed in from the batteries beyond the
house's need when the credit of the hour is higher than what the energy is
worth later plus the minimum gain and the wear costs of the batteries (the
energy is mostly charged again later, an extra cycle) – accepting grid import
later. Not below
the morning reserve, at most the maximum grid export while discharging and
the feed-in cap. This only pays with a feed-in credit that follows the market
price per hour; with a fixed or monthly credit the strategy says the option
has no effect. Check your contract and any subsidy first: some do not allow
feeding in energy that was charged from the grid.

**Measured saving**: the sensor **Saving price control** adds up per month what
the price aware control saved: after every night (or other run until PV takes
over) in which it held, charged or fed in, SLEMS compares the recorded costs
with the same hours played *as usual* through the battery model, starting from
the measured state of charge. Nights without such an action count nothing.
The tariff comparison shows the value next to the estimate.

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

SLEMS learns the consumption from the long-term statistics of Home Assistant
and forecasts today and tomorrow hourly. Recent days count more than older
ones, so changes are followed within days. Heat pumps are forecast from the
outdoor temperature of the day, so a warm day in the heating season
immediately predicts less heating energy. The share that does not depend on
the temperature (mostly hot water) comes from the last two weeks only, the
dependency on the temperature from the whole year: if a heating rod heats the
hot water in summer and the heat pump in winter, the forecast follows the
switch within days. After a sudden rise (e.g. the heat
pump starting the heating season) the forecast is raised at once: if the last
three hours took on average at least 200 W and 30 % more than forecast, that
excess is added to the next 24 hours, so the night discharge and the
grid friendly charging plan with it until the learned forecast has caught up
(card **Forecast accuracy**: **Raised**). It is never lowered this way. Two
optional settings help at the start:

- **Outdoor temperature**: a temperature sensor with history. Without it,
  SLEMS records the temperature of the weather entity itself; the heat pump
  model then needs about a week before it uses the temperature.
- **House consumption history**: a power sensor of the house consumption with
  existing history (e.g. from another battery integration), used for the time
  before SLEMS recorded its own values.

Public holidays count as weekend days, in the learned profile and in the
forecast: those of the country set in Home Assistant and of the state or
canton selected as **Public holidays** in the system settings. When the
system settings are opened and no region is stored yet, SLEMS suggests one
from the location of Home Assistant: a single request to Nominatim
(OpenStreetMap) with the coordinates rounded to about 100 m, the service the
onboarding of Home Assistant uses for the country as well. With **National
holidays**, or if no region is found, only the holidays of the whole country
count (in Switzerland those kept in at least half of the cantons).

Recommended setup when switching from another battery integration:

1. Select that integration's house consumption sensor as **House consumption
   history**. SLEMS then learns from the full history right away. Without it,
   SLEMS derives the history from grid and PV only; the battery power before
   SLEMS is unknown, so charging and discharging would distort the learned
   consumption.
2. Select an outdoor temperature sensor with history if one exists. Otherwise
   the heat pump forecast uses the mean of the last days until SLEMS has
   recorded about a week of temperatures.

**Forecast accuracy** (card in the overview, sensors **Consumption forecast
accuracy** and **PV forecast accuracy**):

- Consumption: SLEMS recalculates the forecast of each of the last 14 days as
  it would have been made at midnight, with the history up to then, and
  compares it with the measured consumption. Shown are the accuracy of the
  daily energy (100 % minus the mean deviation), the tendency (too high or too
  low), the deviation per hour (how well the course of the day is hit), the
  data basis (days of consumption, days of heat pump data with temperature)
  and tomorrow's forecast with its expected deviation. The heat pump is
  recalculated with the measured temperature of the day, so its share looks
  somewhat better than it was.
- PV: past forecasts are not available from the solar forecast integration, so
  SLEMS records the forecast of each day at its start and compares it with the
  production in the evening. The card shows accuracy and expected deviation
  from 7 compared days on (before: **collecting data**), because a few days say
  little.

The sensor **House consumption** (used for the forecast and shown in the energy
flow) is calculated as grid + PV − batteries. The smart meter often reports a
change later than PV and batteries; a momentarily negative result is therefore
replaced by the last valid value (for at most 30 seconds, then unknown).
If an enabled, controllable battery cannot be read, it counts with its last
measured power for up to 5 minutes, since it keeps its last set point without
new commands. After that the house consumption is unknown until the battery can
be read again, instead of adding its power to the house. A disabled battery
never makes the house consumption unknown: while it is not yet handed back to
its own logic it keeps counting with its last measured power, once released it
counts with 0 W. In release state **Automatic** it may charge or discharge on
its own: it counts with its last measured power for up to 5 minutes, then with
0 W, and a repair points out that the house consumption is inaccurate. The consumption forecast leaves out hours in which the statistics
of a battery lack a value.

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

Every consumer needs its own power **and** energy sensor.

Which consumers to add: the ones SLEMS controls (heating rod, wallbox,
dehumidifier, …) and heat pumps (their consumption is forecast from the
weather). Adding other devices only to measure them, e.g. a washing machine or
a dishwasher, currently does not improve the planning: their consumption is
already part of the house consumption, and its forecast (a profile per weekday
and hour) contains their average use. They only get their own card and box in
the energy flow.

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
- **Release** (control): for devices an automation of your own starts,
  because it has to know more than SLEMS (e.g. a washing machine with a start
  command, or the hot water charge of a heat pump via SG Ready only while its
  compressor runs steadily). SLEMS switches nothing itself, but plans the
  consumer like an on/off consumer with the configured power and provides:
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
    (row **Time window**) and the day chart show it, and it is also released for
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

  **Heat pump with release** (hot water): with the type **Heat pump** the
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
  the heating. The [guide to the hot water of a heat pump](docs/heat-pump-hot-water.md)
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

### Current control

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
  [Connecting a wallbox with evcc](docs/wallbox-evcc.md). Without a start/stop entity evcc decides itself when to
  start and stop (e.g. in its PV mode) and SLEMS only caps the current; with
  the charge mode as start/stop entity SLEMS controls the charging fully, so
  its battery support and forecasts decide. The card shows the planned power
  with amperes and phases, e.g. **4,140 W (6 A, 3 phases)**.

### Car at the wallbox

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

### Temperature sensors of the storage

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

### Battery support

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

### Role in the feed-in cap

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

### Daily target

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
  the release before the forced run, the forced run and the deadline
  (a mark). A consumer with the role
  **Supporting** is left out of the surplus part while the feed-in cap is on:
  the feed-in cap plans it with the surplus above the limit.

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
  discharge and the [battery support](#battery-support).
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
