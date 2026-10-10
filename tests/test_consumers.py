"""Tests for consumer state and the resting of consumers with a cycling thermostat."""

import time
from dataclasses import replace
from types import SimpleNamespace

from custom_components.slems.const import ConsumerType, ControlMode
from custom_components.slems.consumers import ConsumerConfig, ConsumerState, read_consumer_state
from custom_components.slems.controller import RealTimeController


def consumer(block_entity: str | None = None, cycles: bool = False) -> ConsumerConfig:
    return ConsumerConfig(
        subentry_id="rod",
        name="Heating rod",
        consumer_type=ConsumerType.HEATING_ROD,
        power_entity_id="sensor.rod_power",
        energy_entity_id="sensor.rod_energy",
        included_in_meter=True,
        control_mode=ControlMode.POWER,
        control_entity_id="number.rod_power",
        nominal_power_w=None,
        min_power_w=0,
        max_power_w=3000,
        block_entity_id=block_entity,
        priority=1,
        thermostat_cycles=cycles,
    )


def hass_with(states: dict[str, str]):
    return SimpleNamespace(
        states=SimpleNamespace(
            get=lambda entity_id: (
                SimpleNamespace(state=states[entity_id], attributes={})
                if entity_id in states
                else None
            )
        )
    )


def test_water_heater_is_blocked_in_mode_off() -> None:
    config = consumer("water_heater.rod")
    assert read_consumer_state(hass_with({"water_heater.rod": "off"}), config).blocked
    assert not read_consumer_state(hass_with({"water_heater.rod": "electric"}), config).blocked
    switch = consumer("switch.rod_lock")
    assert read_consumer_state(hass_with({"switch.rod_lock": "on"}), switch).blocked


def test_resting_instead_of_saturation() -> None:
    controller = RealTimeController.__new__(RealTimeController)
    controller._consumer_commands = {"rod": (2000.0, 0.0)}
    controller._low_since = {}
    controller._resting = set()
    controller._saturated_until = {}
    controller.consumer_response = {}

    def snapshot(power_w: float):
        return SimpleNamespace(consumers={"rod": ConsumerState(power_w=power_w)})

    controller._check_resting("rod", snapshot(0), 100.0)
    assert "rod" not in controller.resting  # just stopped
    controller._check_resting("rod", snapshot(0), 131.0)  # > 2 x default response time
    assert "rod" in controller.resting
    assert not controller.saturated
    controller._check_resting("rod", snapshot(1900), 140.0)
    assert "rod" not in controller.resting


def test_resting_consumer_is_seen_at_its_command() -> None:
    controller = RealTimeController.__new__(RealTimeController)
    controller._consumer_commands = {"rod": (2000.0, 0.0)}
    controller._consumer_before = {"rod": 2000.0}
    controller._resting = set()
    controller.consumer_response = {}
    controller.consumer_grid_response = {}
    controller.battery_response = SimpleNamespace(value=5.0)
    assert controller.consumer_power_seen("rod", 0.0, 200.0) == 0.0
    # Resting: a restart the grid meter shows before the rod's own sensor
    # is no new house load.
    controller._resting = {"rod"}
    assert controller.consumer_power_seen("rod", 0.0, 200.0) == 2000.0


def test_command_kept_for_the_learners_while_saturated() -> None:
    controller = RealTimeController.__new__(RealTimeController)
    controller._consumer_commands = {"rod": (2000.0, 0.0)}
    controller._device_commands = {"rod": 2000.0}
    controller._saturated_until = {}
    controller.consumer_response = {}
    # The thermostat switched off: saturated, the next command is planned anew,
    # but the set point stays on the device.
    snapshot = SimpleNamespace(consumers={"rod": ConsumerState(power_w=0.0)})
    controller._check_saturation("rod", snapshot, time.monotonic())
    assert "rod" in controller.saturated
    assert "rod" not in controller._consumer_commands
    assert controller.consumer_command("rod") == 2000.0


def test_no_saturation_while_the_device_starts() -> None:
    from custom_components.slems.response import DirectionalResponse

    controller = RealTimeController.__new__(RealTimeController)
    controller._saturated_until = {}
    learner = DirectionalResponse(10.0, 100)
    learner.on.response_s = 120.0  # learned start delay of 2 minutes
    learner.off.response_s = 3.0
    controller.consumer_response = {"rod": learner}
    now = time.monotonic()
    snapshot = SimpleNamespace(consumers={"rod": ConsumerState(power_w=0.0)})
    # 90 s after switching on it still starts: not saturated.
    controller._consumer_commands = {"rod": (300.0, now - 90)}
    controller._check_saturation("rod", snapshot, now)
    assert "rod" not in controller.saturated
    # After 1.5 x the start delay it counts as saturated.
    controller._consumer_commands = {"rod": (300.0, now - 190)}
    controller._check_saturation("rod", snapshot, now)
    assert "rod" in controller.saturated


def test_avoid_cycling_starts_only_with_lasting_surplus_and_bridges_dips() -> None:
    from custom_components.slems.consumers import AVOID_CYCLING_BRIDGE_S, cycling_holds

    def surplus(value: float):
        return lambda seconds: value

    # Off: the forecast surplus of the next run decides.
    assert cycling_holds(False, 300, 0.0, None, 500, surplus(250), 1800)[1] is True
    assert cycling_holds(False, 300, 0.0, None, 500, surplus(400), 1800)[1] is False
    # Running with enough surplus: nothing to hold.
    assert cycling_holds(True, 300, 0.0, None, 450, None, 1800) == (False, False, None)
    # A dip: kept on, remembered since when.
    keep_on, _, since = cycling_holds(True, 300, 100.0, None, 120, None, 1800)
    assert keep_on and since == 100.0
    keep_on, _, since = cycling_holds(True, 300, 100.0 + AVOID_CYCLING_BRIDGE_S - 1, since, 120, None, 1800)
    assert keep_on
    # Longer than the bridge: let go.
    keep_on, _, _ = cycling_holds(True, 300, 100.0 + AVOID_CYCLING_BRIDGE_S + 1, since, 120, None, 1800)
    assert not keep_on


def test_saturation_ends_when_the_consumer_draws_again() -> None:
    controller = RealTimeController.__new__(RealTimeController)
    controller._device_commands = {"rod": 2000.0}
    controller._saturated_until = {"rod": time.monotonic() + 900}
    requested = []
    controller.request = lambda: requested.append(True)

    def snapshot(power_w: float):
        return SimpleNamespace(consumers={"rod": ConsumerState(power_w=power_w)})

    controller._check_drawing_again("rod", snapshot(300.0))
    assert "rod" in controller.saturated
    # The thermostat switched on again: controlled again at once.
    controller._check_drawing_again("rod", snapshot(1900.0))
    assert "rod" not in controller.saturated
    assert requested


def test_observed_thermostat_cycling_applies_without_learned_values() -> None:
    from custom_components.slems.coordinator import SlemsCoordinator
    from custom_components.slems.learning import ConsumerLearner

    learner = ConsumerLearner.from_dict({"powers": [2500] * 40, "cycles": 2})
    coordinator = SimpleNamespace(consumer_learners={"rod": learner}, consumer_learning=set())
    effective = SlemsCoordinator.effective_consumer(coordinator, consumer())
    assert effective.thermostat_cycles
    # The learned power is used only with the learned values switched on, and
    # only for planning: the commanded maximum stays as configured.
    assert effective.nominal_power_w is None
    assert SlemsCoordinator._full_power_w(effective) == 3000
    coordinator.consumer_learning = {"rod"}
    effective = SlemsCoordinator.effective_consumer(coordinator, consumer())
    assert effective.max_power_w == 3000
    assert SlemsCoordinator._full_power_w(effective) == 2500


def test_nothing_is_planned_for_a_consumer_with_its_control_off() -> None:
    from datetime import datetime

    from homeassistant.util import dt as dt_util

    from custom_components.slems.consumer_targets import TargetSettings, TargetType
    from custom_components.slems.coordinator import SlemsCoordinator

    coordinator = SimpleNamespace(
        consumers=[consumer()],
        effective_consumer=lambda c: c,
        consumer_targets={"rod": TargetSettings(type=TargetType.TEMPERATURE)},
        target_energy_estimate={"rod": 6000.0},
        consumer_control_disabled={"rod"},
        settings=SimpleNamespace(feed_in_cap=False),
    )
    snapshot = SimpleNamespace(consumption_forecast=None, consumers={})
    SlemsCoordinator._target_states(coordinator, snapshot, None, None, None, datetime(2026, 10, 4, 9, 0, tzinfo=dt_util.UTC))
    assert coordinator.target_states == {}
    assert coordinator.target_load == {}
    assert coordinator.target_demands == []


def test_release_is_published_without_a_service_call() -> None:
    import asyncio

    release = ConsumerConfig(
        subentry_id="boiler",
        name="Boiler",
        consumer_type=ConsumerType.OTHER,
        power_entity_id="sensor.boiler_power",
        energy_entity_id="sensor.boiler_energy",
        included_in_meter=True,
        control_mode=ControlMode.RELEASE,
        control_entity_id=None,
        nominal_power_w=1500,
        min_power_w=None,
        max_power_w=None,
        block_entity_id=None,
        priority=1,
    )
    controller = RealTimeController.__new__(RealTimeController)
    controller._coordinator = SimpleNamespace(consumers=[release], effective_consumer=lambda c: c)
    controller._consumer_commands = {}
    controller._consumer_before = {}
    controller._device_commands = {}
    controller._low_since = {}
    controller._resting = set()
    controller._saturated_until = {}
    controller.consumer_response = {}
    controller.consumer_grid_response = {}
    controller.battery_response = SimpleNamespace(value=5.0)
    controller._last_grid_w = 0.0

    async def no_call(*args, **kwargs):
        raise AssertionError("a release calls no service")

    controller._call = no_call
    controller._hass = None
    snapshot = SimpleNamespace(
        allocation=SimpleNamespace(consumer_power_w={"boiler": 1500.0}),
        consumers={"boiler": ConsumerState()},
        saturated=frozenset(),
        is_controllable_now=lambda subentry_id: True,
    )
    asyncio.run(controller._async_apply_consumers(snapshot))
    assert controller.consumer_command("boiler") == 1500


def test_heat_pump_power_counts_for_the_control_only_in_hot_water_mode() -> None:
    from dataclasses import replace

    heat_pump = replace(
        consumer(),
        consumer_type=ConsumerType.HEAT_PUMP,
        power_entity_id="sensor.hp_power",
        control_mode=ControlMode.RELEASE,
        control_entity_id=None,
        mode_entity_id="sensor.hp_status",
        hot_water_states=("hot_water",),
    )
    assert heat_pump.hot_water_release
    heating = read_consumer_state(hass_with({"sensor.hp_power": "1800", "sensor.hp_status": "heating_mode"}), heat_pump)
    assert heating.power_w == 0
    assert heating.total_power_w == 1800
    hot_water = read_consumer_state(hass_with({"sensor.hp_power": "2400", "sensor.hp_status": "hot_water"}), heat_pump)
    assert hot_water.power_w == hot_water.total_power_w == 2400
    # Without a mode it is not known to heat hot water.
    unknown = read_consumer_state(hass_with({"sensor.hp_power": "2400"}), heat_pump)
    assert unknown.power_w == 0


def test_water_heater_as_storage_temperature() -> None:
    from custom_components.slems.consumers import storage_temperature_c

    def hass(attributes: dict, unit: str = "°C"):
        heater = SimpleNamespace(state="heat_pump", attributes=attributes)
        return SimpleNamespace(
            states=SimpleNamespace(get=lambda entity_id: heater if entity_id == "water_heater.dhw" else None),
            config=SimpleNamespace(units=SimpleNamespace(temperature_unit=unit)),
        )

    assert storage_temperature_c(hass({"current_temperature": 48.5}), "water_heater.dhw") == 48.5
    assert storage_temperature_c(hass({"current_temperature": 68}, "°F"), "water_heater.dhw") == 20
    for attributes in ({}, {"current_temperature": None}, {"current_temperature": float("nan")}):
        assert storage_temperature_c(hass(attributes), "water_heater.dhw") is None
    assert storage_temperature_c(hass({}), "water_heater.other") is None


def test_saturation_ends_when_the_target_is_met_or_waiting() -> None:
    from custom_components.slems.consumer_targets import TargetMode, TargetState
    from custom_components.slems.consumers import RuntimeTracker
    from custom_components.slems.coordinator import SlemsCoordinator

    ended: list[str] = []
    runtime = RuntimeTracker()
    config = replace(consumer(), min_on_s=5400.0)
    runtime.update("rod", True, 0.0)
    coordinator = SimpleNamespace(
        consumers=[config],
        controller=SimpleNamespace(saturated=frozenset({"rod"}), end_saturation=ended.append),
        _runtime=runtime,
        target_states={},
    )
    end = SlemsCoordinator._end_needless_saturation
    for mode in (TargetMode.SURPLUS, TargetMode.FORCED, TargetMode.NONE):
        coordinator.target_states = {"rod": TargetState(mode)}
        end(coordinator, 6000.0)
    assert ended == []
    # Met, but still within its minimum runtime.
    coordinator.target_states = {"rod": TargetState(TargetMode.DONE)}
    end(coordinator, 5000.0)
    assert ended == []
    end(coordinator, 6000.0)
    coordinator.target_states = {"rod": TargetState(TargetMode.WAITING)}
    end(coordinator, 6000.0)
    assert ended == ["rod", "rod"]


def test_end_saturation_controls_the_consumer_again() -> None:
    controller = RealTimeController.__new__(RealTimeController)
    controller._saturated_until = {"rod": time.monotonic() + 900}
    requested: list[bool] = []
    controller.request = lambda: requested.append(True)
    assert controller.saturated == frozenset({"rod"})
    controller.end_saturation("rod")
    assert controller.saturated == frozenset()
    controller.end_saturation("rod")
    assert requested == [True]


def _release_coordinator(settings, mode=None):
    from custom_components.slems.consumer_targets import TargetMode, TargetProgress, TargetState
    from custom_components.slems.consumers import RuntimeTracker

    runtime = RuntimeTracker()
    return SimpleNamespace(
        consumer_targets={"rod": settings},
        target_progress={"rod": TargetProgress()},
        target_states={"rod": TargetState(mode or TargetMode.SURPLUS)},
        target_energy_wh={},
        _runtime=runtime,
        _short_since={},
        _full_power_w=lambda consumer: 1200.0,
    )


def test_release_window_keeps_the_release_open() -> None:
    from custom_components.slems.consumer_targets import TargetMode, TargetSettings
    from custom_components.slems.coordinator import SlemsCoordinator

    release = replace(consumer(), control_mode=ControlMode.RELEASE)
    coordinator = _release_coordinator(TargetSettings(release_window_min=150))
    holds = SlemsCoordinator._window_holds
    # Off: nothing to hold.
    assert not holds(coordinator, release, 0.0, TargetMode.SURPLUS)
    coordinator._runtime.update("rod", True, 0.0)
    assert holds(coordinator, release, 8000.0, TargetMode.SURPLUS)
    assert holds(coordinator, release, 8000.0, TargetMode.NONE)
    assert not holds(coordinator, release, 9000.0, TargetMode.SURPLUS)
    # Met or waiting for its period: no longer held.
    assert not holds(coordinator, release, 60.0, TargetMode.DONE)
    assert not holds(coordinator, release, 60.0, TargetMode.WAITING)
    # Only for a release, and only with a window.
    assert not holds(coordinator, consumer(), 60.0, TargetMode.SURPLUS)
    coordinator.consumer_targets["rod"].release_window_min = 0
    assert not holds(coordinator, release, 60.0, TargetMode.SURPLUS)


def test_avoiding_short_runs_with_a_temperature_target_needs_the_charge_only() -> None:
    from custom_components.slems.const import TargetType
    from custom_components.slems.consumer_targets import TargetMode, TargetSettings
    from custom_components.slems.coordinator import SlemsCoordinator

    config = replace(consumer(), control_mode=ControlMode.RELEASE, avoid_cycling=True, min_on_s=5400.0)
    coordinator = _release_coordinator(TargetSettings(type=TargetType.TEMPERATURE))
    runs: list[float] = []

    def run_surplus(run_s: float) -> float:
        runs.append(run_s)
        return 2000.0

    holds = SlemsCoordinator._cycling_holds
    # Energy to the target not known: the minimum runtime.
    holds(coordinator, config, 0.0, TargetMode.SURPLUS, 0.0, run_surplus)
    # 1.2 kWh at 1200 W: one hour.
    coordinator.target_energy_wh["rod"] = 1200.0
    holds(coordinator, config, 0.0, TargetMode.SURPLUS, 0.0, run_surplus)
    # More than the minimum runtime: the minimum runtime.
    coordinator.target_energy_wh["rod"] = 3000.0
    holds(coordinator, config, 0.0, TargetMode.SURPLUS, 0.0, run_surplus)
    assert runs == [5400.0, 3600.0, 5400.0]
    # The window before the forced run opens without surplus.
    assert holds(coordinator, config, 0.0, TargetMode.WINDOW, 0.0, run_surplus) == (False, False)


def test_surplus_window_is_kept_once_open_and_releases() -> None:
    from datetime import datetime, timedelta

    from homeassistant.util import dt as dt_util

    from custom_components.slems.const import SurplusWindow, TargetType
    from custom_components.slems.consumer_targets import TargetMode, TargetSettings, TargetState
    from custom_components.slems.coordinator import SlemsCoordinator

    zone = dt_util.get_default_time_zone()
    day = datetime(2026, 10, 12, tzinfo=zone)
    release = replace(consumer(), control_mode=ControlMode.RELEASE)
    settings = TargetSettings(type=TargetType.TEMPERATURE, surplus_window=SurplusWindow.SHOW, release_window_min=120)
    coordinator = SimpleNamespace(surplus_windows={}, target_energy_wh={"rod": 2800.0})
    state = TargetState(TargetMode.SURPLUS, end=day.replace(hour=15, minute=30))
    plan = SlemsCoordinator._surplus_window
    surplus = {day.replace(hour=12): 3000.0}

    def at(hour: int, minute: int = 0, forecast=surplus, current=settings):
        return plan(
            coordinator, release, current, state, day.replace(hour=hour, minute=minute),
            hourly_surplus=forecast, power_w=2800.0,
        )

    # Shown only: planned 11:30–13:30, the mode unchanged.
    assert at(9).mode is TargetMode.SURPLUS
    assert coordinator.surplus_windows["rod"][:2] == (day.replace(hour=11, minute=30), day.replace(hour=13, minute=30))
    # Released: open from 11:30; kept while open although the forecast moved.
    released = replace(settings, surplus_window=SurplusWindow.RELEASE)
    assert at(11, current=released).mode is TargetMode.SURPLUS
    assert at(11, 45, current=released).mode is TargetMode.PLANNED
    later = {day.replace(hour=14): 3000.0}
    assert at(12, forecast=later, current=released).mode is TargetMode.PLANNED
    assert coordinator.surplus_windows["rod"][0] == day.replace(hour=11, minute=30)
    # Off: no window.
    assert at(12, current=replace(settings, surplus_window=SurplusWindow.OFF)).mode is TargetMode.SURPLUS
    assert "rod" not in coordinator.surplus_windows
