"""Tests for the profile driver: against the Venus simulator's register model,
compared with the direct Venus driver, and with own PV, flash registers and
32 bit set points."""

import importlib.util
from pathlib import Path
from unittest.mock import patch

from modbus_connection import IllegalDataAddressError, ModbusTimeoutError
import pytest

from custom_components.slems.battery_profiles import bundled_profiles, parse_profile
from custom_components.slems.const import ReleaseState
from custom_components.slems.drivers import marstek_venus_e3 as venus
from custom_components.slems.drivers import profile as profile_driver
from custom_components.slems.drivers.base import BatteryDriverError
from custom_components.slems.drivers.marstek_venus_e3 import MarstekVenusE3Driver
from custom_components.slems.drivers.profile import (
    ISSUE_OUTSIDE_REST,
    ISSUE_PERSISTENT_BLOCKED,
    ISSUE_PERSISTENT_FREQUENT,
    HaUnitTransport,
    ProfileDriver,
    read_blocks,
)

SIMULATOR = Path(__file__).parents[1] / "dev" / "venus_sim" / "simulator.py"


def load_simulator():
    spec = importlib.util.spec_from_file_location("venus_sim", SIMULATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SIM = load_simulator()
# Reads of the control registers of the Venus (42000-42010 is refused).
CONTROL_READS = [(42000, 1), (42010, 1), (42020, 2)]


class SimLink:
    """A link to the simulator's register model, as the device answers it
    (refused reads over unassigned registers, writes only to control registers);
    records every frame."""

    def __init__(self, model) -> None:
        self.model = model
        self.frames: list[tuple] = []
        self.connected = False
        # Writes the device acknowledges but ignores.
        self.ignored: set[int] = set()

    async def connect(self) -> bool:
        self.connected = True
        return True

    async def close(self) -> None:
        self.connected = False

    async def read(self, address: int, count: int, *, input_registers: bool = False) -> list[int] | None:
        self.frames.append(("read", address, count))
        return self.model.read(address, count)

    async def write(self, address: int, value) -> bool:
        self.frames.append(("write", address, value))
        values = value if isinstance(value, list) else [value]
        if any(address + i not in SIM.WRITABLE for i in range(len(values))):
            return False
        if address not in self.ignored:
            self.model.command(address, values)
        return True

    def writes(self) -> list[tuple]:
        return [frame[1:] for frame in self.frames if frame[0] == "write"]

    def reads(self) -> list[tuple]:
        return [frame[1:] for frame in self.frames if frame[0] == "read"]


class Registers:
    """A plain register store for profiles of other devices."""

    def __init__(self, values: dict[int, int]) -> None:
        self.registers = dict(values)

    def read(self, address: int, count: int) -> list[int] | None:
        addresses = range(address, address + count)
        if any(a not in self.registers for a in addresses):
            return None
        return [self.registers[a] for a in addresses]

    def write(self, address: int, values: list[int]) -> None:
        for offset, value in enumerate(values):
            self.registers[address + offset] = value


class RegisterLink(SimLink):
    async def write(self, address: int, value) -> bool:
        self.frames.append(("write", address, value))
        self.model.write(address, value if isinstance(value, list) else [value])
        return True


@pytest.fixture(autouse=True)
def no_confirm_delay():
    async def sleep(_seconds: float) -> None:
        return None

    with patch.object(profile_driver.asyncio, "sleep", sleep), patch.object(venus.asyncio, "sleep", sleep):
        yield


def venus_profile_driver(link, release_state=ReleaseState.AUTO) -> ProfileDriver:
    return ProfileDriver(
        bundled_profiles()["marstek_venus_e3"], link, capacity_wh=5120,
        max_charge_power_w=2500, max_discharge_power_w=2500, release_state=release_state,
    )


def venus_driver(link, release_state=ReleaseState.AUTO) -> MarstekVenusE3Driver:
    return MarstekVenusE3Driver(
        "host", 502, 1, capacity_wh=5120, max_charge_power_w=2500, max_discharge_power_w=2500,
        release_state=release_state, link=link,
    )


# --- compared with the Venus driver --------------------------------------------


def test_blocks_of_the_venus_profile_are_the_drivers() -> None:
    profile = bundled_profiles()["marstek_venus_e3"]
    registers = [register for read in profile.read.values() for register in read.registers]
    assert {(start, count) for start, count, _ in read_blocks(registers)} == set(venus.TELEMETRY_BLOCKS)


async def test_telemetry_like_the_venus_driver() -> None:
    # 169 × 0.1 is 16.900000000000002 unrounded.
    model = SIM.VenusModel(5120, 16.9)
    model.write(42000, [SIM.RS485_ENABLE])
    model.write(42010, [2])
    model.write(42021, [1200])
    model.tick()
    direct, by_profile = SimLink(model), SimLink(model)
    expected = await venus_driver(direct).read_telemetry()
    telemetry = await venus_profile_driver(by_profile).read_telemetry()

    assert telemetry.soc_pct == expected.soc_pct
    assert telemetry.power_w == expected.power_w
    assert telemetry.ac_power_w == expected.ac_power_w == -1200
    for key, value in expected.extra.items():
        if key not in ("rs485_control", "force_mode"):
            assert telemetry.extra[key] == value, key
    # The same frames (in another order); the driver also reads its control registers 42000 and 42010.
    assert sorted(by_profile.reads()) == sorted(read for read in direct.reads() if read[0] not in (42000, 42010))


async def test_values_at_rest_like_the_venus_driver() -> None:
    # Integers stay integers (cycles 29, not 29.0) and a power of 0 is not -0.0.
    model = SIM.VenusModel(5120, 18.8)
    model.tick()
    expected = await venus_driver(SimLink(model)).read_telemetry()
    telemetry = await venus_profile_driver(SimLink(model)).read_telemetry()

    assert repr(telemetry.soc_pct) == repr(expected.soc_pct) == "18.8"
    assert repr(telemetry.power_w) == repr(expected.power_w) == "0"
    assert repr(telemetry.ac_power_w) == repr(expected.ac_power_w) == "0"
    for key, value in expected.extra.items():
        if key not in ("rs485_control", "force_mode"):
            assert repr(telemetry.extra[key]) == repr(value), key


async def test_slow_values_are_read_once_a_minute() -> None:
    model = SIM.VenusModel(5120, 63.0)
    link = SimLink(model)
    driver = venus_profile_driver(link)
    first = await driver.read_telemetry()
    link.frames.clear()
    second = await driver.read_telemetry()
    assert (30100, 1) not in link.reads() and (35000, 1) not in link.reads()
    assert second.extra["battery_voltage"] == first.extra["battery_voltage"]
    link.frames.clear()
    await driver.read_telemetry(complete=True)
    assert (30100, 1) in link.reads()


async def test_writes_like_the_venus_driver() -> None:
    commands = [(800, False), (800, False), (-600, False), (-600, True), (0, False), (1500, True)]
    direct, by_profile = SimLink(SIM.VenusModel(5120, 50.0)), SimLink(SIM.VenusModel(5120, 50.0))
    driver, profile = venus_driver(direct), venus_profile_driver(by_profile)
    await driver.connect()
    await profile.connect()
    for power, refresh in commands:
        assert await driver.apply_power(power, refresh=refresh)
        assert await profile.apply_power(power, refresh=refresh)
    assert by_profile.writes() == direct.writes()
    # Read before the first write (range at rest) and after every complete write, like the driver.
    assert sorted(by_profile.reads()) == sorted([*CONTROL_READS, *CONTROL_READS, *CONTROL_READS])
    assert sorted(direct.reads()) == sorted([*CONTROL_READS, *CONTROL_READS])
    assert by_profile.model.registers[42020] == 1500


@pytest.mark.parametrize("state", [ReleaseState.AUTO, ReleaseState.STANDBY])
async def test_release_like_the_venus_driver(state: ReleaseState) -> None:
    direct, by_profile = SimLink(SIM.VenusModel(5120, 50.0)), SimLink(SIM.VenusModel(5120, 50.0))
    driver, profile = venus_driver(direct, state), venus_profile_driver(by_profile, state)
    for battery in (driver, profile):
        await battery.connect()
        assert await battery.apply_power(-900)
        await battery.release_control()
    assert by_profile.writes() == direct.writes()
    expected = SIM.RS485_DISABLE if state is ReleaseState.AUTO else SIM.RS485_ENABLE
    assert by_profile.model.registers[42000] == expected
    assert by_profile.model.registers[42010] == 0


async def test_release_not_confirmed_raises() -> None:
    link = SimLink(SIM.VenusModel(5120, 50.0))
    driver = venus_profile_driver(link)
    await driver.connect()
    assert await driver.apply_power(500)
    link.ignored = {42010}
    with pytest.raises(BatteryDriverError, match="42010 = 1"):
        await driver.release_control()


# --- checks before and after writing ---------------------------------------------


async def test_register_outside_its_range_at_rest_is_not_written() -> None:
    link = SimLink(SIM.VenusModel(5120, 50.0))
    link.model.write(42010, [7])
    driver = venus_profile_driver(link)
    await driver.connect()
    assert not await driver.apply_power(800)
    assert link.writes() == []
    assert driver.issues() == {ISSUE_OUTSIDE_REST: {"registers": "42010 = 7 (0–2)"}}
    # Checked again before the next command; nothing else is read in between.
    link.model.write(42010, [0])
    assert await driver.apply_power(800)
    assert driver.issues() == {}
    link.frames.clear()
    assert await driver.apply_power(900)
    assert link.reads() == []


async def test_unconfirmed_set_point_is_checked_again() -> None:
    link = SimLink(SIM.VenusModel(5120, 50.0))
    driver = venus_profile_driver(link)
    await driver.connect()
    assert await driver.apply_power(800)
    link.ignored = {42020}
    assert not await driver.apply_power(1500, refresh=True)
    link.ignored = set()
    link.frames.clear()
    # The next command reads the ranges at rest first and writes everything again.
    assert await driver.apply_power(1500)
    assert sorted(link.reads()[:3]) == CONTROL_READS
    assert [address for address, _ in link.writes()] == [42000, 42021, 42020, 42010]


async def test_reconnect_checks_the_ranges_again() -> None:
    link = SimLink(SIM.VenusModel(5120, 50.0))
    driver = venus_profile_driver(link)
    await driver.connect()
    assert await driver.apply_power(800)
    await driver.close()
    link.frames.clear()
    assert await driver.apply_power(800)
    assert sorted(link.reads()[:3]) == CONTROL_READS


async def test_silent_battery_is_not_outside_its_range_at_rest() -> None:
    link = SimLink(SIM.VenusModel(5120, 50.0))
    driver = venus_profile_driver(link)
    await driver.connect()
    read = link.model.read
    link.model.read = lambda address, count: None
    assert not await driver.apply_power(800)
    assert link.writes() == []
    assert driver.issues() == {}
    assert not link.connected
    # Answering again, the take-over checks the ranges and writes.
    link.model.read = read
    link.frames.clear()
    assert await driver.apply_power(800)
    assert sorted(link.reads()) == CONTROL_READS


async def test_silent_battery_raises_and_closes() -> None:
    link = SimLink(SIM.VenusModel(5120, 50.0))
    link.model.read = lambda address, count: None
    driver = venus_profile_driver(link)
    with pytest.raises(BatteryDriverError):
        await driver.read_telemetry()
    assert not link.connected


# --- other devices -------------------------------------------------------------

FLASH = """
slems_battery_profile: 1
id: flash
name: Flash battery
limits: {max_charge_w: 2000, max_discharge_w: 2000}
read:
  soc_pct: {address: 100, type: uint16}
  power_ac_w: {address: 101, type: int16, positive: charge}
control:
  type: split
  apply:
    - {address: 200, type: uint16, value: "{charge_w}", at_rest: [0, 2000]}
    - {address: 201, type: uint16, value: "{discharge_w}", at_rest: [0, 2000]}
    - {address: 202, type: uint16, value: "{mode}", modes: {idle: 0, charge: 1, discharge: 2}, at_rest: [0, 2], persistent: true,
       max_changes_per_day: 3}
  release:
    auto:
      - {address: 202, value: 0}
probe: {address: 100, type: uint16, range: [0, 100]}
"""


async def test_persistent_register_warns_and_blocks() -> None:
    link = RegisterLink(Registers({100: 50, 101: 0, 200: 0, 201: 0, 202: 0}))
    driver = ProfileDriver(
        parse_profile(FLASH), link, capacity_wh=5000, max_charge_power_w=2000, max_discharge_power_w=2000
    )
    await driver.connect()
    now = [1000.0]
    with patch.object(profile_driver.time, "monotonic", lambda: now[0]):
        assert await driver.apply_power(500)
        # A keep-alive does not write the flash register again.
        link.frames.clear()
        assert await driver.apply_power(500, refresh=True)
        assert 202 not in [address for address, _ in link.writes()]
        assert driver.issues() == {}
        # Changed within a minute: written with a warning.
        now[0] += 10
        assert await driver.apply_power(-500)
        assert link.model.registers[202] == 2
        assert driver.issues() == {ISSUE_PERSISTENT_FREQUENT: {"registers": "202"}}
        # The fourth change within 24 h is not written.
        now[0] += 120
        assert await driver.apply_power(0)
        now[0] += 120
        assert not await driver.apply_power(600)
        assert link.model.registers[202] == 0
        assert driver.issues()[ISSUE_PERSISTENT_BLOCKED] == {"registers": "202 (3/24 h)"}
        # Written again once the oldest change left the window.
        now[0] = 1000.0 + 24 * 3600
        assert await driver.apply_power(600)
        assert link.model.registers[202] == 1
        assert ISSUE_PERSISTENT_BLOCKED not in driver.issues()


OWN_PV = """
slems_battery_profile: 1
id: own_pv
name: Battery with own PV
limits: {max_charge_w: 2500, max_discharge_w: 2500}
own_pv: {max_output_w: 1200, ac_charge: true}
read:
  soc_pct: {address: 10000, type: uint16}
  pv_w: [{address: 10002, type: uint16}, {address: 10004, type: uint16}]
  connection_w: {address: 10012, type: int16, positive: output}
control:
  type: signed
  setpoint: connection
  positive: discharge
  apply:
    - {address: 10064, type: uint16, value: 3, at_rest: [0, 3]}
    - {address: 10071, value: "{power_w}", type: int32, at_rest: [-2500, 1200]}
  release:
    auto:
      - {address: 10064, value: 0}
probe: {address: 10000, type: uint16, range: [0, 100]}
"""


async def test_own_pv_with_the_set_point_at_the_connection() -> None:
    registers = Registers({10000: 60, 10002: 300, 10003: 0, 10004: 200, 10012: 100, 10064: 0, 10071: 0, 10072: 0})
    link = RegisterLink(registers)
    driver = ProfileDriver(
        parse_profile(OWN_PV), link, capacity_wh=5000, max_charge_power_w=2500, max_discharge_power_w=2500
    )
    assert driver.capabilities.own_pv and driver.capabilities.max_output_w == 1200
    telemetry = await driver.read_telemetry()
    # PV 500 W, output 100 W: the battery charges 400 W.
    assert (telemetry.pv_w, telemetry.output_w, telemetry.input_w) == (500, 100, 0)
    assert telemetry.ac_power_w == telemetry.power_w == 400

    # Charging 300 W from 500 W PV: output 200 W, written as +200 (+discharge), int32 in one frame.
    assert await driver.apply_power(300)
    assert link.writes()[-1] == (10071, [0, 200])
    # Charging 800 W: 300 W input from the grid, written as −300.
    assert await driver.apply_power(800)
    assert link.writes()[-1] == (10071, [0xFFFF, (-300) & 0xFFFF])
    # The output follows the PV.
    assert await driver.apply_power(300)
    assert not driver.needs_write(300)
    registers.registers[10002] = 600
    await driver.read_telemetry()
    assert driver.needs_write(300)
    assert await driver.apply_power(300)
    assert link.writes()[-1] == (10071, [0, 500])
    assert not driver.needs_write(300)


# --- Home Assistant's Modbus backend ---------------------------------------------


class FakeUnit:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.fail = False
        self.refuse = False
        self.reads = 0

    def set_message_spacing(self, seconds: float) -> None:
        self.calls.append(("spacing", seconds))

    def require_timeout(self, seconds: float) -> None:
        self.calls.append(("timeout", seconds))

    async def disconnect(self) -> None:
        self.calls.append(("disconnect",))

    async def read_holding_registers(self, address: int, count: int) -> list[int]:
        self.reads += 1
        if self.fail:
            raise ModbusTimeoutError("no answer")
        if self.refuse:
            raise IllegalDataAddressError()
        return [address] * count

    async def read_input_registers(self, address: int, count: int) -> list[int]:
        return [-address] * count

    async def write_register(self, address: int, value: int) -> None:
        self.calls.append(("write", address, value))

    async def write_registers(self, address: int, values: list[int]) -> None:
        self.calls.append(("write", address, values))


async def test_home_assistant_unit_transport() -> None:
    unit = FakeUnit()
    transport = HaUnitTransport(lambda: unit, message_spacing_s=0.15, timeout_s=3.0)
    assert not transport.connected
    assert await transport.read(1, 1) is None
    assert await transport.connect()
    assert unit.calls == [("spacing", 0.15), ("timeout", 3.0)]
    assert await transport.read(7, 2) == [7, 7]
    assert await transport.read(7, 1, input_registers=True) == [-7]
    assert await transport.write(9, 1) and await transport.write(9, [1, 2])
    unit.fail = True
    assert await transport.read(7, 1) is None
    await transport.close()
    assert unit.calls[-1] == ("disconnect",) and not transport.connected


async def test_home_assistant_unit_transport_drops_a_silent_connection() -> None:
    unit = FakeUnit()
    transport = HaUnitTransport(lambda: unit, message_spacing_s=0.15, timeout_s=3.0)
    assert await transport.connect()
    # A refusal is an answer: the connection stays.
    unit.refuse = True
    assert await transport.read(7, 1) is None
    assert transport.connected and ("disconnect",) not in unit.calls
    unit.refuse = False
    # No answer: dropped, the remaining requests fail without asking the device.
    unit.fail = True
    assert await transport.read(7, 1) is None
    assert unit.calls[-1] == ("disconnect",) and not transport.connected
    unit.fail = False
    assert await transport.read(7, 1) is None
    assert not await transport.write(9, 1)
    assert unit.reads == 2
    # Used again only after the device freed its connection.
    waits = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    with patch.object(profile_driver.asyncio, "sleep", sleep):
        assert await transport.connect()
    assert waits and 0 < waits[0] <= profile_driver.RECONNECT_SETTLE_S
    assert await transport.read(7, 1) == [7]


class DroppingLink(SimLink):
    """A transport that cannot read a refusal: a refused read drops the connection
    (like an exception frame of the Venus v3 over Home Assistant's backend)."""

    async def read(self, address: int, count: int, *, input_registers: bool = False) -> list[int] | None:
        if not self.connected:
            return None
        words = await super().read(address, count, input_registers=input_registers)
        if words is None:
            self.connected = False
        return words


async def test_block_refused_without_an_answer_is_read_one_by_one() -> None:
    link = DroppingLink(SIM.VenusModel(5120, 50.0))
    # A device that refuses 30001-30006 (30003 unassigned).
    del link.model.registers[30003]
    driver = venus_profile_driver(link)
    with pytest.raises(BatteryDriverError):
        await driver.read_telemetry()
    # The next read reads its registers one by one, which proves the refusal.
    telemetry = await driver.read_telemetry()
    assert telemetry.soc_pct == 50.0 and telemetry.ac_power_w == 0
    link.frames.clear()
    await driver.read_telemetry()
    assert ("read", 30001, 6) not in link.frames
    assert ("read", 30001, 1) in link.frames and ("read", 30006, 1) in link.frames
    # Only the block that dropped the connection was read one by one.
    assert ("read", 37007, 2) in link.frames


async def test_block_failing_while_the_device_was_silent_is_not_refused() -> None:
    link = DroppingLink(SIM.VenusModel(5120, 50.0))
    driver = venus_profile_driver(link)
    await driver.read_telemetry()
    # Silent from the first block on: it drops the connection.
    read = link.model.read
    link.model.read = lambda address, count: None
    with pytest.raises(BatteryDriverError):
        await driver.read_telemetry()
    link.model.read = read
    # Answering again: read one by one and asked once more, the block answers.
    await driver.read_telemetry()
    link.frames.clear()
    await driver.read_telemetry()
    assert ("read", 30001, 6) in link.frames and ("read", 30001, 1) not in link.frames
