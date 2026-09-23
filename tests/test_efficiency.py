"""Tests for the round trip efficiency."""

import pytest

from custom_components.slems.const import EfficiencyMode
from custom_components.slems.efficiency import EfficiencyTracker, estimate_efficiency


def test_estimate_needs_enough_throughput() -> None:
    assert estimate_efficiency(10, 8, 0, capacity_kwh=5) is None
    assert estimate_efficiency(50, 42, 1, capacity_kwh=5) == pytest.approx(0.86)


def test_battery_counters_mode() -> None:
    tracker = EfficiencyTracker(EfficiencyMode.BATTERY_COUNTERS, 90, capacity_kwh=5)
    assert tracker.round_trip == pytest.approx(0.90)  # configured start value
    tracker.update(0, None, soc_pct=40, total_charged_kwh=100, total_discharged_kwh=83)
    # (83 + 0.4 * 5) / 100
    assert tracker.round_trip == pytest.approx(0.85)
    assert tracker.is_learned
    assert tracker.one_way == pytest.approx(0.85**0.5)


def test_learned_mode_integrates_power() -> None:
    tracker = EfficiencyTracker(EfficiencyMode.LEARNED, 88, capacity_kwh=1)
    t = 0.0
    # 4 cycles: charge 1 kWh at 1000 W, discharge 0.8 kWh at 800 W, 60 s steps.
    for _ in range(4):
        for _ in range(60):
            tracker.update(t, 1000, 50, None, None)
            t += 60
        for _ in range(60):
            tracker.update(t, -800, 50, None, None)
            t += 60
    assert tracker.round_trip == pytest.approx(0.8, abs=0.01)


def test_manual_mode_ignores_measurements() -> None:
    tracker = EfficiencyTracker(EfficiencyMode.MANUAL, 92, capacity_kwh=5)
    tracker.update(0, None, 50, 100, 70)
    assert tracker.round_trip == pytest.approx(0.92)
    assert not tracker.is_learned
