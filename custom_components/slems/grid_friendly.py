"""Grid friendly charging: absorb the PV feed-in peak instead of charging early.

Charging as early as possible fills the batteries before noon, exactly when
the PV feed-in peak follows. Instead, the batteries only charge with the
surplus above a feed-in limit ``T``. ``T`` is the highest limit for which the
expected surplus above it, until PV production ends, still fills the
batteries (charge losses and the safety buffer included, at most the maximum
charge power per hour):

    sum over remaining hours of min(max(0, surplus_h - T), max_charge) >= needed

The highest hours of the forecast are cut, whenever they occur (clouds can
move the peak away from noon). The limit is recalculated every cycle from the
current state of charge and the remaining forecast, so it drops by itself when
charging falls behind.

The PV forecast is corrected by the ratio of the PV energy produced today to
the energy forecast for the same time (``pv_correction``). The energy produced
today comes from the 5 minute statistics of the PV power sensor at startup
(``energy_from_means``) and is then integrated from the live values.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta

from homeassistant.util import dt as dt_util

from .pv_forecast import hourly

PERIOD = timedelta(hours=1)
# Forecast energy today (Wh) before the correction factor is trusted.
MIN_FORECAST_FOR_CORRECTION_WH = 1000.0
CORRECTION_LIMITS = (0.5, 1.2)
# Precision of the limit search (W).
RESOLUTION_W = 10.0


def pv_correction(
    forecast: Mapping[datetime, float], now: datetime, produced_today_wh: float | None
) -> float:
    """Ratio of the PV energy produced today to the forecast until now."""
    if produced_today_wh is None:
        return 1.0
    day_start = dt_util.start_of_local_day(dt_util.as_local(now))
    expected = 0.0
    for start, wh in hourly(forecast).items():
        end = start + PERIOD
        if end <= day_start or start >= now:
            continue
        covered = (min(end, now) - max(start, day_start)) / PERIOD
        expected += wh * covered
    if expected < MIN_FORECAST_FOR_CORRECTION_WH:
        return 1.0
    low, high = CORRECTION_LIMITS
    return min(high, max(low, produced_today_wh / expected))


def remaining_surplus(
    pv_forecast: Mapping[datetime, float],
    consumption_forecast: Mapping[datetime, float] | None,
    load_w: float | None,
    now: datetime,
) -> list[tuple[float, float]]:
    """Expected surplus of the remaining hours today as (surplus W, hours).

    Without a consumption forecast the current load is assumed to stay.
    """
    return [(power, hours) for _, power, hours in remaining_surplus_by_hour(
        pv_forecast, consumption_forecast, load_w, now
    )]


def remaining_surplus_by_hour(
    pv_forecast: Mapping[datetime, float],
    consumption_forecast: Mapping[datetime, float] | None,
    load_w: float | None,
    now: datetime,
    until: datetime | None = None,
) -> list[tuple[datetime, float, float]]:
    """Like ``remaining_surplus`` with the start of each hour (until the end of the day)."""
    pv = hourly(pv_forecast)
    consumption = hourly(consumption_forecast) if consumption_forecast is not None else {}
    end_of_day = until or dt_util.start_of_local_day(dt_util.as_local(now)) + timedelta(days=1)
    result = []
    for start, pv_wh in pv.items():
        end = start + PERIOD
        if end <= now or start >= end_of_day:
            continue
        hours = (end - max(start, now)) / PERIOD
        load = consumption.get(start, load_w or 0.0)
        surplus = pv_wh - load
        if surplus > 0:
            result.append((start, surplus, hours))
    return result


def chargeable_wh(surplus: list[tuple[float, float]], limit_w: float, max_charge_w: float) -> float:
    """Energy the batteries get when charging only above ``limit_w``."""
    return sum(min(max(0.0, power - limit_w), max_charge_w) * hours for power, hours in surplus)


def feed_in_limit(
    surplus: list[tuple[float, float]], needed_wh: float, max_charge_w: float
) -> float | None:
    """Highest feed-in limit that still fills the batteries; None if none.

    None means even charging everything (limit 0) is not enough: charge at
    once. A result of 0 means no feed-in may be left over.
    """
    if needed_wh <= 0:
        return max((power for power, _ in surplus), default=0.0)
    if chargeable_wh(surplus, 0.0, max_charge_w) < needed_wh:
        return None
    low, high = 0.0, max((power for power, _ in surplus), default=0.0)
    while high - low > RESOLUTION_W:
        middle = (low + high) / 2
        if chargeable_wh(surplus, middle, max_charge_w) >= needed_wh:
            low = middle
        else:
            high = middle
    return low


def planned_charging(
    surplus: list[tuple[float, float]],
    limit_w: float | None,
    max_charge_w: float,
    needed_wh: float,
) -> list[float]:
    """Planned charge power per entry of ``surplus`` until ``needed_wh`` is reached.

    With a feed-in limit only the surplus above it, otherwise everything as
    early as possible.
    """
    result = []
    remaining = max(0.0, needed_wh)
    for power, hours in surplus:
        charge = min(max(0.0, power - (limit_w or 0.0)), max_charge_w)
        charge = min(charge, remaining / hours) if hours > 0 else 0.0
        remaining -= charge * hours
        result.append(charge)
    return result


def energy_from_means(
    means: Mapping[datetime, float], period: timedelta
) -> tuple[float, datetime | None]:
    """Energy (Wh) of consecutive mean power values and the end of the last period."""
    if not means:
        return 0.0, None
    hours = period / PERIOD
    energy = sum(max(0.0, power) * hours for power in means.values())
    return energy, max(means) + period
