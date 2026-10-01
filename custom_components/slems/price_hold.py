"""Price aware discharging: keep the stored energy for the expensive hours.

Until the batteries are refilled (PV takes over), the stored energy may not
cover every hour with a deficit. The batteries then cover the hours with the
highest import price (from the current tariff: time windows, dynamic prices)
and keep their energy in the hours that are cheaper by at least the minimum
gain: there the house draws from the grid (*hold*). The hour the energy only
partly covers gets a limit (mean power) if a more expensive covered hour
comes after it, so it does not use the energy meant for the later one. The
total grid import stays the same, only its time moves to cheaper hours; no
energy is charged from the grid or fed in.

Without prices for every hour until the refill, or if the energy covers all
hours, there is no hold. The plan is made again every cycle from the current
state of charge, so deviations of the forecast correct themselves.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from homeassistant.util import dt as dt_util

PERIOD = timedelta(hours=1)


@dataclass(frozen=True)
class PriceHold:
    # Local hour starts in which the batteries keep their energy.
    hold_hours: frozenset[datetime]
    until: datetime
    # Per local hour start the mean power (W) the batteries may deliver: 0 in
    # the held hours, the allotted part in the partly covered hour.
    limits_w: Mapping[datetime, float]
    # Lowest import price (ct/kWh) of an hour the batteries still cover.
    covered_from_ct: float
    # Highest import price of a held hour.
    held_up_to_ct: float

    def holds(self, moment: datetime) -> bool:
        return _hour(moment) in self.hold_hours

    def limit_w(self, moment: datetime) -> float | None:
        """Mean discharge power allowed in the hour of ``moment`` (None: no limit)."""
        return self.limits_w.get(_hour(moment))


def _hour(moment: datetime) -> datetime:
    return dt_util.as_local(moment).replace(minute=0, second=0, microsecond=0)


def plan_price_hold(
    now: datetime,
    usable_wh: float,
    deficits: Mapping[datetime, float],
    prices: Mapping[datetime, float | None],
    until: datetime | None,
    min_gain_ct: float,
) -> PriceHold | None:
    """Hold hours until ``until`` (refill); None if nothing is worth holding.

    ``usable_wh``: what the batteries can still deliver (AC) above their
    minimum. ``deficits``: per local hour start the energy the batteries would
    cover (consumption minus PV, Wh), ``prices``: the import price (ct/kWh).
    """
    if until is None or usable_wh < 0:
        return None
    local_now = dt_util.as_local(now)
    first = local_now.replace(minute=0, second=0, microsecond=0)
    needs: dict[datetime, float] = {}
    first_share = (first + PERIOD - local_now) / PERIOD
    hour = first
    while hour < until:
        deficit = deficits.get(hour, 0.0)
        if hour == first:
            deficit *= first_share
        if deficit > 0:
            needs[hour] = deficit
        hour += PERIOD
    if not needs or usable_wh >= sum(needs.values()):
        return None
    if any(prices.get(hour) is None for hour in needs):
        return None
    left = usable_wh
    covered: list[datetime] = []
    # The most expensive hours first; at the same price the earlier one.
    for hour in sorted(needs, key=lambda h: (-prices[h], h)):
        if left <= 0:
            break
        covered.append(hour)
        left -= needs[hour]
    if not covered:
        return None
    covered_from = min(prices[hour] for hour in covered)
    held = frozenset(
        hour for hour in needs if hour not in covered and prices[hour] <= covered_from - min_gain_ct
    )
    if not held:
        return None
    limits = {hour: 0.0 for hour in held}
    partial = covered[-1]
    if left < 0 and any(
        later > partial and prices[later] >= prices[partial] + min_gain_ct for later in covered
    ):
        share = first_share if partial == first else 1.0
        limits[partial] = (needs[partial] + left) / share
    return PriceHold(
        hold_hours=held,
        until=until,
        limits_w=limits,
        covered_from_ct=covered_from,
        held_up_to_ct=max(prices[hour] for hour in held),
    )
