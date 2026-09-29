"""Tests for the operating limits of a battery."""

import pytest

from custom_components.slems.battery_limits import (
    BatteryLimitSettings,
    SocWindow,
    TemperatureLimit,
    power_limits,
    temperature_factor,
)

ON = TemperatureLimit(enabled=True)


def test_soc_window_with_reentry_margin() -> None:
    settings = BatteryLimitSettings(min_soc_pct=12, max_soc_pct=90)
    window = SocWindow()
    window.update(12.0, settings)
    assert window.discharge_blocked
    window.update(13.5, settings)  # rebound after the load
    assert window.discharge_blocked
    window.update(14.0, settings)
    assert not window.discharge_blocked
    window.update(90.0, settings)
    assert window.charge_blocked
    window.update(88.5, settings)
    assert window.charge_blocked
    window.update(88.0, settings)
    assert not window.charge_blocked


def test_full_charge_is_left_to_the_bms() -> None:
    window = SocWindow()
    window.update(100.0, BatteryLimitSettings(max_soc_pct=100))
    assert not window.charge_blocked


def test_temperature_factor() -> None:
    assert temperature_factor(None, ON) == 1.0
    assert temperature_factor(60, TemperatureLimit()) == 1.0  # disabled
    assert temperature_factor(-1, ON) == 0.0
    assert temperature_factor(2.5, ON) == pytest.approx(0.5)
    assert temperature_factor(25, ON) == 1.0
    assert temperature_factor(45, ON) == pytest.approx(0.7)
    assert temperature_factor(55, ON) == pytest.approx(0.4)


def test_power_limits_combine() -> None:
    settings = BatteryLimitSettings(charge_limit_w=800, discharge_limit_w=800)
    window = SocWindow()
    limits = power_limits(2500, 2500, settings, window, 45, ON)
    assert limits.charge_w == pytest.approx(560) and limits.charge_reason == "temperature"
    assert limits.discharge_w == 800 and limits.discharge_reason == "power"
    window.discharge_blocked = True
    limits = power_limits(2500, 2500, settings, window, 20, ON)
    assert limits.discharge_w == 0 and limits.discharge_reason == "soc"
    # Cell balancing ignores the SoC window.
    limits = power_limits(2500, 2500, settings, window, 20, ON, use_soc_window=False)
    assert limits.discharge_w == 800


def test_full_charge_may_exceed_the_maximum_soc() -> None:
    settings = BatteryLimitSettings(max_soc_pct=90)
    window = SocWindow()
    window.update(92.0, settings)
    assert power_limits(2500, 2500, settings, window, None, TemperatureLimit()).charge_w == 0
    due = power_limits(2500, 2500, settings, window, None, TemperatureLimit(), full_charge=True)
    assert due.charge_w == 2500
