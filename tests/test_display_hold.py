"""Tests for holding a displayed value until a change has lasted."""

from custom_components.slems.display_hold import DisplayHold


def test_first_value_is_shown_at_once() -> None:
    hold = DisplayHold(30)
    assert hold.update("night_discharge", 0) == "night_discharge"


def test_short_change_is_not_shown() -> None:
    hold = DisplayHold(30)
    hold.update("night_discharge", 0)
    assert hold.update("grid_friendly", 100) == "night_discharge"
    assert hold.update("grid_friendly", 101) == "night_discharge"
    assert hold.update("night_discharge", 102) == "night_discharge"
    # The short change does not count towards a later one.
    assert hold.update("grid_friendly", 120) == "night_discharge"
    assert hold.update("grid_friendly", 149) == "night_discharge"


def test_lasting_change_is_shown() -> None:
    hold = DisplayHold(30)
    hold.update("night_discharge", 0)
    hold.update("grid_friendly", 100)
    assert hold.update("grid_friendly", 130) == "grid_friendly"


def test_change_to_another_new_value_restarts_the_wait() -> None:
    hold = DisplayHold(30)
    hold.update("a", 0)
    hold.update("b", 10)
    assert hold.update("c", 30) == "a"
    assert hold.update("c", 59) == "a"
    assert hold.update("c", 60) == "c"


def test_unknown_is_shown_at_once() -> None:
    hold = DisplayHold(30)
    hold.update("a", 0)
    assert hold.update(None, 1) is None
    assert hold.update("b", 2) == "b"
