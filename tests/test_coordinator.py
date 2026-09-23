"""Tests for the energy balance in the system snapshot."""

from custom_components.slems.const import ConsumerType, ControlMode
from custom_components.slems.consumers import ConsumerConfig, ConsumerState
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


def _consumer(subentry_id: str, included: bool) -> ConsumerConfig:
    return ConsumerConfig(
        subentry_id=subentry_id,
        name=subentry_id,
        consumer_type=ConsumerType.OTHER,
        power_entity_id="sensor.power",
        energy_entity_id="sensor.energy",
        included_in_meter=included,
        control_mode=ControlMode.NONE,
        control_entity_id=None,
        nominal_power_w=None,
        min_power_w=None,
        max_power_w=None,
        block_entity_id=None,
        priority=5,
    )


def test_consumers_inside_and_outside_meter() -> None:
    snapshot = SystemSnapshot(
        grid_power_w=2500,
        pv_power_w=0,
        consumer_configs={
            "heat_pump": _consumer("heat_pump", included=True),
            "garage": _consumer("garage", included=False),
        },
        consumers={
            "heat_pump": ConsumerState(power_w=1800),
            "garage": ConsumerState(power_w=400),
        },
    )
    assert snapshot.house_power_w == 2500
    assert snapshot.base_load_w == 700
    assert snapshot.total_consumption_w == 2900
