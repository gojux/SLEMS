<p align="center"><img src="assets/logo.png" alt="SLEMS" height="96"></p>

**English** | [Deutsch](README.de.md)

# SLEMS

SLEMS is an energy management system for Home Assistant. It controls home
batteries and consumers such as a heating rod, a heat pump or a wallbox. It
does not only react to the moment, but plans the day from the PV and
consumption forecasts.

![SLEMS overview: energy flow, key figures and the forecast and plan chart of the day](docs/images/dashboard_overview_en.png)

> **Status: beta.** SLEMS has been running in production without problems
> for quite some time; new features keep coming often. Details under
> [Status and limits](#status-and-limits).

## What SLEMS does for you

**A feed-in cap without wasted power.** If you may feed in only part of your
PV power (e.g. 60 or 70 %), SLEMS stores the energy above the limit and keeps
room in the batteries for it in time, instead of curtailing PV.

**Lower import peaks.** With power based grid fees the highest quarter hour
of the month counts. SLEMS keeps energy in the batteries for these peaks and
picks the import limit with the lowest expected cost.

**More of your own power in the house.** The surplus (the PV power your house
does not need right now) goes to batteries and consumers by your priorities.
With a daily target the hot water, for example, is warm by 5 pm: from the
surplus where possible and only if needed from the batteries or the grid.

**More from your tariff.** SLEMS calculates with your tariff as it is on the
bill. With a dynamic tariff it keeps stored energy for expensive hours, and a
monthly comparison shows what another tariff would have cost.

**Charging when the sun is strongest.** Most controls charge as soon as there
is surplus. The battery is then full in the morning, and the midday peak goes
into the grid. SLEMS charges in a *grid friendly* way: with the surplus above
a calculated limit, so the batteries absorb the midday peak and are still
full in the afternoon.

![Grid friendly charging](docs/images/grid_friendly_en.svg)

What you gain:

- The battery spends less time at a high state of charge and ages more slowly
  (see [HTW Berlin](https://solar.htw-berlin.de/wp-content/uploads/WENIGER-2025-Smarte-Ladestrategien.pdf),
  in German).
- You feed in during the morning, when power is worth more and more likely to
  find a taker in an energy community.
- Your installation is prepared for feed-in limits such as those in force for
  new installations in Germany and passed in Austria.
- The grid is relieved at midday, and more PV systems fit into it.

**Little to set, nothing to risk.** SLEMS learns losses, response times and
forecast buffers by itself. The operating mode **Observe only** shows what it
would do before it controls anything, also next to an existing control. The
**simulation** shows what other settings, another battery or more PV would
change, and an assistant suggests fitting values.

## Does SLEMS fit your installation?

What you need:

- Home Assistant 2026.10 or later.
- A smart meter in Home Assistant that reports the grid power (import and
  export) continuously, e.g. a Shelly Pro 3EM. The more often it reports, the
  more precisely SLEMS controls (e.g. every second).
- Optional: a PV forecast (e.g. Forecast.Solar), batteries and consumers that
  SLEMS should control.

| Battery | Support |
|---|---|
| AC battery (its own inverter, at a socket or in the distribution board) | Marstek Venus E 3.0 directly over Modbus TCP; other models through a [Modbus profile](docs/guide/battery-modbus-profile.md) or the entities of their Home Assistant integration (control experimental) |
| Plug-in battery with PV modules (balcony solar with a battery, e.g. Marstek B2500, Zendure SolarFlow, Anker Solarbank) | through the entities of its integration with **Own PV modules at the battery** (experimental, testers and feedback welcome) |
| Hybrid inverter (PV and battery at one inverter) | its battery through the entities of its integration (experimental, testers and feedback welcome) |
| No battery | PV surplus into consumers, with daily targets, tariffs and forecasts ([what works without batteries](docs/guide/features.md#without-batteries)) |

If you would like to use SLEMS with another battery, please
[open an issue](https://github.com/gojux/SLEMS/issues/new?template=feature_request.yml) with the model and how
it is connected (Modbus, local API, Home Assistant integration), and whether
you could test it.

**Tariffs:** templates with public list prices exist for Austria (suppliers,
the grid fees of all grid areas, levies), Germany (the grid fees of 19 grid
operators, the EEG feed-in credit, levies) and Switzerland (the tariff of
your municipality from ElCom). You enter any other tariff as on the bill, or
have an AI make it from your bill with the prompt provided (see
[Templates](docs/guide/tariffs.md#templates)).

## Quick start

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=gojux&repository=SLEMS&category=integration)

1. Install SLEMS through the Marketplace (from Home Assistant 2026.11) or
   HACS and restart Home Assistant. The button above opens SLEMS there; by
   hand, add the custom repository `https://github.com/gojux/SLEMS` (see
   [Installation](docs/guide/setup.md#installation)).
2. Add the integration, select the smart meter and the PV systems and check
   the sign of the grid power (see [Setup](docs/guide/setup.md#setup)).
3. Add batteries and consumers. Most batteries allow only one Modbus
   connection: disconnect them from other integrations first.
4. Let SLEMS run in the operating mode **Observe only** for a while (the
   default). The dashboard shows the forecast and the plan; nothing is
   controlled yet.
5. Turn off other controls of the same batteries and consumers (automations,
   other integrations) and set the operating mode to **Active**. New
   batteries first run their
   [self-test](docs/guide/batteries.md#self-test-of-a-battery) (about 5–10
   minutes), then SLEMS takes over.

The defaults are fine to start with. If you set the operating mode back to
**Off**, the batteries follow their own logic.

## Features at a glance

The complete list is under [All features](docs/guide/features.md).

### Batteries

SLEMS controls any number of home batteries, plans their use over the day and
protects them along the way. A battery can be added in three ways:

- **Marstek Venus E 3.0** directly over Modbus TCP: the fastest way, with the
  most data. SLEMS searches the network for it.
- **Existing Home Assistant entities** (control experimental): any battery
  that another integration brings in, also balcony solar with a battery or a
  hybrid inverter.
- **Modbus profile** (experimental): a battery with Modbus TCP, described by a
  profile an AI creates from its manual.

What SLEMS brings for every battery:

- A **self-test** when it is added, checking signs, values and response
  before the battery is controlled.
- **Several batteries**: SLEMS switches on only as many as is most efficient
  for the current power. The others stay in standby as a reserve and take
  over with a smooth transition when needed.
- **Limits and protection**: state of charge, charge and discharge power
  (e.g. 800 W), gentle charging near full, monitoring of the cell voltages, a
  pause for firmware updates.
- **Regular full charge**: LFP batteries measure their state of charge
  exactly again only after a full charge. SLEMS takes care of it, with
  surplus that would be stored anyway.

Details: [Batteries](docs/guide/batteries.md) and
[Battery from a Modbus profile](docs/guide/battery-modbus-profile.md).

### Consumers

SLEMS steers the surplus into controllable consumers (heating rod, wallbox,
dehumidifier, heat pump, …) and plans them together with the batteries.

- **Control**: on/off, a power set point in W, a current set point in A
  (wallbox, also with evcc) or a **release** for an automation of your own,
  e.g. for the hot water charge of a heat pump.
- **Priorities**, minimum runtime and pause, and **Avoid short runs** for
  devices with a compressor. SLEMS recognises a device's own thermostat by
  itself.
- **Daily targets**: runtime, energy, temperature or the car's state of
  charge by a time of day.
- **Battery support**: per consumer you decide whether the batteries may
  supply it.

Details: [Consumers](docs/guide/consumers.md), plus the guides to the
[hot water of a heat pump](docs/heat-pump-hot-water.md) and the
[wallbox with evcc](docs/wallbox-evcc.md). evcc specialises in EV charging
and complements SLEMS well.

### Operation and control

The entity **SLEMS Operating mode** switches between **Off**, **Observe
only** (forecasts and plans are shown, but nothing is controlled) and
**Active**. In the operating mode **Active** SLEMS controls on every report
of the smart meter and takes the learned response time of every battery into
account, so the control does not oscillate. On top there is the plan for the
day. Its options can be combined:

- **Grid friendly charging** (on by default): absorb the midday peak and
  still be full in the afternoon.
- **Feed-in cap**: store the energy above a feed-in limit.
- **Import peak shaving**: keep energy for the peaks.
- **Night discharge**: feed in at night the energy that would still be in the
  batteries in the morning; useful e.g. in an energy community.
- **Bad weather mode**: store as much as possible before bad weather and keep
  it over the night.

Details with a chart per option:
[Operation and control](docs/guide/control.md), including
[Which option when?](docs/guide/control.md#which-option-when).

### Tariffs and prices

With a tariff SLEMS works in money: it checks your electricity bill, shows
every month what another tariff would have cost and can control the batteries
by the prices.

- **Enter a tariff** as on the bill, from **templates** or as a YAML file.
- **Dynamic tariffs** with day-ahead prices per quarter hour (fetched only when
  you switch it on) and **power prices** for the monthly peak.
- **Price aware control** (optional): keeps the batteries' energy for expensive
  hours, on request charges cheaply from the grid or feeds in when it pays
  well, and measures what that saved.

Details: [Tariffs and prices](docs/guide/tariffs.md).

### Forecasts

- **PV forecast**: any integration that provides a solar forecast for the
  energy dashboard, e.g. Forecast.Solar or Solcast. SLEMS corrects it during
  the day with the production so far.
- **Consumption forecast**: learned from the long-term statistics of your
  house, for today and tomorrow by the hour, with the weather, public
  holidays and a separate model for heat pumps.

Details: [Setup](docs/guide/setup.md#pv-forecast) and
[Consumption forecast](docs/guide/consumption-forecast.md).

### Dashboard

SLEMS adds the entry **SLEMS** to the Home Assistant sidebar:

- **Overview**: energy flow, key figures, the chart with the day's forecast,
  plan and measurements, with a tariff also the prices.
- **Batteries** and **Consumers**: one card per device with its state, plan
  and daily target.
- **Simulation**: try other settings and compare them with the current ones,
  without changing anything; also what other batteries or more PV would have
  changed in the recorded year.
- **Settings**: all settings by section, with a search and an assistant that
  suggests values from the tariff, the plant and an optional goal.

Details: [Dashboard](docs/guide/dashboard.md).

## Status and limits

- **Beta**: SLEMS runs stably in production, but is still extended often.
- **Experimental**: the control of batteries through entities and Modbus
  profiles. The control of a wallbox is not yet tested with a real wallbox.
  Testers and feedback are welcome.
- **At your own risk**: in the operating mode **Active** SLEMS sends commands
  to batteries and switches consumers. Check the limits of your batteries and
  the fuses of your circuits.
- **Local**: control, forecasts and learning run in Home Assistant, without a
  cloud account. SLEMS goes to the internet only for market prices and
  monthly market values (only with your consent), for Swiss tariffs from the
  open data of ElCom and once to suggest the region for public holidays.

## Help and feedback

Hints on problems are under
[Problems and notifications](docs/guide/control.md#problems-and-notifications).
Please report faults as an
[issue](https://github.com/gojux/SLEMS/issues/new?template=bug_report.yml)
and attach the diagnostics: **Settings → Devices & services → SLEMS → ⋮ →
Download diagnostics**. Addresses and serial numbers of the batteries are
redacted in them.

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
- Consumers preferred at very cheap or negative prices: a price threshold per
  consumer (e.g. the heating rod or a boiler), based on the total import
  price including grid fees, not only the exchange price.
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

**Batteries and installations**

- Battery groups with a shared fuse: several batteries on one circuit (e.g.
  two Venus behind one circuit breaker) get a highest power together, which
  the active batteries of the group share, instead of a fixed half for each.
- Batteries with their own PV: the first real devices (Marstek B2500,
  Zendure SolarFlow) and hybrid inverters through their integrations or
  through Modbus profiles (candidates to test are available), and a
  shared highest output for several units at one inverter.
- Further kinds of installations: batteries with their own meter or a backup
  output with loads behind it, and batteries serving several inverters.
- Further battery models: shipped Modbus profiles from own profiles and
  [candidates](docs/battery-profiles/README.md) that passed their trial with
  users of these batteries; a driver of its own only for models that do not
  fit a profile.

## The name

*Slug* (our "favourite pets" and a funny word for a whole range of
[very interesting animals](https://en.wikipedia.org/wiki/Sea_slug)) and *EMS*
(energy management system).

## Development

See [developers.md](developers.md).

## License

GPL-3.0, see [LICENSE](LICENSE). The Marstek Venus register map and control
sequence are based on [Omnibattery](https://github.com/ffunes/Omnibattery)
(GPL-3.0).
