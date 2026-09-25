"""Tests for the automatic peak shaving import limit."""

from datetime import datetime, timedelta

import pytest

from homeassistant.util import dt as dt_util

from custom_components.slems.peak_shaving import (
    PeakProfile,
    auto_limit,
    hours_until_refill,
)


@pytest.fixture(autouse=True)
def vienna() -> None:
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Vienna"))


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 6, day, hour, minute, tzinfo=dt_util.get_default_time_zone())


def profile_with_peak() -> PeakProfile:
    """Two days: 500 W all the time, at 19:00-19:10 a 3000 W peak."""
    means = {}
    for day in (1, 2):
        for hour in range(24):
            for minute in range(0, 60, 5):
                power = 3000.0 if hour == 19 and minute < 10 else 500.0
                means[at(day, hour, minute)] = power
    return PeakProfile.from_means(means)


def test_energy_above_uses_the_5_minute_peaks() -> None:
    profile = profile_with_peak()
    # 10 minutes 2500 W above 500 W per day: about 417 Wh.
    assert profile.energy_above(19, 500) == pytest.approx(2500 * 10 / 60)
    assert profile.energy_above(19, 3000) == 0
    assert profile.energy_above(3, 0) == pytest.approx(500)


def test_auto_limit_fits_the_usable_energy() -> None:
    profile = profile_with_peak()
    hours = [(h, 1.0) for h in (18, 19, 20)]
    # Enough energy for everything: no import needed.
    assert auto_limit(profile, hours, 10_000, 0.0) == 0
    # Only the peak above the base load fits: limit at the base load.
    assert auto_limit(profile, hours, 2500 * 10 / 60 + 10, 0.0) == pytest.approx(500, abs=60)
    # Less energy: the limit rises into the peak.
    assert 500 < auto_limit(profile, hours, 200, 0.0) < 3000
    # The reserve keeps energy back: a higher limit.
    assert auto_limit(profile, hours, 500, 0.5) > auto_limit(profile, hours, 500, 0.0)


def test_hours_until_refill() -> None:
    pv, consumption = {}, {}
    for day in (1, 2):
        for hour in range(24):
            start = at(day, hour)
            pv[start] = 2000.0 if 8 <= hour < 17 else 0.0
            consumption[start] = 500.0
    hours = hours_until_refill(at(1, 20, 30), pv, consumption)
    assert hours[0] == (20, 0.5)
    assert hours[-1] == (7, 1.0)  # until PV exceeds the consumption at 08:00
    assert len(hours) == 12
    # During a surplus only the current hour counts.
    assert hours_until_refill(at(1, 12, 0), pv, consumption) == [(12, 1.0)]
