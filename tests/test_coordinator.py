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


def test_negative_house_power_keeps_the_last_valid_value() -> None:
    from custom_components.slems.coordinator import HousePowerHold

    hold = HousePowerHold()

    def snapshot(grid: float) -> SystemSnapshot:
        return SystemSnapshot(
            grid_power_w=grid,
            pv_power_w=3000,
            batteries={"a": BatteryTelemetry(soc_pct=50, power_w=1000)},
        )

    ok = snapshot(-1500)  # house 500 W
    hold.check(ok, 0)
    assert ok.house_power_w == 500
    late_meter = snapshot(-2500)  # PV rose, the meter has not caught up: -500 W
    hold.check(late_meter, 5)
    assert late_meter.raw_house_power_w == -500
    assert late_meter.house_power_w == 500
    stale = snapshot(-2500)
    hold.check(stale, 5 + HousePowerHold.HOLD_S)
    assert stale.house_power_w is None  # unknown


def test_planning_powers_of_a_full_or_blocked_battery() -> None:
    from types import SimpleNamespace

    from custom_components.slems.battery_limits import PowerLimits
    from custom_components.slems.coordinator import SlemsCoordinator

    def battery(battery_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            subentry_id=battery_id,
            plannable=True,
            capacity_wh=5000.0,
            driver=SimpleNamespace(capabilities=SimpleNamespace(controllable=True)),
            limits=SimpleNamespace(min_soc_pct=10.0, max_soc_pct=100.0),
            efficiency=SimpleNamespace(one_way=0.95),
            delivery=SimpleNamespace(bms_full=False),
            # Right now: the full one takes nothing, the other is blocked by its SoC window.
            power_limits=PowerLimits(charge_w=0.0, discharge_w=2500.0),
        )

    coordinator = SlemsCoordinator.__new__(SlemsCoordinator)
    coordinator.batteries = [battery("full"), battery("blocked")]
    coordinator.full_charge_battery = None
    coordinator._power_limits = lambda battery, telemetry, use_soc_window=True: (
        PowerLimits(charge_w=0.0, discharge_w=2500.0) if use_soc_window else PowerLimits(2500.0, 2500.0)
    )
    snapshot = SimpleNamespace(
        batteries={"full": SimpleNamespace(soc_pct=100.0), "blocked": SimpleNamespace(soc_pct=95.0)}
    )
    now = coordinator._battery_group(snapshot)
    planning = coordinator._battery_group(snapshot, planning=True)
    assert now.max_charge_w == 0.0
    assert planning.max_charge_w == 5000.0 and planning.max_discharge_w == 5000.0
    assert planning.soc_pct == now.soc_pct and planning.all_full == now.all_full


def test_grid_target_samples_the_target_aimed_at() -> None:
    from types import SimpleNamespace

    from custom_components.slems.allocation import Allocation, Strategy
    from custom_components.slems.const import OperatingMode
    from custom_components.slems.controller import ControlStatus
    from custom_components.slems.coordinator import ControlSettings, SlemsCoordinator
    from custom_components.slems.learning import GridTargetLearner

    coordinator = SlemsCoordinator.__new__(SlemsCoordinator)
    coordinator.grid_targets = GridTargetLearner()
    coordinator.controller = SimpleNamespace(status=ControlStatus.ACTIVE)
    settings = ControlSettings(operating_mode=OperatingMode.ACTIVE, discharge_grid_target_w=50.0)
    battery = SimpleNamespace(max_charge_w=2500.0, max_discharge_w=2500.0)
    snapshot = SimpleNamespace(grid_power_w=0.0)

    # Discharging with the maximum grid export at 0 W: the batteries aim at
    # 0 W, the grid at 0 W is no deviation.
    discharge = Allocation(strategy=Strategy.SELF_CONSUMPTION, battery_power_w=-500.0)
    coordinator._learn_grid_target(snapshot, discharge, battery, settings, 0.0)
    assert list(coordinator.grid_targets.discharge) == [0.0]
    coordinator._learn_grid_target(snapshot, discharge, battery, settings, 5000.0)
    assert list(coordinator.grid_targets.discharge) == [0.0, 50.0]

    # Grid friendly charging holds the batteries back on purpose.
    charge = Allocation(strategy=Strategy.GRID_FRIENDLY, battery_power_w=500.0)
    coordinator._learn_grid_target(snapshot, charge, battery, settings, 5000.0)
    assert not coordinator.grid_targets.charge
