"""Tests for the comparison with other hardware (made-up values)."""

from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest

from homeassistant.util import dt as dt_util

from custom_components.slems.hardware_comparison import hardware_rows, simulated_battery
from custom_components.slems.price_backtest import BacktestBattery
from custom_components.slems.tariff import Group, Role, Side, Tariff, TariffItem, Unit


@pytest.fixture(autouse=True)
def vienna() -> None:
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Vienna"))


# Charging reduced by a battery with own PV (2500 W of the group's 3000 W).
BASE = BacktestBattery(
    capacity_wh=5120, min_wh=512, full_wh=4864, max_charge_w=2500, max_discharge_w=2500,
    efficiency=0.95, wear_ct=1.0, grid_charge=False, grid_max_wh=4608,
)
REAL = {"capacity_kwh": 5.12, "min_soc_pct": 10.0, "max_soc_pct": 95.0, "max_charge_w": 3000.0, "max_discharge_w": 2500.0}
FIXED = Tariff("Fixed", Role.CURRENT, (
    TariffItem("Energy", Side.IMPORT, Group.ENERGY, Unit.KWH, 30.0),
    TariffItem("Feed-in", Side.EXPORT, Group.ENERGY, Unit.KWH, 5.0),
), {})


def test_unchanged_values_keep_the_backtest_battery() -> None:
    assert simulated_battery(BASE, REAL, dict(REAL)) == BASE


def test_changed_values() -> None:
    # A larger capacity keeps the states of charge in %.
    larger = simulated_battery(BASE, REAL, REAL | {"capacity_kwh": 10.24})
    assert (larger.capacity_wh, larger.min_wh, larger.full_wh, larger.grid_max_wh) == pytest.approx(
        (10240, 1024, 9728, 9216)
    )
    # Another maximum state of charge; the grid charge limit stays below it.
    lower = simulated_battery(BASE, REAL, REAL | {"max_soc_pct": 80.0})
    assert lower.full_wh == pytest.approx(4096)
    assert lower.grid_max_wh == pytest.approx(4096)
    # Power: the backtest power shifted by the change, never below 0.
    stronger = simulated_battery(BASE, REAL, REAL | {"max_charge_w": 5000.0, "max_discharge_w": 0.0})
    assert (stronger.max_charge_w, stronger.max_discharge_w) == (4500, 0)
    # A minimum above the maximum: no usable energy, not a negative one.
    crossed = simulated_battery(BASE, REAL, REAL | {"min_soc_pct": 98.0})
    assert crossed.full_wh == crossed.min_wh


def scenario(days: int = 3):
    start = datetime(2026, 6, 1, tzinfo=dt_util.get_default_time_zone())
    hours = [start + timedelta(hours=i) for i in range(24 * days)]
    load = {h: 600.0 for h in hours}
    pv = {h: 3000.0 if 9 <= h.hour < 15 else 0.0 for h in hours}
    return load, pv


def rows(simulated: BacktestBattery, pv_factor: float = 1.0, load_factor: float = 1.0) -> dict:
    load, pv = scenario()
    result = hardware_rows(
        {"a": FIXED}, date(2026, 6, 1), date(2026, 6, 3), load, pv, BASE, simulated, pv_factor, load_factor, {}
    )
    assert [row["month"] for row in result] == ["2026-06"]
    return result[0]


def test_unchanged_hardware_costs_the_same() -> None:
    row = rows(BASE)
    assert row["current"] == row["simulated"]


def test_larger_battery_imports_and_feeds_in_less() -> None:
    row = rows(simulated_battery(BASE, REAL, REAL | {"capacity_kwh": 15.0}))
    current, simulated = row["current"], row["simulated"]
    assert simulated["import_kwh"] < current["import_kwh"]
    assert simulated["export_kwh"] < current["export_kwh"]
    assert simulated["costs"]["a"] < current["costs"]["a"]


def test_pv_and_consumption_factors() -> None:
    more_pv = rows(BASE, pv_factor=2.0)
    assert more_pv["simulated"]["export_kwh"] > more_pv["current"]["export_kwh"] + 20
    more_load = rows(BASE, load_factor=1.5)
    assert more_load["simulated"]["import_kwh"] > more_load["current"]["import_kwh"]
    assert more_load["simulated"]["costs"]["a"] > more_load["current"]["costs"]["a"]


def test_months_without_energy_in_one_run_are_left_out() -> None:
    load, pv = scenario()
    # The simulated batteries cover everything: no import and no feed-in.
    huge = replace(BASE, capacity_wh=1e6, min_wh=0, full_wh=1e6, max_charge_w=1e6, max_discharge_w=1e6, efficiency=1.0)
    result = hardware_rows(
        {"a": FIXED}, date(2026, 6, 1), date(2026, 6, 3), load, {h: 0.0 for h in pv}, BASE, huge, 1.0, 1.0, {}
    )
    # play starts half full: 1e6 Wh cover the three days without import.
    assert result == []
