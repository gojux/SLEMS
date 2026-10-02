"""Tests for the measured saving of the price aware control."""

from datetime import datetime, timedelta

import pytest

from homeassistant.util import dt as dt_util

from custom_components.slems.price_savings import PriceSavings


@pytest.fixture(autouse=True)
def vienna() -> None:
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Vienna"))


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=dt_util.get_default_time_zone())


def test_only_runs_with_an_action_are_kept() -> None:
    savings = PriceSavings()
    savings.observe(at(3, 18, 5), True, 3000.0, False)
    assert not savings.observe(at(4, 8, 0), False, 1000.0, False)  # no action: dropped
    assert not savings.pending
    savings.observe(at(4, 18, 5), True, 3000.0, False)
    savings.observe(at(4, 22, 0), True, 2500.0, True)
    assert savings.observe(at(5, 8, 10), False, 900.0, False)
    run = savings.pending[0]
    assert run.start == at(4, 18) and run.end == at(5, 8) and run.start_wh == 3000.0
    assert savings.due(at(5, 8, 30), timedelta(hours=1)) == []
    assert savings.due(at(5, 9, 30), timedelta(hours=1)) == [run]


def test_monthly_total_and_round_trip() -> None:
    savings = PriceSavings()
    savings.observe(at(29, 18), True, 3000.0, True)
    savings.observe(at(30, 8), False, None, False)
    savings.add(savings.pending[0], 0.42)
    assert savings.total_now(at(30, 12)) == pytest.approx(0.42) and savings.runs == 1
    assert savings.total_now(datetime(2026, 12, 1, 12, tzinfo=dt_util.get_default_time_zone())) == 0.0
    restored = PriceSavings.from_dict(savings.as_dict())
    assert restored.total_eur == pytest.approx(0.42) and restored.last["saving_eur"] == 0.42
    # A long run (winter without PV) is cut after 24 hours.
    savings.observe(at(1, 0), True, 3000.0, True)
    assert savings.observe(at(2, 0), True, 2000.0, True)
    assert savings.run is not None and savings.run.start == at(2, 0)
