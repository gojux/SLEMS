"""Tests for grid friendly charging."""

from datetime import datetime, timedelta

import math

import pytest

from homeassistant.util import dt as dt_util

from custom_components.slems.allocation import (
    AllocationSettings,
    BatteryGroup,
    Strategy,
    allocate,
)
from custom_components.slems.grid_friendly import (
    chargeable_wh,
    feed_in_limit,
    pv_correction,
    remaining_surplus,
)


@pytest.fixture(autouse=True)
def vienna() -> None:
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Vienna"))


def day() -> datetime:
    return datetime(2026, 6, 1, tzinfo=dt_util.get_default_time_zone())


# Bell shaped surplus 08:00-17:00 (W): peak 5 kW at noon
SURPLUS = [(p, 1.0) for p in (500, 1500, 2500, 3500, 5000, 3500, 2500, 1500, 500)]


def test_limit_cuts_the_peak() -> None:
    limit = feed_in_limit(SURPLUS, needed_wh=5000, max_charge_w=5000)
    # Above ~2.4 kW: 1.1 + 2.6 + 1.1 kWh from the three peak hours plus a little
    # from the 2.5 kW hours give the 5 kWh.
    assert 2350 < limit < 2450
    assert chargeable_wh(SURPLUS, limit, 5000) == pytest.approx(5000, abs=50)


def test_max_charge_power_lowers_the_limit() -> None:
    # With 2 kW max charge the peak hour cannot absorb all energy above the limit.
    free = feed_in_limit(SURPLUS, needed_wh=5000, max_charge_w=5000)
    limited = feed_in_limit(SURPLUS, needed_wh=5000, max_charge_w=2000)
    assert limited < free


def test_not_enough_surplus_charges_at_once() -> None:
    assert feed_in_limit(SURPLUS, needed_wh=50000, max_charge_w=5000) is None


def test_full_battery_needs_no_charging() -> None:
    assert feed_in_limit(SURPLUS, needed_wh=0, max_charge_w=5000) == 5000


def test_remaining_surplus_uses_consumption_forecast() -> None:
    start = day()
    pv = {start.replace(hour=h): 3000 for h in (11, 12)}
    consumption = {start.replace(hour=h): 500 for h in range(24)}
    now = start.replace(hour=11, minute=30)
    assert remaining_surplus(pv, consumption, 999, now) == [(2500, 0.5), (2500, 1.0)]
    # Without consumption forecast the current load is used.
    assert remaining_surplus(pv, None, 1000, now) == [(2000, 0.5), (2000, 1.0)]


def test_pv_correction() -> None:
    start = day()
    forecast = {start.replace(hour=h): 2000 for h in range(8, 18)}
    now = start.replace(hour=12)
    # Forecast until noon: 4 h x 2 kWh = 8 kWh; produced 6 kWh.
    assert pv_correction(forecast, now, 6000) == pytest.approx(0.75)
    assert pv_correction(forecast, now, None) == 1.0
    assert pv_correction(forecast, start.replace(hour=8, minute=15), 100) == 1.0
    assert pv_correction(forecast, now, 100) == 0.5


SETTINGS = AllocationSettings(
    battery_priority_soc_pct=30,
    battery_share_when_secured_pct=100,
    charge_secured_buffer_wh=1000,
    charge_grid_target_w=0,
    discharge_grid_target_w=0,
    discharge_max_grid_export_w=5000,
    peak_shaving=False,
    peak_shaving_grid_limit_w=3000,
    peak_shaving_soc_threshold_pct=20,
)
BATTERY = BatteryGroup(
    soc_pct=50, capacity_wh=10000, max_charge_w=5000, max_discharge_w=5000,
    charge_efficiency=0.95,
)


def test_allocation_charges_only_above_limit_when_secured() -> None:
    result = allocate(3000, BATTERY, [], SETTINGS, 20000, feed_in_limit_w=2000)
    assert result.strategy is Strategy.GRID_FRIENDLY
    assert result.battery_power_w == 1000


def test_allocation_ignores_limit_when_not_secured() -> None:
    result = allocate(3000, BATTERY, [], SETTINGS, 1000, feed_in_limit_w=2000)
    assert result.strategy is Strategy.BATTERY_PRIORITY
    assert result.battery_power_w == 3000


def _simulate_day(grid_friendly: bool) -> tuple[float, float]:
    """Clear day, 8 kWp, 500 W load, 10 kWh battery from 20 %.

    Returns (maximum export in W, final state of charge in %).
    """
    start = day()
    pv_forecast = {
        start.replace(hour=h): max(0.0, 7000 * math.sin((h + 0.5 - 6) / 14 * math.pi))
        for h in range(24)
    }
    consumption = {start.replace(hour=h): 500.0 for h in range(24)}
    settings = AllocationSettings(**{**SETTINGS.__dict__, "battery_priority_soc_pct": 0})
    soc = 20.0
    max_export = 0.0
    step = 0.25
    t = start.replace(hour=6)
    while t < start.replace(hour=21):
        pv_w = pv_forecast[t.replace(minute=0)]
        surplus_w = pv_w - 500
        battery = BatteryGroup(
            soc_pct=soc, capacity_wh=10000, max_charge_w=5000, max_discharge_w=5000,
            charge_efficiency=1.0,
        )
        limit = None
        needed = battery.energy_to_full_wh + settings.charge_secured_buffer_wh
        if grid_friendly:
            limit = feed_in_limit(
                remaining_surplus(pv_forecast, consumption, None, t), needed, 5000
            )
        expected = sum(
            power * hours for power, hours in remaining_surplus(pv_forecast, consumption, None, t)
        )
        result = allocate(surplus_w, battery, [], settings, expected, feed_in_limit_w=limit)
        charge = max(0.0, result.battery_power_w)
        soc = min(100.0, soc + charge * step / 10000 * 100)
        max_export = max(max_export, surplus_w - charge)
        t += timedelta(hours=step)
    return max_export, soc


def test_day_simulation_absorbs_the_peak() -> None:
    early_export, early_soc = _simulate_day(grid_friendly=False)
    friendly_export, friendly_soc = _simulate_day(grid_friendly=True)
    # Charging early: battery full before noon, the full peak goes to the grid.
    assert early_soc == pytest.approx(100)
    assert early_export > 6000
    # Grid friendly: the battery is still full and takes the top of the peak.
    # With 10 kWh against ~60 kWh surplus on a broad clear day this is about
    # 1 kW (6.5 kW -> 5.4 kW); the smaller the surplus, the larger the effect.
    assert friendly_soc > 99
    assert friendly_export < early_export - 900
