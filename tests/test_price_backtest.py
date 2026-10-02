"""Tests for the backtest of the price aware control (made-up values)."""

from datetime import datetime, timedelta

import pytest

from homeassistant.util import dt as dt_util

from custom_components.slems.price_backtest import BacktestBattery, play


@pytest.fixture(autouse=True)
def vienna() -> None:
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Vienna"))


def day_hours(days: int = 2) -> list[datetime]:
    start = datetime(2026, 11, 3, 0, 0, tzinfo=dt_util.get_default_time_zone())
    return [start + timedelta(hours=i) for i in range(24 * days)]


def battery(grid_charge: bool = False) -> BacktestBattery:
    return BacktestBattery(
        capacity_wh=5000, min_wh=500, full_wh=5000, max_charge_w=2000, max_discharge_w=2000,
        efficiency=0.95, wear_ct=1.0, grid_charge=grid_charge, grid_max_wh=4500,
    )


def scenario():
    hours = day_hours()
    # 500 Wh per hour, PV 3 kWh per hour from 10 to 14.
    load = {h: 500.0 for h in hours}
    pv = {h: 3000.0 if 10 <= h.hour < 14 else 0.0 for h in hours}
    # Cheap at night, expensive in the evening.
    prices = {h: 40.0 if 17 <= h.hour < 21 else 15.0 if h.hour < 6 else 25.0 for h in hours}
    return hours, load, pv, prices


def cost(imported, prices) -> float:
    return sum(wh * prices[h] for h, wh in imported.items()) / 1000


def test_same_energy_balance_and_lower_costs_with_price_control() -> None:
    hours, load, pv, prices = scenario()
    usual = play(hours, load, pv, battery(), None, 2.0, start_wh=1500)
    aware = play(hours, load, pv, battery(), prices, 2.0, start_wh=1500)
    # Holding moves import to cheaper hours; the amount stays about the same.
    assert sum(aware[0].values()) == pytest.approx(sum(usual[0].values()), rel=0.05)
    assert cost(aware[0], prices) < cost(usual[0], prices)


def test_grid_charging_adds_import_but_saves() -> None:
    hours, load, pv, prices = scenario()
    # Little PV: the batteries never fill, cheap night charging pays for the evening.
    pv = {h: 0.0 for h in hours}
    hold = play(hours, load, pv, battery(), prices, 2.0, start_wh=500)
    charged = play(hours, load, pv, battery(grid_charge=True), prices, 2.0, start_wh=500)
    assert sum(charged[0].values()) > sum(hold[0].values())
    assert cost(charged[0], prices) < cost(hold[0], prices)


def test_surplus_is_stored_then_exported() -> None:
    hours, load, pv, _ = scenario()
    imported, exported = play(hours, load, pv, battery(), None, 2.0, start_wh=500)
    noon = hours[11]
    assert imported[noon] == 0 and exported[noon] >= 0
    assert sum(exported.values()) > 0


def test_measured_saving_of_a_held_night() -> None:
    from custom_components.slems.price_backtest import measured_saving

    hours = day_hours(1)[:8]  # 0–8
    load = {h: 500.0 for h in hours}
    pv = {h: 0.0 for h in hours}
    prices = {h: 40.0 if h.hour >= 6 else 20.0 for h in hours}
    # As usual the battery (1 kWh usable) covers 0–2, the grid 2–8.
    # Recorded: held until 6, the grid took 0–6 at 20 ct, the battery 6–8.
    actual = {h: (500.0 if h.hour < 6 else 0.0) for h in hours}
    saving = measured_saving(
        hours, load, pv, battery(), 500 + 1000 / 0.95, actual, {}, prices, {}
    )
    usual_cost = (4 * 500 * 20 + 2 * 500 * 40) / 1000  # 2–6 at 20 ct, 6–8 at 40 ct
    real_cost = 6 * 500 * 20 / 1000
    assert saving == pytest.approx((usual_cost - real_cost) / 100, abs=0.01)
