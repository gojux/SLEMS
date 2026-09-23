"""Tests for the learned meter cadence and response times."""

import pytest

from custom_components.slems.response import MeterCadence, StepResponse


def test_meter_cadence() -> None:
    cadence = MeterCadence()
    assert cadence.value == 2.0  # default until measured
    for index in range(20):
        cadence.report(index * 1.5)
    assert cadence.value == pytest.approx(1.5)


def test_step_response_learns_delay() -> None:
    response = StepResponse(default_s=3.0)
    for start in (0.0, 100.0, 200.0):
        # 1000 W more charging: the grid rises from -1000 to 0 after 2.5 s.
        response.command(start, -1000, 1000)
        response.sample(start + 1.0, -1000)
        response.sample(start + 2.5, -50)
    assert response.value == pytest.approx(2.5)


def test_step_response_ignores_small_and_disturbed_steps() -> None:
    response = StepResponse(default_s=3.0)
    response.command(0, 0, 100)  # below the minimum step
    response.sample(1, 100)
    assert response.response_s is None
    response.command(10, 0, -800)
    response.command(11, 0, 50)  # another command interferes
    response.sample(12, -800)
    assert response.response_s is None
    response.command(20, 0, 800)
    response.sample(60, 800)  # nothing within the maximum time
    assert response.response_s is None
