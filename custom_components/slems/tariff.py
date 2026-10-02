"""Electricity tariffs as they appear on a bill, and what a period costs.

A tariff is a list of items, each as a line of a bill:

* ``side``: ``import`` (consumption bill) or ``export`` (feed-in credit),
* ``group``: ``energy``, ``grid`` or ``levies`` (the VAT rate is per side and
  group, e.g. no VAT on the feed-in energy but on its grid items),
* ``unit``: ``kwh`` (price in ct/kWh for the energy in its time window),
  ``year`` (€ per year, charged per day: price × days / 365), ``spot`` (the
  day-ahead price of the hour) or ``market_month`` (a monthly market price:
  entered per month, else the mean day-ahead price of the month weighted by
  the export, which approximates the market price of PV feed-in); for the
  last two the price is spot × (1 + ``factor_pct`` / 100) + ``price`` ct/kWh,
  or ``percent`` (``price`` % of the net amount of the other items of its
  side and group, e.g. a municipal levy of 7 % on the energy),
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
from dataclasses import dataclass, field, replace
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
    SPOT = "spot"
    MARKET_MONTH = "market_month"
    PERCENT = "percent"

    @property
    def per_kwh(self) -> bool:
        return self in (Unit.KWH, Unit.SPOT, Unit.MARKET_MONTH)

    @property
    def dynamic(self) -> bool:
        return self in (Unit.SPOT, Unit.MARKET_MONTH)


class Role(StrEnum):
    CURRENT = "current"
    COMPARISON = "comparison"


@dataclass(frozen=True)
class TariffItem:
    name: str
    side: Side
    group: Group
    unit: Unit
    # ct/kWh or €/year; for dynamic units the markup in ct/kWh.
    price: float
    valid_from: date | None = None
    # Empty: every month / weekday (1 = January, 0 = Monday).
    months: frozenset[int] = frozenset()
    weekdays: frozenset[int] = frozenset()
    # Local time of day [time_from, time_to); None: the whole day.
    time_from: time | None = None
    time_to: time | None = None
    # Dynamic units: share of the market price in % on top (negative: less).
    factor_pct: float = 0.0
    # Market price per month ("2026-01", ct/kWh), e.g. as published.
    month_prices: tuple[tuple[str, float], ...] = ()

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
            "factor_pct": self.factor_pct,
            "month_prices": dict(self.month_prices),
        }

    def ct_per_kwh(self, market_ct: float | None) -> float | None:
        """Price of a kWh; for dynamic units from the market price (None: unknown)."""
        if not self.unit.dynamic:
            return self.price
        if market_ct is None:
            return None
        return market_ct * (1 + self.factor_pct / 100) + self.price

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
            factor_pct=float(data.get("factor_pct") or 0.0),
            month_prices=tuple(sorted((str(k), float(v)) for k, v in (data.get("month_prices") or {}).items())),
        )


@dataclass(frozen=True)
class Tariff:
    name: str
    role: Role
    items: tuple[TariffItem, ...]
    # VAT in % per (side, group).
    vat_pct: Mapping[tuple[Side, Group], float]
    # Name of the tariff(s) a side comes from, if not ``name`` (see combine_tariffs).
    side_names: Mapping[Side, str] = field(default_factory=dict)

    def name_for(self, side: Side) -> str:
        return self.side_names.get(side, self.name)

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

    @property
    def dynamic(self) -> bool:
        return any(item.unit.dynamic for item in self.items)


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
    # Energy whose dynamic price was unknown (no market price for its hour).
    unpriced_kwh: float = 0.0

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
    market_prices: Mapping[datetime, float] | None = None,
) -> Bill:
    """Bill for the days ``start`` to ``end`` (both included).

    ``import_wh`` / ``export_wh`` map period starts (hours or quarter hours,
    any time zone) to the energy of that period, ``market_prices`` the same
    starts to the day-ahead price in €/MWh.
    """
    market_prices = market_prices or {}
    monthly = monthly_market_prices(export_wh, market_prices)
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

    def add_percent(items: list[TariffItem], side: Side, base: Mapping[Group, float]) -> None:
        """Percent items of ``side`` on the net amounts ``base`` of their group."""
        for item in items:
            if item.unit is Unit.PERCENT and item.side is side and base.get(item.group):
                add(item, base[item.group] * item.price / 100)

    day = start
    while day <= end:
        active = tariff.active_items(day)
        yearly: dict[Side, dict[Group, float]] = {side: {} for side in Side}
        for item in active:
            if item.unit is Unit.YEAR and _day_in_window(item, day):
                add(item, item.price / DAYS_PER_YEAR)
                yearly[item.side][item.group] = yearly[item.side].get(item.group, 0.0) + item.price / DAYS_PER_YEAR
        for side in Side:
            add_percent([i for i in active if _day_in_window(i, day)], side, yearly[side])
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
            spot = market_prices.get(hour)
            unpriced = False
            base: dict[Group, float] = {}
            for item in _chosen_items(tariff, side, local):
                price = item.ct_per_kwh(
                    _market_ct(item, local, None if spot is None else spot / 10, monthly.get((local.year, local.month)))
                )
                if price is None:
                    unpriced = True
                    continue
                add(item, kwh * price / 100, kwh)
                base[item.group] = base.get(item.group, 0.0) + kwh * price / 100
            add_percent(_percent_items(tariff, side, local), side, base)
            if unpriced:
                bill.unpriced_kwh += kwh

    bill.vat = sum(
        amount * tariff.vat(side, group) / 100 for (side, group), amount in bill.groups.items()
    )
    return bill


def _chosen_items(tariff: Tariff, side: Side, local: datetime) -> list[TariffItem]:
    """The kWh items of a side in effect at ``local``: per name the most specific."""
    chosen: dict[str, TariffItem] = {}
    for item in tariff.active_items(local.date()):
        if item.side is side and item.unit.per_kwh and item.in_window(local):
            other = chosen.get(item.name)
            if other is None or item.specificity > other.specificity:
                chosen[item.name] = item
    return list(chosen.values())


def _percent_items(tariff: Tariff, side: Side, local: datetime) -> list[TariffItem]:
    return [
        item
        for item in tariff.active_items(local.date())
        if item.unit is Unit.PERCENT and item.side is side and item.in_window(local)
    ]


def _market_ct(
    item: TariffItem, local: datetime, spot_ct: float | None, month_ct: float | None
) -> float | None:
    """Market price (ct/kWh) a dynamic item refers to at ``local``."""
    if item.unit is Unit.SPOT:
        return spot_ct
    if item.unit is Unit.MARKET_MONTH:
        entered = dict(item.month_prices).get(f"{local.year:04d}-{local.month:02d}")
        return entered if entered is not None else month_ct
    return None


def kwh_price(
    tariff: Tariff, side: Side, local: datetime, spot_ct: float | None, month_ct: float | None
) -> float | None:
    """Price of a kWh at ``local`` in ct incl. VAT, without yearly items.

    Import: what a kWh costs; export: what a kWh earns (credit minus the
    per kWh costs of the feed-in). None if a market price is missing.
    """
    groups: dict[Group, float] = {}
    for item in _chosen_items(tariff, side, local):
        price = item.ct_per_kwh(_market_ct(item, local, spot_ct, month_ct))
        if price is None:
            return None
        groups[item.group] = groups.get(item.group, 0.0) + price
    total = 0.0
    # Percent items raise their group's price.
    percent = {group: 0.0 for group in groups}
    for item in _percent_items(tariff, side, local):
        if item.group in percent:
            percent[item.group] += item.price
    for group, price in groups.items():
        sign = -1.0 if side is Side.EXPORT and group is Group.ENERGY else 1.0
        total += sign * price * (1 + percent[group] / 100) * (1 + tariff.vat(side, group) / 100)
    return total if side is Side.IMPORT else -total


def monthly_market_prices(
    export_wh: Mapping[datetime, float], market_prices: Mapping[datetime, float]
) -> dict[tuple[int, int], float]:
    """Mean day-ahead price (ct/kWh) per local month, weighted by the export.

    A month without export uses the plain mean.
    """
    sums: dict[tuple[int, int], list[float]] = {}
    for hour, price in market_prices.items():
        local = dt_util.as_local(hour)
        weight = max(0.0, export_wh.get(hour, 0.0))
        total = sums.setdefault((local.year, local.month), [0.0, 0.0, 0.0, 0.0])
        total[0] += price * weight
        total[1] += weight
        total[2] += price
        total[3] += 1
    return {
        month: (weighted / weight if weight > 0 else plain / count) / 10
        for month, (weighted, weight, plain, count) in sums.items()
    }


def parse_month_prices(text: str) -> tuple[tuple[str, float], ...]:
    """'2026-01: 8.5; 2026-02: 7,9' -> (("2026-01", 8.5), ("2026-02", 7.9))."""
    prices: dict[str, float] = {}
    for part in text.replace("\n", ";").split(";"):
        if not part.strip():
            continue
        month, _, value = part.partition(":")
        month = month.strip()
        year, _, number = month.partition("-")
        if not (len(year) == 4 and year.isdigit() and number.isdigit() and 1 <= int(number) <= 12):
            raise ValueError(month)
        prices[f"{int(year):04d}-{int(number):02d}"] = float(value.strip().replace(",", "."))
    return tuple(sorted(prices.items()))


def format_month_prices(prices: Iterable[tuple[str, float]]) -> str:
    return "; ".join(f"{month}: {price:g}" for month, price in prices)


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


def _sides(tariff: Tariff) -> set[Side]:
    return {item.side for item in tariff.items}


def _with_side(target: Tariff, source: Tariff, side: Side) -> Tariff:
    """``target`` with the items and VAT of ``source`` for ``side`` added."""
    return replace(
        target,
        items=target.items + tuple(item for item in source.items if item.side is side),
        vat_pct={
            **target.vat_pct,
            **{key: value for key, value in source.vat_pct.items() if key[0] is side},
        },
    )


def combine_tariffs(tariffs: Mapping[str, Tariff]) -> dict[str, Tariff]:
    """The current contract first, then the comparison tariffs, each complete.

    Several current tariffs (e.g. import and feed-in with different
    contracts) form one contract, kept under the id of the first. A
    comparison tariff without items for a side takes that side from the
    current contract, so tariffs are compared with their total costs.
    """
    current_ids = [key for key, tariff in tariffs.items() if tariff.role is Role.CURRENT]
    result: dict[str, Tariff] = {}
    current: Tariff | None = None
    for key in current_ids:
        tariff = tariffs[key]
        if current is None:
            current = tariff
            continue
        current = replace(current, name=f"{current.name} + {tariff.name}")
        for side in _sides(tariff):
            current = _with_side(current, tariff, side)
    if current is not None and len(current_ids) > 1:
        current = replace(
            current,
            side_names={
                side: " + ".join(tariffs[key].name for key in current_ids if side in _sides(tariffs[key]))
                for side in _sides(current)
            },
        )
    if current is not None:
        result[current_ids[0]] = current
    for key, tariff in tariffs.items():
        if tariff.role is Role.CURRENT:
            continue
        if current is not None:
            for side in Side:
                if side not in _sides(tariff) and side in _sides(current):
                    tariff = replace(
                        _with_side(tariff, current, side),
                        side_names={**tariff.side_names, side: current.name_for(side)},
                    )
        result[key] = tariff
    return result
