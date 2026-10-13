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


def test_reversal_stops_first_and_shows_its_new_direction_later() -> None:
    from custom_components.slems.controller import command_arrivals

    history = [(0.0, -800.0), (10.0, -500.0), (20.0, 600.0), (20.5, 700.0), (30.0, 0.0)]
    arrivals = command_arrivals(history, 0.5, 1.7)
    # Same direction 0.5 s; the reversal stops after 0.5 s and charges after
    # 1.7 s, the correction right after it not before the reversal; a step to
    # standby 0.5 s again.
    assert [(power, shown) for _, power, shown in arrivals] == [
        (-800.0, 0.5),
        (-500.0, 10.5),
        (0.0, 20.5),
        (600.0, 21.7),
        (700.0, 21.7),
        (0.0, 30.5),
    ]
    # Without a separate reversal time all commands keep their own delay.
    assert [a for _, p, a in command_arrivals(history, 0.5, 0.5) if p] == [0.5, 10.5, 20.5, 21.0]


def test_start_out_of_standby_takes_the_start_time() -> None:
    from custom_components.slems.controller import command_arrivals

    history = [(0.0, 0.0), (5.0, 800.0), (6.0, 900.0), (10.0, 0.0), (20.0, -500.0)]
    shown = [a for _, _, a in command_arrivals(history, 0.5, 1.7, 2.7)]
    # Out of standby 2.7 s; the correction right after it not earlier.
    assert shown == [0.5, 7.7, 7.7, 10.5, 22.7]
    # The power before the oldest command decides about its first one.
    assert command_arrivals([(0.0, 800.0)], 0.5, 1.7, 2.7, before=0.0)[0][2] == 2.7
    assert command_arrivals([(0.0, 800.0)], 0.5, 1.7, 2.7, before=600.0)[0][2] == 0.5
    assert command_arrivals([(0.0, 800.0)], 0.5, 1.7, 2.7)[0][2] == 0.5
    # Without a start time a start is a normal step.
    assert command_arrivals([(0.0, 800.0)], 0.5, 1.7, before=0.0)[0][2] == 0.5


def test_start_and_reversal_times_fall_back_to_the_driver() -> None:
    from custom_components.slems.controller import RealTimeController

    battery = SimpleNamespace(
        subentry_id="a", name="A", driver=SimpleNamespace(start_delay_s=2.7), measured_power_w=None
    )
    other = SimpleNamespace(
        subentry_id="b", name="B", driver=SimpleNamespace(start_delay_s=None), measured_power_w=None
    )
    coordinator = SimpleNamespace(
        hass=None,
        batteries=[battery, other],
        settings=SimpleNamespace(control_gain=0.5),
        consumers=[],
    )
    controller = RealTimeController(coordinator)
    controller.battery_responses.restore({"a": 0.8, "b": 0.8})
    # Nothing learned: the driver's start delay, also for the new direction of a reversal.
    assert controller.battery_start_response_s("a") == 2.7
    assert controller.battery_reversal_response_s("a") == 2.7
    # A driver without a start delay: like any other step.
    assert controller.battery_start_response_s("b") == 0.8
    assert controller.battery_reversal_response_s("b") == 0.8
    # Learned values win, never shorter than a normal step.
    controller.battery_responses.restore_starts({"a": 2.4, "b": 0.3})
    controller.battery_responses.restore_reversals({"a": 1.8})
    assert controller.battery_start_response_s("a") == 2.4
    assert controller.battery_start_response_s("b") == 0.8
    assert controller.battery_reversal_response_s("a") == 1.8


def test_power_before_the_history_decides_about_a_start() -> None:
    from custom_components.slems.controller import BATTERY_HISTORY, RealTimeController

    battery = SimpleNamespace(subentry_id="a", driver=SimpleNamespace(start_delay_s=2.7), measured_power_w=-600.0)
    coordinator = SimpleNamespace(hass=None, batteries=[battery], settings=SimpleNamespace(control_gain=0.5))
    controller = RealTimeController(coordinator)
    # Still running in its own logic: the first command is no start.
    controller._record_command("a", -800.0, 0.0)
    assert controller._history_base["a"] == -600.0
    # Old commands dropped from the history: the last one dropped is the base.
    for step in range(1, BATTERY_HISTORY + 2):
        controller._record_command("a", 0.0 if step == 1 else 100.0 * step, float(step))
    assert controller._history_base["a"] == 0.0
    # Standing still before SLEMS took over: the first command is a start.
    controller._battery_history.clear()
    battery.measured_power_w = 20.0
    controller._record_command("a", 800.0, 50.0)
    assert controller._history_base["a"] == 0.0


def test_commands_report_starts_and_stops() -> None:
    from custom_components.slems.controller import RealTimeController

    battery = SimpleNamespace(subentry_id="a", driver=SimpleNamespace(start_delay_s=2.7), measured_power_w=None)
    coordinator = SimpleNamespace(hass=None, batteries=[battery], settings=SimpleNamespace(control_gain=0.5))
    controller = RealTimeController(coordinator)
    # Out of standby (nothing measured before), a correction, a stop and a restart 20 s later.
    for timestamp, power in ((0.0, 800.0), (1.0, 900.0), (10.0, 0.0), (30.0, -500.0), (31.0, 400.0)):
        controller._record_command("a", power, timestamp)
    (counts,) = controller.battery_starts.days["a"].values()
    # The reversal is no start.
    assert counts == {"starts": 2, "within_60_s": 1, "within_300_s": 1}
