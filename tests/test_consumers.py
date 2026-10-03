"""Tests for consumer state and the resting of consumers with a cycling thermostat."""

import time
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
