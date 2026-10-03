"""Tests for the prices of the price chart (made-up tariff and prices)."""

from datetime import date, datetime, time
from types import SimpleNamespace

import pytest

from homeassistant.util import dt as dt_util

from custom_components.slems.market_prices import hourly_means
from custom_components.slems.price_chart import day_prices
from custom_components.slems.tariff import Group, Role, Side, Tariff, TariffItem, Unit


@pytest.fixture(autouse=True)
def vienna() -> None:
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Vienna"))


def fake_prices(prices: dict[int, float]) -> SimpleNamespace:
    def price_at(moment: datetime) -> float | None:
        stamp = int(moment.timestamp())
        return prices.get(stamp - stamp % 900)

    return SimpleNamespace(
        price_at=price_at, hourly_means=lambda start, end: hourly_means(prices, start, end), references={}
    )


def test_quarter_hours_of_a_day_with_monthly_mean() -> None:
    tariff = Tariff("T", Role.CURRENT, (
        TariffItem("Energy", Side.IMPORT, Group.ENERGY, Unit.SPOT, 2.0),
        TariffItem("Feed-in", Side.EXPORT, Group.ENERGY, Unit.MARKET_MONTH, 0.0),
    ), {(Side.IMPORT, Group.ENERGY): 10.0})
    day = date(2026, 3, 29)  # 23 hours: the clock goes forward
    zone = dt_util.get_default_time_zone()
    first = int(datetime.combine(date(2026, 3, 1), time(), zone).timestamp())
    noon = int(datetime.combine(day, time(12), zone).timestamp())
    prices = {first: 40.0, noon: 100.0}
    slots = day_prices(tariff, fake_prices(prices), day)
    assert len(slots) == 92
    at_noon = next(s for s in slots if s["start"].startswith("2026-03-29T12:00"))
    assert at_noon["spot"] == pytest.approx(10.0)
    assert at_noon["import"] == pytest.approx((10.0 + 2.0) * 1.1)
    # The monthly market price so far: mean of the two stored hours.
    assert at_noon["export"] == pytest.approx(7.0)
    assert slots[0]["spot"] is None and slots[0]["import"] is None


def test_prices_per_quarter_hour_and_per_hour() -> None:
    from datetime import timedelta

    from custom_components.slems.price_chart import hourly_import_prices, period_import_prices

    tariff = Tariff("T", Role.CURRENT, (TariffItem("Energy", Side.IMPORT, Group.ENERGY, Unit.SPOT, 0.0),), {})
    zone = dt_util.get_default_time_zone()
    start = datetime(2026, 11, 3, 18, 20, tzinfo=zone)
    hour = int(datetime(2026, 11, 3, 18, 0, tzinfo=zone).timestamp())
    # €/MWh per quarter: 18:00 100, 18:15 200, 18:30 300, 18:45 400.
    prices = {hour + 900 * i: 100.0 * (i + 1) for i in range(4)}
    quarters = period_import_prices(tariff, fake_prices(prices), start, start + timedelta(minutes=40))
    # From the quarter of the start (18:15) on, in ct/kWh.
    assert list(quarters.values()) == pytest.approx([20.0, 30.0, 40.0])
    assert min(quarters) == datetime(2026, 11, 3, 18, 15, tzinfo=zone)
    hourly = hourly_import_prices(tariff, fake_prices(prices), start, start + timedelta(minutes=40))
    assert list(hourly.values()) == pytest.approx([25.0])
