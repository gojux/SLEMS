"""Tests for the Marstek Venus E 3.0 driver using a fake Modbus link."""

import pytest

from custom_components.slems.drivers.base import BatteryDriverError
from custom_components.slems.drivers.marstek_venus_e3 import (
    FORCE_CHARGE,
    FORCE_DISCHARGE,
    FORCE_NONE,
    REG_FORCE_MODE,
    REG_RS485_CONTROL,
    REG_SET_CHARGE_POWER,
    REG_SET_DISCHARGE_POWER,
    RS485_DISABLE,
    RS485_ENABLE,
    MarstekVenusE3Driver,
)


class FakeLink:
    """In-memory replacement for ModbusTcpLink."""

    def __init__(self, registers: dict[int, int] | None = None) -> None:
        self.registers = registers or {}
        self.writes: list[tuple[int, int]] = []
        self.connected = True

    async def connect(self) -> bool:
        self.connected = True
        return True

    async def close(self) -> None:
        self.connected = False

    async def read(self, address: int, count: int) -> list[int] | None:
        if address not in self.registers:
            return None
        return [self.registers.get(address + i, 0) for i in range(count)]

    async def write(self, address: int, value: int) -> bool:
        self.writes.append((address, value))
        self.registers[address] = value
        return True


def make_driver(link: FakeLink, max_power: int = 2500) -> MarstekVenusE3Driver:
    return MarstekVenusE3Driver(
        "host",
        502,
        1,
        capacity_wh=5120,
        max_charge_power_w=max_power,
        max_discharge_power_w=max_power,
        link=link,
    )


async def test_read_telemetry_decodes_values() -> None:
    link = FakeLink(
        {
            34002: 644,
            37005: 64,
            30001: 0x10000 - 700,  # -700 W: discharging
            30006: 700,
            30100: 5230,
            35100: 3,
            42000: RS485_ENABLE,
        }
    )
    telemetry = await make_driver(link).read_telemetry()
    assert telemetry.soc_pct == pytest.approx(64.4)
    assert telemetry.power_w == -700
    assert telemetry.extra["battery_voltage"] == pytest.approx(52.3)
    assert telemetry.extra["inverter_state"] == "discharge"
    assert telemetry.extra["rs485_control"] is True


async def test_read_telemetry_without_answer_raises() -> None:
    link = FakeLink()
    with pytest.raises(BatteryDriverError):
        await make_driver(link).read_telemetry()
    assert link.connected is False


@pytest.mark.parametrize(
    ("net_power", "expected"),
    [
        (1200, (1200, 0, FORCE_CHARGE)),
        (-800, (0, 800, FORCE_DISCHARGE)),
        (0, (0, 0, FORCE_NONE)),
        (9000, (2000, 0, FORCE_CHARGE)),
        (-9000, (0, 2000, FORCE_DISCHARGE)),
    ],
)
async def test_apply_power(net_power: int, expected: tuple[int, int, int]) -> None:
    link = FakeLink()
    assert await make_driver(link, max_power=2000).apply_power(net_power)
    charge, discharge, force_mode = expected
    assert link.registers[REG_RS485_CONTROL] == RS485_ENABLE
    assert link.registers[REG_SET_CHARGE_POWER] == charge
    assert link.registers[REG_SET_DISCHARGE_POWER] == discharge
    assert link.registers[REG_FORCE_MODE] == force_mode
    # The force mode is written last, after both set points are valid.
    assert link.writes[-1][0] == REG_FORCE_MODE


async def test_release_control_returns_to_internal_logic() -> None:
    link = FakeLink()
    driver = make_driver(link)
    await driver.apply_power(1000)
    await driver.release_control()
    assert link.registers[REG_FORCE_MODE] == FORCE_NONE
    assert link.registers[REG_SET_CHARGE_POWER] == 0
    assert link.registers[REG_RS485_CONTROL] == RS485_DISABLE


def test_power_limits_are_capped_to_hardware() -> None:
    driver = make_driver(FakeLink(), max_power=5000)
    assert driver.capabilities.max_charge_power_w == 2500
    assert driver.capabilities.max_discharge_power_w == 2500


async def test_unchanged_registers_are_not_written_again() -> None:
    link = FakeLink()
    driver = make_driver(link)
    await driver.apply_power(-800)
    assert len(link.writes) == 4
    link.writes.clear()
    await driver.apply_power(-900)
    # Only the discharge set point changed.
    assert link.writes == [(REG_SET_DISCHARGE_POWER, 900)]
    link.writes.clear()
    await driver.apply_power(-900, refresh=True)
    assert len(link.writes) == 4


async def test_soc_falls_back_to_whole_percent_register() -> None:
    link = FakeLink({37005: 64, 30001: 0})
    telemetry = await make_driver(link).read_telemetry()
    assert telemetry.soc_pct == 64
