"""Tests for the tariff comparison (made-up tariffs)."""

from datetime import date, datetime, time

import pytest

from homeassistant.util import dt as dt_util

from custom_components.slems.tariff import Group, Role, Side, Tariff, TariffItem, Unit
from custom_components.slems.tariff_comparison import compare, month_start


@pytest.fixture(autouse=True)
def vienna() -> None:
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Vienna"))


def hour(day: date, h: int) -> datetime:
    return dt_util.as_utc(datetime.combine(day, time(h), dt_util.get_default_time_zone()))


def test_month_start() -> None:
    assert month_start(date(2026, 3, 15)) == date(2026, 3, 1)
    assert month_start(date(2026, 3, 15), 11) == date(2025, 4, 1)
    assert month_start(date(2026, 12, 5), -1) == date(2027, 1, 1)


def test_costs_per_month_and_tariff() -> None:
    fixed = Tariff("Fixed", Role.CURRENT, (
        TariffItem("Energy", Side.IMPORT, Group.ENERGY, Unit.KWH, 20.0),
        TariffItem("Feed-in", Side.EXPORT, Group.ENERGY, Unit.KWH, 5.0),
    ), {(Side.IMPORT, Group.ENERGY): 20.0})
    spot = Tariff("Spot", Role.COMPARISON, (
        TariffItem("Energy", Side.IMPORT, Group.ENERGY, Unit.SPOT, 2.0),
    ), {})
    may, june = date(2026, 5, 31), date(2026, 6, 1)
    # The last hour of May (local) belongs to May, not to June.
    imported = {hour(may, 23): 1000, hour(june, 8): 2000}
    exported = {hour(june, 12): 4000}
    market = {hour(may, 23): 100.0}
    rows = compare({"a": fixed, "b": spot}, date(2026, 4, 1), date(2026, 6, 10), imported, exported, market)
    # April has no energy and is left out; June only to the 10th.
    assert [(row["month"], row["days"]) for row in rows] == [("2026-05", 31), ("2026-06", 10)]
    may_row, june_row = rows
    assert may_row["costs"]["a"]["total"] == pytest.approx(0.24)
    assert may_row["costs"]["b"]["total"] == pytest.approx(0.12)
    # June: 2 kWh × 20 ct + 20 % VAT minus 4 kWh × 5 ct credit.
    assert june_row["costs"]["a"] == {"import": 0.48, "export": 0.2, "total": 0.28, "unpriced_kwh": 0.0}
    # No market price in June.
    assert june_row["costs"]["b"]["unpriced_kwh"] == pytest.approx(2.0)
    assert june_row["import_kwh"] == 2.0 and june_row["export_kwh"] == 4.0
