"""Tests for current controlled consumers (A, phases, start/stop)."""

from types import SimpleNamespace

from custom_components.slems.consumers import ConsumerConfig, active_phases, amps_for
from custom_components.slems.const import ConsumerType, ControlMode
from custom_components.slems.controller import RealTimeController


def wallbox(**data) -> ConsumerConfig:
    return ConsumerConfig.from_subentry(
        "wb",
        "Wallbox",
        {
            "consumer_type": ConsumerType.WALLBOX.value,
            "power_entity": "sensor.wb_power",
            "energy_entity": "sensor.wb_energy",
            "included_in_meter": True,
            "control_mode": ControlMode.CURRENT.value,
            "control_entity": "number.wb_current",
            "min_current_a": 6.0,
            "max_current_a": 16.0,
            "phases": 3,
            **data,
        },
    )


class FakeHass:
    """States and recorded service calls."""

    def __init__(self, states: dict[str, SimpleNamespace]) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.states = SimpleNamespace(get=states.get)

        async def call(domain, service, data):
            self.calls.append((domain, service, data))

        self.services = SimpleNamespace(async_call=call)


def state(value, **attributes) -> SimpleNamespace:
    return SimpleNamespace(state=str(value), attributes=attributes)


def controller(hass: FakeHass) -> RealTimeController:
    instance = RealTimeController.__new__(RealTimeController)
    instance._hass = hass
    return instance


def test_power_range_follows_the_phases() -> None:
    config = wallbox()
    assert (config.min_power_w, config.max_power_w) == (4140, 11040)
    single = config.with_phases(1)
    assert (single.min_power_w, single.max_power_w) == (1380, 3680)
    hass = FakeHass({"sensor.phases": state(1)})
    assert active_phases(hass, wallbox(phases_entity="sensor.phases")) == 1
    # Unknown or 0 (not charging): the configured phases.
    hass = FakeHass({"sensor.phases": state(0)})
    assert active_phases(hass, wallbox(phases_entity="sensor.phases")) == 3


def test_amperes_are_rounded_down() -> None:
    assert amps_for(4140, 230, 3) == 6
    assert amps_for(4800, 230, 3) == 6  # 6.96 A: never above the planned power
    assert amps_for(4830, 230, 3) == 7
    assert amps_for(0, 230, 3) == 0


async def test_current_set_point_and_stop_without_start_entity() -> None:
    hass = FakeHass({"number.wb_current": state(6, min=6, max=16, step=1)})
    control = controller(hass)
    assert await control._async_apply_current(wallbox(), 5000, hass.states.get("number.wb_current"))
    assert hass.calls == [("number", "set_value", {"entity_id": "number.wb_current", "value": 7})]
    # Below the minimum current: the lowest current the entity takes (evcc decides).
    hass.calls.clear()
    hass = FakeHass({"number.wb_current": state(7, min=6, max=16, step=1)})
    control = controller(hass)
    await control._async_apply_current(wallbox(), 3000, hass.states.get("number.wb_current"))
    assert hass.calls == [("number", "set_value", {"entity_id": "number.wb_current", "value": 6})]
    # Unchanged current: nothing sent (1 A dead band).
    hass = FakeHass({"number.wb_current": state(7, min=0, max=16, step=1)})
    control = controller(hass)
    assert not await control._async_apply_current(wallbox(), 5000, hass.states.get("number.wb_current"))
    assert hass.calls == []


async def test_start_and_stop_through_a_select() -> None:
    config = wallbox(start_entity="select.evcc_mode", start_on="now", start_off="off")
    hass = FakeHass(
        {
            "number.wb_current": state(6, min=6, max=16, step=1),
            "select.evcc_mode": state("off", options=["off", "pv", "minpv", "now"]),
        }
    )
    control = controller(hass)
    await control._async_apply_current(config, 9000, hass.states.get("number.wb_current"))
    assert hass.calls == [
        ("number", "set_value", {"entity_id": "number.wb_current", "value": 13}),
        ("select", "select_option", {"entity_id": "select.evcc_mode", "option": "now"}),
    ]
    # Stop: only the mode, the current stays.
    hass = FakeHass(
        {
            "number.wb_current": state(13, min=6, max=16, step=1),
            "select.evcc_mode": state("now", options=["off", "pv", "minpv", "now"]),
        }
    )
    control = controller(hass)
    await control._async_apply_current(config, 0, hass.states.get("number.wb_current"))
    assert hass.calls == [("select", "select_option", {"entity_id": "select.evcc_mode", "option": "off"})]


async def test_start_and_stop_through_a_switch() -> None:
    config = wallbox(start_entity="switch.wb_enable")
    hass = FakeHass(
        {"number.wb_current": state(0, min=0, max=16, step=1), "switch.wb_enable": state("off")}
    )
    control = controller(hass)
    await control._async_apply_current(config, 4200, hass.states.get("number.wb_current"))
    assert ("switch", "turn_on", {"entity_id": "switch.wb_enable"}) in hass.calls


def test_minimum_pause_only_after_a_real_stop() -> None:
    from custom_components.slems.consumers import RuntimeTracker

    tracker = RuntimeTracker()
    config = wallbox(min_off_minutes=5, min_on_minutes=5)
    # Off since the start: may start at once.
    tracker.update("wb", False, 100.0)
    assert not tracker.must_stay_off(config, 101.0)
    tracker.update("wb", True, 110.0)
    assert tracker.must_stay_on(config, 200.0)
    tracker.update("wb", False, 500.0)
    assert tracker.must_stay_off(config, 600.0)
    assert not tracker.must_stay_off(config, 801.0)


async def test_current_through_a_select_with_ampere_options() -> None:
    # ha-evcc: the maximum current of a loadpoint is a select (6 … 32 A).
    config = wallbox(control_entity="select.evcc_garage_max_current")
    options = [str(a) for a in range(6, 33)]
    hass = FakeHass({"select.evcc_garage_max_current": state("16", options=options)})
    control = controller(hass)
    await control._async_apply_current(config, 9000, hass.states.get("select.evcc_garage_max_current"))
    assert hass.calls == [
        ("select", "select_option", {"entity_id": "select.evcc_garage_max_current", "option": "13"})
    ]
    # Stop without start entity: the lowest option (evcc decides itself).
    hass = FakeHass({"select.evcc_garage_max_current": state("13", options=options)})
    control = controller(hass)
    await control._async_apply_current(config, 0, hass.states.get("select.evcc_garage_max_current"))
    assert hass.calls[-1][2]["option"] == "6"
