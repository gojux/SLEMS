"""Tests for the detection of batteries that do not deliver power."""

from custom_components.slems.delivery_monitor import (
    COOLDOWN_S,
    ENGAGE_GRACE_S,
    FAIL_THRESHOLD,
    Action,
    DeliveryMonitor,
)


def fail_until_action(monitor: DeliveryMonitor, start: float) -> tuple[Action, float]:
    t = start
    for _ in range(FAIL_THRESHOLD):
        action = monitor.observe(t, -1000, 0, 0, 60)
        t += 5
    return action, t


def test_wake_first_then_exclude_and_retry() -> None:
    monitor = DeliveryMonitor("test")
    action, t = fail_until_action(monitor, ENGAGE_GRACE_S)
    assert action is Action.WAKE
    action, t = fail_until_action(monitor, t)
    assert action is Action.EXCLUDE
    assert monitor.excluded(t)
    monitor.tick(t + COOLDOWN_S)
    assert not monitor.excluded(t + COOLDOWN_S)


def test_grace_small_commands_and_delivery() -> None:
    monitor = DeliveryMonitor("test")
    assert monitor.observe(10, -1000, 0, 0, 60) is Action.NONE  # engaging
    assert monitor.observe(40, -50, 0, 0, 60) is Action.NONE  # too small to judge
    monitor.observe(40, -1000, 0, 0, 60)
    assert monitor.fail_count == 1
    monitor.observe(45, -1000, 0, -900, 60)  # delivers
    assert monitor.fail_count == 0


def test_bms_limits_are_no_failure() -> None:
    monitor = DeliveryMonitor("test")
    for t in range(40, 100, 5):
        assert monitor.observe(t, 1000, 0, 0, 99.5) is Action.NONE  # full
        assert monitor.observe(t, -1000, 0, 0, 15) is Action.NONE  # BMS cut-off range
    assert monitor.fail_count == 0


def test_communication_failures_exclude_without_wake() -> None:
    monitor = DeliveryMonitor("test")
    actions = [monitor.record_comm_failure(t) for t in range(FAIL_THRESHOLD)]
    assert actions[-1] is Action.EXCLUDE
