"""Tests for the detection of batteries that do not deliver power."""

from custom_components.slems.battery_limits import (
    BatteryLimitSettings,
    SocWindow,
    TemperatureLimit,
    power_limits,
)
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
    assert monitor.fail_count == 0


def test_bms_discharge_stop_retries_lower_then_blocks_without_failure() -> None:
    monitor = DeliveryMonitor("test")
    actions = [monitor.observe(40 + 5 * i, -2500, 0, 0, 12.4) for i in range(FAIL_THRESHOLD)]
    assert actions[-1] is Action.BMS_STOP
    assert all(action is Action.NONE for action in actions[:-1])
    assert monitor.bms_limit_w == 1250 and monitor.bms_stop_soc == 12.4
    assert monitor.fail_count == 0 and not monitor.excluded(100)
    # The lower power gets time to take effect.
    for t in range(55, 55 + int(ENGAGE_GRACE_S), 5):
        assert monitor.observe(t, -1250, 0, 0, 12.2) is Action.NONE
    # It delivers: the lower limit stays.
    monitor.observe(90, -1250, 0, -1200, 12.2)
    assert monitor.bms_limit_w == 1250
    actions = [monitor.observe(95 + 5 * i, -1250, 0, 0, 12.0) for i in range(FAIL_THRESHOLD)]
    assert actions[-1] is Action.BMS_STOP
    assert monitor.bms_limit_w == 0 and monitor.bms_stop_soc == 12.4
    assert monitor.fail_count == 0 and not monitor.excluded(200)


def test_bms_stop_at_low_power_blocks_at_once() -> None:
    monitor = DeliveryMonitor("test")
    for i in range(FAIL_THRESHOLD):
        monitor.observe(40 + 5 * i, -300, 0, 0, 12.4)
    assert monitor.bms_limit_w == 0
    monitor = DeliveryMonitor("test")
    for i in range(FAIL_THRESHOLD):
        monitor.observe(40 + 5 * i, -500, 0, 0, 12.4)
    assert monitor.bms_limit_w == 300


def test_bms_stop_needs_consecutive_polls() -> None:
    monitor = DeliveryMonitor("test")
    monitor.observe(40, -2500, 0, 0, 12.4)
    monitor.observe(45, -2500, 0, -2400, 12.4)  # delivers again
    monitor.observe(50, -2500, 0, 0, 12.4)
    assert monitor.bms_limit_w is None


def test_bms_stop_ends_after_charging_or_rising_soc() -> None:
    monitor = DeliveryMonitor("test")
    for i in range(FAIL_THRESHOLD):
        monitor.observe(40 + 5 * i, -2500, 0, 0, 12.4)
    assert not monitor.update_stop(13.0, 0)
    assert monitor.bms_limit_w is not None
    assert monitor.update_stop(13.0, 500)  # charging
    assert monitor.bms_limit_w is None and monitor.bms_stop_soc is None

    for i in range(FAIL_THRESHOLD):
        monitor.observe(100 + 5 * i, -2500, 0, 0, 12.4)
    assert monitor.update_stop(14.4, 0)  # SoC rose by 2 %
    assert monitor.bms_limit_w is None


def test_communication_failures_exclude_without_wake() -> None:
    monitor = DeliveryMonitor("test")
    actions = [monitor.record_comm_failure(t) for t in range(FAIL_THRESHOLD)]
    assert actions[-1] is Action.EXCLUDE


def test_bms_full_below_100_percent_blocks_charging_without_failure() -> None:
    monitor = DeliveryMonitor("test")
    actions = [monitor.observe(40 + 5 * i, 200, 0, 0, 88, 3.58) for i in range(FAIL_THRESHOLD)]
    assert actions[-1] is Action.BMS_FULL
    assert all(action is Action.NONE for action in actions[:-1])
    assert monitor.bms_full and monitor.bms_full_soc == 88
    assert monitor.fail_count == 0 and not monitor.excluded(100)
    # Below the top of the charge it is a failure as before.
    other = DeliveryMonitor("other")
    for i in range(FAIL_THRESHOLD):
        other.observe(40 + 5 * i, 200, 0, 0, 88, 3.40)
    assert not other.bms_full and other.fail_count == 0 and other._wake_used


def test_bms_full_despite_charging_and_standby_alternating() -> None:
    monitor = DeliveryMonitor("test")
    t = 40.0
    action = Action.NONE
    for delivered in (0, 150, 0, 160, 0):
        action = monitor.observe(t, 200, 0, delivered, 88, 3.58)
        t += 5
    assert action is Action.BMS_FULL
    # Charging polls in a row count the battery again.
    fresh = DeliveryMonitor("fresh")
    for delivered in (0, 0, 150, 150, 150, 0, 0):
        assert fresh.observe(t, 200, 0, delivered, 88, 3.58) is Action.NONE
        t += 5
    assert not fresh.bms_full


def test_bms_full_without_cell_voltages_needs_a_high_soc() -> None:
    monitor = DeliveryMonitor("test")
    for i in range(FAIL_THRESHOLD):
        monitor.observe(40 + 5 * i, 200, 0, 0, 98.5)
    assert monitor.bms_full
    assert not monitor.update_full(97.0, 0, None)
    assert monitor.update_full(96.5, 0, None)  # 2 % below the SoC at the stop
    assert not monitor.bms_full


def test_bms_full_ends_after_discharging_or_leaving_the_top() -> None:
    monitor = DeliveryMonitor("test")
    for i in range(FAIL_THRESHOLD):
        monitor.observe(40 + 5 * i, 200, 0, 0, 88, 3.58)
    assert not monitor.update_full(88, 0, 3.50)
    assert monitor.update_full(88, -300, 3.50)
    for i in range(FAIL_THRESHOLD):
        monitor.observe(100 + 5 * i, 200, 0, 0, 88, 3.58)
    assert monitor.update_full(87, 0, 3.38)


def test_bms_full_blocks_the_charge_power() -> None:
    limits = power_limits(
        2500, 2500, BatteryLimitSettings(), SocWindow(), None, TemperatureLimit(), bms_full=True
    )
    assert (limits.charge_w, limits.charge_reason) == (0.0, "bms")
    assert limits.discharge_w == 2500
    # The plans ahead ignore it (like the SoC window).
    ahead = power_limits(
        2500, 2500, BatteryLimitSettings(), SocWindow(), None, TemperatureLimit(),
        use_soc_window=False, bms_full=True,
    )
    assert ahead.charge_w == 2500
