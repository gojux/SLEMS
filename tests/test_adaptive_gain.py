"""Tests for the automatic adaptation of the control gain."""

import pytest

from custom_components.slems.adaptive_gain import (
    COOLDOWN_S,
    INCREASE_STEP,
    MIN_GAIN,
    RECOVERY_S,
    AdaptiveGain,
    GainAdjustment,
)


def feed(
    adapter: AdaptiveGain, corrections, start: float = 0.0, step: float = 2.0, limit: float = 0.5
) -> float:
    t = start
    for correction in corrections:
        adapter.observe(t, correction, limit)
        t += step
    return t


def test_oscillation_reduces_gain() -> None:
    adapter = AdaptiveGain(0.5)
    feed(adapter, [800, -900, 850, -900])
    assert adapter.gain == pytest.approx(0.4)
    assert adapter.last_adjustment is GainAdjustment.DECREASED


def test_decaying_alternation_is_no_oscillation() -> None:
    adapter = AdaptiveGain(0.5)
    feed(adapter, [800, -300, 100, -60])
    assert adapter.gain == 0.5


def test_external_load_steps_are_no_oscillation() -> None:
    # Kettle on/off: corrections follow the load, not alternating each cycle.
    adapter = AdaptiveGain(0.5)
    feed(adapter, [1000, 500, 250, -1000, -500, -250])
    assert adapter.gain == 0.5


def test_approach_to_a_large_step_keeps_gain() -> None:
    # Gain 0.3 towards a 2.3 kW step: shrinking same-direction corrections.
    adapter = AdaptiveGain(0.3)
    feed(adapter, [700, 490, 343, 240, 168, 118, 82, 58], limit=0.3)
    assert adapter.gain == 0.3
    assert adapter.last_adjustment is GainAdjustment.NONE


def test_ramp_does_not_raise_gain() -> None:
    adapter = AdaptiveGain(0.5)
    feed(adapter, [120, 130, 125, 140, 135, 130, 128])
    assert adapter.gain == 0.5


def test_cooldown_after_adjustment() -> None:
    adapter = AdaptiveGain(0.5)
    t = feed(adapter, [800, -900, 850, -900])
    t = feed(adapter, [800, -900, 850, -900], start=t)  # within the cooldown
    assert adapter.gain == pytest.approx(0.4)
    feed(adapter, [800, -900, 850, -900], start=t + COOLDOWN_S)
    assert adapter.gain == pytest.approx(0.32)


def test_gaps_split_movements() -> None:
    adapter = AdaptiveGain(0.5)
    feed(adapter, [800, -900], step=2)
    feed(adapter, [850, -900], start=100, step=2)
    assert adapter.gain == 0.5


def test_bounds() -> None:
    adapter = AdaptiveGain(MIN_GAIN)
    feed(adapter, [800, -900, 850, -900])
    assert adapter.gain == MIN_GAIN
    # A set gain below the lowest reduction stays as set.
    adapter = AdaptiveGain(0.1)
    feed(adapter, [800, -900, 850, -900], limit=0.1)
    assert adapter.gain == 0.1


def test_set_gain_is_the_limit() -> None:
    # Learned before as 0.9, the set gain 0.5 limits it.
    adapter = AdaptiveGain(0.9)
    assert adapter.current(0.5) == 0.5
    feed(adapter, [800, -900, 850, -900])
    assert adapter.gain == pytest.approx(0.4)


def test_recovers_towards_the_limit() -> None:
    adapter = AdaptiveGain(0.5)
    t = feed(adapter, [800, -900, 850, -900])
    assert adapter.gain == pytest.approx(0.4)
    feed(adapter, [0], start=t + RECOVERY_S - 10)
    assert adapter.gain == pytest.approx(0.4)
    t = feed(adapter, [0], start=t + RECOVERY_S)
    assert adapter.gain == pytest.approx(0.4 + INCREASE_STEP)
    assert adapter.last_adjustment is GainAdjustment.INCREASED
    t = feed(adapter, [0], start=t + RECOVERY_S)
    assert adapter.gain == pytest.approx(0.5)
    feed(adapter, [0], start=t + RECOVERY_S)
    assert adapter.gain == pytest.approx(0.5)


def test_reset_ends_the_cooldown() -> None:
    adapter = AdaptiveGain(0.5)
    t = feed(adapter, [800, -900, 850, -900])
    adapter.reset(0.5)
    feed(adapter, [800, -900, 850, -900], start=t)
    assert adapter.gain == pytest.approx(0.4)
