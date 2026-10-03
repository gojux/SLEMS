"""Price aware control: keep the stored energy for the expensive hours, and
start the grid part of a daily target in the cheapest window.

Until the batteries are refilled (PV takes over), the stored energy may not
cover every quarter hour with a deficit. The batteries then cover the
quarter hours with the highest import price (from the current tariff: time
windows, dynamic prices) and keep their energy in those that are cheaper by
at least the minimum gain: there the house draws from the grid (*hold*). The
quarter hour the energy only partly covers gets a limit (mean power) if a
more expensive covered one comes after it, so it does not use the energy
meant for the later one. The total grid import stays the same, only its time
moves to cheaper quarter hours; no energy is charged from the grid or fed in.

After the last known market price the estimated prices count (see
market_prices.estimate_prices). Without prices for every quarter hour until
the refill, or if the energy covers all of them, there is no hold. The plan is made again every cycle from the current
state of charge, so deviations of the forecast correct themselves.

Daily targets with the source "grid" (see consumer_targets) run forced from
their latest start. If the forecast surplus is short for them anyway, the
forced run may start earlier in the window with the lowest mean import price
(``cheapest_start``), when it is cheaper by at least the minimum gain.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from homeassistant.util import dt as dt_util

from .grid_charge import HOUR, QUARTER, hourly_means, slot_start

@dataclass(frozen=True)
class PriceHold:
    # Local period starts in which the batteries keep their energy.
    hold_slots: frozenset[datetime]
    until: datetime
    # Per local period start the mean power (W) the batteries may deliver: 0
    # in the held periods, the allotted part in the partly covered one.
    limits_w: Mapping[datetime, float]
    # Lowest import price (ct/kWh) of a period the batteries still cover.
    covered_from_ct: float
    # Highest import price of a held period.
    held_up_to_ct: float
    period: timedelta = QUARTER
    # Per local hour start with a limit: the mean power the batteries deliver
    # in it (for the SoC projection).
    hourly_limits_w: Mapping[datetime, float] = field(default_factory=dict)

    def holds(self, moment: datetime) -> bool:
        return slot_start(moment, self.period) in self.hold_slots

    def limit_w(self, moment: datetime) -> float | None:
        """Mean discharge power allowed in the period of ``moment`` (None: no limit)."""
        return self.limits_w.get(slot_start(moment, self.period))


def cheapest_start(
    prices: Mapping[datetime, float | None],
    first: datetime,
    latest: datetime,
    duration: timedelta,
    min_gain_ct: float,
    period: timedelta = QUARTER,
) -> datetime | None:
    """Start between ``first`` and ``latest`` of a run of ``duration`` with the
    lowest mean import price (``prices`` per local period start).

    None if it is not cheaper than starting at ``latest`` by ``min_gain_ct``,
    or a price in the way is missing.
    """
    if first >= latest or duration <= timedelta(0):
        return None

    def mean(start: datetime) -> float | None:
        total = 0.0
        moment, end = start, start + duration
        while moment < end:
            slot = slot_start(moment, period)
            until = min(slot + period, end)
            price = prices.get(slot)
            if price is None:
                return None
            total += price * (until - moment) / duration
            moment = until
        return total

    base = mean(latest)
    if base is None:
        return None
    candidates = [first]
    quarter = slot_start(first, HOUR)
    while quarter < latest:
        quarter += QUARTER
        if first < quarter < latest:
            candidates.append(quarter)
    best: tuple[float, datetime] | None = None
    for start in candidates:
        value = mean(start)
        if value is not None and (best is None or value < best[0]):
            best = (value, start)
    if best is None or best[0] > base - min_gain_ct:
        return None
    return best[1]


def plan_price_hold(
    now: datetime,
    usable_wh: float,
    deficits: Mapping[datetime, float],
    prices: Mapping[datetime, float | None],
    until: datetime | None,
    min_gain_ct: float,
    period: timedelta = QUARTER,
) -> PriceHold | None:
    """Held periods until ``until`` (refill); None if nothing is worth holding.

    ``usable_wh``: what the batteries can still deliver (AC) above their
    minimum. ``deficits``: per local period start the energy the batteries
    would cover (consumption minus PV, Wh of the whole period), ``prices``:
    the import price (ct/kWh).
    """
    if until is None or usable_wh < 0:
        return None
    local_now = dt_util.as_local(now)
    first = slot_start(local_now, period)
    needs: dict[datetime, float] = {}
    starts: list[datetime] = []
    first_share = (first + period - local_now) / period
    hour = first
    while hour < until:
        starts.append(hour)
        deficit = deficits.get(hour, 0.0)
        if hour == first:
            deficit *= first_share
        if deficit > 0:
            needs[hour] = deficit
        hour += period
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
    length = period / HOUR
    limits = {hour: 0.0 for hour in held}
    partial = covered[-1]
    if left < 0 and any(
        later > partial and prices[later] >= prices[partial] + min_gain_ct for later in covered
    ):
        share = first_share if partial == first else 1.0
        limits[partial] = (needs[partial] + left) / (share * length)
    full_w = {
        start: wh / ((first_share if start == first else 1.0) * length) for start, wh in needs.items()
    }
    return PriceHold(
        hold_slots=held,
        until=until,
        limits_w=limits,
        covered_from_ct=covered_from,
        held_up_to_ct=max(prices[hour] for hour in held),
        period=period,
        hourly_limits_w=hourly_means(limits, starts, full_w),
    )
