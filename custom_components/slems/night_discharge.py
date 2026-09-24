"""Optional night discharge down to a forecast based target state of charge.

Over night the batteries may discharge more than the house needs (the grid
target is ignored), spread evenly until the forecast PV production exceeds the
forecast consumption again (the batteries start charging). The target is the
reserve (a percentage of tomorrow's forecast daily consumption) above the
minimum SoC of the batteries, raised to the
level from which tomorrow's forecast PV surplus (minus a safety buffer,
including charge losses) can still fill the batteries.

The discharge power is recomputed every cycle from the remaining energy and
time, so deviations correct themselves.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from homeassistant.util import dt as dt_util

from .pv_forecast import hourly

PERIOD = timedelta(hours=1)
MAX_LOOKAHEAD = timedelta(hours=36)


@dataclass(frozen=True)
class NightDischargePlan:
    """Result of the night discharge planning."""

    # Stored energy that must remain when PV takes over.
    target_wh: float
    target_soc_pct: float
    # AC power to deliver on average until ``until``; 0 if at or below target.
    power_w: float
    # Start of the first period in which PV exceeds consumption.
    until: datetime


def _energy(forecast: Mapping[datetime, float], start: datetime) -> float:
    return forecast.get(start, 0.0)




def plan_night_discharge(
    now: datetime,
    soc_pct: float,
    capacity_wh: float,
    charge_efficiency: float,
    discharge_efficiency: float,
    pv_forecast: Mapping[datetime, float],
    consumption_forecast: Mapping[datetime, float],
    reserve_pct_of_consumption: float,
    buffer_wh: float,
    min_wh: float = 0.0,
) -> NightDischargePlan | None:
    """Plan the night discharge; None if not applicable right now.

    Both forecasts map period starts to Wh. ``min_wh`` is the energy below
    the minimum SoC of the batteries: it cannot be used, so the reserve comes
    on top of it. Not applicable while PV already
    exceeds consumption, or if no crossover is found within the lookahead.
    """
    pv_forecast = hourly(pv_forecast)
    consumption_forecast = hourly(consumption_forecast)
    period_start = dt_util.as_local(now).replace(minute=0, second=0, microsecond=0)
    crossover: datetime | None = None
    start = period_start
    while start < period_start + MAX_LOOKAHEAD:
        if _energy(pv_forecast, start) > _energy(consumption_forecast, start):
            crossover = start
            break
        start += PERIOD
    if crossover is None or crossover <= period_start:
        return None

    day_start = dt_util.start_of_local_day(dt_util.as_local(crossover))
    day_end = day_start + timedelta(days=1)
    daily_consumption = sum(
        wh for start, wh in consumption_forecast.items() if day_start <= start < day_end
    )
    surplus = 0.0
    start = crossover
    while start < day_end:
        surplus += max(0.0, _energy(pv_forecast, start) - _energy(consumption_forecast, start))
        start += PERIOD

    reserve = reserve_pct_of_consumption / 100 * daily_consumption
    rechargeable = max(0.0, surplus - buffer_wh) * charge_efficiency
    target = min(capacity_wh, max(min_wh + reserve, capacity_wh - rechargeable, 0.0))

    stored = soc_pct / 100 * capacity_wh
    hours = (crossover - now).total_seconds() / 3600
    power = max(0.0, stored - target) * discharge_efficiency / hours if hours > 0 else 0.0
    return NightDischargePlan(
        target_wh=target,
        target_soc_pct=target / capacity_wh * 100 if capacity_wh else 0.0,
        power_w=power,
        until=crossover,
    )
