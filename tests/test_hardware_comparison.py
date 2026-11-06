"""Tests for the comparison with other hardware (made-up values)."""

from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest

from homeassistant.util import dt as dt_util

from custom_components.slems.allocation import BatteryGroup
from custom_components.slems.hardware_comparison import hardware_rows
from custom_components.slems.simulation import simulated_group
from custom_components.slems.price_backtest import BacktestBattery
from custom_components.slems.tariff import Group, Role, Side, Tariff, TariffItem, Unit


@pytest.fixture(autouse=True)
def vienna() -> None:
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Vienna"))


BASE = BacktestBattery(
    capacity_wh=5120, min_wh=512, full_wh=4864, max_charge_w=2500, max_discharge_w=2500,
    efficiency=0.95, wear_ct=1.0, grid_charge=False, grid_max_wh=4608,
)
FIXED = Tariff("Fixed", Role.CURRENT, (
    TariffItem("Energy", Side.IMPORT, Group.ENERGY, Unit.KWH, 30.0),
    TariffItem("Feed-in", Side.EXPORT, Group.ENERGY, Unit.KWH, 5.0),
), {})
REAL = {"capacity_kwh": 5.12, "min_soc_pct": 10.0, "max_soc_pct": 95.0, "max_charge_w": 3000.0, "max_discharge_w": 2500.0}


def larger(capacity_wh: float) -> BacktestBattery:
    scale = capacity_wh / BASE.capacity_wh
    return replace(BASE, capacity_wh=capacity_wh, min_wh=BASE.min_wh * scale, full_wh=BASE.full_wh * scale)


def test_simulated_group_changes_only_the_changed_values() -> None:
    group = BatteryGroup(
        soc_pct=50.0, capacity_wh=5123.4, max_charge_w=3000.4, max_discharge_w=2500.0,
        charge_efficiency=0.95, min_soc_pct=10.04, full_soc_pct=95.0,
    )
    # Starting values are rounded; unchanged ones keep the exact value.
    assert simulated_group(group, REAL | {"capacity_kwh": 5.12, "min_soc_pct": 10.0}, REAL) == group
    changed = simulated_group(group, REAL, REAL | {"capacity_kwh": 10.0, "max_charge_w": 5000.0})
    assert (changed.capacity_wh, changed.max_charge_w, changed.min_soc_pct) == (10000.0, 5000.0, 10.04)


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
    row = rows(larger(15000))
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


def test_price_control_share() -> None:
    load, pv = scenario()
    # Expensive evening, cheap night: the price plan keeps energy for the evening.
    import_ct = {h: 50.0 if 17 <= h.hour < 21 else 20.0 for h in load}
    spot = Tariff("Spot", Role.COMPARISON, (TariffItem("Energy", Side.IMPORT, Group.ENERGY, Unit.SPOT, 0.0),), {})
    market = {h: value * 10 for h, value in import_ct.items()}
    battery = replace(BASE, grid_charge=True, grid_charge_w=1000.0)
    result = hardware_rows(
        {"a": FIXED, "b": spot}, date(2026, 6, 1), date(2026, 6, 3), load, pv, battery,
        replace(battery, capacity_wh=15000, full_wh=14000, grid_max_wh=14000), 1.0, 1.0, market,
        prices={"b": (import_ct, None)}, min_gain_ct=1.0,
    )
    row = result[0]
    for side in ("current", "simulated"):
        # Only for the tariffs with prices; the price aware control is never more expensive.
        assert set(row[side]["price_costs"]) == {"b"}
        assert row[side]["price_costs"]["b"] <= row[side]["costs"]["b"] + 1e-9


def test_grid_charge_power_apart_from_the_surplus_charge_power() -> None:
    from custom_components.slems.price_backtest import play

    hours = [datetime(2026, 6, 1, tzinfo=dt_util.get_default_time_zone()) + timedelta(hours=i) for i in range(24)]
    load = {h: 200.0 for h in hours}
    pv = {h: 3000.0 if 10 <= h.hour < 12 else 0.0 for h in hours}
    battery = replace(BASE, max_charge_w=2500, grid_charge_w=500.0, min_wh=0, full_wh=5000, efficiency=1.0)
    imported, exported = play(hours, load, pv, battery, None, 0.0, start_wh=0.0)
    # The surplus charges with 2500 W, not with the grid charge power.
    assert sum(exported.values()) == pytest.approx(2 * (2800 - 2500))
