"""Tests for keeping the stored energy for the expensive hours (made-up prices)."""

from datetime import datetime, timedelta

import pytest

from homeassistant.util import dt as dt_util

from custom_components.slems.price_hold import plan_price_hold


@pytest.fixture(autouse=True)
def vienna() -> None:
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Vienna"))


def evening() -> datetime:
    return datetime(2026, 11, 3, 18, 0, tzinfo=dt_util.get_default_time_zone())


def hours(start: datetime, count: int) -> list[datetime]:
    return [start + timedelta(hours=i) for i in range(count)]


def test_energy_goes_to_the_expensive_hours() -> None:
    now = evening()
    night = hours(now, 6)  # 18–24
    deficits = {hour: 1000.0 for hour in night}
    prices = dict(zip(night, [30.0, 32.0, 25.0, 20.0, 18.0, 18.0]))
    plan = plan_price_hold(now, 2500.0, deficits, prices, now + timedelta(hours=6), 2.0)
    assert plan is not None
    # 2.5 kWh: 19:00 (32 ct), 18:00 (30 ct), part of 20:00 (25 ct) – held: 21–24.
    assert plan.hold_hours == frozenset(night[3:])
    assert plan.covered_from_ct == 25.0 and plan.held_up_to_ct == 20.0
    assert plan.holds(now + timedelta(hours=3, minutes=10))
    assert not plan.holds(now + timedelta(minutes=10))


def test_no_hold_when_the_energy_suffices_or_prices_are_flat_or_missing() -> None:
    now = evening()
    night = hours(now, 4)
    deficits = {hour: 1000.0 for hour in night}
    prices = dict(zip(night, [30.0, 29.0, 29.0, 28.5]))
    until = now + timedelta(hours=4)
    assert plan_price_hold(now, 5000.0, deficits, prices, until, 2.0) is None
    # Cheaper by less than the minimum gain: not worth it.
    assert plan_price_hold(now, 1500.0, deficits, prices, until, 2.0) is None
    assert plan_price_hold(now, 1500.0, deficits, {**prices, night[2]: None}, until, 2.0) is None
    assert plan_price_hold(now, 1500.0, deficits, prices, None, 2.0) is None


def test_current_hour_counts_with_its_remaining_part() -> None:
    now = evening() + timedelta(minutes=30)
    night = hours(evening(), 3)
    deficits = {hour: 1000.0 for hour in night}
    prices = dict(zip(night, [10.0, 30.0, 30.0]))
    # 500 Wh left of this hour + 2 × 1000 Wh; 2 kWh usable: the cheap current hour is held.
    plan = plan_price_hold(now, 2000.0, deficits, prices, evening() + timedelta(hours=3), 2.0)
    assert plan is not None and plan.hold_hours == frozenset({night[0]})
    assert plan.holds(now)


def test_partly_covered_hour_is_limited_before_a_more_expensive_one() -> None:
    now = evening()
    night = hours(now, 4)
    deficits = {hour: 1000.0 for hour in night}
    # 18: 30 ct, 19: 20 ct (cheap, held), 20: 25 ct (partly), 21: 40 ct.
    prices = dict(zip(night, [30.0, 20.0, 25.0, 40.0]))
    plan = plan_price_hold(now, 2400.0, deficits, prices, now + timedelta(hours=4), 2.0)
    assert plan is not None
    assert plan.hold_hours == frozenset({night[1]})
    # 21 and 18 take 2 kWh; 20:00 gets the remaining 400 Wh, limited for 21:00 after it.
    assert plan.limit_w(night[2]) == pytest.approx(400.0)
    assert plan.limit_w(night[3]) is None
    prices = dict(zip(night, [30.0, 20.0, 35.0, 40.0]))
    plan = plan_price_hold(now, 2400.0, deficits, prices, now + timedelta(hours=4), 2.0)
    # 21 (40) and 20 (35) first, 18:00 (30) partly with 400 Wh before them.
    assert plan.limit_w(night[0]) == pytest.approx(400.0)
    assert plan.limit_w(night[1]) == 0.0
