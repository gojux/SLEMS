"""Tests for the automatic adaptation of the control gain."""

import pytest

from custom_components.slems.adaptive_gain import (
    COOLDOWN_S,
    MAX_GAIN,
    MIN_GAIN,
    AdaptiveGain,
    GainAdjustment,
)


def feed(adapter: AdaptiveGain, corrections, start: float = 0.0, step: float = 2.0) -> float:
    t = start
    for correction in corrections:
        adapter.observe(t, correction)
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


def test_slow_approach_raises_gain() -> None:
    adapter = AdaptiveGain(0.3)
    feed(adapter, [700, 490, 343, 240, 168])  # gain 0.3: many small steps
    assert adapter.gain == pytest.approx(0.35)
    assert adapter.last_adjustment is GainAdjustment.INCREASED


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
    assert AdaptiveGain(5).gain == MAX_GAIN
    adapter = AdaptiveGain(MIN_GAIN)
    feed(adapter, [800, -900, 850, -900])
    assert adapter.gain == MIN_GAIN
