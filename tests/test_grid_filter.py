"""Tests for the conservative grid power filter."""

import pytest

from custom_components.slems.grid_filter import GridPowerFilter


def test_time_weighted_average() -> None:
    grid_filter = GridPowerFilter(window_s=10)
    grid_filter.add(0, -1000)
    grid_filter.add(5, -3000)
    # 5 s at -1000 W and 5 s at -3000 W
    assert grid_filter.average(10) == pytest.approx(-2000)


def test_rising_pv_is_followed_after_window_only() -> None:
    grid_filter = GridPowerFilter(window_s=10)
    grid_filter.add(0, -1000)
    grid_filter.add(9, -4000)  # sudden PV increase
    # Average -1300 W is less surplus than the current -4000 W.
    assert grid_filter.conservative(10) == pytest.approx(-1300)


def test_falling_pv_is_followed_immediately() -> None:
    grid_filter = GridPowerFilter(window_s=10)
    grid_filter.add(0, -4000)
    grid_filter.add(9, 500)  # cloud: importing now
    assert grid_filter.conservative(10) == 500


def test_window_zero_disables_averaging() -> None:
    grid_filter = GridPowerFilter(window_s=0)
    grid_filter.add(0, -1000)
    grid_filter.add(1, -4000)
    assert grid_filter.conservative(2) == -4000


def test_old_samples_leave_the_window() -> None:
    grid_filter = GridPowerFilter(window_s=5)
    grid_filter.add(0, 2000)
    grid_filter.add(10, -1000)
    assert grid_filter.average(20) == -1000


def test_short_load_pulse_is_ignored() -> None:
    grid_filter = GridPowerFilter(window_s=3, hold_s=1.2)
    for step in range(10):
        grid_filter.add(step * 0.5, -100)
    # A coffee machine heats 1300 W for one meter report.
    grid_filter.add(5.0, 1200)
    grid_filter.add(5.5, -100)
    for now in (5.0, 5.4, 5.6, 7.0, 9.0):
        assert grid_filter.conservative(now) == pytest.approx(-100)


def test_lasting_load_counts_after_the_hold_time() -> None:
    grid_filter = GridPowerFilter(window_s=3, hold_s=1.2)
    for step in range(10):
        grid_filter.add(step * 0.5, -100)
    for step in range(10, 13):
        grid_filter.add(step * 0.5, 1200)
    assert grid_filter.conservative(6.0) == pytest.approx(-100)
    grid_filter.add(6.5, 1200)
    assert grid_filter.conservative(6.5) == pytest.approx(1200)


def test_load_switching_off_is_followed_after_the_window() -> None:
    grid_filter = GridPowerFilter(window_s=4, hold_s=1)
    grid_filter.add(0, 1000)
    grid_filter.add(10, -1000)
    # Held value at once, average 1000 W for 2 s and -1000 W for 2 s.
    assert grid_filter.held(12) == -1000
    assert grid_filter.conservative(12) == pytest.approx(0)
    assert grid_filter.conservative(14) == pytest.approx(-1000)


def test_own_battery_corrections_do_not_stay_in_the_average() -> None:
    grid_filter = GridPowerFilter(window_s=3)
    # The house needs 1000 W more; the batteries take over 1000 W within 1 s.
    grid_filter.add(0, -100, battery_power_w=1500)
    grid_filter.add(1, 900, battery_power_w=1500)
    grid_filter.add(2, -100, battery_power_w=500)
    # Without the batteries the house needs 1000 W more since the second 1:
    # the average of the grid power itself (566 W) would count the
    # correction already in the battery power a second time.
    assert grid_filter.conservative(4) == pytest.approx(-600)
    assert grid_filter.conservative(4) + 500 == pytest.approx(-100)
