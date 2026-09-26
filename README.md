<p align="center"><img src="assets/logo.png" alt="SLEMS" height="96"></p>

**English** | [Deutsch](README.de.md)

# SLEMS

SLEMS is a Home Assistant integration that manages home batteries and
controllable consumers. Its goal is to operate the batteries in a grid friendly
way, e.g. by shifting the charging time so that the daily feed-in peak is
absorbed, based on forecasts of consumption and PV production.

The name is a combination of *Slug* (a wonderful word for a very interesting
animal) and *EMS* (energy management system).

## Why SLEMS?

Most home battery controls react to the moment: they keep the grid power
at zero and charge whenever there is surplus. The battery is then full by
late morning, and the midday peak goes into the grid anyway. SLEMS plans
ahead and controls precisely:

- **Grid friendly instead of full by 10 o'clock.** From the PV and consumption
  forecasts SLEMS calculates a feed-in limit and charges the batteries with the
  surplus above it, so they absorb the feed-in peak and are still full in the
  evening. The limit is recalculated continuously and drops by itself when the
  day turns out worse than forecast.
- **Its own consumption forecast.** Learned from the long-term statistics of
  your house, with the weather and a separate heat pump model; vacation mode,
  night discharge to a forecast based reserve and a projection of the state of
  charge for today and tomorrow.
- **Batteries and consumers in one plan.** The surplus is distributed between
  batteries and controllable consumers (heating rod, heat pump, …) with
  priorities, battery priority until the charge is secured, minimum runtimes,
  external blocking and thermostat pauses.
- **Several batteries at their efficient point.** SLEMS learns the conversion
  losses of every battery, runs as few batteries as useful and switches
  between them with a smooth transition.
- **Precise control without oscillation.** Event driven on every report of the
  smart meter; the learned response time of the batteries is compensated and
  the control gain adapts itself.
- **Battery care and safety.** State of charge window, power limits (e.g.
  800 W), temperature charge limit, cell delta monitoring with active cell
  balancing, detection of batteries that do not respond, repair issues and
  notifications.
- **Transparent and local.** A dashboard with energy flow, forecast chart and
  all settings; everything runs locally in Home Assistant without a cloud. The
  simulation mode shows what SLEMS would do before it controls anything, also
  next to an existing battery integration.

**When another solution fits better (today):** many different battery brands
(SLEMS currently supports the Marstek Venus E 3.0 and read-only batteries from
existing entities), charging from the grid by dynamic tariffs or electric
vehicle charging. Omnibattery covers many battery models, evcc specialises in
EV charging; evcc complements SLEMS well (see [roadmap](#roadmap)).

## Features

| Feature | Status |
|---|---|
| Any number of batteries, added/edited/removed at any time | ✅ |
| Marstek Venus E 3.0 via Modbus TCP | ✅ (control not yet tested on a real device) |
| Read-only battery from existing entities (e.g. while Omnibattery is in control) | ✅ |
| Smart meter, PV power and weather entity freely selectable | ✅ |
| PV forecast from any solar forecast integration (Forecast.Solar, Solcast, …) | ✅ |
| Consumers with own power/energy sensors, inside or outside the smart meter | ✅ configuration |
| Heat pump as consumer type (weather dependent forecast) | ✅ configuration |
| Operating mode *Off / Simulation (read-only) / Active* | ✅ |
| Vacation switch | ✅ |
| Import peak shaving at low state of charge (manually enabled) | ✅ |
| Consumption forecast today/tomorrow (history + weather, heat pump temperature dependent) | ✅ |
| Grid friendly charging: absorb PV feed-in peaks | ✅ |
| Feed-in cap: store the PV energy above a feed-in limit, make room in time | ✅ |
| Distribution of surplus between batteries and consumers (priorities, split, minimum runtime/pause) | ✅ |
| Battery efficiency (battery counters, learned or manual) | ✅ |
| Averaged grid surplus (0–300 s) | ✅ |
| Grid surplus targets for charging/discharging, maximum export while discharging | ✅ |
| Night discharge to a forecast based reserve | ✅ |
| Distribution between batteries by efficiency, rotation with smooth transition | ✅ |
| Real-time control of batteries and consumers (operating mode *active*) | ✅ |
| Cell delta and active cell balancing (Marstek Venus E 3.0) | ✅ (not yet tested on a real device) |
| Battery limits: minimum/maximum SoC, charge/discharge power limit (e.g. 800 W), temperature charge limit | ✅ |
| Detection of batteries that do not deliver the commanded power, confirmation of set points | ✅ |
| Dashboard (sidebar panel): energy flow, key figures, daily forecast and plan chart, batteries, consumers, settings | ✅ |

## Installation

### HACS (custom repository)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=gojux&repository=SLEMS&category=integration)

The button opens SLEMS in HACS of your Home Assistant instance and adds the custom repository; then continue with step 2. Or by hand:

1. HACS → ⋮ → *Custom repositories* → add `https://github.com/gojux/SLEMS`, type *Integration*.
2. Install *SLEMS* and restart Home Assistant.

### Manual

Copy `custom_components/slems` into the `custom_components` folder of your
Home Assistant configuration and restart Home Assistant.

Requires Home Assistant 2026.9 or newer.

## Configuration

1. *Settings → Devices & services → Add integration → SLEMS*.
2. Select the grid power entity of your smart meter (positive = import,
   negative = export; enable *invert* if your meter uses the opposite sign),
   optionally PV power, PV forecast and weather.
3. On the SLEMS integration page choose **Add battery** for every battery and
   **Add consumer** for every consumer you want to measure or control.

### PV forecast

Every integration that provides a solar forecast for the Home Assistant energy
dashboard can be selected, e.g. Forecast.Solar or Solcast. Several entries
(e.g. one Forecast.Solar entry per roof plane) are summed up. SLEMS uses the
finest resolution the provider delivers (e.g. 15 or 30 minutes). Forecast.Solar
marks each period by its end; SLEMS takes this into account.

### Weather (optional)

The weather entity improves the consumption forecast, especially for a heat
pump. Recommendations for Vorarlberg / the Alpine region:

- **GeoSphere Austria AROME**: high resolution model of the Austrian weather
  service, well suited for alpine valleys. Available in Home Assistant through
  custom integrations such as
  [GeoSphere Austria Plus](https://github.com/coding-pagro/GeoSphere-Austria-Plus) or
  [GeoSphere Austria Next](https://github.com/slettmayer/ha-geosphere-next) (HACS).
  The built-in *GeoSphere Austria* integration only provides station
  measurements, no forecast.
- **Open-Meteo** (built-in integration): no account needed, combines several
  weather models.
- **Met.no** (Home Assistant default): works everywhere, less detailed in
  alpine terrain.

### Consumption forecast

SLEMS learns the consumption from the long-term statistics of Home Assistant
and forecasts today and tomorrow hourly. Recent days count more than older
ones, so changes are followed within days. Heat pumps are forecast from the
outdoor temperature of the day, so a warm day in the heating season
immediately predicts less heating energy. Two optional settings help at the
start:

- **Outdoor temperature**: a temperature sensor with history. Without it,
  SLEMS records the temperature of the weather entity itself; the heat pump
  model then needs about a week before it uses the temperature.
- **House consumption history**: a power sensor of the house consumption with
  existing history (e.g. from another battery integration), used for the time
  before SLEMS recorded its own values.

Recommended setup when switching from another battery integration:

1. Select that integration's house consumption sensor as *House consumption
   history*. SLEMS then learns from the full history right away. Without it,
   SLEMS derives the history from grid and PV only; the battery power before
   SLEMS is unknown, so charging and discharging would distort the learned
   consumption.
2. Select an outdoor temperature sensor with history if one exists. Otherwise
   the heat pump forecast uses the mean of the last days until SLEMS has
   recorded about a week of temperatures.
3. Holidays are currently treated like workdays.

**Forecast accuracy** (card in the overview, sensors *Consumption forecast
accuracy* and *PV forecast accuracy*):

- Consumption: SLEMS recalculates the forecast of each of the last 14 days as
  it would have been made at midnight, with the history up to then, and
  compares it with the measured consumption. Shown are the accuracy of the
  daily energy (100 % minus the mean deviation), the tendency (too high or too
  low), the deviation per hour (how well the course of the day is hit), the
  data basis (days of consumption, days of heat pump data with temperature)
  and tomorrow's forecast with its expected deviation. The heat pump is
  recalculated with the measured temperature of the day, so its share looks
  somewhat better than it was.
- PV: past forecasts are not available from the solar forecast integration,
  so SLEMS records the forecast of each day at its start and compares it with
  the production in the evening; the values become meaningful after about a
  week.

The sensor *House consumption* (used for the forecast and shown in the energy
flow) is calculated as grid + PV − batteries. The smart meter often reports a
change later than PV and batteries; a momentarily negative result is therefore
replaced by the last valid value (for at most 30 seconds, then unknown).

### Consumers

Every consumer needs its own power **and** energy sensor.

- **Included in smart meter**: enable if the consumer is behind the smart
  meter (its consumption is already part of the grid power). Disable for
  consumers on a separate supply.
- **Type**: *heat pump* (heating and hot water, forecast from the weather),
  *heating rod* (e.g. hot water in summer) or *other*. Heat pump and heating
  rod may run at the same time.
- **Control**: none (measurement only), on/off via a switch, or a power set
  point via a number entity in W. For controlled consumers an entity can be
  selected that blocks them externally (SLEMS leaves the consumer alone while
  a switch or binary sensor is on, or while a water heater is in operation
  mode *off*), a priority (1 = highest) and optionally a minimum runtime
  and a minimum pause.
- **Thermostat cycles by itself**: for consumers whose own thermostat
  switches them on and off while they are commanded (e.g. a heating rod that
  measures at the element). Normally a consumer that draws nothing although
  commanded counts as saturated for 15 minutes and keeps its last command.
  With this option SLEMS keeps controlling it: during a pause (no power for
  2 response times, 10–60 s) the batteries get its unused power, and as soon
  as it draws again it gets it back (shown as *thermostat pause*).
- **Control active** (switch per controllable consumer, also on its card in
  the dashboard): off means SLEMS only measures the consumer. Switching it
  off in operating mode *active* sets it to 0 W (or off) once; afterwards
  SLEMS leaves it alone and plans it like an uncontrolled load.
- **With feed-in cap** (select per controlled consumer, on its card in the
  dashboard while the feed-in cap is on): *Count and use* – the consumer
  takes the surplus above the feed-in limit before the batteries, so they need
  less free space; *Only in an emergency* (default) – only what the batteries
  cannot absorb; *Never*. See *Feed-in cap*.

**Battery efficiency** (round trip, AC to AC), one of three sources per
battery:

- *Battery counters* (recommended for the Marstek Venus E 3.0): from the
  lifetime charge and discharge counters of the battery and its state of
  charge: (discharged + stored) / charged. The battery counts itself, fast and
  over its whole operating time, so the value is accurate at once and stable.
  The only assumption is an empty battery at the start of the counters; its
  influence disappears after a few cycles.
- *Learned*: SLEMS adds up the measured battery power itself (every 5
  seconds) from the start of SLEMS. It only counts after about three full
  charge cycles (until then the start value applies), and short power peaks
  between two polls are missed. Recommended for batteries without their own
  counters (read-only batteries from existing entities).
- *Manual*: a fixed value, e.g. from the data sheet.

The efficiency is used where energy is converted: whether the PV surplus will
fill the batteries (charge secured), grid friendly charging, the night
discharge and the state of charge projection. It is **not** used to decide
how many batteries run: for that SLEMS learns a separate loss curve per
battery from the difference of AC and DC power at every power level (fixed
loss of a running inverter plus losses rising with the power). From it the
distribution calculates the number of batteries with the lowest total loss:
at low power one battery, above the break-even point several (see *Batteries*
below). This works with every efficiency source, but only for
batteries that report AC and DC power (Marstek Venus E 3.0).

### Dashboard

SLEMS adds the entry **SLEMS** to the Home Assistant sidebar:

- **Overview**: energy flow between grid, PV, house, every battery (with its
  state of charge) and the consumers; the animated dots run in the direction
  of the flow, faster and on a thicker line the higher the power. Key figures
  (status: problems first – smart meter without values, battery unreadable or
  not responding – otherwise the operating mode; strategy, state of charge, feed-in limit,
  stored energy and capacity, forecasts), and the forecast chart (mean power per half
  hour in kW): *Today* shows PV and consumption
  forecast (dashed), the measured values so far (solid), the planned
  battery charging (light bars) and the measured charging (solid bars), plus the projected total state of charge (dashed)
  and the measured one (solid) with their scale in % on the right. *Tomorrow* shows the forecasts,
  planned charging and state of charge of the next day, continued from
  today's projection. The projection follows the planning: charging only
  with the planned surplus, deficits covered by the batteries, import peak
  shaving and night discharge if enabled. Hovering (on a phone: tapping, a
  tap elsewhere closes it) shows the values of an hour; *Show table*
  switches to a table.
- **Batteries**: state of charge, stored energy and capacity (kWh), power on the grid side
  (AC) with its direction, the SLEMS set point, efficiency, state and
  the *Enabled* switch of every battery (disabling asks for confirmation),
  the cell delta with its balance status, a recommendation for active cell
  balancing and the phase of a running one.
- **Consumers**: measured and planned power, blocked/saturated state, the
  learned response time and the *Control active* switch.
- **Settings**: all settings grouped and editable directly; the *Control*
  card also shows the learned values (current control gain, smart meter
  update interval, battery response time).

A click on a value (tile, box in the energy flow, row of a card) opens the
Home Assistant dialog of its entity with history and settings.

The dashboard follows the language and the light/dark theme of Home
Assistant and works on phones (with little space the batteries in the energy
flow are shown one below the other).

### Batteries

- **Marstek Venus E 3.0**: host/IP, port (502) and Modbus unit ID. The battery
  accepts only **one** Modbus TCP connection. Never let two integrations
  (e.g. SLEMS and Omnibattery) talk to the same battery at the same time.
  Its sensor *AC power* is positive when discharging and negative when
  charging, as the Home Assistant energy dashboard expects for battery power.

  **Search**: when adding a Venus, SLEMS first searches the network of Home
  Assistant for batteries (TCP port 502, then a test read of the state of
  charge; a few seconds) and offers the ones found; batteries already added
  are left out. A battery whose single Modbus connection is held by another
  integration cannot be found.

  **Finding the IP address**: Modbus TCP is only available on the **LAN
  port** of the battery (connect it with a network cable), not over Wi‑Fi. The
  Marstek app does not show the LAN IP address; look it up in your router /
  internet gateway / DHCP server (list of connected devices). Give the battery
  a fixed address there (DHCP reservation), so it does not change later.
  Alternatively scan your network for devices with the Modbus port open, e.g.
  with [nmap](https://nmap.org) (adjust the network to yours):

  ```bash
  nmap -p 502 --open 192.168.0.0/24
  ```

  Every address listed with `502/tcp open` is a Modbus TCP device (nmap shows
  the MAC address when run with `sudo` in the same network, which helps to
  tell several batteries apart). When adding the battery, SLEMS checks the
  connection by reading its state of charge; stop other integrations using
  the battery first.
- **Existing Home Assistant entities (read-only)**: state of charge and power
  sensors of a battery that is controlled by something else. SLEMS never sends
  commands to such a battery. This allows running SLEMS in simulation mode side
  by side with an existing battery integration.

With several batteries SLEMS decides how many run and which: at low power a
single battery is usually more efficient, at high power sharing is. When
discharging the battery with the highest state of charge runs, when charging
the one with the lowest. If the running battery drifts more than *Battery
rotation threshold* (default 5 %) away from the best inactive one, SLEMS
switches, at most once per *Battery rotation minimum interval* (default
15 min) and with a smooth transition: the power moves with *Battery rotation
ramp rate* (default 100 W/s), but never takes longer than *Battery rotation
maximum ramp time* (default 30 s). The conversion losses per power level are learned from the
battery's AC and DC power.

Every battery has an *Enabled* switch. A disabled battery is still measured
(its power is part of the energy balance), but it is neither planned with nor
controlled, and it does not count towards the total state of charge. If it is
discharging in active mode, the other batteries take over within 5 seconds
before it is handed back to its own logic.

### Switching from another battery integration

When SLEMS takes over a battery from another integration (e.g. Omnibattery),
the history of the old sensors (e.g. the charged and discharged energy used in
the energy dashboard) can be carried over to the SLEMS sensors with
[HA Merge Sensor History](https://github.com/mayerwin/HA-Merge-Sensor-History).
It copies states and long-term statistics, so the running totals of the energy
dashboard continue.

- Make a full backup of Home Assistant first; the tool writes directly into
  the database.
- Check the preview: the counters of the Venus (registers 33000/33002) have the
  same value in both integrations, so the total continues without a jump.
- Compare the sign of power sensors: the SLEMS *AC power* is positive when
  discharging.
- Afterwards replace the old sensors in the energy dashboard with the SLEMS
  sensors and delete the old entities once everything looks right.

### Cell delta and active cell balancing

For batteries that report their cell voltages (Marstek Venus E 3.0), SLEMS
shows the *Cell delta* (highest minus lowest cell voltage). With LFP cells the
live value is only meaningful near full charge: in the middle of the charge
the voltage curve is so flat that unequal cells show almost the same voltage.
SLEMS therefore records the *Cell delta at top of charge*: after the highest
cell reached 3.60 V or the BMS ended the charge at 100 %, and the battery then
rested for 60 seconds. The curve is steep there; Marstek cells typically show
about 180 mV from the factory, which is normal. Status: below 200 mV good,
below 230 mV minor, below 250 mV moderate, otherwise high imbalance. From 230 mV the dashboard
recommends active cell balancing.

Active cell balancing (switch *Active cell balancing*, or the button in the
dashboard) follows the Omnibattery balancing blueprint. It can only be started
in operating mode *active*:

1. If the battery is discharging, it first hands over smoothly (as when it is
   disabled). It then leaves the normal planning; the other batteries take
   over.
2. It charges until the highest cell reaches 3.49 V, with the PV surplus
   (before the other batteries), at least 95 W. Without enough surplus the
   other batteries discharge to cover the 95 W.
3. It charges with 95 W until the highest cell reaches 3.60 V, stands by for
   60 seconds and measures the cell delta.
4. Above 30 mV it discharges with 200 W to the retry voltage (3.49 V) and
   repeats from step 3. If the BMS refuses to charge, the retry voltage is
   lowered in steps of 10 mV (down to 3.40 V).
5. At 30 mV or less it discharges with 200 W to 3.48 V and ends.

The discharge of the balancing battery is fed into the grid; the other
batteries do not store it (they keep charging only if they charge anyway).
Its expected charge is taken into account in the expected PV surplus and in
grid friendly charging. The run ends after 24 hours at the latest, and at
once if the battery cannot be read; it pauses outside operating mode
*active* and continues after a restart of Home Assistant. When it ends, the
battery is handed back to its own logic and then returns to the normal
planning. The sensor *Cell balancing phase* shows the phase and the result of
the last run.

### Firmware updates and battery menu

During a firmware update of a Marstek battery there must be no Modbus
communication at all. The menu (⋮) of a battery card offers *Pause
communication (firmware update)*: SLEMS hands the battery back to its own
logic, closes the connection and does not read or send anything for the
*Communication pause duration* (default 20 minutes; switch *Communication
paused*). Afterwards it reconnects by itself; *Resume* ends the pause earlier.
Meanwhile the battery is treated like a disabled one. If the battery itself
reports a running firmware update (state *OTA update*), SLEMS pauses
automatically; as this is only noticed with the next poll, pause manually
before an update.

The same menu enables or disables the battery, starts or cancels the cell
balancing and opens *Details*: model, device name, firmware versions (EMS,
VMS, BMS, communication module; sensor *Firmware*), MAC address, capacity,
charge cycles and the total charged and discharged energy.

### Battery limits and protection

Every controllable battery has these settings (dashboard: *Settings*):

- *Minimum state of charge* (default 12 %) and *Maximum state of charge*
  (default 100 %): SLEMS does not discharge at or below the minimum and does
  not charge at or above the maximum (with 100 % the BMS ends the charge).
  After reaching a limit the battery is used again 2 % away from it, because
  the resting SoC rebounds after a load. The maximum counts as "full" for
  grid friendly charging, the charge secured check and the projection; the
  minimum is the lowest target of the night discharge. Active cell balancing
  ignores the SoC window (it needs the top of the charge).
- *Charge power limit* and *Discharge power limit* (default: the maximum of
  the battery), e.g. 800 W for a plug-in system.

*Temperature charge limit* (off by default, after Omnibattery) limits the
charge power by the battery temperature: above *Charge derating
temperature* (40 °C) it decreases linearly across *Charge derating range*
(10 °C) down to *Charge power at high temperature* (40 %); at or below
*Minimum charging temperature* (0 °C) there is no charging, and within 5 °C
above it the power rises to full again. The Venus reports its internal
temperature, not the cell temperature; the BMS keeps its own protection.
The sensors *Allowed charge power* and *Allowed discharge power* show the
current limit and its reason.

In operating mode *active* SLEMS checks, like Omnibattery, whether every
battery delivers the commanded power. A battery that delivers less than
10 % of a command of at least 100 W (after 30 s in that direction) three
polls in a row first gets all control registers written again; if that does
not help, it is excluded for 5 minutes (like a disabled battery, handed back
to its own logic, the others take over) and then retried. A full battery
that stops charging or a battery at 20 % or less that stops discharging does
not count (the BMS protects it). The Venus also reads its control registers
back after every complete write (first command and every 60 s); a write that
is not confirmed counts as well. The binary sensor *Not responding* and the
dashboard show an excluded battery.

### Problems and notifications

Ongoing problems appear under *Settings → Repairs* and disappear by
themselves when they are solved:

- a battery does not respond (excluded, see above),
- a battery could not be read for more than 5 minutes,
- the smart meter does not report while SLEMS is in operating mode *active*.

The dashboard also shows them: a red note on the battery card (*cannot be
read*, *not responding*), in the energy flow and, for the smart meter, at the
top of the overview. The end of an active cell balancing run (finished, after
24 hours or because the battery could not be read) creates a notification
with the cell delta before and after and the duration; cancelling it
yourself does not. The warnings of the feed-in cap (batteries too small, not
enough time to make room, charge power too low, feed-in above the limit, see
*Feed-in cap*) are notifications too; they disappear by themselves when the
problem is gone.

### Operating mode

The entity *SLEMS Operating mode* switches between:

- **Off** – nothing is planned or sent.
- **Simulation** – forecasts and plans are computed and shown, but no command
  is sent to batteries or consumers. This is the default.
- **Active** – plans are executed: SLEMS sends set points to the batteries
  and switches/sets the consumers. It reacts to every change of the smart
  meter. The entity *Control status* shows whether the control is active or
  paused because the smart meter did not report for a while (60 s or ten
  update intervals; the batteries then follow their own logic until the meter
  is back).

### How the control works

In operating mode *active* SLEMS reacts to every new value of the smart
meter:

1. From the energy balance it calculates which battery power would bring the
   grid power exactly to the target (e.g. 100 W export while charging).
2. It only counts battery commands that the smart meter can already show.
   A new command needs some time until it appears in the meter value; SLEMS
   learns this *response time* and does not count a command twice.
3. It does not jump to the calculated value at once but moves a share of the
   way per cycle, the *control gain*. With 0.5, half of the remaining
   deviation is corrected per cycle. A high gain reacts faster, a too high
   gain overshoots and makes the grid power swing back and forth.

**Automatic control gain** (switch, on by default): SLEMS watches its own
corrections and adjusts the gain between 0.2 and 0.9:

- If the corrections change direction several times in a row without dying
  out, the control swings: the gain is lowered at once (× 0.8).
- If the grid power approaches the target only slowly over many cycles
  without ever overshooting, the gain is raised in small steps (+ 0.05).
- After each change SLEMS waits 30 seconds to see its effect. Normal load
  changes (a kettle, a cloud) are not mistaken for swinging. The learned value
  is kept across restarts.

*Control gain* is the start value of the automatic adjustment; changing it
restarts the adjustment from the new value. With the automatic adjustment
switched off, *Control gain* is used as a fixed value. *Control interval*
(default 1 s) limits how often SLEMS sends commands.

Diagnostic sensors show what SLEMS has learned:

| Sensor | Meaning |
|---|---|
| *Current control gain* | gain used right now |
| *Smart meter update interval* | how often the smart meter reports |
| *Battery response time* | time from a battery command until the smart meter shows it |
| *Planned power* of a consumer, attribute `response_time_s` | time from a command until the consumer's own power sensor reacts |

Switch the automatic adjustment off only if the gain keeps changing
noticeably, e.g. because the smart meter reports very irregularly; then set a
fixed value (0.3–0.5 is a good start).

### Which option when?

All options are optional and can be combined. The charts show the same
example with and without each option: 8 kWp PV on a sunny day, a household
with morning and evening peaks, a 10 kWh battery, from 18:00 until midnight
of the next day. They are calculated with the SLEMS state of charge
projection (hourly means), so they show how SLEMS plans; the real curves
depend on your forecasts. Top: total state of charge; bottom: grid power
(+ import / − export); grey dashed without, coloured with the option.

**Grid friendly charging** (on by default)

![Grid friendly charging](docs/images/grid_friendly_en.svg)

Without it the batteries are full before noon and the whole midday peak goes
to the grid (here 5.7 kW). With it they charge with the surplus above a
feed-in limit and are still full in the afternoon; the peak drops to about
4.4 kW. Useful whenever many PV systems feed in at the same time (grid,
energy community). Switch it off if the batteries should be full as early as
possible, e.g. for backup power.

**Night discharge**

![Night discharge](docs/images/night_discharge_en.svg)

Energy still in the batteries in the morning is fed in over night, down to a
reserve that the PV forecast can refill the next day. Useful if the batteries
are still well charged in the morning (large battery, low night consumption,
summer):

- In an **energy community** your energy is more likely to find a buyer at
  night: during the day most members produce themselves, at night they
  consume. Night discharge shifts part of your surplus from noon to the night.
- It works well together with the **feed-in cap**: the batteries start the
  day with room for the energy above the limit, so SLEMS rarely has to feed in
  shortly before the peak.
- The energy passes the battery twice (charge and discharge losses, about
  10 %), and less energy is left for a power outage until the next charge.

**Import peak shaving** (useful on days with little PV, e.g. in winter)

![Import peak shaving](docs/images/peak_shaving_en.svg)

Without it the battery covers everything until it is empty in the evening;
the morning peak then comes from the grid in full (here 2.1 kW). With it,
below the state of charge threshold the battery only covers the power above
the import limit: the base load comes from the grid, the battery keeps its
energy for the peaks (here 1.5 kW at most). Useful with a power based tariff
or grid fee, or if the grid connection is weak. Somewhat more energy comes
from the grid (here 1 kWh), but it is still in the battery at the end: the
peaks are lower, the energy balance stays about the same.

**Feed-in cap** (if the feed-in is limited, e.g. to 60 % of the peak power)

![Feed-in cap](docs/images/feed_in_cap_en.svg)

Without it the batteries are full at noon and the inverter curtails
everything above the limit (red, here 1.7 kWh). With it SLEMS keeps enough
room free; here the night discharge makes the room and the batteries absorb
the energy above the limit. Needed whenever your grid operator limits the
feed-in; it takes precedence over the other options.

### Grid friendly charging

Charging as soon as there is surplus fills the batteries in the morning, and
the PV feed-in peak around noon then goes to the grid in full. With *Grid
friendly charging* (switch, on by default) SLEMS shifts charging into the
peak:

- From the PV and consumption forecasts it calculates a *Feed-in limit*: the
  highest grid export at which the surplus above it still fills the batteries
  by the end of the day (charge losses, maximum charge power and *Grid
  friendly charging buffer* included; default 1 kWh, a larger buffer lowers
  the limit and makes the batteries full earlier and more reliably when the
  forecast is too optimistic). Without a limit the overview tile shows the
  reason: *off* (grid friendly charging switched off), *none – charge at
  once* (the surplus is not enough) or *no forecast*; the sensor then has no
  value and the reason in its attribute `reason`.
- The batteries only charge with the surplus above this limit; below it the
  power goes to the consumers or to the grid. The highest hours of the day
  are cut, wherever the clouds put them.
- The limit is recalculated continuously from the current state of charge
  and the remaining forecast. If charging falls behind (e.g. more clouds
  than forecast), the limit drops by itself.
- The PV forecast is corrected with today's actual production (diagnostic
  sensor *PV forecast correction*). After a restart SLEMS takes the production
  so far from the statistics of the PV sensor, so nothing is lost.
- As long as the charge is not secured (state of charge below *Battery
  priority below state of charge* or the forecast is not sufficient), SLEMS
  charges at once as before.

How much of the peak can be absorbed depends on the battery size compared to
the day's surplus: on a clear summer day with a large surplus a 10 kWh
battery takes about the top kilowatt of the peak, on days with less surplus
a much larger share.

### Feed-in cap

Some grid operators or regulations only allow a PV system to feed in a share
of its peak power (e.g. 60 %); the inverter curtails everything above it.
With *Feed-in cap* (switch, off by default) SLEMS stores that energy in the
batteries instead of losing it. The limit is *PV peak power* (kWp) × *Feed-in
cap limit* (%), measured at the grid connection point (grid export, after the
house consumption).

- **Planning**: from the PV forecast in its finest resolution (15/30/60 min, so
  short peaks are not averaged away) and the consumption forecast, SLEMS
  calculates until the end of tomorrow how much energy lies above the limit
  and how much free space the batteries need for it. Several peaks per day
  (clouds in between) and peaks today and tomorrow are covered: a cloud dip or
  the night in between, in which the batteries supply the house, makes room
  again; a surplus below the limit does not.
- **Buffer**: *Feed-in cap buffer* (default +20 %, negative values plan with
  less) is added to the energy to absorb, and *Feed-in cap minimum buffer*
  (default 5 % of the PV peak power as energy of one hour, e.g. 0.5 kWh at
  10 kWp) per peak in any case. With *Automatic feed-in cap buffer* SLEMS uses
  the recorded PV forecast errors instead (see *Forecast accuracy*): of the
  days with more PV than forecast, the underestimation not exceeded on 80 % of
  them raises the PV forecast. This needs 14 recorded days; until then the
  fixed buffer applies. The settings show the limit and the buffer in use.
- **Making room**: charging with surplus below the limit only happens as long
  as the space needed later stays free; above the limit the batteries always
  charge. The night discharge (if enabled) stops early enough to leave the
  space. If the house consumption and the night discharge are not enough,
  SLEMS feeds battery energy into the grid before the peak, as late as
  possible: it is planned to be finished one hour before the peak with 70 %
  of the possible power, never above the limit. For this it may exceed
  *Maximum grid export while discharging*.
- **Order during the peak**: the surplus above the limit goes to consumers set
  to *Count and use*, then to the batteries (regardless of battery priority,
  battery share and grid friendly charging), then to consumers set to *Only in
  an emergency*. The feed-in cap takes precedence over grid friendly charging
  (its feed-in limit never lies above the cap), night discharge and battery
  priority; the peak shaving threshold stays a floor for feeding in.
- **Overview**: the tile *Feed-in cap* shows the next peak, the energy the
  batteries have to absorb and, if needed, the energy to feed in before it
  and by when. The day chart shows a dashed line at consumption + limit (the
  PV level above which is capped), the energy above it as a bar on that line
  and the part that would be curtailed in red.
- **Warnings** (overview and notifications): batteries too small for
  the space needed, not enough time or power left to feed in before the peak,
  charge power too low for the surplus above the limit, and feed-in above the
  limit for more than 5 minutes.

Sensors: *Feed-in cap energy to absorb* (on the day of the next peak, with the
peaks, the space needed, the export plan and the buffer as attributes) and
*Feed-in cap export before the peak*.

### Further settings (entities)

- *Vacation* (switch) – the household is away; switch manually or by an automation.
- *Battery priority below state of charge* (default 30 %), *Charge secured
  safety buffer* (default 1 kWh) and *Battery share when charge is secured*
  (default 75 %) – the batteries get all surplus until their charge is
  secured: the state of charge is above the threshold and the expected PV
  surplus of the day covers the energy to fill them plus the safety buffer.
  Afterwards the surplus is split, the consumers' share is distributed by
  priority. Power one side cannot use goes to the other.
- *Grid surplus target while charging* (0–5000 W, default 100 W) – the
  batteries only charge from the surplus above this value.
- *Grid surplus target while discharging* (−1000…+1000 W, default 50 W;
  positive = export, negative = import) – the grid power the discharging
  batteries aim at. Between the two targets the batteries stay idle.
- *Maximum grid export while discharging* (0 up to the sum of the maximum
  discharge power of all batteries, default 5000 W) – discharging never causes
  more export than this. 0 W means never feeding battery energy into the grid,
  the maximum switches the limit off. Below the *grid surplus target while
  discharging* it wins (the dashboard shows a note).
- *Night discharge* (switch, off by default) and *Night discharge reserve*
  (default 25 % of tomorrow's forecast consumption) – over night the batteries
  discharge evenly down to the reserve (usable energy above the minimum state
  of charge of the batteries) until PV production exceeds the
  consumption again, ignoring the discharge grid target (the maximum grid
  export still applies). If tomorrow's PV forecast cannot refill the batteries
  from the reserve, a higher reserve is kept. Requires the consumption
  forecast.
- *Surplus averaging window* (0–300 s, default 5 s, 0 = off) – the grid power
  is averaged; the less favourable of average and current value is used, so
  the control does not overshoot with fluctuating PV.
- *Import peak shaving* (switch) – off by default. When enabled and the total
  state of charge is at or below *Peak shaving state of charge threshold*, the
  batteries only discharge to keep the grid import below *Peak shaving grid
  import limit*. The threshold is an absolute state of charge but cannot be set
  below the minimum state of charge of the batteries; below 20 % the settings
  show how much is left above the minimum. With *Automatic peak shaving limit*
  SLEMS calculates the import limit itself: the lowest one for which the
  expected energy above it, until PV refills the batteries (the forecast
  surplus adds up to the energy back to the threshold, so a little surplus on a
  rainy day does not count), fits into the
  usable energy above the minimum state of charge minus *Peak shaving safety
  reserve* (default 20 %). The expected energy comes from the 5 minute
  statistics of the house consumption of the last days, so short peaks such as
  an oven are included; the limit is recalculated continuously and rises when
  more is used than expected. Above the threshold it is calculated as if the
  threshold were reached, and during the day for the coming evening and night,
  so it shows the limit that will apply. In the settings
  the import limit then shows the calculated value (read only); the sensor
  *Peak shaving import limit in effect* shows the limit used.

## Language

SLEMS is available in English and German. Home Assistant uses two different
language settings:

- **Entity names** (e.g. *House consumption*, *Operating mode*, also the key
  figures in the dashboard) follow the **server language** under
  *Settings → System → General*. They are set when the integration loads, so
  reload SLEMS after changing it.
- Dialogs, menus, states (e.g. *Simulation (read-only)*) and the texts of the
  dashboard follow the language in the **user profile**.

If names stay in the wrong language after an update of SLEMS, restart Home
Assistant (translations are only read at startup) and reload the browser
without cache. Entity IDs such as `sensor.slems_house_consumption` keep the
language of their creation; only the displayed names change.

## Roadmap

Possible extensions:

- Load exclusion and evcc connection: large loads such as a wallbox are not
  covered by the batteries; an evcc load point as controllable or excluded
  consumer, so both do not control the same surplus.
- Regular full charge (e.g. weekly) for the SoC calibration of LFP cells,
  coordinated with grid friendly charging.
- Dynamic electricity tariffs (charging from the grid at low or negative
  prices) and further battery models via the driver interface.

## Development

See [developers.md](developers.md).

## License

GPL-3.0, see [LICENSE](LICENSE). The Marstek Venus register map and control
sequence are based on [Omnibattery](https://github.com/ffunes/Omnibattery) (GPL-3.0).
