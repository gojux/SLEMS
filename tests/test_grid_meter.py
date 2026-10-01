"""Tests for the grid power read from a SunSpec meter over Modbus."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from custom_components.slems import config_flow
from custom_components.slems.const import (
    CONF_GRID_MODBUS,
    CONF_GRID_MODBUS_HOST,
    CONF_GRID_MODBUS_INTERVAL_S,
    CONF_GRID_MODBUS_INVERTED,
    CONF_GRID_MODBUS_PORT,
    CONF_GRID_MODBUS_REGISTER,
    CONF_GRID_MODBUS_SIGN,
    CONF_GRID_MODBUS_UNIT_ID,
    CONF_GRID_POWER_ENTITY,
    CONF_GRID_POWER_INVERTED,
)
from custom_components.slems.grid_meter import (
    BACKOFF_AFTER,
    FRESH_MIN_S,
    ModbusGridMeter,
    SignMismatchError,
    SignUndecidableError,
    SunSpecError,
    decode_power,
    detect_inversion,
    find_meters,
)


def _text(text: str, registers: int) -> list[int]:
    raw = text.encode().ljust(registers * 2, b"\0")
    return [(raw[i] << 8) | raw[i + 1] for i in range(0, len(raw), 2)]


def _common(address: int, manufacturer: str, model: str, option: str) -> dict[int, int]:
    words = [1, 65, *_text(manufacturer, 16), *_text(model, 16), *_text(option, 8)]
    return dict(enumerate(words, address))


def solaredge_registers(meter_1_w: int = 0xFF38, meter_2_w: int = 1234) -> dict[int, int]:
    """SolarEdge layout: inverter, export+import meter, consumption meter."""
    registers = {40000: 0x5375, 40001: 0x6E53}
    registers |= _common(40002, "SolarEdge", "SE10K", "")
    registers |= {40069: 103, 40070: 50}
    registers |= _common(40121, "SolarEdge", "WND-3Y-400-MB", "Export+Import")
    registers |= {40188: 203, 40189: 105, 40206: meter_1_w, 40210: 0}
    registers |= _common(40295, "SolarEdge", "WND-3Y-400-MB", "Consumption")
    registers |= {40362: 203, 40363: 105, 40380: meter_2_w, 40384: 0xFFFF}  # SF -1
    registers |= {40469: 0xFFFF, 40470: 0}
    return registers


def reader(registers: dict[int, int], fail: bool = False):
    async def read(address: int, count: int) -> list[int]:
        if fail:
            raise ConnectionError("refused")
        return [registers.get(address + i, 0) for i in range(count)]

    return read


async def test_find_meters_in_the_sunspec_chain() -> None:
    meters = await find_meters(reader(solaredge_registers()))
    assert [m.power_register for m in meters] == [40206, 40380]
    assert meters[0].option == "Export+Import"
    assert meters[1].option == "Consumption"
    assert meters[0].model == "WND-3Y-400-MB"


async def test_find_meters_without_sunspec_or_connection() -> None:
    with pytest.raises(SunSpecError):
        await find_meters(reader({40000: 1}))
    with pytest.raises(ConnectionError):
        await find_meters(reader({}, fail=True))


def test_decode_power_with_scale_factor() -> None:
    assert decode_power([0xFF38, 0, 0, 0, 0]) == -200
    assert decode_power([1234, 0, 0, 0, 0xFFFF]) == pytest.approx(123.4)
    assert decode_power([0x8000, 0, 0, 0, 0]) is None


def test_detect_inversion() -> None:
    # SolarEdge reports export as positive: opposite to the entity.
    assert detect_inversion([(-500, 520), (-480, 470), (5, 0)]) is True
    assert detect_inversion([(500, 520), (480, 470)]) is False
    with pytest.raises(SignUndecidableError):
        detect_inversion([(20, -10), (0, 30)])
    with pytest.raises(SignUndecidableError):
        detect_inversion([(0, 140), (0, 120)])  # meter not fed yet
    with pytest.raises(SignMismatchError):
        detect_inversion([(2000, 300), (2100, 250)])


async def test_meter_polls_inverts_and_falls_back() -> None:
    registers = solaredge_registers()
    values = []
    failing = False

    async def read(address: int, count: int) -> list[int]:
        if failing:
            raise TimeoutError
        return [registers.get(address + i, 0) for i in range(count)]

    meter = ModbusGridMeter(read, 40206, 0.5, True, values.append)
    assert await meter.poll() is True
    assert values == [200]
    assert meter.fresh(meter.updated + FRESH_MIN_S)
    assert not meter.fresh(meter.updated + FRESH_MIN_S + 0.1)
    assert meter.unavailable_for(meter.updated + FRESH_MIN_S + 10) == pytest.approx(10)
    failing = True
    for _ in range(BACKOFF_AFTER):
        assert await meter.poll() is False
    assert meter.errors == BACKOFF_AFTER and meter.last_error.startswith("TimeoutError")
    assert meter.failing_since is not None
    failing = False
    await meter.poll()
    assert meter.failing_since is None
    assert meter.diagnostics(meter.updated)["reads"] == 2


class _Flow(config_flow._GridModbusSteps):
    """The Modbus steps without Home Assistant's flow manager."""

    def __init__(self, entity_w: float) -> None:
        self.hass = SimpleNamespace(
            states=SimpleNamespace(
                get=lambda _: SimpleNamespace(
                    state=str(entity_w), attributes={"unit_of_measurement": "W"}
                )
            )
        )

    def _async_finish_system(self):
        return {"type": "create_entry", "data": self._system}

    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}


def _temporary_unit(registers: dict[int, int]):
    @asynccontextmanager
    async def temporary(_hass, _params, _unit_id):
        yield SimpleNamespace(read_holding_registers=reader(registers))

    return temporary


SYSTEM = {CONF_GRID_POWER_ENTITY: "sensor.grid", CONF_GRID_POWER_INVERTED: False, CONF_GRID_MODBUS: True}
CONNECTION = {
    CONF_GRID_MODBUS_HOST: " 192.168.1.5 ",
    CONF_GRID_MODBUS_PORT: 503.0,
    CONF_GRID_MODBUS_UNIT_ID: 1.0,
    CONF_GRID_MODBUS_INTERVAL_S: 0.5,
    CONF_GRID_MODBUS_SIGN: "auto",
}


async def test_flow_finds_meter_choice_and_sign() -> None:
    # Meter 1 reads +200 W (export positive), the entity -200 W (export).
    flow = _Flow(-200)
    with (
        patch.object(config_flow, "async_get_temporary_unit", _temporary_unit(solaredge_registers(200))),
        patch.object(config_flow, "SIGN_SAMPLE_S", 0),
    ):
        result = await flow._async_system_done(SYSTEM, {})
        assert result["step_id"] == "grid_modbus"
        result = await flow.async_step_grid_modbus(dict(CONNECTION))
        assert result["step_id"] == "grid_meter"
        result = await flow.async_step_grid_meter({CONF_GRID_MODBUS_REGISTER: "40206"})
    assert result["type"] == "create_entry"
    data = result["data"]
    assert data[CONF_GRID_MODBUS_HOST] == "192.168.1.5"
    assert data[CONF_GRID_MODBUS_PORT] == 503
    assert data[CONF_GRID_MODBUS_REGISTER] == 40206
    assert data[CONF_GRID_MODBUS_INVERTED] is True


async def test_flow_reports_an_undecidable_sign_and_keeps_settings() -> None:
    flow = _Flow(10)
    registers = solaredge_registers(5)
    del registers[40362]  # only one meter left
    with (
        patch.object(config_flow, "async_get_temporary_unit", _temporary_unit(registers)),
        patch.object(config_flow, "SIGN_SAMPLE_S", 0),
    ):
        await flow._async_system_done(SYSTEM, {})
        result = await flow.async_step_grid_modbus(dict(CONNECTION))
    assert result["step_id"] == "grid_modbus"
    assert result["errors"] == {"base": "sign_undecidable"}
    # Chosen by hand it finishes without comparing.
    with patch.object(config_flow, "async_get_temporary_unit", _temporary_unit(registers)):
        result = await flow.async_step_grid_modbus(dict(CONNECTION, **{CONF_GRID_MODBUS_SIGN: "normal"}))
    assert result["data"][CONF_GRID_MODBUS_INVERTED] is False


async def test_switched_off_modbus_keeps_its_settings() -> None:
    flow = _Flow(0)
    previous = {CONF_GRID_MODBUS_HOST: "proxy", CONF_GRID_MODBUS_REGISTER: 40206, "pv_power_entity": "x"}
    result = await flow._async_system_done(dict(SYSTEM, **{CONF_GRID_MODBUS: False}), previous)
    assert result["type"] == "create_entry"
    assert result["data"][CONF_GRID_MODBUS_HOST] == "proxy"
    assert "pv_power_entity" not in result["data"]
