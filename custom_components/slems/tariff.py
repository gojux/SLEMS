"""Electricity tariffs as they appear on a bill, and what a period costs.

A tariff is a list of items, each as a line of a bill:

* ``side``: ``import`` (consumption bill) or ``export`` (feed-in credit),
* ``group``: ``energy``, ``grid`` or ``levies`` (the VAT rate is per side and
  group, e.g. no VAT on the feed-in energy but on its grid items),
* ``unit``: ``kwh`` (price in ct/kWh for the energy in its time window) or
  ``year`` (€ per year, charged per day: price × days / 365),
* optional time window (months, weekdays, hours ``from`` – ``to`` local time,
  e.g. a reduced grid price at noon in summer) and ``valid_from`` (a later
  version of the same item replaces it from that date).

Items with the same name and side are one price: in an hour the most specific
matching item counts (a time window before months before weekdays before none),
so "grid 8 ct, at noon in summer 4 ct" is two items of the same name.

On the import side every item is a cost (a discount has a negative price); on
the export side the energy items are a credit and all other items a cost, as
on a feed-in credit note.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from typing import Any

from homeassistant.util import dt as dt_util

DAYS_PER_YEAR = 365


class Side(StrEnum):
    IMPORT = "import"
    EXPORT = "export"


class Group(StrEnum):
    ENERGY = "energy"
    GRID = "grid"
    LEVIES = "levies"


class Unit(StrEnum):
    KWH = "kwh"
    YEAR = "year"


class Role(StrEnum):
    CURRENT = "current"
    COMPARISON = "comparison"


@dataclass(frozen=True)
class TariffItem:
    name: str
    side: Side
    group: Group
    unit: Unit
    # ct/kWh or €/year.
    price: float
    valid_from: date | None = None
    # Empty: every month / weekday (1 = January, 0 = Monday).
    months: frozenset[int] = frozenset()
    weekdays: frozenset[int] = frozenset()
    # Local time of day [time_from, time_to); None: the whole day.
    time_from: time | None = None
    time_to: time | None = None

    @property
    def specificity(self) -> int:
        return (
            4 * (self.time_from is not None and self.time_to is not None)
            + 2 * bool(self.months)
            + bool(self.weekdays)
        )

    def in_window(self, moment: datetime) -> bool:
        """Whether an hour starting at ``moment`` (local) falls into the item's window."""
        if self.months and moment.month not in self.months:
            return False
        if self.weekdays and moment.weekday() not in self.weekdays:
            return False
        if self.time_from is not None and self.time_to is not None:
            clock = moment.time()
            if self.time_from <= self.time_to:
                return self.time_from <= clock < self.time_to
            return clock >= self.time_from or clock < self.time_to
        return True

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "side": self.side.value,
            "group": self.group.value,
            "unit": self.unit.value,
            "price": self.price,
            "valid_from": self.valid_from.isoformat() if self.valid_from else None,
            "months": sorted(self.months),
            "weekdays": sorted(self.weekdays),
            "time_from": self.time_from.strftime("%H:%M") if self.time_from else None,
            "time_to": self.time_to.strftime("%H:%M") if self.time_to else None,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TariffItem:
        def clock(value: str | None) -> time | None:
            return time.fromisoformat(value) if value else None

        return cls(
            name=data["name"],
            side=Side(data["side"]),
            group=Group(data["group"]),
            unit=Unit(data["unit"]),
            price=float(data["price"]),
            valid_from=date.fromisoformat(data["valid_from"]) if data.get("valid_from") else None,
            months=frozenset(int(m) for m in data.get("months") or ()),
            weekdays=frozenset(int(d) for d in data.get("weekdays") or ()),
            time_from=clock(data.get("time_from")),
            time_to=clock(data.get("time_to")),
        )


@dataclass(frozen=True)
class Tariff:
    name: str
    role: Role
    items: tuple[TariffItem, ...]
    # VAT in % per (side, group).
    vat_pct: Mapping[tuple[Side, Group], float]

    def vat(self, side: Side, group: Group) -> float:
        return self.vat_pct.get((side, group), 0.0)

    def active_items(self, day: date) -> list[TariffItem]:
        """The items in effect on ``day``: per name and window the latest version."""
        latest: dict[tuple, TariffItem] = {}
        for item in self.items:
            if item.valid_from is not None and item.valid_from > day:
                continue
            key = (item.name, item.side, item.months, item.weekdays, item.time_from, item.time_to)
            current = latest.get(key)
            if current is None or (item.valid_from or date.min) >= (current.valid_from or date.min):
                latest[key] = item
        return list(latest.values())


@dataclass
class Bill:
    """Net amounts (€) per item name and side, and the totals with VAT."""

    lines: dict[tuple[Side, str], float] = field(default_factory=dict)
    # kWh billed per item (for kWh items).
    quantities: dict[tuple[Side, str], float] = field(default_factory=dict)
    groups: dict[tuple[Side, Group], float] = field(default_factory=dict)
    vat: float = 0.0
    import_kwh: float = 0.0
    export_kwh: float = 0.0

    @property
    def net(self) -> float:
        return sum(self.groups.values())

    @property
    def gross(self) -> float:
        """Amount due (+) or credited (−) including VAT."""
        return self.net + self.vat

    def side_gross(self, tariff: Tariff, side: Side) -> float:
        return sum(
            amount * (1 + tariff.vat(side, group) / 100)
            for (item_side, group), amount in self.groups.items()
            if item_side is side
        )


def compute_bill(
    tariff: Tariff,
    start: date,
    end: date,
    import_wh: Mapping[datetime, float],
    export_wh: Mapping[datetime, float],
) -> Bill:
    """Bill for the days ``start`` to ``end`` (both included).

    ``import_wh`` / ``export_wh`` map hour starts (any time zone) to the energy
    of that hour.
    """
    bill = Bill()
    sign = {
        (side, group): (-1.0 if side is Side.EXPORT and group is Group.ENERGY else 1.0)
        for side in Side
        for group in Group
    }

    def add(item: TariffItem, amount: float, kwh: float | None = None) -> None:
        key = (item.side, item.name)
        amount *= sign[(item.side, item.group)]
        bill.lines[key] = bill.lines.get(key, 0.0) + amount
        bill.groups[(item.side, item.group)] = bill.groups.get((item.side, item.group), 0.0) + amount
        if kwh is not None:
            bill.quantities[key] = bill.quantities.get(key, 0.0) + kwh

    day = start
    while day <= end:
        for item in tariff.active_items(day):
            if item.unit is Unit.YEAR and _day_in_window(item, day):
                add(item, item.price / DAYS_PER_YEAR)
        day += timedelta(days=1)

    for series, side in ((import_wh, Side.IMPORT), (export_wh, Side.EXPORT)):
        for hour, wh in series.items():
            local = dt_util.as_local(hour)
            if not start <= local.date() <= end or wh <= 0:
                continue
            kwh = wh / 1000
            if side is Side.IMPORT:
                bill.import_kwh += kwh
            else:
                bill.export_kwh += kwh
            chosen: dict[str, TariffItem] = {}
            for item in tariff.active_items(local.date()):
                if item.side is side and item.unit is Unit.KWH and item.in_window(local):
                    other = chosen.get(item.name)
                    if other is None or item.specificity > other.specificity:
                        chosen[item.name] = item
            for item in chosen.values():
                add(item, kwh * item.price / 100, kwh)

    bill.vat = sum(
        amount * tariff.vat(side, group) / 100 for (side, group), amount in bill.groups.items()
    )
    return bill


def _day_in_window(item: TariffItem, day: date) -> bool:
    """A yearly item with months / weekdays only counts on those days."""
    if item.months and day.month not in item.months:
        return False
    return not item.weekdays or day.weekday() in item.weekdays


def tariff_from_data(name: str, data: Mapping[str, Any]) -> Tariff:
    """Tariff from the data of its config subentry."""
    vat = {
        (Side(side), Group(group)): float(value)
        for key, value in (data.get("vat") or {}).items()
        for side, group in [key.split(".", 1)]
    }
    return Tariff(
        name=name,
        role=Role(data.get("role", Role.CURRENT)),
        items=tuple(TariffItem.from_dict(item) for item in data.get("items") or ()),
        vat_pct=vat,
    )


def vat_data(values: Iterable[tuple[Side, Group, float]]) -> dict[str, float]:
    return {f"{side.value}.{group.value}": value for side, group, value in values}
