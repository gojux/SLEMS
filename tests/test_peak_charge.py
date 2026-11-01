"""Tests for charging from the grid for peak shaving."""

from datetime import datetime, timedelta

import pytest

from homeassistant.util import dt as dt_util

from custom_components.slems.peak_charge import plan_peak_charge


@pytest.fixture(autouse=True)
def vienna() -> None:
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Vienna"))


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, 10, hour, minute, tzinfo=dt_util.get_default_time_zone())


def quarters(start: datetime, end: datetime) -> list[datetime]:
    result = []
    while start < end:
        result.append(start)
        start += timedelta(minutes=15)
    return result


def test_charges_the_latest_quarter_hours_with_a_fixed_price() -> None:
    room = {start: 1500.0 for start in quarters(at(12), at(18))}
    # 750 Wh missing, 100 % efficiency, 1000 W: three quarter hours of 1000 W, the latest before 18:00.
    plan = plan_peak_charge(at(12), 750, 1.0, 1000, room, {start: 25.0 for start in room}, at(18))
    assert plan == {at(17, 15): 1000, at(17, 30): 1000, at(17, 45): 1000}


def test_cheapest_quarter_hours_and_the_room_below_the_limit() -> None:
    room = {start: 400.0 for start in quarters(at(12), at(18))}
    prices = {start: 30.0 for start in room}
    prices[at(13)] = 10.0
    # Only 400 W below the limit; the cheap quarter hour first, the losses on top.
    plan = plan_peak_charge(at(12), 180, 0.9, 1000, room, prices, at(18))
    assert plan[at(13)] == pytest.approx(400)
    assert sum(plan.values()) * 0.25 == pytest.approx(200)
    assert plan_peak_charge(at(12), 0, 0.9, 1000, room, prices, at(18)) == {}
