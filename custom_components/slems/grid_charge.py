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
price of the plan, after the discharge losses.

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
    max_charge_w: float
    max_discharge_w: float
    # One way efficiency (AC to stored and back).
    efficiency: float
    wear_ct: float


@dataclass(frozen=True)
class GridChargePlan:
    # Per local hour start: AC power charged from the grid (W).
    charge_w: Mapping[datetime, float]
    # Per local hour start: the mean power the batteries may deliver (W),
    # only for hours in which they deliver less than the deficit.
    limits_w: Mapping[datetime, float]
    until: datetime
    # Expected saving against covering the hours in order (ct).
    saving_ct: float
    # Per local hour start: AC power fed in from the batteries beyond the deficit (W).
    export_w: Mapping[datetime, float] = field(default_factory=dict)

    def charge_at(self, moment: datetime) -> float:
        return self.charge_w.get(_hour(moment), 0.0)

    def export_at(self, moment: datetime) -> float:
        return self.export_w.get(_hour(moment), 0.0)

    def limit_w(self, moment: datetime) -> float | None:
        return self.limits_w.get(_hour(moment))


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
) -> GridChargePlan | None:
    """Plan of the hours from now until ``until`` (refill) or the last known price.

    ``deficits``: consumption minus PV per local hour start (Wh), ``prices``:
    import price (ct/kWh). None without a price for the current hour or if the
    plan neither charges nor holds (it then also covers the price hold, up to
    the last known price instead of only with prices until the refill).
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
    left_value = 0.0 if refilled else min(price for _, _, price in hours) * eff

    stored = battery.stored_wh / step
    export_floor = export_floor_wh / step

    def options(x: float, index: int) -> list[tuple[str, float, float, float]]:
        """(kind, stored level after, AC Wh delivered or charged, cost of the hour) at level ``x``."""
        hour, share, price = hours[index]
        deficit = max(0.0, deficits.get(hour, 0.0)) * share
        # The whole deficit as far as the batteries can deliver it.
        full = min(deficit, battery.max_discharge_w * share, max(0.0, x - floor) * step * eff)
        result = [("full", x - full / eff / step, full, price * (deficit - full) / 1000)]
        # Less than that (hold or part): the energy kept costs the minimum gain.
        k = 0
        while k * step * eff < full - 1e-6:
            part = k * step * eff
            result.append(
                ("part" if k else "hold", x - k, part,
                 price * (deficit - part) / 1000 + min_gain_ct * (full - part) / 1000)
            )
            k += 1
        # Feeding in beyond the deficit, with a credit of the hour.
        credit = (export_prices or {}).get(hour)
        if credit is not None and full >= deficit - 1e-6:
            room = min(battery.max_discharge_w * share - full, export_max_w * share)
            level = x - full / eff / step
            k = 1
            while k * step * eff <= room + 1e-6 and level - k >= max(export_floor, floor) - 1e-9:
                fed = k * step * eff
                result.append(
                    ("export", level - k, fed,
                     price * (deficit - full) / 1000 - (credit - min_gain_ct) * fed / 1000)
                )
                k += 1
        # Charging from the grid (the house from the grid too).
        charge_ac_max = min(battery.max_charge_w * share, max(0.0, import_limit_w * share - deficit))
        early = (battery.wear_ct + min_gain_ct + EARLY_CT * (len(hours) - index)) / 1000
        k = 1
        while x + k <= grid_top + 1e-9 and k * step / eff <= charge_ac_max + 1e-6:
            charged = k * step / eff
            result.append(
                ("charge", x + k, charged,
                 price * (deficit + charged) / 1000 + early * k * step + min_gain_ct * full / 1000)
            )
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
            [min(value + after(following, target) for _, target, _, value in options(s, index)) for s in range(levels)],
        )

    charge: dict[datetime, float] = {}
    limits: dict[datetime, float] = {}
    exports: dict[datetime, float] = {}
    level = stored
    for index, (hour, share, _) in enumerate(hours):
        kind, target, energy, _ = min(
            options(level, index), key=lambda option: option[3] + after(stages[index + 1], option[1])
        )
        if kind == "charge":
            charge[hour] = energy / share
            limits[hour] = 0.0
        elif kind in ("hold", "part"):
            limits[hour] = energy / share
        elif kind == "export":
            exports[hour] = energy / share
        level = target
    if not charge and not limits and not exports:
        return None
    baseline = _in_order_cost(battery, hours, deficits, step, eff, floor, stored)
    return GridChargePlan(
        charge_w=charge,
        limits_w=limits,
        until=hours[-1][0] + PERIOD,
        saving_ct=max(
            0.0,
            baseline
            - _plan_cost(battery, hours, deficits, charge, limits)
            + sum(watts * (export_prices or {}).get(hour, 0.0) for hour, watts in exports.items()) / 1000,
        ),
        export_w=exports,
    )


def _in_order_cost(battery, hours, deficits, step, eff, floor, start) -> float:
    """Import costs (ct) if the batteries cover the deficits as they come."""
    stored = (start - floor) * step
    total = 0.0
    for hour, share, price in hours:
        deficit = max(0.0, deficits.get(hour, 0.0)) * share
        delivered = min(deficit, battery.max_discharge_w * share, max(0.0, stored) * eff)
        stored -= delivered / eff
        total += price * (deficit - delivered) / 1000
    return total


def _plan_cost(battery, hours, deficits, charge, limits) -> float:
    total = 0.0
    for hour, share, price in hours:
        deficit = max(0.0, deficits.get(hour, 0.0)) * share
        if hour in charge:
            total += price * (deficit + charge[hour] * share) / 1000
        elif hour in limits:
            total += price * (deficit - limits[hour] * share) / 1000
    return total
