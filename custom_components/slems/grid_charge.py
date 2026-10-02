"""Charging the batteries from the grid and feeding in from them when it pays
(both optional, price aware).

Planned hour by hour until PV refills the batteries, at most until the end of
the known prices, with dynamic programming over the stored energy (steps of
``STEP_PCT`` of the capacity). In each hour with a deficit the batteries may
cover all of it (exactly, levels in between interpolated), a part of it in steps, nothing
(*hold*), or charge from the grid. Costs:

* import price × grid import of the hour,
* per kWh charged from the grid: the wear costs and the minimum gain (so a
  charge must save more than that, after both conversion losses),
* per kWh the batteries could have delivered but kept: the minimum gain (as
  the price hold, a shift must be worth it),
* a tiny amount per hour of earlier charging, so of equal plans the one that
  charges later wins: home storage ages mostly with time at a high state of
  charge, not with the cycle itself,
* feeding in from the batteries beyond the deficit (optional): the credit of
  the hour is a gain, the minimum gain a cost; not below the export floor
  (morning reserve) and at most the export power.

Energy left at the refill is worth nothing (PV fills the batteries anyway);
if the plan ends before (prices unknown), it is worth the lowest import
price of the plan (at least 0), after the discharge losses.

The plan is made again with every new state of charge, so deviations of the
forecast correct themselves; only its first hour is acted on.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import math

from homeassistant.util import dt as dt_util

PERIOD = timedelta(hours=1)
STEP_PCT = 1.0
MIN_STEP_WH = 25.0
# Cost (ct) per kWh and hour of charging earlier than needed (tie breaker).
EARLY_CT = 0.01


@dataclass(frozen=True)
class ChargeBattery:
    capacity_wh: float
    stored_wh: float
    floor_wh: float
    # Highest stored energy reached by charging from the grid.
    grid_max_wh: float
    # Charge power of the batteries (from the grid only up to ``grid_charge_w``).
    max_charge_w: float
    max_discharge_w: float
    # One way efficiency (AC to stored and back).
    efficiency: float
    wear_ct: float
    # Highest stored energy (PV surplus); None: the capacity.
    full_wh: float | None = None
    # AC power charged from the grid at most; None: ``max_charge_w``.
    grid_charge_w: float | None = None


@dataclass(frozen=True)
class GridChargePlan:
    # Per local hour start: AC power the batteries charge (W) in hours they
    # charge from the grid (in a surplus hour including the PV surplus).
    charge_w: Mapping[datetime, float]
    # Per local hour start: the mean power the batteries may deliver (W),
    # only for hours in which they deliver less than the deficit.
    limits_w: Mapping[datetime, float]
    until: datetime
    # Expected saving against the batteries working as usual (ct).
    saving_ct: float
    # Per local hour start: AC power fed in from the batteries beyond the deficit (W).
    export_w: Mapping[datetime, float] = field(default_factory=dict)
    # Per local hour start of a surplus hour: the batteries take at most this
    # (W) of the PV surplus, the rest is fed in (room kept for cheaper hours).
    charge_caps_w: Mapping[datetime, float] = field(default_factory=dict)

    def charge_at(self, moment: datetime) -> float:
        return self.charge_w.get(_hour(moment), 0.0)

    def export_at(self, moment: datetime) -> float:
        return self.export_w.get(_hour(moment), 0.0)

    def limit_w(self, moment: datetime) -> float | None:
        return self.limits_w.get(_hour(moment))

    def charge_cap_at(self, moment: datetime) -> float | None:
        return self.charge_caps_w.get(_hour(moment))


def _hour(moment: datetime) -> datetime:
    return dt_util.as_local(moment).replace(minute=0, second=0, microsecond=0)


def plan_grid_charge(
    now: datetime,
    battery: ChargeBattery,
    deficits: Mapping[datetime, float],
    prices: Mapping[datetime, float | None],
    until: datetime | None,
    min_gain_ct: float,
    import_limit_w: float = math.inf,
    export_prices: Mapping[datetime, float | None] | None = None,
    export_floor_wh: float = 0.0,
    export_max_w: float = math.inf,
    battery_export: bool = True,
) -> GridChargePlan | None:
    """Plan of the hours from now until ``until`` (refill) or the last known price.

    ``deficits``: consumption minus PV per local hour start (Wh); negative is
    a PV surplus: the batteries take it (or part of it, the rest is fed in at
    the hour's credit) and may charge from the grid on top. ``prices``: import
    price, ``export_prices``: feed-in credit (ct/kWh). ``battery_export``:
    feeding in from the batteries beyond the deficit is allowed. None without
    a price for the current hour or if the plan does not differ from the
    batteries working as usual.
    """
    local_now = dt_util.as_local(now)
    first = _hour(local_now)
    hours: list[tuple[datetime, float, float]] = []  # (hour, share, price)
    hour = first
    end = until or first + timedelta(hours=36)
    while hour < end and prices.get(hour) is not None:
        share = (hour + PERIOD - local_now) / PERIOD if hour == first else 1.0
        hours.append((hour, share, prices[hour]))
        hour += PERIOD
    if not hours:
        return None
    refilled = until is not None and hour >= until
    step = max(MIN_STEP_WH, battery.capacity_wh * STEP_PCT / 100)
    eff = battery.efficiency or 1.0
    levels = int(battery.capacity_wh // step) + 1
    floor = min(levels - 1, math.ceil(battery.floor_wh / step - 1e-9))
    grid_top = int(battery.grid_max_wh // step + 1e-9)
    full_top = (battery.full_wh if battery.full_wh is not None else battery.capacity_wh) / step
    grid_power = battery.max_charge_w if battery.grid_charge_w is None else battery.grid_charge_w
    # Energy left at the end replaces later import; never worth less than nothing
    # (a negative price in the plan says nothing about later hours).
    left_value = 0.0 if refilled else max(0.0, min(price for _, _, price in hours)) * eff
    credits = export_prices or {}

    stored = battery.stored_wh / step
    export_floor = export_floor_wh / step

    # An option: (kind, stored level after, AC Wh delivered / charged, cost of
    # the hour incl. penalties, money of the hour in ct).
    def options(x: float, index: int) -> list[tuple[str, float, float, float, float]]:
        hour, share, price = hours[index]
        net = deficits.get(hour, 0.0) * share
        credit = credits.get(hour) or 0.0
        early = (battery.wear_ct + min_gain_ct + EARLY_CT * (len(hours) - index)) / 1000
        if net < 0:
            surplus = -net
            room = max(0.0, full_top - x) * step / eff
            pv_max = min(surplus, battery.max_charge_w * share, room)
            money = -credit * (surplus - pv_max) / 1000
            result = [("absorb", x + pv_max * eff / step, pv_max, money, money)]
            # Less than the surplus: the rest is fed in now, room kept for later.
            k = 0
            while k * step / eff < pv_max - 1e-6:
                part = k * step / eff
                money = -credit * (surplus - part) / 1000
                result.append(("cap", x + k, part, money + min_gain_ct * (pv_max - part) / 1000, money))
                k += 1
            # The whole surplus and more from the grid.
            grid_max = min(
                battery.max_charge_w * share - pv_max, grid_power * share, import_limit_w * share
            )
            level = x + pv_max * eff / step
            k = 1
            while k * step / eff <= grid_max + 1e-6 and level + k <= grid_top + 1e-9:
                charged = k * step / eff
                money = -credit * (surplus - pv_max) / 1000 + price * charged / 1000
                result.append(("charge", level + k, pv_max + charged, money + early * k * step, money))
                k += 1
            return result
        deficit = net
        # The whole deficit as far as the batteries can deliver it.
        full = min(deficit, battery.max_discharge_w * share, max(0.0, x - floor) * step * eff)
        money = price * (deficit - full) / 1000
        result = [("full", x - full / eff / step, full, money, money)]
        # Less than that (hold or part): the energy kept costs the minimum gain.
        k = 0
        while k * step * eff < full - 1e-6:
            part = k * step * eff
            money = price * (deficit - part) / 1000
            result.append(("part" if k else "hold", x - k, part, money + min_gain_ct * (full - part) / 1000, money))
            k += 1
        # Feeding in beyond the deficit, with a credit of the hour.
        hour_credit = credits.get(hour)
        if battery_export and hour_credit is not None and full >= deficit - 1e-6:
            room = min(battery.max_discharge_w * share - full, export_max_w * share)
            level = x - full / eff / step
            k = 1
            while k * step * eff <= room + 1e-6 and level - k >= max(export_floor, floor) - 1e-9:
                fed = k * step * eff
                money = price * (deficit - full) / 1000 - hour_credit * fed / 1000
                result.append(("export", level - k, fed, money + min_gain_ct * fed / 1000, money))
                k += 1
        # Charging from the grid (the house from the grid too).
        charge_ac_max = min(
            battery.max_charge_w * share, grid_power * share, max(0.0, import_limit_w * share - deficit)
        )
        k = 1
        while x + k <= grid_top + 1e-9 and k * step / eff <= charge_ac_max + 1e-6:
            charged = k * step / eff
            money = price * (deficit + charged) / 1000
            result.append(("charge", x + k, charged, money + early * k * step + min_gain_ct * full / 1000, money))
            k += 1
        return result

    def after(costs: list[float], x: float) -> float:
        """Cost to go at a fractional level (linear between the levels)."""
        x = min(max(x, 0.0), levels - 1)
        low = int(x)
        high = min(low + 1, levels - 1)
        return costs[low] + (costs[high] - costs[low]) * (x - low)

    # stages[i][s]: lowest cost from hour i on with stored level s.
    stages: list[list[float]] = [
        [-max(0.0, s - floor) * step / 1000 * left_value for s in range(levels)]
    ]
    for index in range(len(hours) - 1, -1, -1):
        following = stages[0]
        stages.insert(
            0,
            [min(cost + after(following, target) for _, target, _, cost, _ in options(s, index)) for s in range(levels)],
        )

    charge: dict[datetime, float] = {}
    limits: dict[datetime, float] = {}
    exports: dict[datetime, float] = {}
    caps: dict[datetime, float] = {}
    planned_money = 0.0
    level = stored
    for index, (hour, share, _) in enumerate(hours):
        kind, target, energy, _, money = min(
            options(level, index), key=lambda option: option[3] + after(stages[index + 1], option[1])
        )
        planned_money += money
        if kind == "charge":
            charge[hour] = energy / share
            limits[hour] = 0.0
        elif kind in ("hold", "part"):
            limits[hour] = energy / share
        elif kind == "export":
            exports[hour] = energy / share
        elif kind == "cap":
            caps[hour] = energy / share
        level = target
    if not charge and not limits and not exports and not caps:
        return None
    usual = _usual_money(battery, hours, deficits, credits, step, eff, floor, full_top, stored)
    return GridChargePlan(
        charge_w=charge,
        limits_w=limits,
        until=hours[-1][0] + PERIOD,
        saving_ct=max(0.0, usual - planned_money),
        export_w=exports,
        charge_caps_w=caps,
    )


def _usual_money(battery, hours, deficits, credits, step, eff, floor, full_top, start) -> float:
    """Costs (ct) if the batteries work as usual: cover deficits as they come
    and take the PV surplus as far as they can."""
    stored = (start - floor) * step
    full = (full_top - floor) * step
    total = 0.0
    for hour, share, price in hours:
        net = deficits.get(hour, 0.0) * share
        if net < 0:
            taken = min(-net, battery.max_charge_w * share, max(0.0, full - stored) / eff)
            stored += taken * eff
            total -= (credits.get(hour) or 0.0) * (-net - taken) / 1000
            continue
        delivered = min(net, battery.max_discharge_w * share, max(0.0, stored) * eff)
        stored -= delivered / eff
        total += price * (net - delivered) / 1000
    return total
