"""Tests for the rounding of sensor states (each change is a recorder row)."""

from custom_components.slems.sensor import rounded_state, without_small_dip


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


def test_a_counter_keeps_its_value_over_a_small_step_back() -> None:
    assert without_small_dip(210.71, None, 0.1) == 210.71
    # A Venus stepped back from 210.71 to 210.67 kWh.
    assert without_small_dip(210.67, 210.71, 0.1) == 210.71
    assert without_small_dip(210.72, 210.71, 0.1) == 210.72
    # A larger drop is a reset of the counter.
    assert without_small_dip(210.5, 210.71, 0.1) == 210.5
    assert without_small_dip(0.0, 210.71, 0.1) == 0.0
    assert without_small_dip(None, 210.71, 0.1) is None
