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
