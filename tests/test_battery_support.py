"""Tests for the battery support of consumers."""

from datetime import datetime, timedelta

import pytest

from homeassistant.util import dt as dt_util

from custom_components.slems.battery_support import (
    RESUME_WH,
    SupportBudget,
    next_refill,
    support_budget_wh,
)


@pytest.fixture(autouse=True)
def vienna() -> None:
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Vienna"))


def at(hour: int, day: int = 0) -> datetime:
    return datetime(2026, 6, 1, tzinfo=dt_util.get_default_time_zone()) + timedelta(days=day, hours=hour)


def forecasts() -> tuple[dict, dict]:
    pv = {at(h, d): (2000 if 8 <= h < 17 else 0) for d in (0, 1, 2) for h in range(24)}
    consumption = {at(h, d): 500 for d in (0, 1, 2) for h in range(24)}
    return pv, consumption


def test_next_refill_at_night_and_during_the_surplus() -> None:
    pv, consumption = forecasts()
    # At night: the coming morning.
    assert next_refill(at(22), pv, consumption) == at(8, day=1)
    assert next_refill(at(3), pv, consumption) == at(8)
    # During the surplus: the morning after the coming night.
    assert next_refill(at(11), pv, consumption) == at(8, day=1)
    assert next_refill(at(11), {}, consumption) is None


def test_budget_is_the_lowest_projected_energy_above_the_floor() -> None:
    soc = {at(h): 80 - (h - 18) * 5 for h in range(18, 24)} | {at(h, 1): 50 + h for h in range(10)}
    # Lowest until the refill at 08:00: 50 % of 10 kWh (hour 0 of the next day).
    budget = support_budget_wh(8000, soc, 10000, at(8, day=1), floor_wh=3000)
    assert budget == pytest.approx(2000)
    assert support_budget_wh(8000, soc, 10000, at(8, day=1), floor_wh=9000) == 0
    # The current stored energy counts as well.
    assert support_budget_wh(4000, soc, 10000, at(8, day=1), floor_wh=3000) == pytest.approx(1000)


def test_used_up_budget_counts_again_from_the_resume_level() -> None:
    budget = SupportBudget()
    assert budget.available  # no budget: like "always"
    budget.update(500)
    assert budget.available
    budget.update(0)
    assert not budget.available
    budget.update(RESUME_WH - 1)
    assert not budget.available
    budget.update(RESUME_WH)
    assert budget.available
