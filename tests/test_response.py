"""Tests for the learned meter cadence and response times."""

import pytest

from custom_components.slems.response import BatteryResponses, MeterCadence, StepResponse


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
    response.command(11, 0, 50)  # a correction the other way hides the step
    response.sample(12, -800)
    assert response.response_s is None
    response.command(20, 0, 800)
    response.sample(60, 800)  # nothing within the maximum time
    assert response.response_s is None


def test_small_corrections_the_same_way_keep_the_measurement() -> None:
    response = StepResponse(default_s=3.0)
    # The damped control follows the 1000 W step with smaller ones the same way.
    response.command(0.0, -1000, 1000)
    response.command(1.0, -800, 200)
    response.command(2.0, -700, 100)
    response.sample(1.5, -950)
    response.sample(2.4, -300)
    assert response.response_s == pytest.approx(2.4)


def test_switching_on_and_off_learned_apart() -> None:
    from custom_components.slems.response import DirectionalResponse

    # A dehumidifier behind a smart plug: starts 2 min after switching on,
    # the plug shows switching off after 3 s.
    response = DirectionalResponse(default_s=10.0, min_step_w=100)
    for start in (0.0, 1000.0):
        response.command(start, 0, 300)
        response.sample(start + 60, 0)
        response.sample(start + 120, 290)
        response.command(start + 500, 290, -290)
        response.sample(start + 503, 5)
    assert response.learned(True) == pytest.approx(120)
    assert response.learned(False) == pytest.approx(3)
    # A command the other way ends the measurement still waiting.
    response.command(3000, 0, 300)
    response.command(3010, 0, -300)
    response.sample(3100, 300)
    assert response.learned(True) == pytest.approx(120)


def test_battery_responses_learn_from_own_steps() -> None:
    responses = BatteryResponses()
    # Shared step: no battery learns.
    responses.command(0.0, 0.0, {"a": 400, "b": 400})
    responses.sample(1.0, 800)
    assert responses.learned("a") is None and responses.learned("b") is None
    # Battery a makes the step mostly alone.
    responses.command(10.0, 0.0, {"a": 900, "b": 100})
    responses.sample(10.6, 300)
    responses.sample(11.2, 900)
    assert responses.learned("a") == pytest.approx(1.2)
    assert responses.learned("b") is None
    # Restored after a restart.
    restored = BatteryResponses()
    restored.restore(responses.as_dict())
    assert restored.learned("a") == pytest.approx(1.2)


def test_battery_responses_log_each_measurement(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("DEBUG", logger="custom_components.slems.response")
    responses = BatteryResponses({"a": "Venus 1"}.get)
    responses.command(0.0, -500.0, {"a": 1000}, started_ids=frozenset({"a"}))
    responses.sample(2.6, 200.0)
    messages = [record.getMessage() for record in caplog.records]
    assert "Response start Venus 1: step +1000 W from -500 W arrived after 2.60 s (grid 200 W), learned 2.60 s" in messages
    # A start is no step.
    assert not any(message.startswith("Response step Venus 1:") for message in messages)
    # Restored learners log with the battery name too.
    caplog.clear()
    restored = BatteryResponses({"a": "Venus 1"}.get)
    restored.restore_reversals({"a": 1.8})
    restored.command(10.0, 0.0, {"a": -1000}, reversed_from={"a": 500.0})
    restored.sample(40.5, 0.0)
    assert any(
        record.getMessage().startswith("Response reversal Venus 1: step -500 W from -500 W not arrived")
        for record in caplog.records
    )


def test_battery_responses_shared_step_ends_a_pending_measurement() -> None:
    responses = BatteryResponses()
    responses.command(0.0, 0.0, {"a": 1000})
    # A small correction the same way keeps it pending.
    responses.command(0.5, 200.0, {"a": 100})
    # Another battery's large step would hide the end of a's step.
    responses.command(0.8, 400.0, {"b": 800})
    responses.sample(1.5, 1600)
    assert responses.learned("a") is None
    assert responses.learned("b") == pytest.approx(0.7)


def test_reversal_learned_apart_for_its_battery() -> None:
    responses = BatteryResponses()
    # A normal step of battery a (more discharging): 2 s.
    responses.command(0.0, 0.0, {"a": -800.0})
    responses.sample(2.0, -800.0)
    # A reversal of battery a (discharging -> charging): 5 s.
    responses.command(10.0, -800.0, {"a": 1600.0}, {"a": -800.0})
    responses.sample(12.0, -700.0)
    responses.sample(15.0, 800.0)
    assert responses.learned("a") is not None
    assert responses.learned_reversal("a") == 5.0
    assert responses.learned_reversal("b") is None
    restored = BatteryResponses()
    restored.restore_reversals(responses.reversals_as_dict())
    assert restored.learned_reversal("a") == 5.0


def test_start_from_standby_learned_apart_for_its_battery() -> None:
    responses = BatteryResponses()
    # Battery a leaves standby (0 W -> 1000 W charging): 0.8 s.
    responses.command(0.0, 0.0, {"a": 1000.0}, started_ids=frozenset({"a"}))
    responses.sample(0.8, 1000.0)
    # A reversal is no start.
    responses.command(10.0, 1000.0, {"a": -2000.0}, {"a": 1000.0})
    responses.sample(12.0, -1000.0)
    assert responses.learned_start("a") == pytest.approx(0.8)
    assert responses.learned_reversal("a") == pytest.approx(2.0)
    assert responses.learned_start("b") is None
    restored = BatteryResponses()
    restored.restore_starts(responses.starts_as_dict())
    assert restored.learned_start("a") == pytest.approx(0.8)


def test_reversal_is_learned_until_the_new_direction_shows() -> None:
    responses = BatteryResponses()
    # Battery a reverses from 1500 W discharging to 1500 W charging.
    responses.command(0.0, -1500.0, {"a": 3000.0}, {"a": -1500.0})
    # Its stop shows after 0.5 s: 50 % of the step, but no charging yet.
    responses.sample(0.5, 0.0)
    responses.sample(1.5, 300.0)
    # 60 % of the new direction.
    responses.sample(1.8, 900.0)
    assert responses.learned_reversal("a") == pytest.approx(1.8)


def test_movement_before_the_earliest_response_drops_the_measurement() -> None:
    responses = BatteryResponses(earliest=lambda _battery, _kind: 0.45)
    # The stove switches off 0.26 s after a command for more discharging.
    responses.command(0.0, 1250.0, {"a": -620.0})
    responses.sample(0.26, -130.0)
    responses.sample(1.0, -700.0)
    assert responses.learned("a") is None
    # Before the earliest response but within half the step: measured on.
    responses.command(10.0, 1250.0, {"a": -620.0})
    responses.sample(10.3, 1100.0)
    responses.sample(10.9, 800.0)
    assert responses.learned("a") == pytest.approx(0.9)


def test_foreign_jump_at_the_arrival_drops_the_measurement() -> None:
    responses = BatteryResponses()
    # The grid moves twice the step: a load switched off along.
    responses.command(0.0, 1200.0, {"a": -600.0})
    responses.sample(0.9, 0.0)
    assert responses.learned("a") is None


def test_commands_on_their_way_count_in_the_expected_change() -> None:
    responses = BatteryResponses()
    responses.restore({"a": 1.0})
    # The damped control: -700 W, then 0.5 s later another -400 W; both take 1 s.
    responses.command(0.0, 1500.0, {"a": -700.0})
    responses.command(0.5, 1500.0, {"a": -400.0})
    responses.sample(1.0, 800.0)  # the first command arrived, not yet 60 % of the second
    responses.sample(1.5, 400.0)
    assert responses.learned("a") == pytest.approx(1.0)


def test_starts_and_reversals_are_no_steps_and_short_stops_no_starts() -> None:
    responses = BatteryResponses()
    responses.command(0.0, -800.0, {"a": 1600.0}, {"a": -800.0})
    responses.sample(2.0, 800.0)
    assert responses.learned("a") is None
    assert responses.learned_reversal("a") == pytest.approx(2.0)
    # Started again right after a stop: nothing is learned.
    responses.command(10.0, 0.0, {"a": 800.0}, restarted_ids=frozenset({"a"}))
    responses.sample(11.0, 800.0)
    assert responses.learned("a") is None and responses.learned_start("a") is None


def test_learned_value_is_the_median_and_its_samples_are_stored() -> None:
    response = StepResponse(default_s=3.0)
    for start, elapsed in ((0.0, 1.0), (100.0, 0.2), (200.0, 0.9), (300.0, 1.1)):
        response.command(start, 0.0, 1000.0)
        response.sample(start + elapsed, 1000.0)
    # One short outlier does not pull the value down.
    assert response.value == pytest.approx(0.95)
    restored = StepResponse(default_s=3.0)
    restored.response_s = 0.5  # stored by an older version
    restored.restore_samples(response.samples)
    assert restored.value == pytest.approx(0.95)
    restored.restore_samples([])
    assert restored.value == pytest.approx(0.95)
    responses = BatteryResponses()
    responses.command(0.0, 0.0, {"a": 1000.0})
    responses.sample(0.8, 1000.0)
    again = BatteryResponses()
    again.restore_samples(responses.samples_as_dict())
    assert again.learned("a") == pytest.approx(0.8)


def test_share_of_measurements_dropped_as_too_early() -> None:
    from custom_components.slems.response import OUTCOMES_KEPT

    responses = BatteryResponses(earliest=lambda _battery, _kind: 0.45)
    for k in range(OUTCOMES_KEPT):
        start = 100.0 * k
        responses.command(start, 0.0, {"a": 500.0})
        # Every other measurement moves before the earliest response.
        responses.sample(start + (0.3 if k % 2 else 0.9), 400.0)
        if k < OUTCOMES_KEPT - 1:
            assert responses.early_share("a") is None
    assert responses.early_share("a") == 0.5
    responses.reset_outcomes("a")
    assert responses.early_share("a") is None


def test_measurements_from_elsewhere_and_forgetting_a_battery() -> None:
    responses = BatteryResponses()
    responses.add("step", "a", 0.9)
    responses.add("step", "a", 1.1)
    responses.add("start", "a", 2.5)
    assert responses.learned("a") == pytest.approx(1.0)
    assert responses.learned_start("a") == 2.5
    responses.clear("a")
    assert responses.learned("a") is None and responses.learned_start("a") is None
