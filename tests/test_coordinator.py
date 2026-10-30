"""Tests for the energy balance in the system snapshot."""

from types import SimpleNamespace

import pytest

from custom_components.slems.const import ConsumerType, ControlMode, ReleaseState
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


def _unread_battery(
    battery_id: str,
    *,
    enabled: bool = True,
    controllable: bool = True,
    release_state: ReleaseState = ReleaseState.STANDBY,
) -> SimpleNamespace:
    return SimpleNamespace(
        subentry_id=battery_id,
        enabled=enabled,
        driver=SimpleNamespace(
            capabilities=SimpleNamespace(controllable=controllable), release_state=release_state
        ),
        measured_power_w=-800.0,
        measured_at=100.0,
        measured_pv_w=None,
        leaving_until=None,
        release_pending_since=None,
    )


def _unread_coordinator(*batteries: SimpleNamespace) -> SimpleNamespace:
    from custom_components.slems.coordinator import SlemsCoordinator

    coordinator = SimpleNamespace(batteries=list(batteries), controls=lambda _battery: False)
    coordinator.disabled_and_released = lambda b: SlemsCoordinator.disabled_and_released(coordinator, b)
    return coordinator


def test_unread_battery_keeps_its_last_power_then_house_power_unknown() -> None:
    from custom_components.slems.coordinator import UNREAD_BATTERY_HOLD_S, SlemsCoordinator

    coordinator = _unread_coordinator(
        _unread_battery("a"),
        _unread_battery("b"),
        _unread_battery("off", enabled=False),
        _unread_battery("ro", controllable=False),
    )
    snapshot = SystemSnapshot(
        grid_power_w=200,
        pv_power_w=0,
        batteries={"a": BatteryTelemetry(soc_pct=50, power_w=-500)},
    )
    unread = SlemsCoordinator._unread_battery_power_w
    snapshot.unread_battery_power_w = unread(coordinator, snapshot, 100 + UNREAD_BATTERY_HOLD_S)
    # b still discharges with its last 800 W; a released disabled battery and
    # read-only batteries do not count.
    assert snapshot.unread_battery_power_w == -800
    assert snapshot.battery_power_w == -1300
    assert snapshot.house_power_w == 1500
    snapshot.unread_battery_power_w = unread(coordinator, snapshot, 101 + UNREAD_BATTERY_HOLD_S)
    assert snapshot.battery_power_w is None
    assert snapshot.house_power_w is None
    assert snapshot.base_load_w is None
    assert snapshot.total_consumption_w is None
    # Never read since the start: unknown at once.
    coordinator.batteries[1].measured_at = None
    assert unread(coordinator, snapshot, 100) is None


def test_unread_disabled_battery_in_standby_counts_zero() -> None:
    from custom_components.slems.coordinator import UNREAD_BATTERY_HOLD_S, SlemsCoordinator

    off = _unread_battery("off", enabled=False)
    coordinator = _unread_coordinator(off)
    snapshot = SystemSnapshot(grid_power_w=200, pv_power_w=0)
    # Released in standby it stands at 0 W, also right after its last reading.
    for now in (110.0, 1000 + UNREAD_BATTERY_HOLD_S):
        snapshot.unread_battery_power_w = SlemsCoordinator._unread_battery_power_w(coordinator, snapshot, now)
        assert snapshot.unread_battery_power_w == 0
        assert snapshot.house_power_w == 200
    assert not SlemsCoordinator.unread_power_unknown(coordinator, off)


def test_unread_disabled_battery_keeps_its_power_until_released() -> None:
    from custom_components.slems.coordinator import UNREAD_BATTERY_HOLD_S, SlemsCoordinator

    off = _unread_battery("off", enabled=False, release_state=ReleaseState.AUTO)
    off.release_pending_since = 150.0
    coordinator = _unread_coordinator(off)
    snapshot = SystemSnapshot(grid_power_w=200, pv_power_w=0)
    unread = SlemsCoordinator._unread_battery_power_w
    # It may still discharge with its last set point, also after the hold time
    # of an enabled battery; the house power stays known.
    for now in (110.0, 1000 + UNREAD_BATTERY_HOLD_S):
        snapshot.unread_battery_power_w = unread(coordinator, snapshot, now)
        assert snapshot.unread_battery_power_w == -800
        assert snapshot.house_power_w == 1000
    assert not SlemsCoordinator.unread_power_unknown(coordinator, off)
    # Never read since the start: nothing to keep, but never unknown.
    off.measured_at = None
    off.measured_power_w = None
    assert unread(coordinator, snapshot, 100) == 0
    # Still ramping out (not released yet): the same.
    off.release_pending_since = None
    off.leaving_until = 120.0
    off.measured_power_w = -300.0
    assert unread(coordinator, snapshot, 110) == -300
    # Released: 0 W.
    off.leaving_until = None
    assert unread(coordinator, snapshot, 110) == 0


def test_unread_disabled_battery_on_its_own_logic_counts_zero_with_hint() -> None:
    from custom_components.slems.coordinator import UNREAD_BATTERY_HOLD_S, SlemsCoordinator

    off = _unread_battery("off", enabled=False, release_state=ReleaseState.AUTO)
    coordinator = _unread_coordinator(off)
    snapshot = SystemSnapshot(grid_power_w=200, pv_power_w=0)
    unread = SlemsCoordinator._unread_battery_power_w
    # A short gap: it likely goes on like before.
    snapshot.unread_battery_power_w = unread(coordinator, snapshot, 100 + UNREAD_BATTERY_HOLD_S)
    assert snapshot.unread_battery_power_w == -800
    assert snapshot.house_power_w == 1000
    # Afterwards 0 W, but never unknown.
    snapshot.unread_battery_power_w = unread(coordinator, snapshot, 101 + UNREAD_BATTERY_HOLD_S)
    assert snapshot.unread_battery_power_w == 0
    assert snapshot.house_power_w == 200
    off.measured_at = None
    assert unread(coordinator, snapshot, 100) == 0
    off.measured_at = 100.0
    # It regulates on its own: its power is unknown (repair issue, see test_problems).
    assert SlemsCoordinator.unread_power_unknown(coordinator, off)
    off.release_pending_since = 150.0
    assert not SlemsCoordinator.unread_power_unknown(coordinator, off)
    off.release_pending_since = None
    off.enabled = True
    assert not SlemsCoordinator.unread_power_unknown(coordinator, off)


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
    coordinator._power_limits = lambda battery, telemetry, use_soc_window=True, plan=False: (
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


def test_run_costs_follow_the_outdoor_temperature_and_the_surplus() -> None:
    from datetime import datetime, timedelta
    from types import SimpleNamespace

    import pytest
    from homeassistant.util import dt as dt_util

    from custom_components.slems.coordinator import SlemsCoordinator
    from custom_components.slems.learning import OutdoorPowerLearner
    from custom_components.slems.tariff import Side

    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Vienna"))
    now = datetime(2026, 10, 10, 10, 0, tzinfo=dt_util.get_default_time_zone())
    end = now + timedelta(hours=6)
    quarters = [now + timedelta(minutes=15 * i) for i in range(24)]
    # Air source heat pump: 3000 W at −5 °C, 2000 W at 15 °C.
    learner = OutdoorPowerLearner(runs=[(t, 2750.0 - 50 * t) for t in (-5, 0, 5, 10, 15, 15)])

    def prices(wall_now, until, side=Side.IMPORT, **_):
        return {q: 30.0 if side is Side.IMPORT else 8.0 for q in quarters}

    fake = SimpleNamespace(
        _import_prices=prices,
        outdoor_power={"hp": learner},
        forecaster=SimpleNamespace(
            forecast_temperatures={now + timedelta(hours=h): (0.0 if h < 3 else 10.0) for h in range(6)}
        ),
    )
    consumer = SimpleNamespace(subentry_id="hp")
    # 15:00: a forecast surplus of 1000 W (consumption below the PV).
    consumption = SimpleNamespace(total={now + timedelta(hours=h): (-1000.0 if h == 5 else 500.0) for h in range(6)})
    costs = SlemsCoordinator._run_prices(fake, consumer, 2500.0, now, end, None, consumption)
    assert costs[now] == pytest.approx(30 * 2750 / 2500)  # cold morning: more power
    assert costs[now + timedelta(hours=3)] == pytest.approx(30 * 2250 / 2500)  # warmer afternoon
    # The surplus covers 1000 W at the feed-in credit.
    assert costs[now + timedelta(hours=5)] == pytest.approx((1250 * 30 + 1000 * 8) / 2500)
    # Without a learned dependency the nominal power counts.
    fake.outdoor_power = {}
    assert SlemsCoordinator._run_prices(fake, consumer, 2500.0, now, end, None, consumption)[now] == pytest.approx(30)


def test_spot_credit_of_a_given_tariff() -> None:
    from types import SimpleNamespace

    from custom_components.slems.coordinator import SlemsCoordinator
    from custom_components.slems.tariff import Group, Role, Side, Tariff, TariffItem, Unit

    def tariff(unit: Unit) -> Tariff:
        return Tariff("T", Role.CURRENT, (TariffItem("Feed-in", Side.EXPORT, Group.ENERGY, unit, 0.0),), {})

    effective = SlemsCoordinator.battery_export_effective
    assert effective(SimpleNamespace(), tariff(Unit.SPOT))
    assert not effective(SimpleNamespace(), tariff(Unit.KWH))


def test_price_plan_in_the_executor_starts_a_control_cycle() -> None:
    import asyncio
    from functools import partial
    from types import MethodType, SimpleNamespace

    from custom_components.slems.coordinator import SlemsCoordinator
    from custom_components.slems.grid_charge import PlanTiming

    requests = []

    async def executor(job):
        return job()

    coordinator = SimpleNamespace(
        hass=SimpleNamespace(async_add_executor_job=executor),
        grid_charge_timing=PlanTiming(),
        _grid_charge_cache={},
        _grid_charge_last=None,
        _grid_charge_task=object(),
        controller=SimpleNamespace(request=lambda: requests.append(True)),
    )
    coordinator._store_grid_charge = MethodType(SlemsCoordinator._store_grid_charge, coordinator)
    plan = object()
    asyncio.run(SlemsCoordinator._async_grid_charge(coordinator, ("key",), partial(lambda: plan), 96))
    assert coordinator._grid_charge_last is plan
    assert coordinator._grid_charge_cache[("key",)][1] is plan
    assert coordinator._grid_charge_task is None
    assert coordinator.grid_charge_timing.plans == 1 and coordinator.grid_charge_timing.periods == 96
    assert requests == [True]


def test_release_leaves_a_paused_battery_alone() -> None:
    import asyncio
    from types import SimpleNamespace

    from custom_components.slems.coordinator import SlemsCoordinator

    released = []

    async def release_control() -> None:
        released.append(True)

    battery = SimpleNamespace(
        name="Venus",
        driver=SimpleNamespace(capabilities=SimpleNamespace(controllable=True), release_control=release_control),
        communication_paused=True,
        release_pending_since=None,
        trial=None,
    )
    coordinator = SlemsCoordinator.__new__(SlemsCoordinator)
    # E.g. switching to "Observe only" while the battery is paused: no connection.
    assert asyncio.run(coordinator.async_release_battery(battery)) is True
    assert released == []
    assert battery.release_pending_since is None
    battery.communication_paused = False
    assert asyncio.run(coordinator.async_release_battery(battery)) is True
    assert released == [True]


def test_battery_with_own_pv_counts_its_modules_as_pv() -> None:
    from custom_components.slems.coordinator import SlemsCoordinator

    coordinator = SimpleNamespace(_pv_power_w=lambda: 2000.0)
    snapshot = SystemSnapshot(
        grid_power_w=-300,
        batteries={
            # 600 W from the modules, 200 W into the house: charging 400 W.
            "plug_in": BatteryTelemetry(soc_pct=50, power_w=400, ac_power_w=400, pv_w=600, output_w=200),
            "venus": BatteryTelemetry(soc_pct=50, power_w=1000),
        },
    )
    SlemsCoordinator._set_pv_power(coordinator, snapshot)
    assert snapshot.pv_power_w == 2600
    assert snapshot.own_pv_power_w == 600
    # house = grid + other PV + output - other batteries
    assert snapshot.house_power_w == -300 + 2000 + 200 - 1000
    assert snapshot.batteries["plug_in"].connection_power_w == -200
    assert snapshot.batteries["venus"].connection_power_w == 1000


def test_pv_unknown_without_pv_systems_and_own_pv() -> None:
    from custom_components.slems.coordinator import SlemsCoordinator

    snapshot = SystemSnapshot(batteries={"venus": BatteryTelemetry(soc_pct=50, power_w=0)})
    SlemsCoordinator._set_pv_power(SimpleNamespace(_pv_power_w=lambda: None), snapshot)
    assert snapshot.pv_power_w is None
    snapshot.batteries["plug_in"] = BatteryTelemetry(soc_pct=50, ac_power_w=0, pv_w=0, output_w=0)
    SlemsCoordinator._set_pv_power(SimpleNamespace(_pv_power_w=lambda: None), snapshot)
    assert snapshot.pv_power_w == 0


def test_unread_battery_with_own_pv_keeps_only_its_output() -> None:
    from custom_components.slems.coordinator import SlemsCoordinator

    battery = _unread_battery("plug_in")
    # Last read: 600 W PV, 200 W output (charging 400 W).
    battery.measured_power_w, battery.measured_pv_w = 400.0, 600.0
    snapshot = SystemSnapshot(grid_power_w=100, pv_power_w=0)
    snapshot.unread_battery_power_w = SlemsCoordinator._unread_battery_power_w(
        _unread_coordinator(battery), snapshot, 100
    )
    # Its PV is missing from the PV sum, so only the output counts.
    assert snapshot.unread_battery_power_w == -200
    assert snapshot.house_power_w == 300


def _plug_in(subentry_id: str, *, passes_pv_through: bool = False, controls_output: bool = True) -> SimpleNamespace:
    from custom_components.slems.battery_limits import PowerLimits
    from custom_components.slems.drivers.base import BatteryCapabilities

    states = []
    driver = SimpleNamespace(
        capabilities=BatteryCapabilities(
            capacity_wh=2240,
            max_charge_power_w=1800,
            max_discharge_power_w=800,
            controllable=True,
            own_pv=True,
            ac_charge=False,
            max_output_w=800,
            passes_pv_through=passes_pv_through,
            controls_output=controls_output,
        ),
        set_own_pv_state=lambda **kw: states.append(kw),
        own_pv_available_w=lambda pv_w: pv_w,
        states=states,
    )
    return SimpleNamespace(
        subentry_id=subentry_id,
        participating=True,
        driver=driver,
        delivery=SimpleNamespace(bms_full=False),
        power_limits=PowerLimits(600, 200),
    )


def test_batteries_with_own_pv_curtail_for_the_feed_in_cap() -> None:
    from custom_components.slems.allocation import Allocation, CapControl, Strategy
    from custom_components.slems.battery_distribution import Distribution
    from custom_components.slems.coordinator import SlemsCoordinator
    from custom_components.slems.own_pv import CurtailControl

    first, full = _plug_in("first"), _plug_in("full", passes_pv_through=True)
    coordinator = SimpleNamespace(
        batteries=[first, full],
        _curtail=CurtailControl(),
        _own_pv_at_top=SlemsCoordinator._own_pv_at_top,
    )
    snapshot = SystemSnapshot(
        grid_power_filtered_w=-1100,
        batteries={
            "first": BatteryTelemetry(soc_pct=60, ac_power_w=200, pv_w=600, output_w=400),
            # Full and passing its PV through: it cannot curtail.
            "full": BatteryTelemetry(soc_pct=100, ac_power_w=0, pv_w=500, output_w=500),
        },
    )
    snapshot.distribution = Distribution({"first": 200.0, "full": 0.0}, ())
    allocation = Allocation(Strategy.FEED_IN_CAP, cap_excess_w=500)
    cap = CapControl(limit_w=700, margin_w=100)
    SlemsCoordinator._own_pv_control(coordinator, snapshot, allocation, cap, controller_cycle=True)
    # A fifth of the export above 600 W, within the 400 W the first one can curtail.
    assert snapshot.own_pv_curtail_w == pytest.approx(100)
    assert first.driver.states[-1] == {"at_top": False, "curtail_w": pytest.approx(100)}
    assert full.driver.states[-1] == {"at_top": True, "curtail_w": 0}
    # A slow update keeps the curtailment of the controller.
    SlemsCoordinator._own_pv_control(coordinator, snapshot, Allocation(Strategy.IDLE), None, controller_cycle=False)
    assert snapshot.own_pv_curtail_w == pytest.approx(100)


def test_a_hybrid_inverter_controlled_at_its_battery_does_not_curtail() -> None:
    from custom_components.slems.allocation import Allocation, CapControl, Strategy
    from custom_components.slems.battery_distribution import Distribution
    from custom_components.slems.coordinator import SlemsCoordinator
    from custom_components.slems.own_pv import CurtailControl

    hybrid = _plug_in("hybrid", passes_pv_through=True, controls_output=False)
    coordinator = SimpleNamespace(
        batteries=[hybrid],
        _curtail=CurtailControl(),
        _own_pv_at_top=SlemsCoordinator._own_pv_at_top,
    )
    snapshot = SystemSnapshot(
        grid_power_filtered_w=-1100,
        batteries={"hybrid": BatteryTelemetry(soc_pct=60, ac_power_w=200, pv_w=600, output_w=400)},
    )
    snapshot.distribution = Distribution({"hybrid": 200.0}, ())
    allocation = Allocation(Strategy.FEED_IN_CAP, cap_excess_w=500)
    SlemsCoordinator._own_pv_control(
        coordinator, snapshot, allocation, CapControl(limit_w=700, margin_w=100), controller_cycle=True
    )
    # Its set point is the battery: the PV is not counted as curtailable.
    assert snapshot.own_pv_curtail_w == 0
    assert hybrid.driver.states[-1] == {"at_top": False, "curtail_w": 0}


def test_delivery_counts_only_what_the_pv_read_allows() -> None:
    from custom_components.slems.coordinator import SlemsCoordinator
    from custom_components.slems.const import OperatingMode

    battery = _plug_in("plug_in")
    observed = []
    battery.delivery = SimpleNamespace(
        tick=lambda now: None,
        observe=lambda now, commanded, *args: observed.append(commanded),
    )
    battery.leaving_until = None
    coordinator = SimpleNamespace(
        settings=SimpleNamespace(operating_mode=OperatingMode.ACTIVE),
        controller=SimpleNamespace(command_state=lambda battery_id: (500.0, 0.0)),
    )
    # A cloud: 500 W commanded, only 120 W of PV left.
    telemetry = BatteryTelemetry(soc_pct=50, ac_power_w=120, pv_w=120, output_w=0)
    SlemsCoordinator._check_delivery(coordinator, battery, telemetry, 100.0)
    assert observed == [120]


def test_pv_correction_counts_own_pv_only_with_a_forecast() -> None:
    from custom_components.slems.coordinator import SlemsCoordinator

    coordinator = SimpleNamespace(
        batteries=[
            SimpleNamespace(subentry_id="with", own_pv_forecast_entry="forecast"),
            SimpleNamespace(subentry_id="without", own_pv_forecast_entry=None),
        ]
    )
    snapshot = SystemSnapshot(
        pv_power_w=3000,
        batteries={
            "with": BatteryTelemetry(soc_pct=50, ac_power_w=0, pv_w=400, output_w=400),
            "without": BatteryTelemetry(soc_pct=50, ac_power_w=0, pv_w=600, output_w=600),
        },
    )
    assert SlemsCoordinator._forecast_pv_power_w(coordinator, snapshot) == 2400


def _probe_coordinator(battery):
    from types import SimpleNamespace

    from custom_components.slems.const import OperatingMode
    from custom_components.slems.controller import ControlStatus
    from custom_components.slems.coordinator import SlemsCoordinator
    from custom_components.slems.response import BatteryResponses
    from custom_components.slems.response_probe import QuietWatch

    coordinator = SlemsCoordinator.__new__(SlemsCoordinator)
    coordinator.batteries = [battery]
    coordinator.settings = SimpleNamespace(operating_mode=OperatingMode.ACTIVE, feed_in_cap=False)
    coordinator.controller = SimpleNamespace(
        status=ControlStatus.ACTIVE,
        battery_responses=BatteryResponses(),
        latest_command=lambda _id: 800.0,
        last_battery_change=lambda: None,
        last_consumer_command=lambda: None,
        request=lambda: None,
        meter=SimpleNamespace(interval_s=0.48),
    )
    coordinator._probe_quiet = QuietWatch()
    coordinator._store = SimpleNamespace(async_delay_save=lambda *_args: None)
    coordinator.phase_model = SimpleNamespace(devices=())
    return coordinator


def _probe_battery():
    from types import SimpleNamespace

    from custom_components.slems.battery_limits import PowerLimits
    from custom_components.slems.response_probe import ProbeHints, ProbeRecord

    return SimpleNamespace(
        subentry_id="a",
        name="Venus",
        driver=SimpleNamespace(capabilities=SimpleNamespace(controllable=True), min_command_interval_s=0.0),
        plannable=True,
        leaving_until=None,
        self_test=None,
        balancing_requested=False,
        delivery=SimpleNamespace(bms_full=False, bms_stop_soc=None),
        power_limits=PowerLimits(2500.0, 2500.0),
        probe=None,
        probe_record=ProbeRecord(),
        probe_hints=ProbeHints(),
    )


def test_probe_step_starts_when_due_and_begins_once_the_grid_is_quiet() -> None:
    from custom_components.slems.response_probe import Stage

    battery = _probe_battery()
    coordinator = _probe_coordinator(battery)
    coordinator._update_probes(0.0)
    assert battery.probe is None
    battery.probe_record.check({"config": {"key": "a"}})
    coordinator._update_probes(0.0)
    assert battery.probe is not None and battery.probe.reason == "new"
    # Not quiet yet: nothing held.
    coordinator._advance_probe(battery, 1.0, -900.0)
    assert battery.probe.stage is Stage.WAIT and not coordinator.measurement_holds
    for k in range(25):
        coordinator._probe_quiet.add(k * 0.5, -900.0)
    coordinator._advance_probe(battery, 12.0, -900.0)
    # Surplus: 500 W more charging, the others held.
    assert battery.probe.stage is Stage.SETTLE and battery.probe.offset_w == 500.0
    assert coordinator.measurement_holds
    # Switched to simulation meanwhile: stopped, tried again after an hour.
    from custom_components.slems.const import OperatingMode

    coordinator.settings.operating_mode = OperatingMode.SIMULATION
    coordinator._advance_probe(battery, 12.5, None)
    assert battery.probe is None and battery.probe_record.failed == "not_active"
    assert battery.probe_record.due(battery.probe_record.tried_at + 10) is None


def test_finished_probe_step_feeds_the_learner_and_moved_too_early_asks_again() -> None:
    from custom_components.slems.response import OUTCOMES_KEPT
    from custom_components.slems.response_probe import ResponseProbe, Stage

    battery = _probe_battery()
    coordinator = _probe_coordinator(battery)
    probe = battery.probe = ResponseProbe("manual", 0.0)
    probe.measurements = [
        {"change_w": 500, "dead_s": 0.48, "arrived_s": 0.96},
        {"change_w": -500, "dead_s": 0.5, "arrived_s": 1.0},
        {"change_w": 500, "reason": "jump"},
    ]
    probe.result = "ok"
    probe.stage = Stage.DONE
    coordinator._advance_probe(battery, 50.0, None)
    assert battery.probe is None
    assert battery.probe_record.dead_time_s == 0.49
    assert coordinator.probe_dead_time_s("a") == 0.49
    assert coordinator.controller.battery_responses.learned("a") == 0.98
    assert battery.probe_record.arrived_s == 0.98 and battery.probe_record.meter_interval_s == 0.48
    # More than half of the last measurements dropped as too early: due again.
    responses = coordinator.controller.battery_responses
    responses._outcomes["a"].extend(["learned"] * (OUTCOMES_KEPT // 2 - 1) + ["early"] * (OUTCOMES_KEPT // 2 + 1))
    coordinator._update_probes(60.0)
    assert battery.probe is not None and battery.probe.reason == "early"
    assert responses.early_share("a") is None


def test_changed_firmware_drops_the_learned_response_times() -> None:
    from types import MethodType, SimpleNamespace

    from custom_components.slems.const import CONF_GRID_POWER_ENTITY

    battery = _probe_battery()
    battery.config_key = "k"
    battery.control_entity = None
    battery.device_info = {"ems_version": "148"}
    coordinator = _probe_coordinator(battery)
    coordinator.config_entry = SimpleNamespace(options={CONF_GRID_POWER_ENTITY: "sensor.grid"}, data={})
    coordinator._entity_versions = MethodType(lambda _self, _entity: {"sw_version": "1.0"}, coordinator)
    responses = coordinator.controller.battery_responses
    responses.add("step", "a", 0.9)
    coordinator._check_probe_fingerprint(battery)
    # The first fingerprint: a probe step is due, the learned times stay.
    assert battery.probe_record.requested == "new" and responses.learned("a") == 0.9
    battery.probe_record.requested = None
    coordinator._check_probe_fingerprint(battery)
    assert battery.probe_record.requested is None
    battery.device_info = {"ems_version": "150"}
    coordinator._check_probe_fingerprint(battery)
    assert battery.probe_record.requested == "firmware" and responses.learned("a") is None
