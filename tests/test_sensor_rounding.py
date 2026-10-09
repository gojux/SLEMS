"""Tests for the rounding of sensor states (each change is a recorder row)."""

from custom_components.slems.sensor import rounded_state


def test_rounded_to_the_display_precision() -> None:
    assert rounded_state(0.500073969733255, 1) == 0.5
    assert rounded_state(94.8466312722701, 0) == 95
    assert isinstance(rounded_state(94.8, 0), int)
    # 0 and 0.0 are the same state.
    assert str(rounded_state(0.0, 0)) == str(rounded_state(0, 0)) == "0"
    # No "-0.0".
    assert str(rounded_state(-0.001, 1)) == "0.0"
    assert rounded_state(None, 1) is None
    assert rounded_state("charging", 1) == "charging"
    assert rounded_state(True, 0) is True
    assert rounded_state(1.23456, None) == 1.23456
