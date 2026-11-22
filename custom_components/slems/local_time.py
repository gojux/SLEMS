"""Stepping through local periods (quarter hours, hours) across clock changes.

Adding a timedelta to a local datetime counts on the wall clock. Local
buckets of the forecasts (``pv_forecast.hourly``) hold both runs of the hour
the clock repeats in autumn, so stepping on the wall clock visits that bucket
once, as it should; but a step into the hour the clock skips in spring lands
on a time that does not exist (02:00), which no bucket and no price has.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.util import dt as dt_util

HOUR = timedelta(hours=1)


def next_start(moment: datetime, period: timedelta) -> datetime:
    """Local start of the period after the one starting at ``moment``
    (``period`` divides an hour): on the wall clock, a skipped time moved to
    the real one (02:00 -> 03:00 in spring)."""
    return dt_util.as_local(dt_util.as_utc((moment + period).replace(fold=moment.fold)))


def real_duration(start: datetime, end: datetime) -> timedelta:
    """Real time between two moments (the difference of two local datetimes
    counts on the wall clock as well)."""
    return dt_util.as_utc(end) - dt_util.as_utc(start)


def hours_of_day(day_start: datetime) -> list[datetime]:
    """Starts (UTC) of every hour of the local day beginning at ``day_start``:
    23 when the clock is set forward, 25 when it is set back."""
    start = dt_util.as_utc(day_start)
    end = dt_util.as_utc(dt_util.as_local(day_start) + timedelta(days=1))
    hours = []
    while start < end:
        hours.append(start)
        start += HOUR
    return hours
