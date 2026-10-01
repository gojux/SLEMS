"""Tests for the recorded quarter hours of grid import and export."""

from datetime import datetime, timedelta

import pytest

from homeassistant.util import dt as dt_util

from custom_components.slems.grid_quarters import GridQuarters, merge_quarters
from custom_components.slems.market_prices import period_means

HOUR = 1_767_225_600  # an hour start (unix)


def utc(stamp: int) -> datetime:
    return dt_util.utc_from_timestamp(stamp)


def test_values_are_split_at_quarter_hours() -> None:
    quarters = GridQuarters()
    # 1200 W import from 10 min before to 5 min after a boundary, then export.
    for second in (300, 600, 900):
        quarters.add(1200.0, HOUR + second)
    quarters.add(-600.0, HOUR + 1200)
    quarters.add(-600.0, HOUR + 1500)
    assert quarters.imported[HOUR] == pytest.approx(1200 * 600 / 3600)
    assert quarters.imported[HOUR + 900] == pytest.approx(1200 * 300 / 3600)
    assert quarters.exported[HOUR + 900] == pytest.approx(600 * 300 / 3600)
    assert quarters.covered[HOUR] == 600 and quarters.covered[HOUR + 900] == 600
    # Import and export in the same quarter do not cancel out.
    assert HOUR + 900 in quarters.imported and HOUR + 900 in quarters.exported


def test_long_gaps_are_not_bridged_and_complete_needs_coverage() -> None:
    quarters = GridQuarters()
    quarters.add(500.0, HOUR)
    quarters.add(500.0, HOUR + 290)
    quarters.add(500.0, HOUR + 890)  # 600 s gap: not counted
    assert quarters.covered[HOUR] == 290
    assert not quarters.complete(HOUR)
    quarters.add(None, HOUR + 900)
    quarters.add(500.0, HOUR + 1000)  # after an unknown value nothing is bridged
    assert HOUR + 900 not in quarters.covered


def recorded(hours: int, grid_w: float) -> GridQuarters:
    quarters = GridQuarters()
    for second in range(HOUR, HOUR + hours * 3600 + 1, 60):
        quarters.add(grid_w if (second - HOUR) % 1800 < 900 else -grid_w, second)
    return quarters


def test_merge_uses_complete_hours_and_scales_to_counters() -> None:
    quarters = recorded(1, 1000.0)
    start, end = utc(HOUR), utc(HOUR + 2 * 3600)
    counters_import = {utc(HOUR): 1000.0, utc(HOUR + 3600): 700.0}
    imported, exported, lengths = merge_quarters(
        counters_import, {}, quarters, start, end, scale_to_hours=True
    )
    # First hour per quarter: import in quarters 1 and 3, scaled to the counter.
    assert [imported[utc(HOUR + i * 900)] for i in range(4)] == pytest.approx([500, 0, 500, 0])
    assert exported[utc(HOUR + 900)] == pytest.approx(250)  # no export counter: as recorded
    # Second hour not recorded: the counter value of the hour.
    assert imported[utc(HOUR + 3600)] == 700 and lengths[utc(HOUR + 3600)] == 3600
    assert sum(1 for length in lengths.values() if length == 900) == 4


def test_round_trip_and_period_means() -> None:
    quarters = recorded(1, 800.0)
    restored = GridQuarters.from_dict(quarters.as_dict())
    assert restored.imported == pytest.approx(quarters.imported)
    assert restored.covered == pytest.approx({k: round(v) for k, v in quarters.covered.items()})
    prices = {HOUR: 10.0, HOUR + 900: 20.0, HOUR + 1800: 30.0, HOUR + 2700: 40.0}
    means = period_means(prices, {utc(HOUR): 3600, utc(HOUR + 900): 900, utc(HOUR + 7200): 900})
    assert means == {utc(HOUR): 25.0, utc(HOUR + 900): 20.0}
