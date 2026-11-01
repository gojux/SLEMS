"""Tests for the automatic peak shaving import limit."""

from datetime import datetime, timedelta

import pytest

from homeassistant.util import dt as dt_util

from custom_components.slems.peak_shaving import (
    PeakProfile,
    auto_limit,
    auto_threshold,
    economic_limit,
    first_block,
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


def forecasts(pv_by_hour) -> tuple[dict, dict]:
    pv, consumption = {}, {}
    for day in (1, 2):
        for hour in range(24):
            start = at(day, hour)
            pv[start] = pv_by_hour(hour)
            consumption[start] = 500.0
    return pv, consumption


def test_hours_until_refill() -> None:
    pv, consumption = forecasts(lambda hour: 2000.0 if 8 <= hour < 17 else 0.0)
    hours = hours_until_refill(at(1, 20, 30), pv, consumption, 1000)
    assert hours[0] == (20, 0.5)
    assert hours[-1] == (7, 1.0)  # the 1.5 kWh surplus at 08:00 refills
    assert len(hours) == 12
    # During a surplus the following evening and night count.
    during_surplus = hours_until_refill(at(1, 12, 0), pv, consumption, 1000)
    assert during_surplus[0] == (17, 1.0)
    assert during_surplus[-1] == (7, 1.0)


def test_small_surplus_on_a_rainy_day_does_not_refill() -> None:
    # Only 12:00-14:00 slightly above the consumption: 2 x 100 Wh.
    pv, consumption = forecasts(lambda hour: 600.0 if 12 <= hour < 14 else 100.0)
    hours = hours_until_refill(at(1, 8, 0), pv, consumption, 1000)
    covered = {h for h, _ in hours}
    assert 11 in covered and 15 in covered  # continues after the small surplus
    assert 12 not in covered  # the batteries charge a little then
    assert len(hours) == 36 - 4  # never refilled: the whole horizon without surplus hours


def test_refill_with_own_pv_counts_only_what_the_batteries_can_take() -> None:
    pv, consumption = forecasts(lambda hour: 2000.0 if 8 <= hour < 17 else 0.0)
    # 1.5 kWh surplus per hour refills 3 kWh the next morning; if only 300 W
    # can charge (9 h x 300 Wh), the evening and night after count as well.
    hours = hours_until_refill(at(1, 20, 30), pv, consumption, 3000)
    assert hours[-1] == (7, 1.0)
    limited = hours_until_refill(at(1, 20, 30), pv, consumption, 3000, lambda hour: 300.0)
    assert limited[: len(hours)] == hours and (17, 1.0) in limited[len(hours):]


def test_auto_threshold_keeps_the_energy_for_the_peaks_above_the_limit() -> None:
    profile = profile_with_peak()
    # One evening until the refill: 2500 W for 10 minutes above 500 W.
    hours = [(19, 1.0)]
    threshold, full = auto_threshold(profile, lambda _: hours, 500, 10, 100, 10000, 1.0, 0.0, 20)
    assert threshold == pytest.approx(10 + 2500 * 10 / 60 / 10000 * 100)
    assert not full
    # With a reserve and losses the threshold rises; at the highest SoC it says so.
    higher, _ = auto_threshold(profile, lambda _: hours, 500, 10, 100, 10000, 0.9, 0.2, 20)
    assert higher > threshold
    capped, full = auto_threshold(profile, lambda _: hours * 100, 500, 10, 50, 10000, 1.0, 0.0, 20)
    assert capped == 50 and full


def economic(spread: float, max_discharge_w: float = 5000, floor_w: float = 0) -> object:
    """3 € per kW of the limit, 10 days left, one evening hour until the refill."""
    return economic_limit(
        profile_with_peak(), lambda _: [(19, 1.0)], lambda limit: 3.0 * limit / 1000, floor_w,
        10, spread, max_discharge_w, 10, 100, 10000, 1.0, 0.2, 20,
    )


def test_economic_limit_weighs_power_price_against_energy_held_back() -> None:
    # Cheap energy held back: the lowest limit (the batteries cover the whole hour).
    cheap = economic(0.2)
    assert cheap.limit_w == 0
    assert cheap.threshold_pct > 10
    # Energy held back very expensive: the highest peak, nothing held back.
    dear = economic(50.0)
    assert dear.limit_w == pytest.approx(3000)
    assert dear.threshold_pct == 10
    # Not below what the month already costs.
    assert economic(0.2, floor_w=1200).limit_w == pytest.approx(1200)


def test_economic_limit_only_what_the_batteries_can_hold() -> None:
    # 1000 W discharge power: a 3000 W peak needs a limit of at least 2000 W.
    assert economic(0.2, max_discharge_w=1000).limit_w == pytest.approx(2000)


def test_quantile_plans_for_a_bad_day() -> None:
    """Nine days 500 W, one day a 3000 W peak for 10 minutes at 19:00."""
    means = {}
    for day in range(1, 11):
        for minute in range(0, 60, 5):
            means[at(day, 19, minute)] = 3000.0 if day == 10 and minute < 10 else 500.0
    profile = PeakProfile.from_means(means)
    # Mean: a tenth of the peak day; the 90 % quantile: the second worst day (no peak).
    assert profile.energy_above(19, 500) == pytest.approx(2500 * 10 / 60 / 10)
    assert profile.with_quantile().energy_above(19, 500) == 0
    # Two peak days: the second worst has one.
    means[at(9, 19, 0)] = means[at(9, 19, 5)] = 3000.0
    bad = PeakProfile.from_means(means).with_quantile()
    assert bad.energy_above(19, 500) == pytest.approx(2500 * 10 / 60)
    assert bad.energy_above(19, 3000) == 0


def test_first_block_ends_after_two_quiet_hours() -> None:
    profile = profile_with_peak()
    hours = [(hour % 24, 1.0) for hour in range(18, 18 + 30)]
    block = first_block(profile, hours, 500)
    # 18:00 before the peak, the peak at 19:00, 20:00 quiet; the second quiet hour (21:00) ends it.
    assert [hour for hour, _ in block] == [18, 19, 20]
    assert first_block(profile, hours, 3000) == hours


def test_economic_limit_counts_the_charge_losses() -> None:
    # Charging back each day costs more than the power price saves: a higher limit.
    free = economic(0.2)
    costly = economic_limit(
        profile_with_peak(), lambda _: [(19, 1.0)], lambda limit: 3.0 * limit / 1000, 0,
        10, 0.2, 5000, 10, 100, 10000, 1.0, 0.2, 20, cycle_cost=10.0,
    )
    assert costly.limit_w > free.limit_w
