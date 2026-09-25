"""Automatic import limit for peak shaving (pure computations).

Below the state of charge threshold the batteries only cover the consumption
above the import limit. The automatic limit is the lowest one for which the
energy above it, expected until the batteries are refilled, still fits into
the usable energy (above the minimum state of charge, minus a safety reserve).

The hourly consumption forecast averages short peaks (oven, kettle) away, so
the expected energy above a limit comes from the 5 minute statistics of the
house consumption of the last days (``PeakProfile``): per local hour of the
day, how much energy was above a power level on average. The limit is
recalculated with every plan, so it rises by itself when more is used than
expected.

"Refilled" is the first hour in which the PV forecast exceeds the consumption
forecast (as for the night discharge), at most ``MAX_HORIZON`` ahead.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import accumulate

from homeassistant.util import dt as dt_util

PERIOD = timedelta(hours=1)
MAX_HORIZON = timedelta(hours=36)
SAMPLE_HOURS = 5 / 60
RESOLUTION_W = 50.0
# Days of 5 minute statistics used (Home Assistant keeps them about 10 days).
PROFILE_DAYS = 10


@dataclass
class PeakProfile:
    """5 minute mean powers per local hour of the day."""

    # hour -> sorted powers (W) and their prefix sums
    powers: dict[int, list[float]] = field(default_factory=dict)
    prefix: dict[int, list[float]] = field(default_factory=dict)
    # hour -> number of days the hour was observed
    days: dict[int, int] = field(default_factory=dict)

    @classmethod
    def from_means(cls, means: Mapping[datetime, float]) -> PeakProfile:
        profile = cls()
        seen: dict[int, set] = {}
        for start, power in means.items():
            local = dt_util.as_local(start)
            profile.powers.setdefault(local.hour, []).append(max(0.0, power))
            seen.setdefault(local.hour, set()).add(local.date())
        for hour, values in profile.powers.items():
            values.sort()
            profile.prefix[hour] = [0.0, *accumulate(values)]
            profile.days[hour] = len(seen[hour])
        return profile

    @property
    def is_empty(self) -> bool:
        return not self.powers

    def energy_above(self, hour: int, limit_w: float) -> float:
        """Mean energy (Wh) above ``limit_w`` in this hour of the day."""
        values = self.powers.get(hour)
        if not values:
            return 0.0
        index = bisect_right(values, limit_w)
        above = self.prefix[hour][-1] - self.prefix[hour][index]
        count = len(values) - index
        return (above - count * limit_w) * SAMPLE_HOURS / self.days[hour]

    @property
    def max_power_w(self) -> float:
        return max((values[-1] for values in self.powers.values()), default=0.0)


def hours_until_refill(
    now: datetime,
    pv: Mapping[datetime, float],
    consumption: Mapping[datetime, float],
) -> list[tuple[int, float]]:
    """(local hour of the day, share of the hour) from now until PV exceeds consumption.

    ``pv`` and ``consumption`` are hourly local buckets (Wh). The hour in which
    the surplus starts is not included; if there is a surplus right now, only
    the current hour counts.
    """
    local_now = dt_util.as_local(now)
    hour = local_now.replace(minute=0, second=0, microsecond=0)

    def share(start: datetime) -> float:
        return (start + PERIOD - max(start, local_now)) / PERIOD

    result = [(hour.hour, share(hour))]
    if pv.get(hour, 0.0) > consumption.get(hour, 0.0):
        return result
    hour += PERIOD
    while hour < local_now + MAX_HORIZON and pv.get(hour, 0.0) <= consumption.get(hour, 0.0):
        result.append((hour.hour, 1.0))
        hour += PERIOD
    return result


def auto_limit(
    profile: PeakProfile,
    hours: list[tuple[int, float]],
    usable_wh: float,
    reserve: float,
) -> float:
    """Lowest import limit whose expected energy above it fits into the budget."""
    budget = max(0.0, usable_wh) * (1 - reserve)

    def needed(limit: float) -> float:
        return sum(share * profile.energy_above(hour, limit) for hour, share in hours)

    if needed(0.0) <= budget:
        return 0.0
    low, high = 0.0, profile.max_power_w
    while high - low > RESOLUTION_W:
        middle = (low + high) / 2
        if needed(middle) <= budget:
            high = middle
        else:
            low = middle
    return high
