"""Tests for the counted starts of the batteries."""

from custom_components.slems.battery_starts import DAYS_KEPT, BatteryStarts


def test_restarts_shortly_after_a_stop_are_counted_apart() -> None:
    starts = BatteryStarts()
    # First start: no stop before it.
    starts.started("a", 0.0, "2026-10-13")
    starts.stopped("a", 100.0)
    starts.started("a", 130.0, "2026-10-13")  # 30 s after the stop
    starts.stopped("a", 200.0)
    starts.started("a", 400.0, "2026-10-13")  # 200 s after the stop
    starts.stopped("a", 500.0)
    starts.started("a", 1500.0, "2026-10-13")  # 1000 s after the stop
    assert starts.days["a"]["2026-10-13"] == {"starts": 4, "within_60_s": 1, "within_300_s": 2}
    # A start without a new stop before it is no restart.
    starts.started("a", 1510.0, "2026-10-13")
    assert starts.days["a"]["2026-10-13"]["within_60_s"] == 1


def test_only_the_last_days_are_kept_and_restored() -> None:
    starts = BatteryStarts()
    for day in range(1, DAYS_KEPT + 3):
        starts.started("a", float(day), f"2026-10-{day:02d}")
    assert sorted(starts.days["a"]) == [f"2026-10-{day:02d}" for day in range(3, DAYS_KEPT + 3)]
    restored = BatteryStarts()
    restored.restore(starts.as_dict() | {"b": {"2026-10-01": {"starts": 2}}})
    assert restored.days["a"] == starts.days["a"]
    # Counts stored before a window existed start at 0.
    assert restored.days["b"]["2026-10-01"] == {"starts": 2, "within_60_s": 0, "within_300_s": 0}
