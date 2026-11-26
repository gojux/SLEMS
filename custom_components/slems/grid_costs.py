"""Costs of the grid import and credit of the feed-in as running totals.

For the energy dashboard of Home Assistant, which takes them as an "entity
tracking the total costs" next to an energy counter. A ``CostCounter`` per
side adds up, with the current tariff:

* the energy of the configured energy counter (the one the dashboard shows,
  so energy and money come from the same source): what the counter rose by
  between two reported values, spread evenly over that time and priced per
  quarter hour. The last value is stored, so the energy of a time Home
  Assistant did not run is added afterwards. Energy without a price (a
  missing market price) waits for it up to ``MAX_WAIT_S``, then it is left
  out and counted in ``unpriced_kwh``,
* the fixed costs by the time passed (``fixed_per_day``): the yearly items
  per day as on a bill, and the power price of the minimum power, which is
  due in any case, in equal parts over the days of its month,
* the power price above the minimum whenever the billed peak of the month
  rises (import only), in full when it happens.

A counter only follows while its sensor is enabled; ``pause`` forgets where
it stood, so nothing is added for the time in between and the total goes on
from its last value. The first values after that are the starting point: the
energy counter as it is, the fixed costs from now and the power price above
the minimum as billed so far.

The export total is what the feed-in earns: the credit minus the costs of
the feed-in.
"""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

from .power_peak import power_price
from .tariff import Side, Tariff, yearly_per_day

SLOT_S = 900
# An energy counter below this share of its last value was reset; a smaller
# step back is a correction of the device and waits until it is made up.
RESET_SHARE = 0.9
MAX_WAIT_S = 2 * 86400.0


def fixed_per_day(tariff: Tariff, side: Side, day: date) -> float:
    """Fixed costs of a side on ``day`` (€ incl. VAT; export: what the day
    earns): its yearly items and, for the import, that day's part of the
    power price of the minimum power."""
    amount = yearly_per_day(tariff, side, day)
    if side is Side.IMPORT and (price := power_price(tariff, day)) is not None:
        amount += price.minimum_cost / monthrange(day.year, day.month)[1]
    return amount


def spread_cost(kwh: float, start: float, end: float, price: Callable[[int], float | None]) -> float | None:
    """Amount (€) of ``kwh`` spread evenly over ``start`` to ``end`` (unix time),
    with ``price`` in ct/kWh per quarter hour start; None if a price is missing."""
    total = 0.0
    slot = int(start - start % SLOT_S)
    while slot < end:
        ct = price(slot)
        if ct is None:
            return None
        share = (min(end, slot + SLOT_S) - max(start, slot)) / (end - start)
        total += kwh * share * ct / 100
        slot += SLOT_S
    return total


@dataclass
class CostCounter:
    """Running total (€) of one side."""

    total: float = 0.0
    unpriced_kwh: float = 0.0
    # Energy counter: entity, value (kWh) and when it was reported (unix time).
    entity_id: str | None = None
    energy_kwh: float | None = None
    energy_at: float | None = None
    # Until when the fixed costs are added (unix time).
    fixed_at: float | None = None
    # Power price above the minimum added in the month ("YYYY-MM").
    peak_month: str | None = None
    peak_added: float = 0.0

    def pause(self) -> None:
        self.entity_id = None
        self.energy_kwh = None
        self.energy_at = None
        self.fixed_at = None
        self.peak_month = None
        self.peak_added = 0.0

    def add_energy(self, entity_id: str, kwh: float, at: float, price: Callable[[int], float | None]) -> None:
        """A value of the energy counter reported at ``at``."""
        if self.entity_id != entity_id or self.energy_kwh is None or self.energy_at is None:
            self.entity_id, self.energy_kwh, self.energy_at = entity_id, kwh, at
            return
        if at <= self.energy_at:
            return
        if kwh < self.energy_kwh:
            if kwh < RESET_SHARE * self.energy_kwh:
                self.energy_kwh, self.energy_at = kwh, at
            return
        delta = kwh - self.energy_kwh
        if delta > 0:
            amount = spread_cost(delta, self.energy_at, at, price)
            if amount is not None:
                self.total += amount
            elif at - self.energy_at <= MAX_WAIT_S:
                return
            else:
                self.unpriced_kwh += delta
        self.energy_kwh, self.energy_at = kwh, at

    def add_fixed(self, now: float, per_day: Callable[[date], float]) -> None:
        """The fixed costs until ``now``; ``per_day``: their amount (€) of a local day."""
        start = self.fixed_at
        self.fixed_at = now if start is None else max(start, now)
        while start is not None and start < now:
            day = dt_util.as_local(dt_util.utc_from_timestamp(start)).date()
            day_start = dt_util.start_of_local_day(day).timestamp()
            day_end = dt_util.start_of_local_day(day + timedelta(days=1)).timestamp()
            until = min(now, day_end)
            self.total += per_day(day) * (until - start) / (day_end - day_start)
            start = until

    def add_peak(self, month: str, cost: float) -> None:
        """The power price above the minimum of ``month`` as billed so far (€)."""
        if self.peak_month is None:
            self.peak_month, self.peak_added = month, cost
            return
        if month != self.peak_month:
            self.peak_month, self.peak_added = month, 0.0
        if cost > self.peak_added:
            self.total += cost - self.peak_added
            self.peak_added = cost

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": round(self.total, 6),
            "unpriced_kwh": round(self.unpriced_kwh, 3),
            "entity_id": self.entity_id,
            "energy_kwh": self.energy_kwh,
            "energy_at": self.energy_at,
            "fixed_at": self.fixed_at,
            "peak_month": self.peak_month,
            "peak_added": round(self.peak_added, 6),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> CostCounter:
        data = data or {}

        def number(key: str) -> float | None:
            return None if data.get(key) is None else float(data[key])

        return cls(
            total=number("total") or 0.0,
            unpriced_kwh=number("unpriced_kwh") or 0.0,
            entity_id=data.get("entity_id"),
            energy_kwh=number("energy_kwh"),
            energy_at=number("energy_at"),
            fixed_at=number("fixed_at"),
            peak_month=data.get("peak_month"),
            peak_added=number("peak_added") or 0.0,
        )
