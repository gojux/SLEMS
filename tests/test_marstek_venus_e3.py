"""Tests for the Marstek Venus E 3.0 driver using a fake Modbus link."""

import pytest

from custom_components.slems.const import ReleaseState
from custom_components.slems.drivers.base import BatteryDriverError
from custom_components.slems.drivers import marstek_venus_e3 as venus
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
            34003: 12,
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
    assert telemetry.extra["cycle_count"] == 12
    assert telemetry.extra["rs485_control"] is True


async def test_slow_values_are_read_once_a_minute(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [1000.0]
    monkeypatch.setattr(venus.time, "monotonic", lambda: clock[0])
    link = FakeLink({34002: 644, 34003: 12, 30001: 500, 30100: 5230, 37007: 3400})
    driver = make_driver(link)
    reads: list[int] = []
    original = link.read

    async def counting(address: int, count: int) -> list[int] | None:
        reads.append(address)
        return await original(address, count)

    link.read = counting
    await driver.read_telemetry()
    assert 30100 in reads and 33000 in reads
    # Within the minute the slow values keep their last value; the cell voltages are read.
    reads.clear()
    link.registers[34003] = 13
    clock[0] += 30
    telemetry = await driver.read_telemetry()
    assert 30100 not in reads and 37007 in reads
    assert telemetry.extra["cycle_count"] == 12 and telemetry.extra["battery_voltage"] == pytest.approx(52.3)
    # A complete read (self-test) and the next minute read them again.
    assert (await driver.read_telemetry(complete=True)).extra["cycle_count"] == 13
    reads.clear()
    clock[0] += 61
    await driver.read_telemetry()
    assert 30100 in reads


async def test_adjacent_registers_are_read_in_one_frame() -> None:
    link = FakeLink({34002: 644, 34003: 12, 30001: 500, 30006: 0x10000 - 480, 37007: 3400, 37008: 3390, 33000: 0})
    link.registers.update({33001: 13098, 33002: 0, 33003: 10417})
    reads: list[tuple[int, int]] = []
    original = link.read

    async def counting(address: int, count: int) -> list[int] | None:
        reads.append((address, count))
        return await original(address, count)

    link.read = counting
    telemetry = await make_driver(link).read_telemetry()
    assert {(30001, 6), (34002, 2), (37007, 2), (33000, 4)} <= set(reads)
    assert len(reads) == 9
    assert telemetry.power_w == 500 and telemetry.ac_power_w == 480
    assert telemetry.extra["cycle_count"] == 12 and telemetry.extra["min_cell_voltage"] == pytest.approx(3.39)
    assert telemetry.extra["total_charging_energy"] == pytest.approx(130.98)
    assert telemetry.extra["total_discharging_energy"] == pytest.approx(104.17)


async def test_a_refused_block_is_read_one_by_one() -> None:
    link = FakeLink({34002: 644, 34003: 12, 30001: 500, 30006: 0, 37007: 3400, 37008: 3390})
    reads: list[tuple[int, int]] = []
    original = link.read

    async def refusing(address: int, count: int) -> list[int] | None:
        reads.append((address, count))
        return None if (address, count) == (37007, 2) else await original(address, count)

    link.read = refusing
    driver = make_driver(link)
    telemetry = await driver.read_telemetry()
    assert telemetry.extra["max_cell_voltage"] == pytest.approx(3.4)
    assert (37007, 1) in reads and (37008, 1) in reads
    # Not asked again.
    reads.clear()
    await driver.read_telemetry()
    assert (37007, 2) not in reads and (37007, 1) in reads


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


async def test_release_to_standby_keeps_remote_control() -> None:
    link = FakeLink()
    driver = MarstekVenusE3Driver(
        "host", 502, 1, capacity_wh=5120, max_charge_power_w=2500,
        max_discharge_power_w=2500, release_state=ReleaseState.STANDBY, link=link,
    )
    await driver.apply_power(1000)
    await driver.release_control()
    assert link.registers[REG_FORCE_MODE] == FORCE_NONE
    assert link.registers[REG_SET_CHARGE_POWER] == 0
    assert link.registers[REG_RS485_CONTROL] == RS485_ENABLE


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
    # Keep-alive without a read back by the telemetry: RS485, force mode and
    # the active set point (the charge set point has no effect while discharging).
    await driver.apply_power(-900, refresh=True)
    assert sorted(link.writes) == sorted(
        [(REG_RS485_CONTROL, RS485_ENABLE), (REG_SET_DISCHARGE_POWER, 900), (REG_FORCE_MODE, FORCE_DISCHARGE)]
    )


async def test_soc_falls_back_to_whole_percent_register() -> None:
    link = FakeLink({37005: 64, 30001: 0})
    telemetry = await make_driver(link).read_telemetry()
    assert telemetry.soc_pct == 64


async def test_keepalive_skips_registers_the_telemetry_confirmed(monkeypatch) -> None:
    clock = [100.0]
    monkeypatch.setattr(venus.time, "monotonic", lambda: clock[0])
    link = FakeLink({34002: 500, 30001: 0})
    driver = make_driver(link)
    assert await driver.apply_power(-900)
    clock[0] += 55
    await driver.read_telemetry()
    link.writes.clear()
    clock[0] += 5
    assert await driver.apply_power(-900, refresh=True)
    assert link.writes == [(REG_SET_DISCHARGE_POWER, 900)]
    # In standby with a confirmed force mode a keep-alive writes nothing.
    assert await driver.apply_power(0)
    clock[0] += 55
    await driver.read_telemetry()
    link.writes.clear()
    clock[0] += 5
    assert await driver.apply_power(0, refresh=True)
    assert link.writes == []
    # A confirmation older than VERIFIED_MAX_AGE_S does not count.
    clock[0] += venus.VERIFIED_MAX_AGE_S
    assert await driver.apply_power(0, refresh=True)
    assert sorted(link.writes) == sorted([(REG_RS485_CONTROL, RS485_ENABLE), (REG_FORCE_MODE, FORCE_NONE)])


async def test_lost_control_seen_by_the_telemetry_is_written_again(monkeypatch) -> None:
    clock = [100.0]
    monkeypatch.setattr(venus.time, "monotonic", lambda: clock[0])
    link = FakeLink({34002: 500, 30001: 0})
    driver = make_driver(link)
    assert await driver.apply_power(-900)
    assert not driver.needs_write(-900)
    # The battery dropped RS485 control (e.g. after a BMS cut-off).
    link.registers[REG_RS485_CONTROL] = RS485_DISABLE
    clock[0] += 1
    await driver.read_telemetry()
    assert driver.needs_write(-900)
    link.writes.clear()
    assert await driver.apply_power(-900)
    assert link.writes == [(REG_RS485_CONTROL, RS485_ENABLE)]
    assert not driver.needs_write(-900)


async def test_a_write_during_the_telemetry_read_is_not_compared(monkeypatch) -> None:
    clock = [100.0]
    monkeypatch.setattr(venus.time, "monotonic", lambda: clock[0])
    link = FakeLink({34002: 500, 30001: 0})
    driver = make_driver(link)
    assert await driver.apply_power(-900)
    original_read = link.read

    async def read_then_write(address: int, count: int) -> list[int] | None:
        words = await original_read(address, count)
        if address == REG_RS485_CONTROL:
            # The control loop changes the force mode while the telemetry reads.
            await driver.apply_power(900)
        return words

    link.read = read_then_write
    await driver.read_telemetry()
    assert not driver.needs_write(900)


async def test_failed_release_is_reported() -> None:
    from custom_components.slems.drivers import BatteryDriverError

    class FailingLink(FakeLink):
        async def write(self, address: int, value: int) -> bool:
            self.writes.append((address, value))
            return False

    link = FailingLink({REG_FORCE_MODE: 1, REG_RS485_CONTROL: RS485_ENABLE})
    with pytest.raises(BatteryDriverError):
        await make_driver(link).release_control()


async def test_release_is_confirmed_by_reading_back() -> None:
    from custom_components.slems.drivers import BatteryDriverError

    class StuckLink(FakeLink):
        """Accepts the writes, but the battery stays in forced mode."""

        async def write(self, address: int, value: int) -> bool:
            self.writes.append((address, value))
            if address != REG_FORCE_MODE:
                self.registers[address] = value
            return True

    link = StuckLink({REG_FORCE_MODE: 2, REG_RS485_CONTROL: RS485_ENABLE})
    with pytest.raises(BatteryDriverError, match="not confirmed"):
        await make_driver(link).release_control()


async def test_release_reconnects_first() -> None:
    link = FakeLink({REG_FORCE_MODE: 1, REG_RS485_CONTROL: RS485_ENABLE})
    link.connected = False
    await make_driver(link).release_control()
    assert link.connected and link.registers[REG_FORCE_MODE] == FORCE_NONE
