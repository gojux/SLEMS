"""Tests for the estimate of what peak shaving would have saved (made-up prices)."""

from datetime import datetime

import pytest

from homeassistant.util import dt as dt_util

from custom_components.slems.grid_quarters import SLOT_S, GridQuarters
from custom_components.slems.peak_backtest import estimate, month_powers
from custom_components.slems.power_peak import power_price
from custom_components.slems.price_backtest import BacktestBattery
from custom_components.slems.tariff import Group, Role, Side, Tariff, TariffItem, Unit


@pytest.fixture(autouse=True)
def vienna() -> None:
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Vienna"))


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=dt_util.get_default_time_zone())


PRICE = power_price(
    Tariff("Grid", Role.CURRENT, (
        TariffItem("Power", Side.IMPORT, Group.GRID, Unit.KW_MONTH, 3.0, min_kw=2.0),
    ), {}),
    datetime(2026, 10, 1).date(),
)


def battery(discharge_w: float = 5000, usable_wh: float = 5000) -> BacktestBattery:
    return BacktestBattery(
        capacity_wh=10000, min_wh=1000, full_wh=1000 + usable_wh, max_charge_w=5000,
        max_discharge_w=discharge_w, efficiency=1.0, wear_ct=0.0, grid_charge=False, grid_max_wh=0.0,
    )


def test_month_powers_from_recorded_quarter_hours() -> None:
    grid = GridQuarters()
    start = int(at(3, 18).timestamp())
    for slot in range(start, start + 4 * SLOT_S, SLOT_S):
        grid.covered[slot] = SLOT_S
        grid.imported[slot] = 1000.0
    months = month_powers(grid, SLOT_S)
    assert list(months) == [datetime(2026, 10, 1).date()]
    assert months[datetime(2026, 10, 1).date()][dt_util.as_utc(at(3, 18))] == pytest.approx(4.0)
    # Hours: the four quarters together.
    assert month_powers(grid, 3600)[datetime(2026, 10, 1).date()][dt_util.as_utc(at(3, 18))] == pytest.approx(4.0)


def test_lowest_limit_the_batteries_could_hold() -> None:
    powers = {at(3, 18): 6.0, at(3, 18, 15): 3.0, at(4, 19): 2.5}
    # Enough power and energy: down to the minimum of 2 kW, 4 kW less billed.
    result = estimate(PRICE, powers, battery(), 25.0)
    assert result.recorded_kw == pytest.approx(6.0)
    assert result.limit_kw == pytest.approx(2.0)
    assert result.saving == pytest.approx(4 * 3.0)
    # 2 kW discharge power: not below 4 kW.
    weak = estimate(PRICE, powers, battery(discharge_w=2000), 25.0)
    assert weak.limit_kw == pytest.approx(4.0, abs=0.05)
    # Little energy per day: above 3 kW only the 6 kW quarter hour counts, (6 - L) / 4 <= 0.5 kWh needs L >= 4.
    small = estimate(PRICE, powers, battery(usable_wh=500), 25.0)
    assert small.limit_kw == pytest.approx(4.0, abs=0.05)
    assert small.energy_kwh == pytest.approx(0.5, abs=0.02)
    assert estimate(PRICE, {}, battery(), 25.0) is None
