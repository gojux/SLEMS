"""Estimate of what the price aware control would have saved (backtest).

The recorded hourly house consumption and PV production are played through a
simple battery model twice:

* *as usual*: the batteries take the PV surplus and cover the deficits in
  order (minimum and maximum state of charge, power, efficiency),
* *price aware*: the same, but every deficit run (until PV takes over again,
  at most ``MAX_RUN``) is planned with ``grid_charge.plan_grid_charge`` with
  the import prices of the tariff: holding for the expensive hours, and
  charging from the grid if that option is on.

Both give hourly grid import and export; priced with the tariff, their
difference is the estimated saving. The plan knows the consumption and PV of
the run exactly (perfect forecast), so the estimate is an upper bound; the
batteries' own losses at rest and the controller's deviations are left out.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from .grid_charge import ChargeBattery, plan_grid_charge

MAX_RUN = timedelta(hours=36)
PERIOD = timedelta(hours=1)


@dataclass(frozen=True)
class BacktestBattery:
    capacity_wh: float
    min_wh: float
    full_wh: float
    max_charge_w: float
    max_discharge_w: float
    # One way efficiency.
    efficiency: float
    wear_ct: float
    grid_charge: bool
    grid_max_wh: float


def play(
    hours: Sequence[datetime],
    load_wh: Mapping[datetime, float],
    pv_wh: Mapping[datetime, float],
    battery: BacktestBattery,
    prices: Mapping[datetime, float | None] | None,
    min_gain_ct: float,
    start_wh: float | None = None,
) -> tuple[dict[datetime, float], dict[datetime, float]]:
    """Grid import and export (Wh) per hour; ``prices`` None: as usual.

    ``hours`` are consecutive local hour starts.
    """
    eff = battery.efficiency or 1.0
    stored = (battery.min_wh + battery.full_wh) / 2 if start_wh is None else start_wh
    imported: dict[datetime, float] = {}
    exported: dict[datetime, float] = {}
    charge: Mapping[datetime, float] = {}
    limits: Mapping[datetime, float] = {}
    planned_until: datetime | None = None
    for index, hour in enumerate(hours):
        if hour not in load_wh:
            continue
        net = load_wh[hour] - pv_wh.get(hour, 0.0)
        if net <= 0:
            # Surplus: the batteries take what fits, the rest is exported.
            take = min(-net, battery.max_charge_w, max(0.0, battery.full_wh - stored) / eff)
            stored += take * eff
            imported[hour], exported[hour] = 0.0, -net - take
            planned_until = None
            continue
        if prices is not None and (planned_until is None or hour >= planned_until):
            charge, limits, planned_until = _plan_run(hours, index, load_wh, pv_wh, battery, stored, prices, min_gain_ct)
        if hour in charge:
            charged = min(charge[hour], max(0.0, battery.full_wh - stored) / eff)
            stored += charged * eff
            imported[hour], exported[hour] = net + charged, 0.0
            continue
        deliver = min(net, battery.max_discharge_w, max(0.0, stored - battery.min_wh) * eff)
        if hour in limits:
            deliver = min(deliver, limits[hour])
        stored -= deliver / eff
        imported[hour], exported[hour] = net - deliver, 0.0
    return imported, exported


def _plan_run(
    hours: Sequence[datetime],
    index: int,
    load_wh: Mapping[datetime, float],
    pv_wh: Mapping[datetime, float],
    battery: BacktestBattery,
    stored: float,
    prices: Mapping[datetime, float | None],
    min_gain_ct: float,
) -> tuple[Mapping[datetime, float], Mapping[datetime, float], datetime]:
    """Plan of the deficit run starting at ``hours[index]`` (perfect forecast)."""
    start = hours[index]
    deficits: dict[datetime, float] = {}
    until = None
    for hour in hours[index:]:
        if hour >= start + MAX_RUN:
            break
        net = load_wh.get(hour, 0.0) - pv_wh.get(hour, 0.0)
        if net <= 0:
            until = hour
            break
        deficits[hour] = net
    end = until or start + MAX_RUN
    plan = plan_grid_charge(
        start,
        ChargeBattery(
            capacity_wh=battery.capacity_wh,
            stored_wh=stored,
            floor_wh=battery.min_wh,
            grid_max_wh=battery.grid_max_wh,
            max_charge_w=battery.max_charge_w if battery.grid_charge else 0.0,
            max_discharge_w=battery.max_discharge_w,
            efficiency=battery.efficiency,
            wear_ct=battery.wear_ct,
        ),
        deficits,
        prices,
        until,
        min_gain_ct,
    )
    if plan is None:
        return {}, {}, end
    return plan.charge_w, plan.limits_w, end


def run_cost(
    imported: Mapping[datetime, float],
    exported: Mapping[datetime, float],
    import_prices: Mapping[datetime, float | None],
    export_prices: Mapping[datetime, float | None],
) -> float:
    """Costs (ct) of grid import minus the feed-in credit; hours without price count 0."""
    total = 0.0
    for hour, wh in imported.items():
        total += wh * (import_prices.get(hour) or 0.0) / 1000
    for hour, wh in exported.items():
        total -= wh * (export_prices.get(hour) or 0.0) / 1000
    return total


def measured_saving(
    hours: Sequence[datetime],
    load_wh: Mapping[datetime, float],
    pv_wh: Mapping[datetime, float],
    battery: BacktestBattery,
    start_wh: float,
    actual_import: Mapping[datetime, float],
    actual_export: Mapping[datetime, float],
    import_prices: Mapping[datetime, float | None],
    export_prices: Mapping[datetime, float | None],
) -> float:
    """Saving (€) of a run: its costs *as usual* (model) minus the recorded ones."""
    usual = play(hours, load_wh, pv_wh, battery, None, 0.0, start_wh=start_wh)
    real = {hour: actual_import.get(hour, 0.0) for hour in hours}, {hour: actual_export.get(hour, 0.0) for hour in hours}
    return (run_cost(*usual, import_prices, export_prices) - run_cost(*real, import_prices, export_prices)) / 100
