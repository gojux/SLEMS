"""Tests for charging the batteries from the grid (made-up prices)."""

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from homeassistant.util import dt as dt_util

from custom_components.slems.grid_charge import HOUR, QUARTER, ChargeBattery
from custom_components.slems.grid_charge import plan_grid_charge as plan_periods


def plan_grid_charge(*args, **kwargs):
    """Hourly periods unless given (the cases below are worked out by the hour)."""
    return plan_periods(*args, **{"period": HOUR, **kwargs})


@pytest.fixture(autouse=True)
def vienna() -> None:
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Vienna"))


def night() -> datetime:
    return datetime(2026, 11, 3, 0, 0, tzinfo=dt_util.get_default_time_zone())


def battery(stored: float = 500.0, wear: float = 1.0) -> ChargeBattery:
    return ChargeBattery(
        capacity_wh=5000, stored_wh=stored, floor_wh=500, grid_max_wh=4500,
        max_charge_w=2000, max_discharge_w=2000, efficiency=0.95, wear_ct=wear,
    )


def hours(count: int) -> list[datetime]:
    return [night() + timedelta(hours=i) for i in range(count)]


def test_charges_in_cheap_hours_for_expensive_ones() -> None:
    h = hours(8)
    # Cheap night (10 ct), expensive morning (40 ct); 500 Wh deficit per hour.
    prices = dict(zip(h, [10.0] * 5 + [40.0] * 3))
    deficits = {hour: 500.0 for hour in h}
    plan = plan_grid_charge(night(), battery(), deficits, prices, night() + timedelta(hours=8), 2.0)
    assert plan is not None
    charged = sum(plan.charge_w.values())
    # About the morning deficit (1.5 kWh) plus the losses, charged in the night.
    assert 1500 / 0.95**2 - 200 < charged < 1500 / 0.95**2 + 200
    assert all(hour < h[5] for hour in plan.charge_w)
    # Charged as late as possible: in the last cheap hour(s).
    assert h[4] in plan.charge_w and h[0] not in plan.charge_w
    assert plan.saving_ct > 0


def test_no_charge_when_the_difference_does_not_pay() -> None:
    h = hours(8)
    # 10 ct → 13 ct: after losses (≈11.1 ct), wear 1 ct and minimum gain 2 ct not worth it.
    prices = dict(zip(h, [10.0] * 5 + [13.0] * 3))
    deficits = {hour: 500.0 for hour in h}
    assert plan_grid_charge(night(), battery(), deficits, prices, night() + timedelta(hours=8), 2.0) is None


def test_grid_charging_stops_at_its_maximum_and_respects_the_import_limit() -> None:
    h = hours(6)
    prices = dict(zip(h, [5.0, 5.0, 5.0, 50.0, 50.0, 50.0]))
    deficits = {hour: 2000.0 for hour in h}
    plan = plan_grid_charge(
        night(), battery(stored=4000), deficits, prices, night() + timedelta(hours=6), 2.0,
        import_limit_w=2500,
    )
    assert plan is not None
    # From 4.0 kWh up to at most 4.5 kWh stored; at most 500 W next to the 2 kW deficit.
    assert sum(plan.charge_w.values()) * 0.95 <= 500 + 100
    assert all(power <= 500 + 1 for power in plan.charge_w.values())


def test_no_plan_without_prices() -> None:
    assert plan_grid_charge(night(), battery(), {}, {}, None, 2.0) is None


def test_holds_in_cheap_hours_without_charging_and_covers_small_deficits() -> None:
    h = hours(6)
    # 15 kWh, 1.5 kWh usable; 300 Wh per hour (less than a few steps): the
    # cheap hours 2–3 are held, the expensive ones covered.
    big = ChargeBattery(
        capacity_wh=15000, stored_wh=3000, floor_wh=1500, grid_max_wh=13500,
        max_charge_w=2000, max_discharge_w=2000, efficiency=0.95, wear_ct=1.0,
    )
    prices = dict(zip(h, [38.0, 36.0, 22.0, 23.0, 36.0, 35.0]))
    deficits = {hour: 300.0 for hour in h}
    plan = plan_grid_charge(night(), big, deficits, prices, None, 2.0)
    assert plan is not None and not plan.charge_w
    assert plan.limit_w(h[0]) is None and plan.limit_w(h[1]) is None
    # 1.425 kWh: the four expensive hours take 1.2 kWh, the rest goes to 3:00 (23 ct), not to 2:00 (22 ct).
    assert plan.limit_w(h[2]) == 0
    assert 0 < plan.limit_w(h[3]) < 300


def test_feeds_in_at_a_high_credit_above_the_reserve() -> None:
    h = hours(6)
    prices = dict(zip(h, [30.0] * 6))
    # Spot feed-in: 60 ct at 1:00 (price peak), else 5 ct.
    credit = dict(zip(h, [5.0, 60.0, 5.0, 5.0, 5.0, 5.0]))
    deficits = {hour: 300.0 for hour in h}
    plan = plan_grid_charge(
        night(), battery(stored=4000), deficits, prices, None, 2.0,
        export_prices=credit, export_floor_wh=2000, export_max_w=1500,
    )
    assert plan is not None
    assert set(plan.export_w) == {h[1]}
    # At most the export power, and the reserve stays.
    assert plan.export_w[h[1]] <= 1500 + 1
    assert plan.export_w[h[1]] / 0.95 <= 4000 - 2000 - 300 / 0.95 + 1
    # Without a better credit than the later import value nothing is fed in.
    flat = plan_grid_charge(
        night(), battery(stored=4000), deficits, prices, None, 2.0,
        export_prices=dict(zip(h, [8.0] * 6)), export_floor_wh=2000,
    )
    assert flat is None or not flat.export_w


def day_from(hour: int) -> datetime:
    return datetime(2026, 5, 10, hour, 0, tzinfo=dt_util.get_default_time_zone())


def test_room_kept_for_the_pv_surplus_of_negative_hours() -> None:
    # 9–12: surplus 2 kWh/h at +8 ct feed-in; 12–15: surplus 2 kWh/h at −5 ct.
    h = [day_from(9) + timedelta(hours=i) for i in range(6)]
    nets = {hour: -2000.0 for hour in h}
    credit = dict(zip(h, [8.0, 8.0, 8.0, -5.0, -5.0, -5.0]))
    prices = {hour: 30.0 for hour in h}
    empty = ChargeBattery(
        capacity_wh=5000, stored_wh=500, floor_wh=500, grid_max_wh=4500,
        max_charge_w=2500, max_discharge_w=2500, efficiency=0.95, wear_ct=1.0,
        grid_charge_w=0.0,
    )
    plan = plan_grid_charge(day_from(9), empty, nets, prices, None, 2.0, export_prices=credit)
    assert plan is not None
    # The morning surplus is fed in (at +8 ct) instead of filling the battery …
    assert all(plan.charge_cap_at(hour) is not None for hour in h[:3])
    assert sum(plan.charge_caps_w.get(hour, 0.0) for hour in h[:3]) < 1000
    # … so it takes the surplus of the negative hours.
    assert all(plan.charge_cap_at(hour) is None for hour in h[3:])
    assert plan.saving_ct > 0


def test_grid_charging_on_top_of_the_surplus_at_a_negative_import_price() -> None:
    h = [day_from(12) + timedelta(hours=i) for i in range(3)]
    nets = {hour: -500.0 for hour in h}
    # Import price incl. fees negative at 12:00.
    prices = dict(zip(h, [-4.0, 25.0, 25.0]))
    credit = dict(zip(h, [-15.0, 5.0, 5.0]))
    battery_ = ChargeBattery(
        capacity_wh=5000, stored_wh=1000, floor_wh=500, grid_max_wh=4500,
        max_charge_w=2500, max_discharge_w=2500, efficiency=0.95, wear_ct=1.0,
        grid_charge_w=1500.0,
    )
    plan = plan_grid_charge(day_from(12), battery_, nets, prices, None, 2.0, export_prices=credit, import_limit_w=1200)
    assert plan is not None
    # The 500 W surplus plus at most 1200 W import (the import limit).
    assert 500 < plan.charge_at(h[0]) <= 500 + 1200 + 1
    # Without grid charging only the surplus is taken.
    no_grid = replace(battery_, grid_charge_w=0.0)
    plan = plan_grid_charge(day_from(12), no_grid, nets, prices, None, 2.0, export_prices=credit)
    assert plan is None or plan.charge_at(h[0]) == 0


def test_quarter_hours_use_a_cheap_quarter_within_an_hour() -> None:
    # One hour at 40 ct with a single cheap quarter (10 ct) at 01:15, then two
    # expensive hours; 100 Wh deficit per quarter, battery nearly empty.
    starts = [night() + QUARTER * i for i in range(12)]
    prices = {start: 40.0 for start in starts}
    prices[night() + timedelta(minutes=15)] = 10.0
    deficits = {start: 100.0 for start in starts}
    plan = plan_periods(night(), battery(), deficits, prices, night() + timedelta(hours=3), 2.0)
    assert plan is not None and plan.period == QUARTER
    cheap = night() + timedelta(minutes=15)
    assert set(plan.charge_w) == {cheap}
    assert plan.charge_at(cheap + timedelta(minutes=10)) == pytest.approx(plan.charge_w[cheap])
    assert plan.charge_at(night()) == 0.0
    # Hourly for the SoC projection: the quarter's power over the whole hour.
    assert plan.hourly_charge_w[night()] == pytest.approx(plan.charge_w[cheap] / 4)


def test_hourly_means_over_the_planned_periods() -> None:
    from custom_components.slems.grid_charge import hourly_means

    starts = [night() + timedelta(minutes=30), night() + timedelta(minutes=45), night() + timedelta(hours=1)]
    # The first hour starts at 00:30 (planned from then on): mean of two quarters.
    means = hourly_means({starts[0]: 400.0}, starts, {starts[1]: 200.0})
    assert means == {night(): pytest.approx(300.0)}


def test_plan_timing() -> None:
    from custom_components.slems.grid_charge import PlanTiming

    timing = PlanTiming()
    assert timing.as_dict() == {"plans": 0, "last_s": None, "mean_s": None, "max_s": 0.0, "periods": 0}
    timing.add(0.1, 144)
    timing.add(0.3, 140)
    assert timing.as_dict() == {"plans": 2, "last_s": 0.3, "mean_s": 0.2, "max_s": 0.3, "periods": 140}
