"""Tests for controller helpers."""

from types import SimpleNamespace

from custom_components.slems.util import clamp_to_entity


def state(**attributes):
    return SimpleNamespace(attributes=attributes)


def test_set_point_is_clamped_and_stepped() -> None:
    assert clamp_to_entity(3500, state(min=0, max=3000, step=100)) == 3000
    assert clamp_to_entity(1234, state(min=0, max=3000, step=100)) == 1200
    assert clamp_to_entity(-50, state(min=0, max=3000, step=1)) == 0
    assert clamp_to_entity(777, state()) == 777


def test_consumer_as_the_meter_sees_it() -> None:
    from custom_components.slems.controller import seen_consumer_power

    # Heating rod switched on (0 -> 2000 W); meter 4 s, own sensor 1 s.
    args = {"before_w": 0.0, "target_w": 2000.0, "grid_delay_s": 4.0, "sensor_delay_s": 1.0}
    assert seen_consumer_power(1990, elapsed_s=2, **args) == 0  # meter shows it later
    assert seen_consumer_power(1990, elapsed_s=5, **args) == 1990
    # Slow sensor (8 s): after the meter shows the step the commanded power counts.
    slow = args | {"sensor_delay_s": 8.0}
    assert seen_consumer_power(0, elapsed_s=5, **slow) == 2000
    assert seen_consumer_power(0, elapsed_s=9, **slow) == 0  # thermostat: draws nothing
