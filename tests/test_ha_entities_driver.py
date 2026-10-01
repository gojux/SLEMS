"""Tests for the battery driver backed by Home Assistant entities."""

from types import SimpleNamespace

import pytest

from custom_components.slems.const import BatteryControl, ReleaseState
from custom_components.slems.drivers.base import BatteryDriverError
from custom_components.slems.drivers.ha_entities import (
    EntityBatteryConfig,
    HomeAssistantEntityDriver,
)


class FakeHass:
    """States and recorded service calls; a service call updates the state."""

    def __init__(self, states: dict[str, tuple[str, dict]]) -> None:
        self._states = {
            entity_id: SimpleNamespace(entity_id=entity_id, state=state, attributes=attributes)
            for entity_id, (state, attributes) in states.items()
        }
        self.calls: list[tuple[str, str, dict]] = []
        self.states = SimpleNamespace(get=self._states.get)
        self.services = SimpleNamespace(async_call=self._call)

    async def _call(self, domain: str, service: str, data: dict, blocking: bool = False) -> None:
        assert blocking, "SLEMS waits for every service call"
        self.calls.append((domain, service, data))
        state = self._states.get(data.get("entity_id"))
        if state is None:
            return
        if service == "set_value":
            state.state = str(data["value"])
        elif service == "select_option":
            state.state = data["option"]
        elif service in ("turn_on", "turn_off") and domain != "script":
            state.state = "on" if service == "turn_on" else "off"

    def value(self, entity_id: str) -> str:
        return self._states[entity_id].state


NUMBER = {"min": -2500, "max": 2500, "step": 1, "unit_of_measurement": "W"}
POSITIVE = {"min": 0, "max": 2500, "step": 1, "unit_of_measurement": "W"}
MODES = {"options": ["Stop", "Charge", "Discharge", "Auto"]}


def hass() -> FakeHass:
    return FakeHass(
        {
            "sensor.soc": ("64", {"unit_of_measurement": "%"}),
            "sensor.power": ("-300", {"unit_of_measurement": "W"}),
            "sensor.temp": ("77", {"unit_of_measurement": "°F"}),
            "sensor.cell_max": ("3350", {"unit_of_measurement": "mV"}),
            "sensor.charged": ("1200", {"unit_of_measurement": "Wh"}),
            "number.setpoint": ("0", NUMBER),
            "number.charge": ("0", POSITIVE),
            "number.discharge": ("0", POSITIVE),
            "select.mode": ("Auto", MODES),
            "switch.remote": ("off", {}),
            "script.power": ("off", {}),
            "script.release": ("off", {}),
        }
    )


def driver(fake: FakeHass, **options) -> HomeAssistantEntityDriver:
    config = EntityBatteryConfig(
        soc_entity_id="sensor.soc",
        power_entity_id="sensor.power",
        capacity_wh=5000,
        max_charge_power_w=2000,
        max_discharge_power_w=2000,
        **options,
    )
    return HomeAssistantEntityDriver(fake, config)


async def test_read_only_by_default() -> None:
    battery = driver(hass())
    assert not battery.capabilities.controllable
    with pytest.raises(NotImplementedError):
        await battery.apply_power(100)


async def test_telemetry_with_optional_sensors_in_slems_units() -> None:
    battery = driver(
        hass(),
        extra_entities={
            "internal_temperature": "sensor.temp",
            "max_cell_voltage": "sensor.cell_max",
            "total_charging_energy": "sensor.charged",
        },
    )
    telemetry = await battery.read_telemetry()
    assert telemetry.soc_pct == 64
    assert telemetry.power_w == -300
    assert telemetry.extra["internal_temperature"] == pytest.approx(25)
    assert telemetry.extra["max_cell_voltage"] == pytest.approx(3.35)
    assert telemetry.extra["total_charging_energy"] == pytest.approx(1.2)
    assert battery.extra_telemetry_keys == {
        "internal_temperature", "max_cell_voltage", "total_charging_energy"
    }


async def test_setpoint_is_clamped_inverted_and_not_repeated() -> None:
    fake = hass()
    battery = driver(
        fake,
        control=BatteryControl.SETPOINT,
        setpoint_entity_id="number.setpoint",
        setpoint_inverted=True,
        remote_entity_id="switch.remote",
    )
    assert await battery.apply_power(3000)
    assert fake.value("switch.remote") == "on"
    assert float(fake.value("number.setpoint")) == -2000
    calls = len(fake.calls)
    assert await battery.apply_power(3000)
    assert len(fake.calls) == calls
    assert await battery.apply_power(3000, refresh=True)
    assert len(fake.calls) > calls


async def test_split_sets_the_stopping_direction_first_and_the_mode() -> None:
    fake = hass()
    battery = driver(
        fake,
        control=BatteryControl.SPLIT,
        charge_entity_id="number.charge",
        discharge_entity_id="number.discharge",
        mode_entity_id="select.mode",
        mode_charge="Charge",
        mode_discharge="Discharge",
        mode_standby="Stop",
        mode_auto="Auto",
    )
    await battery.apply_power(800)
    assert float(fake.value("number.charge")) == 800
    assert fake.value("select.mode") == "Charge"
    fake.calls.clear()
    await battery.apply_power(-500)
    assert fake.calls[0][2]["entity_id"] == "number.charge"
    assert float(fake.value("number.charge")) == 0
    assert float(fake.value("number.discharge")) == 500
    assert fake.value("select.mode") == "Discharge"
    await battery.apply_power(0)
    assert fake.value("select.mode") == "Stop"


@pytest.mark.parametrize(
    ("release", "mode", "remote"),
    [(ReleaseState.AUTO, "Auto", "off"), (ReleaseState.STANDBY, "Stop", "on")],
)
async def test_release_state(release: ReleaseState, mode: str, remote: str) -> None:
    fake = hass()
    battery = driver(
        fake,
        control=BatteryControl.SPLIT,
        charge_entity_id="number.charge",
        discharge_entity_id="number.discharge",
        mode_entity_id="select.mode",
        mode_standby="Stop",
        mode_auto="Auto",
        mode_charge="Charge",
        remote_entity_id="switch.remote",
        release_state=release,
    )
    await battery.apply_power(1000)
    await battery.release_control()
    assert float(fake.value("number.charge")) == 0
    assert fake.value("select.mode") == mode
    assert fake.value("switch.remote") == remote


async def test_script_receives_the_power_and_releases() -> None:
    fake = hass()
    battery = driver(
        fake,
        control=BatteryControl.SCRIPT,
        power_script="script.power",
        release_script="script.release",
    )
    await battery.apply_power(-700)
    # Called directly, so SLEMS waits until the script is done.
    assert fake.calls[-1] == ("script", "power", {"power_w": -700})
    await battery.release_control()
    assert fake.calls[-1][:2] == ("script", "release")


async def test_failed_service_call_is_a_communication_error() -> None:
    from homeassistant.exceptions import HomeAssistantError

    fake = hass()
    battery = driver(
        fake, control=BatteryControl.SPLIT,
        charge_entity_id="number.charge", discharge_entity_id="number.discharge",
    )

    async def failing(domain, service, data, blocking=False):
        raise HomeAssistantError("device did not answer")

    fake.services.async_call = failing
    assert not await battery.apply_power(800)
    with pytest.raises(BatteryDriverError):
        await battery.release_control()


async def test_unavailable_entity_fails_the_command() -> None:
    fake = hass()
    battery = driver(
        fake, control=BatteryControl.SETPOINT, setpoint_entity_id="number.missing"
    )
    assert not await battery.apply_power(500)
    fake._states["sensor.soc"].state = "unavailable"
    with pytest.raises(BatteryDriverError):
        await battery.read_telemetry()


def test_automatic_release_needs_a_way_back() -> None:
    base = {
        "soc_entity": "sensor.soc",
        "capacity_wh": 5000,
        "max_charge_power_w": 2000,
        "max_discharge_power_w": 2000,
    }
    setpoint = {**base, "battery_control": "setpoint", "setpoint_entity": "number.setpoint"}
    assert not EntityBatteryConfig.from_data(setpoint).can_release_to_auto
    assert EntityBatteryConfig.from_data(
        {**setpoint, "remote_entity": "switch.remote"}
    ).can_release_to_auto
    assert not EntityBatteryConfig.from_data(
        {**setpoint, "remote_entity": "select.remote"}
    ).can_release_to_auto
    assert EntityBatteryConfig.from_data(
        {**base, "battery_control": "split", "mode_entity": "select.mode", "mode_auto": "Auto"}
    ).can_release_to_auto
