"""Tests for the projected total state of charge."""

from datetime import datetime, timedelta

import pytest

from homeassistant.util import dt as dt_util

from custom_components.slems.allocation import BatteryGroup
from custom_components.slems.consumer_targets import SurplusDemand
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
        "charge_buffer_wh": 0.0,
        "night_buffer_wh": 0.0,
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


def test_soc_window_bounds_the_projection() -> None:
    pv, consumption = forecasts(load_w=2000)
    group = BatteryGroup(
        soc_pct=30, capacity_wh=10000, max_charge_w=5000, max_discharge_w=5000,
        charge_efficiency=1.0, min_soc_pct=20, full_soc_pct=80,
    )
    result = project_soc(at(0), group, pv, consumption, None, settings(), None)
    assert result.soc_pct[at(7)] == pytest.approx(20)
    pv, consumption = forecasts(load_w=0)
    result = project_soc(at(0), group, pv, consumption, None, settings(), None)
    assert max(result.soc_pct.values()) == pytest.approx(80)


def test_no_planned_charging_when_full() -> None:
    # The buffer lowers the feed-in limit but is not charged into full batteries.
    pv, consumption = forecasts()
    result = project_soc(
        at(6), battery(95), pv, consumption, None, settings(charge_buffer_wh=5000), None
    )
    full_at = min(h for h, soc in result.soc_pct.items() if soc >= 100)
    # Until the end of today's PV (no discharge in between) nothing more is charged.
    assert all(
        charge == 0 for h, charge in result.planned_charge_w.items() if full_at < h < at(16)
    )
    assert full_at < at(16)


def test_charging_held_back_by_the_feed_in_cap_is_made_up_later() -> None:
    from custom_components.slems.feed_in_cap import CapSettings, plan_cap

    # Peak 5.85 kW, limit 4 kW: charging below the limit is held back before
    # the peak and has to be made up afterwards (the plan is made again every
    # hour, not once per day).
    pv, consumption = forecasts()
    pv = {k: v * 1.3 for k, v in pv.items()}
    now = at(6)
    group = BatteryGroup(
        soc_pct=12, capacity_wh=10000, max_charge_w=7500, max_discharge_w=7500,
        charge_efficiency=1.0, min_soc_pct=12,
    )
    cap = plan_cap(now, group, 7500, 7500, pv, consumption, CapSettings(4000, 20, 500))
    result = project_soc(now, group, pv, consumption, None, settings(), None, 0.0, cap)
    assert result.soc_pct[at(11)] < 80
    assert result.soc_pct[at(14)] == pytest.approx(100)


def test_daily_target_takes_the_surplus_left_after_charging() -> None:
    pv, consumption = forecasts()
    now = at(6)
    # Almost full: the batteries take little, the rest would be exported.
    without = project_soc(now, battery(95), pv, consumption, None, settings(), None)
    demand = SurplusDemand(energy_wh=3000, power_w=2000, start=at(6), end=at(18))
    result = project_soc(now, battery(95), pv, consumption, None, settings(), None, demands=[demand])
    assert sum(result.consumer_w.values()) == pytest.approx(3000)
    assert max(result.consumer_w.values()) <= 2000
    # Only from the surplus: nothing before the PV exceeds the consumption.
    assert all(hour >= at(8) for hour in result.consumer_w)
    # The export drops by what the consumer takes, the charging stays.
    assert sum(result.grid_w.values()) == pytest.approx(sum(without.grid_w.values()) + 3000)
    assert result.soc_pct == without.soc_pct


def test_after_the_night_discharge_the_limit_follows_the_lower_state_of_charge() -> None:
    pv, consumption = forecasts()
    # At midnight the controller's limit only fills the batteries from 77 %.
    result = project_soc(
        at(0), battery(77), pv, consumption, None,
        settings(grid_friendly_charging=True, night_discharge=True), today_limit_w=3000,
    )
    # The night discharge lowers the state of charge by the morning; later the
    # limit is computed from it (as the controller does), so they get full.
    assert result.soc_pct[at(7)] < 50
    assert max(result.soc_pct[at(hour)] for hour in range(8, 17)) == pytest.approx(100)


def test_bad_weather_mode_until_the_evening() -> None:
    pv, consumption = forecasts()
    mode = settings(grid_friendly_charging=True, night_discharge=True, bad_weather_until=at(17, day=1))
    normal = project_soc(at(22), battery(80), pv, consumption, None, settings(
        grid_friendly_charging=True, night_discharge=True), None)
    result = project_soc(at(22), battery(80), pv, consumption, None, mode, None)
    # No night discharge: the night only takes the consumption.
    assert normal.soc_pct[at(7, day=1)] < result.soc_pct[at(7, day=1)]
    assert result.soc_pct[at(7, day=1)] == pytest.approx(80 - 10 * 4)  # 22:00-08:00 × 400 W
    # No grid friendly charging: the morning surplus is charged at once.
    assert normal.planned_charge_w[at(8, day=1)] == 0
    assert result.planned_charge_w[at(8, day=1)] > 0
