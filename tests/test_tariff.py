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


def test_spot_price_with_share_and_markup() -> None:
    from custom_components.slems.tariff import parse_month_prices

    items = (
        TariffItem("Energy", Side.IMPORT, Group.ENERGY, Unit.SPOT, 1.5, factor_pct=10.0),
        TariffItem("Feed-in", Side.EXPORT, Group.ENERGY, Unit.MARKET_MONTH, -0.5,
                   month_prices=parse_month_prices("2026-06: 8")),
    )
    tariff = Tariff("Dynamic", Role.COMPARISON, items, {})
    assert tariff.dynamic
    june, july = date(2026, 6, 1), date(2026, 7, 1)
    noon, evening = dt_util.as_utc(local(june, 12)), dt_util.as_utc(local(june, 20))
    market = {noon: 50.0, evening: 150.0}
    bill = compute_bill(tariff, june, june, {noon: 1000, evening: 1000}, {noon: 2000}, market)
    # Import: 5 ct × 1.1 + 1.5 and 15 ct × 1.1 + 1.5.
    assert bill.groups[(Side.IMPORT, Group.ENERGY)] == pytest.approx((7.0 + 18.0) / 100)
    # Feed-in: the entered market price of June 8 ct − 0.5 ct, a credit.
    assert bill.lines[(Side.EXPORT, "Feed-in")] == pytest.approx(-2 * 0.075)
    # July without an entered value: the mean weighted by the feed-in.
    j1, j2 = dt_util.as_utc(local(july, 11)), dt_util.as_utc(local(july, 13))
    bill = compute_bill(
        tariff, july, july, {}, {j1: 3000, j2: 1000}, {j1: 40.0, j2: 80.0, dt_util.as_utc(local(july, 20)): 200.0}
    )
    assert bill.lines[(Side.EXPORT, "Feed-in")] == pytest.approx(-4 * (5.0 - 0.5) / 100)


def test_hours_without_market_price_are_reported() -> None:
    tariff = Tariff("T", Role.CURRENT, (
        TariffItem("Energy", Side.IMPORT, Group.ENERGY, Unit.SPOT, 0.0),
        TariffItem("Grid", Side.IMPORT, Group.GRID, Unit.KWH, 8.0),
    ), {})
    day = date(2026, 6, 1)
    bill = compute_bill(tariff, day, day, {dt_util.as_utc(local(day, 12)): 2000}, {}, {})
    assert bill.unpriced_kwh == pytest.approx(2)
    assert bill.groups == {(Side.IMPORT, Group.GRID): pytest.approx(0.16)}


def test_month_prices_parsing_and_description() -> None:
    from custom_components.slems.config_flow import _describe_item
    from custom_components.slems.tariff import format_month_prices, parse_month_prices

    assert parse_month_prices("2026-1: 8,5;\n2026-02: 7.9") == (("2026-01", 8.5), ("2026-02", 7.9))
    assert format_month_prices(parse_month_prices("2026-02: 7.9; 2026-01: 8.5")) == "2026-01: 8.5; 2026-02: 7.9"
    with pytest.raises(ValueError):
        parse_month_prices("January: 8")
    item = TariffItem("Energy", Side.IMPORT, Group.ENERGY, Unit.SPOT, 1.5, factor_pct=10.0)
    assert _describe_item(item, "en") == "Energy: spot price × 1.1 + 1.5 ct/kWh (Import, energy)"
    assert TariffItem.from_dict(item.as_dict()) == item


def test_kwh_price_per_quarter_hour() -> None:
    from custom_components.slems.tariff import kwh_price

    tariff = example()
    summer = date(2026, 6, 1)
    noon = local(summer, 12)
    # Energy 20 − 2 ct, grid 4 ct at noon in summer, levy 1 ct; 20 % VAT; no yearly items.
    assert kwh_price(tariff, Side.IMPORT, noon, None, None) == pytest.approx(23 * 1.2)
    assert kwh_price(tariff, Side.IMPORT, local(summer, 20), None, None) == pytest.approx(27 * 1.2)
    # Feed-in: the credit of 6 ct (no VAT).
    assert kwh_price(tariff, Side.EXPORT, noon, None, None) == pytest.approx(6.0)
    spot = Tariff("S", Role.CURRENT, (TariffItem("E", Side.IMPORT, Group.ENERGY, Unit.SPOT, 1.0),), {})
    assert kwh_price(spot, Side.IMPORT, noon, None, None) is None
    assert kwh_price(spot, Side.IMPORT, noon, -3.0, None) == pytest.approx(-2.0)


def test_separate_contracts_are_combined_and_comparisons_completed() -> None:
    from custom_components.slems.tariff import combine_tariffs

    supply = Tariff("Supply", Role.CURRENT, (
        TariffItem("Energy", Side.IMPORT, Group.ENERGY, Unit.KWH, 20.0),
    ), {(Side.IMPORT, Group.ENERGY): 20.0})
    feed_in = Tariff("Feed-in", Role.CURRENT, (
        TariffItem("Credit", Side.EXPORT, Group.ENERGY, Unit.KWH, 7.0),
    ), {(Side.EXPORT, Group.GRID): 20.0})
    dynamic = Tariff("Dynamic", Role.COMPARISON, (
        TariffItem("Energy", Side.IMPORT, Group.ENERGY, Unit.SPOT, 1.5),
    ), {(Side.IMPORT, Group.ENERGY): 20.0})
    combined = combine_tariffs({"a": supply, "b": feed_in, "c": dynamic})
    # One current contract (under the first id), then the comparison.
    assert list(combined) == ["a", "c"]
    current = combined["a"]
    assert current.name == "Supply + Feed-in"
    assert {item.side for item in current.items} == {Side.IMPORT, Side.EXPORT}
    assert current.vat(Side.EXPORT, Group.GRID) == 20.0
    # The comparison keeps its own import and takes the feed-in of the current contract.
    comparison = combined["c"]
    assert [item.name for item in comparison.items] == ["Energy", "Credit"]
    assert comparison.items[0].unit is Unit.SPOT
    # A single complete tariff stays as it is.
    assert combine_tariffs({"x": example()}) == {"x": example()}


def test_side_names_of_a_combined_contract() -> None:
    from custom_components.slems.tariff import combine_tariffs

    supply = Tariff("Supply", Role.CURRENT, (TariffItem("E", Side.IMPORT, Group.ENERGY, Unit.KWH, 20.0),), {})
    feed_in = Tariff("Feed-in", Role.CURRENT, (TariffItem("C", Side.EXPORT, Group.ENERGY, Unit.KWH, 7.0),), {})
    dynamic = Tariff("Dynamic", Role.COMPARISON, (TariffItem("E", Side.IMPORT, Group.ENERGY, Unit.SPOT, 1.0),), {})
    combined = combine_tariffs({"a": supply, "b": feed_in, "c": dynamic})
    assert combined["a"].name_for(Side.IMPORT) == "Supply"
    assert combined["a"].name_for(Side.EXPORT) == "Feed-in"
    assert combined["c"].name_for(Side.IMPORT) == "Dynamic"
    assert combined["c"].name_for(Side.EXPORT) == "Feed-in"
