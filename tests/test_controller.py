"""Tests for controller helpers."""

from types import SimpleNamespace

from custom_components.slems.controller import _clamp_to_entity


def state(**attributes):
    return SimpleNamespace(attributes=attributes)


def test_set_point_is_clamped_and_stepped() -> None:
    assert _clamp_to_entity(3500, state(min=0, max=3000, step=100)) == 3000
    assert _clamp_to_entity(1234, state(min=0, max=3000, step=100)) == 1200
    assert _clamp_to_entity(-50, state(min=0, max=3000, step=1)) == 0
    assert _clamp_to_entity(777, state()) == 777
