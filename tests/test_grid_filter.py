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
