"""Charging the batteries from the grid and feeding in from them when it pays
(both optional, price aware).

Planned quarter hour by quarter hour (the backtest: hour by hour) until PV
refills the batteries, at most until the end of the known prices, with
dynamic programming over the stored energy (steps of ``STEP_PCT`` of the
capacity). In each period with a deficit the batteries may
cover all of it (exactly, levels in between interpolated), a part of it in steps, nothing
(*hold*), or charge from the grid. Costs:

* import price × grid import of the period,
* per kWh charged from the grid: the wear costs and the minimum gain (so a
  charge must save more than that, after both conversion losses),
* per kWh the batteries could have delivered but kept: the minimum gain (as
  the price hold, a shift must be worth it),
* a tiny amount per hour of earlier charging, so of equal plans the one that
  charges later wins: home storage ages mostly with time at a high state of
  charge, not with the cycle itself,
* feeding in from the batteries beyond the deficit (optional): the credit of
  the period is a gain, the minimum gain a cost; not below the export floor
  (morning reserve) and at most the export power.

Energy left at the refill is worth nothing (PV fills the batteries anyway);
if the plan ends before (prices unknown), it is worth the lowest import
price of the plan (at least 0), after the discharge losses.

The plan is made again with every new state of charge, so deviations of the
forecast correct themselves; only its first period is acted on. The SoC
projection works with hours and gets the plan summed up per hour
(``GridChargePlan.hourly_*``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import math

from homeassistant.util import dt as dt_util

HOUR = timedelta(hours=1)
QUARTER = timedelta(minutes=15)
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
    # Per local period start: AC power the batteries charge (W) in periods they
    # charge from the grid (in a surplus period including the PV surplus).
    charge_w: Mapping[datetime, float]
    # Per local period start: the mean power the batteries may deliver (W),
    # only for periods in which they deliver less than the deficit.
    limits_w: Mapping[datetime, float]
    until: datetime
    # Expected saving against the batteries working as usual (ct).
    saving_ct: float
    # Per local period start: AC power fed in from the batteries beyond the deficit (W).
    export_w: Mapping[datetime, float] = field(default_factory=dict)
    # Per local period start of a surplus period: the batteries take at most
    # this (W) of the PV surplus, the rest is fed in (room kept for cheaper periods).
    charge_caps_w: Mapping[datetime, float] = field(default_factory=dict)
    period: timedelta = QUARTER
    # The same as mean power per local hour start, for the SoC projection: in
    # an hour with a limit the batteries deliver ``hourly_limits_w`` in total.
    hourly_charge_w: Mapping[datetime, float] = field(default_factory=dict)
    hourly_limits_w: Mapping[datetime, float] = field(default_factory=dict)
    hourly_export_w: Mapping[datetime, float] = field(default_factory=dict)
    hourly_caps_w: Mapping[datetime, float] = field(default_factory=dict)

    def charge_at(self, moment: datetime) -> float:
        return self.charge_w.get(slot_start(moment, self.period), 0.0)

    def export_at(self, moment: datetime) -> float:
        return self.export_w.get(slot_start(moment, self.period), 0.0)

    def limit_w(self, moment: datetime) -> float | None:
        return self.limits_w.get(slot_start(moment, self.period))

    def charge_cap_at(self, moment: datetime) -> float | None:
        return self.charge_caps_w.get(slot_start(moment, self.period))


@dataclass
class PlanTiming:
    """How long the price plans take (diagnostics: whether a slow host needs
    a coarser plan)."""

    plans: int = 0
    last_s: float | None = None
    max_s: float = 0.0
    total_s: float = 0.0
    # Periods of the last plan.
    periods: int = 0

    def add(self, seconds: float, periods: int) -> None:
        self.plans += 1
        self.last_s = seconds
        self.max_s = max(self.max_s, seconds)
        self.total_s += seconds
        self.periods = periods

    def as_dict(self) -> dict:
        return {
            "plans": self.plans,
            "last_s": None if self.last_s is None else round(self.last_s, 3),
            "mean_s": round(self.total_s / self.plans, 3) if self.plans else None,
            "max_s": round(self.max_s, 3),
            "periods": self.periods,
        }


def slot_start(moment: datetime, period: timedelta = QUARTER) -> datetime:
    """Local start of the period (a quarter hour or an hour) ``moment`` falls in."""
    local = dt_util.as_local(moment)
    minutes = int(period.total_seconds() // 60)
    return local.replace(minute=local.minute - local.minute % minutes, second=0, microsecond=0)


def hourly_means(
    values: Mapping[datetime, float],
    starts: list[datetime],
    default: Mapping[datetime, float] | None = None,
) -> dict[datetime, float]:
    """Mean power (W) per local hour start of power per period (W), over the
    planned periods ``starts`` of the hours with a value; a period without a
    value counts ``default`` or 0."""
    hours: dict[datetime, list[float]] = {}
    for start in starts:
        power = values.get(start)
        if power is None:
            power = (default or {}).get(start, 0.0)
        hours.setdefault(slot_start(start, HOUR), []).append(power)
    wanted = {slot_start(start, HOUR) for start in values}
    return {hour: sum(powers) / len(powers) for hour, powers in hours.items() if hour in wanted}


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
    period: timedelta = QUARTER,
) -> GridChargePlan | None:
    """Plan of the periods from now until ``until`` (refill) or the last known price.

    ``deficits``: consumption minus PV per local period start (Wh of the whole
    period); negative is a PV surplus: the batteries take it (or part of it,
    the rest is fed in at the period's credit) and may charge from the grid
    on top. ``prices``: import price, ``export_prices``: feed-in credit
    (ct/kWh), per local period start. ``battery_export``: feeding in from the
    batteries beyond the deficit is allowed. None without a price for the
    current period or if the plan does not differ from the batteries working
    as usual.
    """
    local_now = dt_util.as_local(now)
    first = slot_start(local_now, period)
    hours: list[tuple[datetime, float, float]] = []  # (period start, share, price)
    hour = first
    end = until or first + timedelta(hours=36)
    while hour < end and prices.get(hour) is not None:
        share = (hour + period - local_now) / period if hour == first else 1.0
        hours.append((hour, share, prices[hour]))
        hour += period
    if not hours:
        return None
    refilled = until is not None and hour >= until
    # Power (W) to energy (Wh) of a whole period.
    length = period / HOUR
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
        early = (battery.wear_ct + min_gain_ct + EARLY_CT * (len(hours) - index) * length) / 1000
        span = share * length
        if net < 0:
            surplus = -net
            room = max(0.0, full_top - x) * step / eff
            pv_max = min(surplus, battery.max_charge_w * span, room)
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
                battery.max_charge_w * span - pv_max, grid_power * span, import_limit_w * span
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
        full = min(deficit, battery.max_discharge_w * span, max(0.0, x - floor) * step * eff)
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
            room = min(battery.max_discharge_w * span - full, export_max_w * span)
            level = x - full / eff / step
            k = 1
            while k * step * eff <= room + 1e-6 and level - k >= max(export_floor, floor) - 1e-9:
                fed = k * step * eff
                money = price * (deficit - full) / 1000 - hour_credit * fed / 1000
                result.append(("export", level - k, fed, money + min_gain_ct * fed / 1000, money))
                k += 1
        # Charging from the grid (the house from the grid too).
        charge_ac_max = min(
            battery.max_charge_w * span, grid_power * span, max(0.0, import_limit_w * span - deficit)
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
    # Per period: what the batteries would deliver to the house or take of
    # the PV surplus as usual (W), for the hourly sums of the limits and caps.
    delivered: dict[datetime, float] = {}
    absorbed: dict[datetime, float] = {}
    planned_money = 0.0
    level = stored
    for index, (hour, share, _) in enumerate(hours):
        chosen = options(level, index)
        kind, target, energy, _, money = min(
            chosen, key=lambda option: option[3] + after(stages[index + 1], option[1])
        )
        span = share * length
        # The first option covers the whole deficit or takes the whole surplus.
        usual_kind, _, usual_energy, _, _ = chosen[0]
        (absorbed if usual_kind == "absorb" else delivered)[hour] = usual_energy / span
        planned_money += money
        if kind == "charge":
            charge[hour] = energy / span
            limits[hour] = 0.0
        elif kind in ("hold", "part"):
            limits[hour] = energy / span
        elif kind == "export":
            exports[hour] = energy / span
        elif kind == "cap":
            caps[hour] = energy / span
        level = target
    if not charge and not limits and not exports and not caps:
        return None
    usual = _usual_money(battery, hours, deficits, credits, step, eff, floor, full_top, stored, length)
    starts = [hour for hour, _, _ in hours]
    return GridChargePlan(
        charge_w=charge,
        limits_w=limits,
        until=hours[-1][0] + period,
        saving_ct=max(0.0, usual - planned_money),
        export_w=exports,
        charge_caps_w=caps,
        period=period,
        hourly_charge_w=hourly_means(charge, starts),
        hourly_limits_w=hourly_means(limits, starts, delivered),
        hourly_export_w=hourly_means(exports, starts),
        hourly_caps_w=hourly_means(caps, starts, absorbed),
    )


def _usual_money(battery, hours, deficits, credits, step, eff, floor, full_top, start, length) -> float:
    """Costs (ct) if the batteries work as usual: cover deficits as they come
    and take the PV surplus as far as they can."""
    stored = (start - floor) * step
    full = (full_top - floor) * step
    total = 0.0
    for hour, share, price in hours:
        net = deficits.get(hour, 0.0) * share
        if net < 0:
            taken = min(-net, battery.max_charge_w * share * length, max(0.0, full - stored) / eff)
            stored += taken * eff
            total -= (credits.get(hour) or 0.0) * (-net - taken) / 1000
            continue
        delivered = min(net, battery.max_discharge_w * share * length, max(0.0, stored) * eff)
        stored -= delivered / eff
        total += price * (net - delivered) / 1000
    return total
