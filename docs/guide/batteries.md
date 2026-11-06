# Batteries

[Back to the README](../../README.md#batteries) · [Deutsch](batteries.de.md)

## Marstek Venus E 3.0

Host/IP, port (502) and Modbus unit ID. The battery accepts only **one**
Modbus TCP connection. Never let two integrations (e.g. SLEMS and Omnibattery)
talk to the same battery at the same time. Its sensor **AC power** is positive
when discharging and negative when charging, as the Home Assistant energy
dashboard expects for battery power.

### Remote control

The Venus only follows set points while its remote control is on; Marstek's
register list calls it **RS485 control mode**. Despite the name it does not
need an RS485 cable: SLEMS controls the Venus over the LAN (Modbus TCP) and
switches the remote control on and off itself; there is nothing to set up.

### Search

When adding a Venus, SLEMS first searches the network of Home Assistant for
batteries (TCP port 502, then a test read of the state of charge; a few
seconds) and offers the ones found; batteries already added are left out. A
battery whose single Modbus connection is held by another integration cannot
be found.

### Finding the IP address

Modbus TCP is only available on the **LAN port** of the battery (connect it
with a network cable), not over Wi‑Fi. The Marstek app does not show the LAN
IP address; look it up in your router / internet gateway / DHCP server (list
of connected devices). Give the battery a fixed address there (DHCP
reservation), so it does not change later. Alternatively scan your network for
devices with the Modbus port open, e.g. with [nmap](https://nmap.org) (adjust
the network to yours):

```bash
nmap -p 502 --open 192.168.0.0/24
```

Every address listed with `502/tcp open` is a Modbus TCP device (nmap shows
the MAC address when run with `sudo` in the same network, which helps to
tell several batteries apart). When adding the battery, SLEMS checks the
connection by reading its state of charge; stop other integrations using the
battery first.

## Existing Home Assistant entities

For any battery that another integration brings into Home Assistant; the
control is experimental. First choose the battery's device: SLEMS suggests its
entities (state of charge, power, set points, mode, optional temperature, cell
voltages and energy counters); check and correct them. Leave the device empty
to choose everything by hand. The battery of a hybrid inverter you set up as a
PV system, or a battery of the energy dashboard, comes first and is
preselected (then also as the model); the dashboard's state of charge, energy
counters and power with its sign are taken over.

- **Read only**: SLEMS never sends commands, e.g. to run SLEMS in the
  operating mode **Observe only** side by side with an existing battery
  integration.
- **Set point**: one number entity with the signed power (+charge /
  −discharge, sign can be inverted).
- **Separate charge and discharge power**: one number each, optionally a
  mode select (charge / discharge / standby / automatic, the options are
  assigned in the next step).
- **Script**: a script of yours receives the power as variable `power_w` (W,
  +charge / −discharge, 0 = standby); optionally a release script. This
  covers batteries controlled through actions (services). SLEMS waits until
  the script is done (at most 10 seconds) so its errors are noticed; keep
  it short (no waits).
- **Output**: one number with the output into the house, for a battery with
  **Own PV modules at the battery** (see below).

### Own PV modules at the battery

For a battery whose PV modules feed the house only through the battery's
output, e.g. a plug-in battery (Marstek B2500, Zendure SolarFlow, Anker
Solarbank) or a hybrid inverter. Choose the power of the modules, the output
into the house and the highest output (e.g. 800 W). SLEMS counts the modules
as PV and the battery power as own PV + input − output, so the balance stays
right; do not enter the modules or the output as a PV system as well. SLEMS
points that out and offers to remove them there; the system settings do not
accept them as **PV power** either. With the control **Output** SLEMS sets
output = own PV − charge power and writes it again when the own PV changes.
Without an input from the grid such a battery charges at most what its modules
produce (none at night) and discharges at most the highest output minus its
own PV: at 600 W PV and 800 W output 200 W are left. PV above the highest
output charges it in any case unless it is full. The sensors **Allowed charge
power** and **Allowed discharge power** show the reason `pv`. Its sensors
**Own PV power**, **Output power** and, with an input, **Input power** show
the values. With the state when released **Standby** input and output stay at
0 W, so the battery charges from its modules only. Use output and input
entities the device does not store permanently: frequent set points wear out
its flash memory.

A device with an AC input (e.g. Zendure SolarFlow with **inputLimit** and
**acMode**) gets the **Input from the grid** sensor, and with the control
**Output** the number for the input and optionally a mode select (charge:
input, discharge: output). It then charges beyond its own PV from the grid
(input = charge power − own PV, output 0) and takes part in charging from
the grid. Without an input it is left out there.

**Passes the PV through when full**: switch it on if the device feeds its
PV into the house when full whatever the output set point. Otherwise the
device curtails its modules once the battery takes less than PV − output
(full, at the top of the charge, limited by temperature), and the PV
sensor shows only what is left. SLEMS then raises the output in steps
(50 W, doubled as long as the PV follows) to find the PV again, and
keeps the output above what the battery takes. Only the **Feed-in cap**
curtails such a battery on purpose: when neither the batteries nor the
consumers can take the surplus above the limit, the batteries with own PV
lower their output until the export is at the limit; this works only with
the control **Output**. The sensor
**Curtailed own PV** shows how much they curtail.

**PV forecast of the modules** (optional): the forecast entry of the
battery's modules (e.g. a Forecast.Solar plane of the balcony). It counts
towards the PV forecast of the installation, also if it is not chosen in
the system settings. In the forecast of the state of charge, the feed-in
cap, the price plan, the night discharge (refill the next day), the
automatic limit of peak shaving and the simulation the battery charges at
most this forecast, and only from it; without the entry it does not charge in them, and its own
PV is left out of the PV correction (actual / forecast).

Optionally a **remote control** switch or select (e.g. **Manual battery
control** of Omnibattery) is switched on before the first set point and off
when SLEMS releases the battery to **Automatic**, so it controls itself again;
leave it empty if you switch it yourself. For controlling, the battery power
sensor is required (with own PV modules SLEMS uses own PV + input − output
instead).
**Minimum time between commands** suits integrations with a rate limit (often
cloud based), **Repeat set point every** batteries that fall back to their own
logic without new commands. Switch off the battery's own control (e.g. its
zero export) in its integration, otherwise both control at the same time.

### Response time

From a set point until the smart meter shows it, the path adds up: the
integration writing the command (often in its own polling cycle, behind a
cloud also with a rate limit), the battery itself and the smart meter's update
interval. SLEMS learns the response time per battery (**Details** of the
battery), and apart from it the longer times of a start out of standby and of
a change of direction (charging ↔ discharging), which the control expects for
such steps: a change of direction stops at once and only then starts the other
direction. Measured on one installation with the same Marstek Venus E 3.0 and
the smart meter read over Modbus: about 0.5–0.9 s directly over Modbus, about
1.8 s until the new direction of a change of direction, about 2.7 s out of
standby (the inverter does nothing for about 2 s, however long it was idle),
about 4 s through Omnibattery (entities); batteries behind a cloud are usually
slower still. The learned time is the median of the last measurements; a
measurement is dropped when the smart meter moved before the battery could
react (see [Measuring response time and
phase](#measuring-response-time-and-phase)) or much more than the step
(another device switched at the same moment), and commands still on their way
count in, so the many corrections of the control in a row do not make the time
too short. A start right after a stop does not count as a start. A slower
battery means more grid exchange after load changes and a coarser control;
together with faster batteries, those take the changes first (see
[distribution between batteries](#distribution-between-batteries)). With cell
voltages SLEMS uses gentle charging near the top, the cell delta and cell
balancing as for a Venus (LFP cells). This control has been tested with
simulated entities and with a Marstek Venus E 3.0 through Omnibattery; please
report your experience with other devices in an
[issue](https://github.com/gojux/SLEMS/issues).

### Directly or through another integration?

Where SLEMS supports a battery directly (Marstek Venus E 3.0 over Modbus),
that is the better choice: it reacts faster, has fewer parts in between and
gives SLEMS more data (cell voltages, counters, firmware, OTA state). Through
another integration makes sense if the battery is not supported directly, if
that integration must keep the battery's only Modbus connection, or if no
network cable reaches it. The difference matters most with fast load changes
(cooking, kettle).

### Another battery model?

If it can be controlled over Modbus TCP, try a [battery from a Modbus
profile](battery-modbus-profile.md). If you would like SLEMS to support a
model directly, please open an [issue](https://github.com/gojux/SLEMS/issues)
with manufacturer and model, interface (Modbus TCP/RTU, local API, cloud
only), a register list or API documentation if available, and the Home
Assistant integration that exists for it.

### Example: a battery through Omnibattery

For a battery model SLEMS does not support directly,
[Omnibattery](https://github.com/ffunes/Omnibattery) can do the communication
and SLEMS the control:

1. In Omnibattery switch on **Manual battery control** for the battery, so its
   own control stops.
2. In SLEMS add the battery as **Existing Home Assistant entities** and choose
   the Omnibattery device. SLEMS suggests the entities (state of charge,
   power, charge and discharge power, operating mode, cell voltages,
   counters); check them.
3. Switch off automations that control the battery, so only SLEMS sends
   set points.

## Battery from a Modbus profile (experimental)

A battery that can be controlled over Modbus TCP, described by a profile
created from its manual with an AI prompt; see [Battery from a Modbus
profile](battery-modbus-profile.md).

## State when released

What SLEMS leaves the battery in when it stops controlling it, e.g. in
operating mode **Off**, without smart meter values or when the battery is
removed from SLEMS. **Automatic** (default): the battery's own logic takes
over again (e.g. its zero export); for a battery from entities this needs a
remote control entity, a mode option for automatic or a release script.
**Standby**: the battery stays idle at 0 W until something else takes it over;
a Venus keeps its remote control on for this.

## Distribution between batteries

With several batteries SLEMS decides how many run and which: at low power a
single battery is usually more efficient, at high power sharing is. When
discharging the battery with the highest state of charge runs, when charging
the one with the lowest. If the running battery drifts more than **Battery
rotation threshold** (default 5 %) away from the best inactive one, SLEMS
switches, at most once per **Battery rotation minimum interval** (default 15
min) and with a smooth transition: the power moves with **Battery rotation ramp
rate** (default 100 W/s), but never takes longer than **Battery rotation maximum
ramp time** (default 30 s). The conversion losses per power level are learned
from the battery's AC and DC power.

Batteries that react at clearly different speeds (learned response times,
the slowest at least twice and 3 s slower than the fastest, e.g. a Venus over
Modbus next to a battery over a cloud integration): changes of the total
power go to the fastest battery first and then move over to the efficient
split with the response time of the slower ones. No battery works against the
direction of the total.

Every battery has an **Enabled** switch. A disabled battery is still measured
(its power is part of the energy balance), but it is neither planned with nor
controlled, and it does not count towards the total state of charge. If it is
discharging in active mode, the other batteries take over within 5 seconds
before it is handed back to its own logic.

## Battery wear costs

Optional in the battery configuration: purchase price and rated cycles
(manufacturer). From them SLEMS computes the wear costs per kWh stored and
delivered again: price ÷ (cycles × usable capacity), e.g. 1200 € ÷ (6000 ×
5 kWh) = 4 ct/kWh. Price aware actions that cost an extra cycle (charging
from the grid) only happen if they earn more than that. A price of 0 means no
wear costs: field measurements of home storage systems show that they age
mostly with time, temperature and state of charge and often reach their end
of life before their rated cycles. Without the values SLEMS assumes a low
estimate of 1 ct/kWh; **Details** of the battery then say so. The sensor **Wear costs**
shows the value.

## Battery efficiency

Round trip efficiency (AC to AC), one of three sources per battery:

- **Battery counters** (recommended for the Marstek Venus E 3.0 and for
  batteries from entities with energy counters): from the lifetime charge and
  discharge counters of the battery and its state of charge: (discharged +
  stored) / charged. The battery counts itself, fast and over its whole
  operating time, so the value is accurate at once and stable. The only
  assumption is an empty battery at the start of the counters; its influence
  disappears after a few cycles.
- **Learned**: SLEMS adds up the measured battery power itself (every 5 seconds)
  from the start of SLEMS. It only counts after about three full charge cycles
  (until then the start value applies), and short power peaks between two
  polls are missed. Recommended for batteries without their own counters.
- **Manual**: a fixed value, e.g. from the data sheet.

The efficiency is used where energy is converted: whether the PV surplus will
fill the batteries (charge secured), grid friendly charging, the night
discharge and the state of charge projection. It is **not** used to decide how
many batteries run: for that SLEMS learns a separate loss curve per battery
from the difference of AC and DC power at every power level (fixed loss of a
running inverter plus losses rising with the power). From it the distribution
calculates the number of batteries with the lowest total loss: at low power
one battery, above the break-even point several (see **Distribution between batteries**). This
works with every efficiency source, but only for batteries that report AC and
DC power (Marstek Venus E 3.0).

## Switching from another battery integration

When SLEMS takes over a battery from another integration (e.g. Omnibattery),
the history of the old sensors (e.g. the charged and discharged energy used in
the energy dashboard) can be carried over to the SLEMS sensors with [HA Merge
Sensor History](https://github.com/mayerwin/HA-Merge-Sensor-History). It
copies states and long-term statistics, so the running totals of the energy
dashboard continue.

- Make a full backup of Home Assistant first; the tool writes directly into
  the database.
- Compare the sign of power sensors: the SLEMS **AC power** is positive when
  discharging.
- Afterwards replace the old sensors in the energy dashboard with the SLEMS
  sensors and delete the old entities once everything looks right.

## Cell delta and active cell balancing

For batteries that report their cell voltages (Marstek Venus E 3.0), SLEMS
shows the **Cell delta** (highest minus lowest cell voltage). With LFP cells the
live value is only meaningful near full charge: in the middle of the charge
the voltage curve is so flat that unequal cells show almost the same voltage.
SLEMS therefore records the **Cell delta at top of charge**: after the highest
cell reached 3.60 V or the BMS ended the charge at 100 %, and the battery then
rested for 60 seconds (standby, including the about 13 W a Venus draws
itself). It is measured once per charge: while the battery stands full the
cells relax and the delta keeps falling without the balance getting better, so
the next measurement follows only after the battery was discharged out of the
top range (highest cell below 3.49 V); after a restart of SLEMS, too. The
curve is steep there; Marstek cells typically show about 180 mV from the
factory, which is normal. Status: below 200 mV good, below 230 mV minor, below
250 mV moderate, otherwise high imbalance. From 230 mV the dashboard
recommends active cell balancing.

Active cell balancing (switch **Active cell balancing**, or the button in the
dashboard) follows the Omnibattery balancing blueprint. It can only be started
in operating mode **active**:

1. If the battery is discharging, it first hands over smoothly (as when it is
   disabled). It then leaves the normal planning; the other batteries take
   over.
2. It charges until the highest cell reaches 3.49 V, with the PV surplus
   (before the other batteries), at least 95 W. Without enough surplus the
   other batteries discharge to cover the 95 W.
3. It charges with 95 W until the highest cell reaches 3.60 V, stands by for
   60 seconds and measures the cell delta.
4. Otherwise it discharges with 200 W to the retry voltage (3.49 V) and
   repeats from step 3. If the BMS refuses to charge (less than 30 W instead
   of 95 W, e.g. because it reports full at about 3.55 V), it does not measure
   (the delta below 3.60 V is smaller and not comparable), discharges to the
   retry voltage (3.49 V, or 10 mV below the voltage it refused at) and tries
   again.
5. It discharges with 200 W to 3.48 V and ends when the delta is in the normal
   range (at most 190 mV), when it has not fallen by 2 mV for 6 hours, or
   after 24 hours.

The BMS bleeds the high cells passively, only a few mV per day; a run lowers
a large delta step by step but does not reach the 0 mV of a lab charger. If
the last measurement is already in the normal range, the dashboard says so
before the start.

The discharge of the balancing battery is fed into the grid; the other
batteries do not store it (they keep charging only if they charge anyway). Its
expected charge is taken into account in the expected PV surplus and in grid
friendly charging. It stops with an error if the battery cannot be read or the
final discharge has not finished 2 hours after the 24 hours; it pauses outside operating mode **active** and
continues after a restart of Home Assistant. When it ends, the battery is
handed back to its own logic and then returns to the normal planning. The
sensor **Cell balancing phase** shows the phase and the result of the last run.

## Self-test of a battery

The self-test checks whether a controlled battery does what SLEMS commands and
whether its values are right. It is meant above all for batteries through Home
Assistant entities, where sign, units and delays of the integration often
differ from what is expected. When adding a battery, **Run a self-test after
saving** is preselected; it starts as soon as SLEMS is in operating mode
**Active**. It can be started at any time from the battery menu (⋮ → **Run
self-test**) or with the switch **Self-test**.

Steps, about 5–10 minutes together:

1. A discharging battery first hands over smoothly.
2. 60 seconds of rest (0 W): the reference.
3. Charging with a quarter of the smaller maximum power, at least 300 W, at
   most 800 W, until smart meter and battery show at least half of it, then 45
   seconds more and until the charge counter moved (at most 4 minutes).
4. 60 seconds of rest, then discharging like the charging.
5. Hand-back to the battery's own logic straight from the discharge (or the
   last step), watched at the smart meter for up to 60 seconds while the
   others keep holding; then normal planning.

If the battery may not charge right now (e.g. full) or not discharge (empty),
that step is left out. Below 20 % or above 90 % (at least 5 percentage points
inside the battery's state of charge window) a battery may hold back on its own
(e.g. a discharge limit in its app). Then the test makes room: below 20 % it
charges on after the charging step until 20 %, above 90 % it discharges first
and on until 90 % (with up to 800 W, at least the test power), then the other direction follows. Meanwhile nothing is
measured and the other batteries and the consumers run normally (the other
batteries may cover the charging). Not reached within 30 minutes, the other
direction is left out and the check **Room for both directions** is unclear. While measuring, the other batteries and the consumers
keep their last set points, so the smart meter shows the effect of the tested
battery alone; a little energy may come from the grid or go into it meanwhile.
SLEMS also takes the measured power of the other batteries out.

Checked are:

- **Effect at the smart meter**: does it move by at least half of the command
  in the right direction? If it does not move, the command does not arrive
  (wrong entity or register, remote control not enabled, battery not behind
  the smart meter); the other way round: the set point is inverted; more than
  twice the command: the set point has a wrong factor. If the smart meter runs
  the wrong way or more than twice as far for 10 seconds (and the battery's
  own power confirms it), the test ends at once and SLEMS releases the
  battery instead of running on with a wrong set point.
- **Sign and size of the battery power**: does the reported power change like
  the smart meter? Finds an inverted sign and a wrong factor (e.g. kW instead
  of W); a smaller difference points to the DC instead of the AC power.
- **Response time** until the effect at the smart meter (above 60 seconds:
  slow).
- **Energy counters**: the charge counter rises while charging, the discharge
  counter while discharging, not the other one and not by a multiple. Coarse
  counters that do not move in the short test are no error.
- **State of charge**: between 0 and 100 %, without jumps, not against the
  direction.
- **Hand-back** to its own logic: the battery confirms it, and the smart
  meter shows it. With the release state **Standby**, battery and smart meter
  go back to the rest; if the battery stays at the test power, it is still
  remote controlled (error). With **Automatic**, the battery leaves the test
  power; if its own logic happens to want about the same power, the check is
  unclear.

If the smart meter measures each phase, the test also learns the battery's
[phase](../../README.md#phase-of-batteries-and-consumers): the phase on which the steps show up.

If the house consumption changes a lot during the test, the checks at the smart
meter are **unclear** instead of failed; repeat the test at a quieter time
then. The same happens with a nearly full or empty battery, which can only
charge or discharge a little: repeat the test at a medium state of charge. A
test that found nothing wrong but could not check everything ends **partly
unclear**, shown as a hint, not as a problem. The result appears as a
notification, on the battery card (if something is unclear, conspicuous or
wrong) and with an explanation per check under ⋮ →
**Details** → **Show test results**; the sensor **Self-test result** keeps it. It stops outside
operating mode **Active**, with paused communication, when the smart meter does
not report or the battery cannot be read for 30 seconds; a restart of Home
Assistant ends it too. Only one self-test runs at a time, not during a cell
balancing run.

## Measuring response time and phase

A measurement of the response time does not count when the smart meter moved
before the battery could react at all: another device moved the grid then.
SLEMS measures this earliest response (the **dead time**) per battery with a
short probe step:

1. SLEMS waits until the grid is quiet: the house load without batteries
   varies by less than 50 W (standard deviation) for 10 seconds, no battery
   set point changed for 3 seconds and no consumer was commanded for a minute.
   The battery has to run (charge or discharge).
2. The other batteries and the consumers keep their set points (as in the
   self-test). After one second of reference the battery gets 500 W more or
   less for about 3 seconds (at least 300 W, within its allowed power), then
   its set point again, watched for 3 seconds. With a surplus it charges more
   or discharges less, with import the other way round; nearly full or empty
   or at its power limit, the step goes the other way. The battery never
   changes its direction, it keeps running with at least 100 W. At the grid
   this costs about 0.4 Wh per attempt.
3. Three attempts with a pause of 10 seconds. The step and the step back are
   one measurement each; every report of the smart meter counts. The dead time
   of a measurement is the time of the last report that did not move by 10 %
   of the step yet (a lower bound, as the meter reports only about every half
   second). A measurement is unclear when the grid was restless before, moved
   the other way or beyond 1.5 times, or did not reach 60 % of the step.

The dead time is the median of the clear measurements. A later measurement
whose smart meter moves by half the step earlier does not count (a start or a
reversal adds the driver's extra time). The times until 60 % count as
measurements of the battery's response time. Without a probe step the driver's
value (Marstek Venus 0.45 s) or half the learned response time applies as
before. If the smart meter reports its phases as fast as the total power, the
step also shows the battery's [phase](../../README.md#phase-of-batteries-and-consumers).

SLEMS measures by itself as soon as the grid is quiet:

- for a new battery (also after the update to this version),
- after a change of the battery's connection, profile, entities or control
  type, its firmware, the smart meter (source, entity, phase entities,
  firmware of its device, version of a custom integration) or, for a battery
  from entities, of the controlling integration; SLEMS drops the response
  times learned for the battery then (Home Assistant updates do not count),
- when more than half of the last 20 measurements were too early,
- at the latest 90 days after the last probe step.

By hand, ⋮ → **Measure response time and phase** or the battery's button of the
same name starts it; it waits for a quiet grid too. Nothing is measured outside
operating mode **Active**, during a self-test or cell balancing run, with an
active feed-in cap, while the BMS limits the battery, when the smart meter does
not report, or for batteries that accept set points less often than every
second. If the grid is not quiet for 15 minutes or a measurement stays unclear,
SLEMS tries again after an hour. The result is under ⋮ → **Details** (**Dead
time**) and in the diagnostics.

Hints in the log (level info, each at most once a day; they start no probe
step): the smart meter reports for a day more than half faster or slower than
at the last probe step; the learned response time differs by more than 30 %
from the probe step's or scatters widely; the battery did not deliver its set
point twice within a day. A probe step by hand may help then.

## Firmware updates and battery menu

During a firmware update of a Marstek battery there must be no Modbus
communication at all. The menu (⋮) of a battery card offers **Pause
communication (firmware update)**: SLEMS hands the battery back to its own
logic, closes the connection and does not read or send anything for the
**Communication pause duration** (default 20 minutes; switch **Communication
paused**). Afterwards it reconnects by itself; **Resume** ends the pause earlier,
**Extend pause** adds the pause duration to a running pause (as does switching
**Communication paused** on again, e.g. in an automation).
Meanwhile the battery is treated like a disabled one. If the battery itself
reports a running firmware update (state **OTA update**), SLEMS pauses
automatically; as this is only noticed with the next poll, pause manually
before an update.

The same menu enables or disables the battery, starts or cancels the cell
balancing and opens **Details**: model, device name, firmware versions (EMS,
VMS, BMS, communication module; sensor **Firmware**), MAC address, capacity,
charge cycles, the total charged and discharged energy, the last full charge,
the current cell delta, when the cell delta at top of charge was last measured
and the phase (see **Phases**). **Open device in Home Assistant**
leads to the device page of the battery with all its entities.

The last full charge (sensor **Last full charge**) is the moment the battery
last reached the top: the highest cell at the charge stop voltage, the SoC the
BMS reports when full, or the BMS ending the charge below that (see the SoC
window above), also if it was discharged again right away. A new full charge
counts once the battery has left the top (highest cell below 3.40 V, without
cell voltages below 97 %). LFP batteries recalibrate their state of charge
only when full, so it should happen regularly.

**Regular full charge** (settings group of the same name, on by default): a
battery not full for longer than **Full charge at the latest every** (default 7
days), or never full since SLEMS records it, is charged first until it was
full once. Only one battery at a time, the one whose last full charge is the
oldest (never full first, then by name). The split between batteries and
consumers does not change; the battery only gets the charge power before the
other batteries. For this it may exceed its maximum SoC once; afterwards the
limit applies again. If it does not get full for lack of PV, it stays first
the next days. When discharging it is spared while all other batteries have
more than 50 % and can deliver the power, so it starts the next day higher.
Once full it rests 90 seconds, so the cell delta at the top is measured. Its
card shows **full charge due** meanwhile; after 14 days without a full charge
the overview names the battery.

## Battery limits and protection

Every controllable battery has these settings (dashboard: **Settings**):

- **Minimum state of charge** (default 12 %) and **Maximum state of charge**
  (default 100 %): SLEMS does not discharge at or below the minimum and does
  not charge at or above the maximum (with 100 % the BMS ends the charge).
  After reaching a limit the battery is used again 2 % away from it, because
  the resting SoC rebounds after a load. The maximum counts as "full" for grid
  friendly charging, the charge secured check and the projection; the minimum
  is the lowest target of the night discharge. Active cell balancing ignores
  the SoC window (it needs the top of the charge).

  Near the minimum the BMS may stop discharging earlier than set, e.g. when a
  weak cell sags under a high load. If a battery delivers nothing below 20 %
  for three polls in a row although SLEMS asks it to discharge, SLEMS first
  limits its discharging to half the power (at least 300 W), as the BMS
  stops later under a lower load; if it delivers nothing at that power
  either, SLEMS blocks its discharging. Both (reason `bms` at **Allowed
  discharge power**) last until it was charged or its SoC rose by 2 %; the
  other batteries and the grid take over.

  At the top the BMS ends the charge as soon as one cell is full; with unequal
  cells that can be well below 100 % (some firmware keeps the SoC then, e.g.
  at 88 %). If a battery whose highest cell is at 3.45 V or more (without cell
  voltages: at 98 % or more) takes nothing for three polls although SLEMS asks
  it to charge, it counts as full: SLEMS blocks its charging (reason `bms` at
  **Allowed charge power**, a note in the dashboard) until it discharged or
  its highest cell fell below 3.40 V (without cell voltages: its SoC fell by
  2 %). A battery that alternates between charging and standby every few
  seconds counts as charging again only after three charging polls in a row.
  Such an early end often comes from a large cell delta; it is measured at
  that moment (see cell delta).
  Independently, the control counts a battery that measurably does not follow
  its set point with its measured power, so the house consumption and the
  consumption forecast do not contain power that does not flow.
- **Charge power limit** and **Discharge power limit** (default: the maximum of
  the battery), e.g. 800 W for a plug-in system.
- **Minimum power**: no charging or discharging with so little power that it
  does not pay off. Below the fixed loss of the inverter (Venus about
  35–48 W) charging even discharges the battery.
  - **Automatic, energetic**: charging only where it stores anything;
    discharging without a threshold (it always delivers, only with a worse
    efficiency).
  - **Automatic, economic** (default): charging with P pays off when the
    stored energy is worth more than its feed-in: efficiency of charging with
    P × import price × typical discharge efficiency > feed-in credit (Venus
    with 19.8 / 7 ct: about 60–70 W). With the charge secured (the battery
    gets full anyway) small charge powers pay off only from the typical
    efficiency: charging later with more power is better. Discharging with P
    pays off when import price × efficiency of discharging with P > feed-in
    credit / typical charge efficiency (Venus: from about 25 W, practically no
    limit). Without a tariff or with a credit of 0 or less: like energetic.
  - **Manual**: the **Minimum power** in W for both directions.
  - **Off**.

  Both automatic modes need the losses below 300 W, which SLEMS learns per
  direction in 50 W steps from 40 W (diagnostics, `min_power`); until then
  there is no threshold. With **No negative grid surplus target** there is no
  discharge threshold. A battery starts only at the threshold, runs on down to
  25 W below it (never below the fixed loss; with the threshold there it
  starts 25 W above) and at least its learned start time. The gap saves
  starts when the surplus moves around the threshold (16 % in a debug log, a
  wider one hardly more). While the minimum power holds the batteries back,
  the **Strategy** shows **Below minimum power**; the small surplus is fed in,
  a small deficit drawn. Not held back with the feed-in cap, grid charging,
  price hold, battery export, night discharge, peak shaving, full charge,
  forced charging, self-test and probe step.

**Temperature charge limit** (off by default, after Omnibattery) limits the
charge power by the battery temperature: above **Charge derating temperature**
(40 °C) it decreases linearly across **Charge derating range** (10 °C) down to
**Charge power at high temperature** (40 %); at or below **Minimum charging
temperature** (0 °C) there is no charging, and within 5 °C above it the power
rises to full again. The Venus reports its internal temperature, not the cell
temperature; the BMS keeps its own protection. The sensors **Allowed charge
power** and **Allowed discharge power** show the current limit and its reason.

Near the top of the charge SLEMS charges gently (always, after Omnibattery's
full charge voltage taper): once the highest cell reaches 3.48 V a battery
charges with at most 200 W until the cell falls below 3.44 V again. At a low
current the BMS balances the cells passively before the highest cell ends the
charge, so the batteries get really full with a smaller cell delta, and the
cells see less voltage peak and heat. It only affects the last one or two
percent; the surplus meanwhile goes to the other batteries or the consumers.
The sensor **Allowed charge power** has the reason `top`; batteries without cell
voltages are not limited.

In operating mode **active** SLEMS checks, like Omnibattery, whether every
battery delivers the commanded power. A battery that delivers less than 10 %
of a command of at least 100 W (after 30 s in that direction) three polls in a
row first gets all control registers written again; if that does not help, it
is excluded for 5 minutes (like a disabled battery, handed back to its own
logic, the others take over) and then retried. A battery at the top that stops
charging or a battery at 20 % or less that stops discharging does not count
(the BMS protects it, see the SoC window). The Venus also reads its control registers back after
every complete write (first command and every 60 s); a write that is not
confirmed counts as well. The binary sensor **Not responding** and the dashboard
show an excluded battery.
