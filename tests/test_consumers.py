"""Tests for consumer state and the resting of consumers with a cycling thermostat."""

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
