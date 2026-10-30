"""Tests for the automatic adaptation of the control gain."""

from collections.abc import Callable

import pytest

from custom_components.slems.adaptive_gain import (
    COOLDOWN_S,
    INCREASE_STEP,
    MIN_GAIN,
    RECOVERY_S,
    AdaptiveGain,
    GainAdjustment,
)

TARGET_W = -100.0
# Earliest and usual response of the commanded battery.
RESPONSES = [(0.45, 1.0)]


def swing(
    adapter: AdaptiveGain,
    start: float = 0.0,
    limit: float = 0.5,
    halves: int = 3,
    amplitude: float = 400.0,
    responses=RESPONSES,
) -> float:
    """Each half swing: the corrections reverse, about 1 s later the grid
    crosses the target to the other side in steps the batteries can make."""
    t = start
    sign = 1
    adapter.observe(t, -sign * 300, limit, responses)
    for _ in range(halves):
        t += 0.5
        adapter.observe(t, sign * 300, limit, responses)
        for i, share in enumerate((-0.5, 0.0, 0.5, 1.0)):
            adapter.sample(t + 0.6 + 0.25 * i, TARGET_W + sign * amplitude * share, TARGET_W)
        sign = -sign
        t += 1.5
    return t


def idle(adapter: AdaptiveGain, start: float, until: float, limit: float = 0.5) -> float:
    """Control cycles without a correction at a steady grid."""
    t = start
    while t < until:
        adapter.sample(t, TARGET_W, TARGET_W)
        adapter.observe(t, 0.0, limit, ())
        t += 0.5
    return t


def control_loop(
    gain: float,
    load: Callable[[int], float],
    assumed: int,
    true: int,
    cycles: int = 600,
) -> tuple[AdaptiveGain, list[float]]:
    """A battery charging against a PV surplus every 0.5 s: the controller sees
    its commands after ``assumed`` cycles, the meter after ``true`` cycles."""
    adapter = AdaptiveGain(gain)
    commands = [0.0] * (cycles + 1)
    grid_values = []
    for k in range(cycles):
        grid = load(k) + (commands[k - true] if k >= true else 0.0)
        seen = commands[k - assumed] if k >= assumed else 0.0
        t = 0.5 * k
        adapter.sample(t, grid, TARGET_W)
        current = commands[k]
        new = current + adapter.current(gain) * (TARGET_W - (grid - seen) - current)
        commands[k + 1] = max(-2500.0, min(2500.0, new))
        adapter.observe(t + 0.01, commands[k + 1] - current, gain, [(0.45, max(0.5 * assumed, 0.5))])
        grid_values.append(grid)
    return adapter, grid_values


def surplus(k: int) -> float:
    return -1500.0


def kick(k: int) -> float:
    # A load of 800 W for 2 s.
    return -1500.0 + (800.0 if 20 <= k < 24 else 0.0)


def stove(k: int) -> float:
    # 1.3 kW, 8 s on and 8 s off.
    return -1500.0 + (1300.0 if (k // 16) % 2 == 0 else 0.0)


def coffee(k: int) -> float:
    # Pulses of 1.4 kW shorter than the response, every 10 s.
    return -1500.0 + (1400.0 if k % 20 == 0 else 0.0)


def test_swinging_control_lowers_gain() -> None:
    adapter = AdaptiveGain(0.5)
    swing(adapter)
    assert adapter.gain == pytest.approx(0.4)
    assert adapter.last_adjustment is GainAdjustment.DECREASED


def test_two_crossings_are_no_oscillation() -> None:
    adapter = AdaptiveGain(0.5)
    swing(adapter, halves=2)
    assert adapter.gain == 0.5


def test_dying_swing_is_no_oscillation() -> None:
    adapter = AdaptiveGain(0.5)
    t = swing(adapter, halves=1, amplitude=1000)
    t = swing(adapter, start=t, halves=1, amplitude=300)
    swing(adapter, start=t, halves=1, amplitude=200)
    assert adapter.gain == 0.5


def test_crossing_before_the_earliest_response_does_not_count() -> None:
    # The grid crosses 1.1 s after the reversal: too early for a battery of 2 s.
    adapter = AdaptiveGain(0.5)
    swing(adapter, responses=[(0.45, 2.0)])
    assert adapter.gain == 0.5


def test_crossings_without_reversal_are_from_outside() -> None:
    adapter = AdaptiveGain(0.5)
    t = 0.0
    adapter.observe(t, 300, 0.5, RESPONSES)
    for value in (-400, 200, -400, 200, -400, 200, -400):
        t += 0.5
        adapter.sample(t, value, TARGET_W)
        adapter.observe(t + 0.01, 0.0, 0.5, ())
    assert adapter.gain == 0.5


def test_no_target_resets_the_series() -> None:
    adapter = AdaptiveGain(0.5)
    t = swing(adapter, halves=2)
    adapter.sample(t, 0.0, None)
    swing(adapter, start=t + 0.5, halves=2)
    assert adapter.gain == 0.5


def test_unstable_loop_is_damped() -> None:
    # Response time learned 1 s too short: with gain 0.67 the grid keeps
    # swinging; the reduced gain settles it.
    adapter, grid = control_loop(0.67, kick, assumed=2, true=4)
    assert adapter.gain < 0.67
    assert max(grid[-100:]) - min(grid[-100:]) < 50


def test_unstable_loop_swings_without_adaptation() -> None:
    commands = [0.0] * 601
    grid_values = []
    for k in range(600):
        grid = kick(k) + (commands[k - 4] if k >= 4 else 0.0)
        seen = commands[k - 2] if k >= 2 else 0.0
        new = commands[k] + 0.67 * (TARGET_W - (grid - seen) - commands[k])
        commands[k + 1] = max(-2500.0, min(2500.0, new))
        grid_values.append(grid)
    assert max(grid_values[-100:]) - min(grid_values[-100:]) > 1000


@pytest.mark.parametrize("load", [stove, coffee, kick, surplus])
def test_loads_from_outside_are_no_oscillation(load) -> None:
    adapter, _ = control_loop(0.5, load, assumed=2, true=2)
    assert adapter.gain == 0.5
    assert adapter.last_adjustment is GainAdjustment.NONE


@pytest.mark.parametrize("load", [stove, coffee])
def test_loads_from_outside_with_a_small_error_are_no_oscillation(load) -> None:
    # Response learned one cycle off: still stable with gain 0.5.
    for assumed, true in ((1, 2), (2, 1), (2, 3)):
        adapter, _ = control_loop(0.5, load, assumed=assumed, true=true)
        assert adapter.gain == 0.5, (load.__name__, assumed, true)


def test_cooldown_after_adjustment() -> None:
    adapter = AdaptiveGain(0.5)
    t = swing(adapter)
    t = swing(adapter, start=t)  # within the cooldown
    assert adapter.gain == pytest.approx(0.4)
    swing(adapter, start=t + COOLDOWN_S)
    assert adapter.gain == pytest.approx(0.32)


def test_bounds() -> None:
    adapter = AdaptiveGain(MIN_GAIN)
    swing(adapter, limit=MIN_GAIN)
    assert adapter.gain == MIN_GAIN
    # A set gain below the lowest reduction stays as set.
    adapter = AdaptiveGain(0.1)
    swing(adapter, limit=0.1)
    assert adapter.gain == 0.1


def test_set_gain_is_the_limit() -> None:
    # Learned before as 0.9, the set gain 0.5 limits it.
    adapter = AdaptiveGain(0.9)
    assert adapter.current(0.5) == 0.5
    swing(adapter)
    assert adapter.gain == pytest.approx(0.4)


def test_recovers_towards_the_limit() -> None:
    adapter = AdaptiveGain(0.5)
    t = swing(adapter)
    assert adapter.gain == pytest.approx(0.4)
    decreased_at = adapter.last_adjustment_at
    t = idle(adapter, t, decreased_at + RECOVERY_S - 1)
    assert adapter.gain == pytest.approx(0.4)
    t = idle(adapter, t, decreased_at + RECOVERY_S + 1)
    assert adapter.gain == pytest.approx(0.4 + INCREASE_STEP)
    assert adapter.last_adjustment is GainAdjustment.INCREASED
    t = idle(adapter, t, decreased_at + 2 * RECOVERY_S + 1)
    assert adapter.gain == pytest.approx(0.5)
    idle(adapter, t, decreased_at + 3 * RECOVERY_S + 1)
    assert adapter.gain == pytest.approx(0.5)


def test_reset_ends_the_cooldown() -> None:
    adapter = AdaptiveGain(0.5)
    t = swing(adapter)
    adapter.reset(0.5)
    swing(adapter, start=t)
    assert adapter.gain == pytest.approx(0.4)
