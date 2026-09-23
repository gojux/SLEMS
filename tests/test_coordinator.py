"""Tests for the energy balance in the system snapshot."""

from custom_components.slems.coordinator import SystemSnapshot
from custom_components.slems.drivers.base import BatteryTelemetry


def test_house_power_balance() -> None:
    snapshot = SystemSnapshot(
        grid_power_w=-500,  # exporting 500 W
        pv_power_w=3000,
        batteries={
            "a": BatteryTelemetry(soc_pct=50, power_w=1500),  # charging
            "b": BatteryTelemetry(soc_pct=80, power_w=None),
        },
    )
    assert snapshot.battery_power_w == 1500
    # 3000 W PV = 500 W export + 1500 W battery + 1000 W house
    assert snapshot.house_power_w == 1000


def test_house_power_unknown_without_grid() -> None:
    assert SystemSnapshot(pv_power_w=1000).house_power_w is None
