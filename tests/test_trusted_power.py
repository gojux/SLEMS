"""Tests for counting a battery with its measured power when it does not follow."""

from custom_components.slems.controller import MISMATCH_MAX_AGE_S, trusted_power

RESPONSE_S = 1.0


def test_battery_that_follows_counts_with_its_command() -> None:
    assert trusted_power(-2500, 0, -2400, 100, 101, RESPONSE_S) == -2500
    # Small commands: the minimum deviation applies.
    assert trusted_power(-150, 0, 0, 100, 101, RESPONSE_S) == -150


def test_battery_stopped_by_its_bms_counts_with_its_measurement() -> None:
    assert trusted_power(-2500, 0, 0, 100, 101, RESPONSE_S) == 0
    assert trusted_power(-2500, 0, -1000, 100, 101, RESPONSE_S) == -1000


def test_measurement_before_the_command_took_effect_is_ignored() -> None:
    # Command constant since 98: the measurement at 100 may still show the old power.
    assert trusted_power(-2500, 98, 0, 100, 101, RESPONSE_S) == -2500


def test_old_or_missing_measurement_is_ignored() -> None:
    assert trusted_power(-2500, 0, 0, 100, 101 + MISMATCH_MAX_AGE_S, RESPONSE_S) == -2500
    assert trusted_power(-2500, 0, None, None, 101, RESPONSE_S) == -2500
    assert trusted_power(-2500, None, 0, 100, 101, RESPONSE_S) == -2500
