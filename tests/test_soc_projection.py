"""Tests for the projected total state of charge."""

from datetime import datetime, timedelta

import pytest

from homeassistant.util import dt as dt_util

from custom_components.slems.allocation import BatteryGroup
from custom_components.slems.soc_projection import ProjectionSettings, project_soc


@pytest.fixture(autouse=True)
def vienna() -> None:
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Vienna"))


def midnight() -> datetime:
    return datetime(2026, 6, 1, tzinfo=dt_util.get_default_time_zone())


# PV per hour of the day (Wh): 08:00-16:00, peak at noon.
PV_DAY = {8: 500, 9: 1500, 10: 2500, 11: 3500, 12: 4500, 13: 3500, 14: 2500, 15: 1500, 16: 500}


def forecasts(load_w: float = 400) -> tuple[dict, dict]:
    pv, consumption = {}, {}
    for day in (0, 1):
        for hour in range(24):
            start = midnight() + timedelta(days=day, hours=hour)
            pv[start] = PV_DAY.get(hour, 0)
            consumption[start] = load_w
    return pv, consumption


def battery(soc: float) -> BatteryGroup:
    return BatteryGroup(
        soc_pct=soc, capacity_wh=10000, max_charge_w=5000, max_discharge_w=5000, charge_efficiency=1.0
    )


def settings(**changes) -> ProjectionSettings:
    values = {
        "grid_friendly_charging": False,
        "buffer_wh": 0.0,
        "peak_shaving": False,
        "peak_shaving_grid_limit_w": 1000.0,
        "peak_shaving_soc_threshold_pct": 20.0,
        "night_discharge": False,
        "night_reserve_pct": 30.0,
    }
    values.update(changes)
    return ProjectionSettings(**values)


def at(hour: int, day: int = 0) -> datetime:
    return midnight() + timedelta(days=day, hours=hour)


def test_night_discharge_and_day_charge() -> None:
    pv, consumption = forecasts()
    now = at(2)
    result = project_soc(now, battery(50), pv, consumption, None, settings(), None)
    # 02:00-08:00: 6 h × 400 W out of 10 kWh.
    assert result.soc_pct[at(7)] == pytest.approx(26)
    # The surplus fills the battery; tomorrow it is full again after the night.
    assert result.soc_pct[at(16)] == pytest.approx(100)
    assert result.soc_pct[at(23, day=1)] < 100
    assert result.planned_charge_w[at(8, day=1)] > 0
    assert min(result.soc_pct.values()) >= 0
    assert at(1) not in result.soc_pct  # past hours


def test_current_hour_counts_only_the_rest() -> None:
    pv, consumption = forecasts()
    now = at(2) + timedelta(minutes=30)
    result = project_soc(now, battery(50), pv, consumption, None, settings(), None)
    assert result.soc_pct[at(2)] == pytest.approx(48)


def test_grid_friendly_tomorrow_charges_the_peak() -> None:
    pv, consumption = forecasts()
    result = project_soc(
        at(20), battery(60), pv, consumption, None, settings(grid_friendly_charging=True), None
    )
    tomorrow = result.planned_charge_w
    # Only the surplus above the feed-in limit: nothing in the morning, most at noon.
    assert tomorrow[at(8, day=1)] == 0
    assert tomorrow[at(12, day=1)] == max(v for k, v in tomorrow.items() if k.date() == at(0, 1).date())
    assert result.soc_pct[at(16, day=1)] == pytest.approx(100, abs=0.5)


def test_peak_shaving_keeps_the_low_battery() -> None:
    pv, consumption = forecasts(load_w=800)
    result = project_soc(
        at(0), battery(15), pv, consumption, None, settings(peak_shaving=True), None
    )
    # Below the threshold only the import above 1 kW is covered: nothing here.
    assert result.soc_pct[at(7)] == pytest.approx(15)


def test_empty_battery_stays_at_zero() -> None:
    pv, consumption = forecasts(load_w=2000)
    result = project_soc(at(0), battery(5), pv, consumption, None, settings(), None)
    assert result.soc_pct[at(7)] == 0
