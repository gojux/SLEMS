"""Automatic import limit or threshold for peak shaving (pure computations).

Below the state of charge threshold the batteries only cover the consumption
above the import limit. The automatic limit is the lowest one for which the
energy above it, expected until the batteries are refilled, still fits into
the usable energy (above the minimum state of charge, minus a safety reserve).
The automatic threshold is the other way round: for a set limit, the lowest
threshold that keeps that energy (plus the reserve) above the minimum.

The hourly consumption forecast averages short peaks (oven, kettle) away, so
the expected energy above a limit comes from the 5 minute statistics of the
house consumption of the last days (``PeakProfile``): per local hour of the
day, how much energy was above a power level on average. The limit is
recalculated with every plan, so it rises by itself when more is used than
expected.

"Refilled" is the moment the forecast PV surplus adds up to the energy that
charges the batteries back to the threshold, at most ``MAX_HORIZON`` ahead; a
short, small surplus on a rainy day does not count. During a PV surplus the
limit is calculated for the following period without surplus (evening and
night), because peak shaving only acts then.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable, Mapping
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
# Rounds of threshold and refill horizon (see auto_threshold).
THRESHOLD_ROUNDS = 3


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
    refill_wh: float,
    charge_limit: Callable[[datetime], float] | None = None,
) -> list[tuple[int, float]]:
    """(local hour of the day, share of the hour) without PV surplus until the refill.

    ``pv`` and ``consumption`` are hourly local buckets (Wh). The batteries
    count as refilled once the forecast surplus adds up to ``refill_wh`` (AC
    energy to charge them back to the threshold): a short, small surplus on a
    rainy day does not end the period. Hours with surplus are not included
    (the batteries charge then). During a surplus right now the period starts
    when it ends, e.g. in the evening, because peak shaving only acts then.
    ``charge_limit``: charge power of the batteries per hour start, with
    batteries with own PV (see soc_projection.own_charge_limit).
    """
    local_now = dt_util.as_local(now)
    end = local_now + MAX_HORIZON
    hour = local_now.replace(minute=0, second=0, microsecond=0)

    def share(start: datetime) -> float:
        return (start + PERIOD - max(start, local_now)) / PERIOD

    def surplus_wh(start: datetime) -> float:
        return pv.get(start, 0.0) - consumption.get(start, 0.0)

    while hour < end and surplus_wh(hour) > 0:
        hour += PERIOD
    result: list[tuple[int, float]] = []
    charged = 0.0
    while hour < end:
        surplus = surplus_wh(hour)
        if surplus > 0:
            if charge_limit is not None:
                surplus = min(surplus, charge_limit(hour))
            charged += surplus * share(hour)
            if charged >= refill_wh:
                break
        else:
            result.append((hour.hour, share(hour)))
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
        return needed_wh(profile, hours, limit)

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


def needed_wh(profile: PeakProfile, hours: list[tuple[int, float]], limit_w: float) -> float:
    """Expected energy above ``limit_w`` in ``hours``."""
    return sum(share * profile.energy_above(hour, limit_w) for hour, share in hours)


def auto_threshold(
    profile: PeakProfile,
    hours_for: Callable[[float], list[tuple[int, float]]],
    limit_w: float,
    min_soc_pct: float,
    max_soc_pct: float,
    capacity_wh: float,
    efficiency: float,
    reserve: float,
    start_pct: float,
) -> tuple[float, bool]:
    """Lowest threshold whose energy above the minimum covers the peaks above
    ``limit_w`` until the refill, and whether it reached ``max_soc_pct``.

    ``hours_for``: the hours until the refill for a threshold (the refill
    depends on how much has to be charged back, see hours_until_refill);
    ``efficiency``: of charging and discharging, ``start_pct``: the threshold
    to start from (a few rounds settle it).
    """
    threshold = max(min_soc_pct, min(start_pct, max_soc_pct))
    if capacity_wh <= 0:
        return threshold, False
    for _ in range(THRESHOLD_ROUNDS):
        energy = needed_wh(profile, hours_for(threshold), limit_w) / max(1e-6, 1 - reserve) / efficiency
        threshold = min(max_soc_pct, min_soc_pct + energy / capacity_wh * 100)
    return threshold, threshold >= max_soc_pct - 0.5
