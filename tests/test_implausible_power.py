"""Tests for rejecting battery powers that cannot be real."""

from custom_components.slems.drivers.base import (
    BatteryCapabilities,
    BatteryTelemetry,
    implausible_battery_power,
)

VENUS = BatteryCapabilities(
    capacity_wh=5120, max_charge_power_w=2500, max_discharge_power_w=2500, controllable=True
)


def test_real_powers_are_plausible() -> None:
    assert not implausible_battery_power(BatteryTelemetry(power_w=-2500), VENUS)
    assert not implausible_battery_power(BatteryTelemetry(power_w=None), VENUS)
    # Configured below what the battery can do: still plausible.
    small = BatteryCapabilities(
        capacity_wh=5120, max_charge_power_w=800, max_discharge_power_w=800, controllable=True
    )
    assert not implausible_battery_power(BatteryTelemetry(power_w=2500), small)


def test_faulty_readings_are_implausible() -> None:
    assert implausible_battery_power(BatteryTelemetry(power_w=65535), VENUS)
    assert implausible_battery_power(BatteryTelemetry(power_w=-4_294_967_295), VENUS)
    assert implausible_battery_power(BatteryTelemetry(power_w=float("nan")), VENUS)
    assert implausible_battery_power(BatteryTelemetry(power_w=0, output_w=float("inf")), VENUS)
