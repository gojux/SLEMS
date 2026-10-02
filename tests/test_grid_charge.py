"""Tests for charging the batteries from the grid (made-up prices)."""

from datetime import datetime, timedelta

import pytest

from homeassistant.util import dt as dt_util

from custom_components.slems.grid_charge import ChargeBattery, plan_grid_charge


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
