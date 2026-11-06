# Tariffs and prices

[Back to the README](../../README.md#tariffs-optional) · [Deutsch](tariffs.de.md)

## Entering a tariff

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

## Import and export as YAML

Instead of entering the lines, **Add tariff →
Paste YAML** takes a tariff file (format `slems-tariff` with a version
number; prices net, plus optional supplier, validity from/to and source).
**Export as YAML** in the menu of the lines shows a tariff as such a file, e.g.
to save it or to use it in another SLEMS. An AI can turn a bill into such a
file: [prompt and instructions](../tariff-prompt.md). A file of a newer
format version is refused with a hint to update SLEMS; older versions are
converted.

## Templates

**Add tariff → From templates** offers tariffs with public list
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

### Own templates

YAML files in the folder `slems_tariff_templates` of your Home
Assistant configuration. Suggest a template for everyone as an
[issue](https://github.com/gojux/SLEMS/issues) with the source of the prices.

### Switzerland

**Add tariff → From ElCom** queries the open data of
ElCom (ld.admin.ch) for the tariff of your municipality and consumption
category (energy, grid and levies, excluding VAT, as the mean of the
category; high and low tariff times are averaged); the next year, published
in September, is offered as new prices, in the background only with
**Fetch market prices** switched on.

### Austria

The grid fees of every grid area (network level 7, from the grid fee
ordinance, with the reduced price from April to September 10–16 h)
and the federal levies; the metering fee is entered at its legal maximum
(your grid operator may charge less) and municipal levies (e.g. the
Gebrauchsabgabe in Vienna) are not included.

### Dynamic tariff (Austria and Germany)

A general template **Dynamic tariff** (energy at the day-ahead price per quarter hour);
enter the markup and the monthly fee of your contract afterwards in its
lines, as suppliers of dynamic tariffs mostly publish them only per postcode.

### Germany

The federal levies
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

## Where the energy comes from

SLEMS records grid import and feed-in per
quarter hour itself from every grid value (kept for 400 days), so import and
feed-in within an hour do not cancel out and dynamic prices apply per quarter
hour. For exact totals choose the energy counters of your smart meter
(**Grid import energy (counter)** / **Grid export energy (counter)**) in the SLEMS
options: the recorded quarter hours are then scaled to the counter of each
hour. Hours before the recording use the counters, without counters the
hourly mean of the grid power (less exact).

## Power price

A line with **€ per kW and month (peak power)**
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
[Import peak shaving and power price](control.md#import-peak-shaving-and-power-price).

## Dynamic tariffs and market prices

A line can also follow the day-ahead
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

## Price aware control

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

### Estimated prices

The day-ahead prices of the next day are published
around 13:00. For the quarter hours after the last known price the price aware
control plans with estimates: per time of day the median of the last 14 days
of the same kind (working day, or weekend and public holiday of the bidding
zone; in Switzerland the days off in at least half of the cantons), its swing around the mean of the day halved as a safety margin. So the night and a cloudy next day are planned
before the real prices are known; a new plan follows as soon as they are.
Only known negative prices start the plan for negative prices. The price
chart shows the estimates faint.

### Charge batteries from the grid

**Charge batteries from the grid** (switch, off by default; only with price
aware control): SLEMS plans until PV refills the batteries (as above; at most
until the last known price, after it with the estimated ones) which hours the
batteries cover, which they hold and in which they charge from the grid. A
smaller PV surplus before is part of the plan: the batteries take it, and
only what is still missing for the expensive hours is charged from the grid
(e.g. at a cheap noon before an expensive evening after a dull day). A charge must pay after the
charge and discharge losses, the [wear costs](batteries.md#battery-wear-costs) and the
minimum gain; of equal plans the later charge wins, as home storage ages
mostly with time at a high state of charge. Limits: **Highest state of charge
from the grid** (default 90 %), **Highest grid charge power** (0 = the charge
power of the batteries), the import limit of peak shaving and the space the
feed-in cap needs when PV takes over. Your own PV surplus is taken first. The plan includes the price hold; the strategy shows **Grid charging** with the energy, the
start and the expected saving, the day chart the planned charging.

### Surplus hours and negative prices

With price aware control and a
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

### Feed in from the batteries

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

### Measured saving

The sensor **Saving price control** adds up per month what
the price aware control saved: after every night (or other run until PV takes
over) in which it held, charged or fed in, SLEMS compares the recorded costs
with the same hours played *as usual* through the battery model, starting from
the measured state of charge. Nights without such an action count nothing.
The tariff comparison shows the value next to the estimate.
