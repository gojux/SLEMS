"""Tests for the peak power with a power price (made-up prices)."""

from datetime import datetime

import pytest

from homeassistant.util import dt as dt_util

from custom_components.slems.grid_quarters import SLOT_S, GridQuarters
from custom_components.slems.power_peak import (
    BUFFER_W,
    MIN_REST_S,
    peak_state,
    power_price,
)
from custom_components.slems.tariff import Group, Role, Side, Tariff, TariffItem, Unit


@pytest.fixture(autouse=True)
def vienna() -> None:
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Vienna"))


def ts(day: int, hour: int, minute: int = 0, second: int = 0) -> float:
    return datetime(2026, 11, day, hour, minute, second, tzinfo=dt_util.get_default_time_zone()).timestamp()


def tariff(**fields) -> Tariff:
    """3 € per kW and month up to 10 kW, 6 € above, at least 2 kW, 20 % VAT."""
    return Tariff("Grid", Role.CURRENT, (
        TariffItem("Power", Side.IMPORT, Group.GRID, Unit.KW_MONTH, 3.0, min_kw=2.0, kw_to=10.0, **fields),
        TariffItem("Power above 10 kW", Side.IMPORT, Group.GRID, Unit.KW_MONTH, 6.0, min_kw=2.0, kw_from=10.0, **fields),
        TariffItem("Grid", Side.IMPORT, Group.GRID, Unit.KWH, 5.0),
    ), {(Side.IMPORT, Group.GRID): 20.0})


def recorded(*quarters: tuple[float, float]) -> GridQuarters:
    """Quarter hours (unix start, Wh) fully recorded, the rest of the month 0 Wh."""
    grid = GridQuarters()
    for slot in range(int(ts(1, 0)), int(ts(10, 12)), SLOT_S):
        grid.covered[slot] = SLOT_S
    for start, wh in quarters:
        grid.imported[int(start)] = wh
    return grid


def test_no_power_price() -> None:
    plain = Tariff("Fix", Role.CURRENT, (TariffItem("Grid", Side.IMPORT, Group.GRID, Unit.KWH, 5.0),), {})
    assert power_price(plain, datetime(2026, 11, 1).date()) is None
    assert power_price(None, datetime(2026, 11, 1).date()) is None


def test_below_the_minimum_the_minimum_is_free() -> None:
    price = power_price(tariff(), datetime(2026, 11, 10).date())
    state = peak_state(price, recorded((ts(3, 18), 300)), ts(10, 12, 5))
    assert state.billed_kw == pytest.approx(2.0)
    assert state.measured_kw == pytest.approx(1.2)
    assert state.free_kw == pytest.approx(2.0, abs=0.01)
    assert state.limit_w == pytest.approx(2000 - BUFFER_W, abs=10)
    assert state.cost == pytest.approx(2 * 3.0 * 1.2)
    assert state.extra_cost == pytest.approx(0.0)


def test_a_higher_peak_raises_what_is_free_and_costs_extra() -> None:
    price = power_price(tariff(), datetime(2026, 11, 10).date())
    state = peak_state(price, recorded((ts(3, 18), 875)), ts(10, 12, 5))
    assert state.billed_kw == pytest.approx(3.5)
    assert state.peak_start == dt_util.utc_from_timestamp(ts(3, 18))
    assert state.free_kw == pytest.approx(3.5, abs=0.01)
    assert state.extra_cost == pytest.approx(1.5 * 3.0 * 1.2)
    # Rounded up to whole kW: up to 4 kW is free.
    stepped = power_price(tariff(kw_step=1.0), datetime(2026, 11, 10).date())
    state = peak_state(stepped, recorded((ts(3, 18), 875)), ts(10, 12, 5))
    assert state.billed_kw == pytest.approx(4.0)
    assert state.free_kw == pytest.approx(4.0, abs=0.01)


def test_mean_of_two_peaks() -> None:
    price = power_price(tariff(peak_count=2), datetime(2026, 11, 10).date())
    state = peak_state(price, recorded((ts(3, 18), 1000), (ts(4, 18), 500)), ts(10, 12, 5))
    # (4 + 2) / 2 = 3 kW; a new quarter hour above the second highest (2 kW) raises the mean.
    assert state.billed_kw == pytest.approx(3.0)
    assert state.free_kw == pytest.approx(2.0, abs=0.01)


def test_budget_of_the_running_quarter_hour() -> None:
    price = power_price(tariff(), datetime(2026, 11, 10).date())
    # 5 minutes into the quarter hour 12:00, 250 Wh imported (3 kW so far).
    state = peak_state(price, recorded((ts(10, 12), 250)), ts(10, 12, 5))
    assert state.elapsed_s == pytest.approx(300)
    assert state.mean_kw == pytest.approx(3.0)
    # Limit 2 kW: 500 Wh in the quarter hour, 250 Wh left for 10 minutes.
    assert state.allowed_w(2000) == pytest.approx(1500)
    # Over the budget: nothing more from the grid (feed-in does not count).
    assert state.allowed_w(800) == 0
    # At the end the time left counts as at least MIN_REST_S.
    late = state.running(recorded((ts(10, 12), 400)), ts(10, 12, 14, 30))
    assert late.allowed_w(2000) == pytest.approx(100 * 3600 / MIN_REST_S)
