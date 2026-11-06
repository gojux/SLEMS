# Consumption forecast

[Back to the README](../../README.md#consumption-forecast) · [Deutsch](consumption-forecast.de.md)

## How the forecast is made

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

## Public holidays

Public holidays count as weekend days, in the learned profile and in the
forecast: those of the country set in Home Assistant and of the state or
canton selected as **Public holidays** in the system settings. When the
system settings are opened and no region is stored yet, SLEMS suggests one
from the location of Home Assistant: a single request to Nominatim
(OpenStreetMap) with the coordinates rounded to about 100 m, the service the
onboarding of Home Assistant uses for the country as well. With **National
holidays**, or if no region is found, only the holidays of the whole country
count (in Switzerland those kept in at least half of the cantons).

## Switching from another battery integration

Recommended setup:

1. Select that integration's house consumption sensor as **House consumption
   history**. SLEMS then learns from the full history right away. Without it,
   SLEMS derives the history from grid and PV only; the battery power before
   SLEMS is unknown, so charging and discharging would distort the learned
   consumption.
2. Select an outdoor temperature sensor with history if one exists. Otherwise
   the heat pump forecast uses the mean of the last days until SLEMS has
   recorded about a week of temperatures.

## Forecast accuracy

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

## House consumption

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
