"""Tests for tariffs and bills (made-up example values)."""

from datetime import date, datetime, time, timedelta

import pytest

from homeassistant.util import dt as dt_util

from custom_components.slems.tariff import (
    Group,
    Role,
    Side,
    Tariff,
    TariffItem,
    Unit,
    compute_bill,
    tariff_from_data,
    vat_data,
)


@pytest.fixture(autouse=True)
def vienna() -> None:
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Vienna"))


def local(day: date, hour: int) -> datetime:
    return datetime.combine(day, time(hour), tzinfo=dt_util.get_default_time_zone())


def example() -> Tariff:
    items = (
        TariffItem("Base fee", Side.IMPORT, Group.ENERGY, Unit.YEAR, 36.5),
        TariffItem("Energy", Side.IMPORT, Group.ENERGY, Unit.KWH, 20.0),
        TariffItem("Discount", Side.IMPORT, Group.ENERGY, Unit.KWH, -2.0),
        TariffItem("Grid", Side.IMPORT, Group.GRID, Unit.KWH, 8.0),
        # Cheaper grid at noon from April to September.
        TariffItem(
            "Grid", Side.IMPORT, Group.GRID, Unit.KWH, 4.0,
            months=frozenset(range(4, 10)), time_from=time(10), time_to=time(16),
        ),
        TariffItem("Levy", Side.IMPORT, Group.LEVIES, Unit.KWH, 1.0),
        TariffItem("Feed-in", Side.EXPORT, Group.ENERGY, Unit.KWH, 6.0),
        TariffItem("Meter", Side.EXPORT, Group.GRID, Unit.YEAR, 7.3),
    )
    vat = {
        (Side.IMPORT, Group.ENERGY): 20.0,
        (Side.IMPORT, Group.GRID): 20.0,
        (Side.IMPORT, Group.LEVIES): 20.0,
        (Side.EXPORT, Group.GRID): 20.0,
    }
    return Tariff("Example", Role.CURRENT, items, vat)


def test_time_windows_and_overlapping_grid_prices() -> None:
    # The window item and the general item have the same name: both apply in
    # their windows; the general one must not apply at noon in summer.
    tariff = example()
    day = date(2026, 6, 1)
    bill = compute_bill(tariff, day, day, {local(day, 12): 1000, local(day, 20): 1000}, {})
    # Noon in summer 4 ct (the window item replaces the general one), evening 8 ct.
    assert bill.groups[(Side.IMPORT, Group.GRID)] == pytest.approx(0.04 + 0.08)
    assert bill.lines[(Side.IMPORT, "Discount")] == pytest.approx(-0.04)
    # Energy 20 − 2 ct, grid, levy 1 ct, base fee one day of 36.5 €/year.
    assert bill.net == pytest.approx(0.40 - 0.04 + 0.12 + 0.02 + 0.1 + 0.02)


def test_year_items_per_day_and_vat_per_group() -> None:
    items = (
        TariffItem("Base fee", Side.IMPORT, Group.ENERGY, Unit.YEAR, 36.5),
        TariffItem("Energy", Side.IMPORT, Group.ENERGY, Unit.KWH, 20.0),
        TariffItem("Feed-in", Side.EXPORT, Group.ENERGY, Unit.KWH, 6.0),
        TariffItem("Meter", Side.EXPORT, Group.GRID, Unit.YEAR, 7.3),
    )
    tariff = Tariff("T", Role.CURRENT, items, {(Side.IMPORT, Group.ENERGY): 20.0, (Side.EXPORT, Group.GRID): 20.0})
    start, end = date(2026, 1, 1), date(2026, 1, 10)
    hours = {local(start + timedelta(days=d), 18): 2000 for d in range(10)}
    feed = {local(start + timedelta(days=d), 12): 5000 for d in range(10)}
    bill = compute_bill(tariff, start, end, hours, feed)
    # 10 days of 36.5 €/year = 1 €; 20 kWh × 20 ct = 4 €.
    assert bill.groups[(Side.IMPORT, Group.ENERGY)] == pytest.approx(5.0)
    # Feed-in is a credit: 50 kWh × 6 ct; the meter 10 days of 7.3 €/year a cost.
    assert bill.lines[(Side.EXPORT, "Feed-in")] == pytest.approx(-3.0)
    assert bill.lines[(Side.EXPORT, "Meter")] == pytest.approx(0.2)
    # VAT: 20 % on the import energy and the export grid item, none on the feed-in.
    assert bill.vat == pytest.approx(1.0 + 0.04)
    assert bill.import_kwh == pytest.approx(20) and bill.export_kwh == pytest.approx(50)
    assert bill.side_gross(tariff, Side.EXPORT) == pytest.approx(-3.0 + 0.24)


def test_window_price_applies_only_inside_it() -> None:
    items = (
        TariffItem("Grid", Side.IMPORT, Group.GRID, Unit.KWH, 8.0,
                   months=frozenset({1, 2, 3, 10, 11, 12})),
        TariffItem("Grid", Side.IMPORT, Group.GRID, Unit.KWH, 8.0,
                   months=frozenset(range(4, 10)), time_from=time(16), time_to=time(10)),
        TariffItem("Grid", Side.IMPORT, Group.GRID, Unit.KWH, 4.0,
                   months=frozenset(range(4, 10)), time_from=time(10), time_to=time(16)),
    )
    tariff = Tariff("T", Role.CURRENT, items, {})
    summer, winter = date(2026, 6, 1), date(2026, 1, 15)
    bill = compute_bill(
        tariff, winter, summer,
        {local(summer, 12): 1000, local(summer, 22): 1000, local(winter, 12): 1000},
        {},
    )
    # 1 kWh at 4 ct (summer noon) + 2 kWh at 8 ct.
    assert bill.groups[(Side.IMPORT, Group.GRID)] == pytest.approx(0.04 + 0.16)
    assert bill.quantities[(Side.IMPORT, "Grid")] == pytest.approx(3)


def test_price_change_from_a_date() -> None:
    items = (
        TariffItem("Energy", Side.IMPORT, Group.ENERGY, Unit.KWH, 20.0),
        TariffItem("Energy", Side.IMPORT, Group.ENERGY, Unit.KWH, 25.0, valid_from=date(2026, 3, 1)),
    )
    tariff = Tariff("T", Role.CURRENT, items, {})
    feb, mar = date(2026, 2, 28), date(2026, 3, 1)
    bill = compute_bill(tariff, feb, mar, {local(feb, 9): 1000, local(mar, 9): 1000}, {})
    assert bill.groups[(Side.IMPORT, Group.ENERGY)] == pytest.approx(0.20 + 0.25)


def test_hours_outside_the_period_do_not_count() -> None:
    tariff = Tariff("T", Role.CURRENT, (TariffItem("E", Side.IMPORT, Group.ENERGY, Unit.KWH, 10.0),), {})
    day = date(2026, 5, 2)
    bill = compute_bill(tariff, day, day, {local(day, 0): 1000, local(day + timedelta(days=1), 0): 1000}, {})
    assert bill.import_kwh == pytest.approx(1)


def test_round_trip_through_subentry_data() -> None:
    tariff = example()
    data = {
        "role": "current",
        "vat": vat_data([(side, group, value) for (side, group), value in tariff.vat_pct.items()]),
        "items": [item.as_dict() for item in tariff.items],
    }
    restored = tariff_from_data("Example", data)
    assert restored.items == tariff.items
    assert restored.vat_pct == tariff.vat_pct


def test_item_description_compresses_months() -> None:
    from custom_components.slems.config_flow import _describe_item

    item = TariffItem(
        "Grid", Side.IMPORT, Group.GRID, Unit.KWH, 3.5,
        months=frozenset(range(4, 10)), weekdays=frozenset({0, 1, 2, 3, 4}),
        time_from=time(10), time_to=time(16),
    )
    assert _describe_item(item, "en") == (
        "Grid: 3.5 ct/kWh (Import, grid) · months 4–9 · weekdays 1–5 · 10:00–16:00"
    )
    assert _describe_item(item, "de").startswith("Grid: 3,5 ct/kWh (Bezug, Netz) · Monate 4–9")
