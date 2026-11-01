"""Peak power with a power price (pure computations, see tariff, unit ``kw_month``).

What counts this month: the power billed if the month ended now (the
measured peak of the finished intervals, at least the minimum, rounded up to
the step) and the interval mean up to which a new interval adds nothing
(``free_kw``). Peak shaving never needs to keep the import below that, so
its limit is at least ``free_kw`` minus ``BUFFER_W`` (the meter of Home
Assistant and the smart meter differ a little, the control reacts late).

The running interval (quarter hour or hour, on the wall clock as the smart
meter counts it) has a budget: the energy at the limit. The import still
allowed for the rest of it is the budget left over the time left, so a short
peak (a battery at its power limit, a kettle) is made up for in the rest of
the interval, and the import may rise above the limit while budget is left.
Feed-in does not count against import, so the allowed import is never below
0. ``MIN_REST_S`` keeps the allowed import from jumping at the end of an
interval, where the batteries could not follow anyway. An interval outside
the time window of the power price does not count: it has no budget.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
import math

from homeassistant.util import dt as dt_util

from .grid_quarters import SLOT_S, GridQuarters
from .tariff import Side, Tariff, TariffItem, Unit

BUFFER_W = 200.0
MIN_REST_S = 120.0
# Resolution of the search for the free interval mean (kW).
FREE_RESOLUTION_KW = 0.005


@dataclass(frozen=True)
class PowerPrice:
    """The power price lines of a tariff in effect on a day."""

    items: tuple[TariffItem, ...]
    # VAT of the grid group and the percent levies of its group (%).
    vat_pct: float
    percent_pct: float

    @property
    def base(self) -> TariffItem:
        """The line of the lowest tier: it defines how the power is measured."""
        return min(self.items, key=lambda item: item.kw_from or 0.0)

    @property
    def interval_s(self) -> int:
        return self.base.peak_minutes * 60

    def counts(self, start: datetime) -> bool:
        """Whether the interval starting at ``start`` counts (the time window of the power price)."""
        return self.base.in_window(dt_util.as_local(start))

    def billed_kw(self, powers: dict[datetime, float]) -> float:
        """Billed power for the interval powers (kW) that count."""
        values = sorted(powers.values(), reverse=True)[: max(1, self.base.peak_count)]
        return self.base.billed_kw(sum(values) / len(values) if values else 0.0)

    def cost(self, billed_kw: float) -> float:
        """Power price of a month for ``billed_kw``, with levies and VAT."""
        net = sum(item.charged_kw(billed_kw) * item.price for item in self.items)
        return net * (1 + self.percent_pct / 100) * (1 + self.vat_pct / 100)


def power_price(tariff: Tariff | None, day: date) -> PowerPrice | None:
    """The power price of ``tariff`` on ``day``, None without one."""
    if tariff is None:
        return None
    active = tariff.active_items(day)
    items = tuple(
        item
        for item in active
        if item.unit is Unit.KW_MONTH and item.side is Side.IMPORT and (not item.months or day.month in item.months)
    )
    if not items:
        return None
    group = items[0].group
    percent = sum(item.price for item in active if item.unit is Unit.PERCENT and item.side is Side.IMPORT and item.group is group)
    return PowerPrice(items, tariff.vat(Side.IMPORT, group), percent)


@dataclass(frozen=True)
class PeakState:
    """Peak power of the current month and the running interval.

    The month part changes only when an interval ends (see ``peak_state``);
    ``running`` updates the running interval.
    """

    billed_kw: float
    # Highest measured interval mean of the month (0 without any) and its start.
    measured_kw: float
    peak_start: datetime | None
    min_kw: float
    free_kw: float
    # Power price of the month for ``billed_kw`` and the part above the minimum.
    cost: float
    extra_cost: float
    interval_start: datetime
    interval_s: int
    # The running interval counts (inside the time window of the power price).
    counted: bool = True
    elapsed_s: float = 0.0
    imported_wh: float = 0.0

    @property
    def limit_w(self) -> float:
        """Import limit below which a new interval adds nothing (minus the buffer)."""
        return max(0.0, self.free_kw * 1000 - BUFFER_W)

    def allowed_w(self, limit_w: float) -> float:
        """Import allowed for the rest of the running interval to stay within ``limit_w`` on average."""
        if not self.counted:
            return math.inf
        budget_wh = limit_w * self.interval_s / 3600
        rest_s = max(self.interval_s - self.elapsed_s, MIN_REST_S)
        return max(0.0, (budget_wh - self.imported_wh) * 3600 / rest_s)

    def running(self, quarters: GridQuarters, now: float) -> PeakState:
        """With the import of the running interval until ``now`` (same interval)."""
        start = int(self.interval_start.timestamp())
        imported = sum(quarters.imported.get(slot, 0.0) for slot in range(start, start + self.interval_s, SLOT_S))
        return replace(self, elapsed_s=max(0.0, now - start), imported_wh=imported)

    @property
    def mean_kw(self) -> float:
        """Mean import of the running interval so far."""
        return self.imported_wh / 1000 * 3600 / self.elapsed_s if self.elapsed_s > 0 else 0.0


def month_powers(quarters: GridQuarters, now: float, interval_s: int) -> dict[datetime, float]:
    """Mean import power (kW) of the finished, recorded intervals of the local month of ``now``."""
    local = dt_util.as_local(dt_util.utc_from_timestamp(now))
    first = int(local.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())
    running = int(now - now % interval_s)
    powers: dict[datetime, float] = {}
    for start in range(first, running, interval_s):
        slots = range(start, start + interval_s, SLOT_S)
        if not all(quarters.complete(slot) for slot in slots):
            continue
        wh = sum(quarters.imported.get(slot, 0.0) for slot in slots)
        powers[dt_util.utc_from_timestamp(start)] = wh / 1000 * 3600 / interval_s
    return powers


def free_kw(price: PowerPrice, powers: dict[datetime, float], at: datetime) -> float:
    """Highest mean of a new interval at ``at`` that leaves the billed power as it is."""
    billed = price.billed_kw(powers)
    if price.billed_kw({**powers, at: billed}) > billed + FREE_RESOLUTION_KW:
        # Mean of several highest intervals: even the billed level raises it.
        low, high = 0.0, billed
    else:
        low, high = billed, billed * 2 + 100.0
    if price.billed_kw({**powers, at: low}) > billed + FREE_RESOLUTION_KW:
        return 0.0
    while high - low > FREE_RESOLUTION_KW:
        middle = (low + high) / 2
        if price.billed_kw({**powers, at: middle}) > billed + FREE_RESOLUTION_KW:
            high = middle
        else:
            low = middle
    return low


def peak_state(price: PowerPrice, quarters: GridQuarters, now: float) -> PeakState:
    """The month part for the interval of ``now``, with the running interval until ``now``."""
    interval_s = price.interval_s
    powers = {start: power for start, power in month_powers(quarters, now, interval_s).items() if price.counts(start)}
    start = int(now - now % interval_s)
    interval_start = dt_util.utc_from_timestamp(start)
    billed = price.billed_kw(powers)
    peak = max(powers.items(), key=lambda entry: entry[1], default=(None, 0.0))
    cost = price.cost(billed)
    minimum = price.base.billed_kw(0.0)
    return PeakState(
        billed_kw=billed,
        measured_kw=peak[1],
        peak_start=peak[0],
        min_kw=price.base.min_kw or 0.0,
        free_kw=free_kw(price, powers, interval_start),
        cost=cost,
        extra_cost=max(0.0, cost - price.cost(minimum)),
        interval_start=interval_start,
        interval_s=interval_s,
        counted=price.counts(interval_start),
    ).running(quarters, now)
