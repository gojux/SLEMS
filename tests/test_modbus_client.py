"""Tests for Modbus register decoding, the v3 frame fix and the link."""

import asyncio
import struct
import time

import pytest
from pymodbus.exceptions import ModbusIOException

from custom_components.slems.drivers import modbus_client
from custom_components.slems.drivers.modbus_client import (
    ModbusTcpLink,
    decode_registers,
    fix_v3_exception_frame,
)


def test_decode_signed_and_unsigned() -> None:
    assert decode_registers([0xFFFF], "int16") == -1
    assert decode_registers([0xFFFF], "uint16") == 0xFFFF
    assert decode_registers([0x0001, 0x0000], "uint32") == 65536
    assert decode_registers([0xFFFF, 0xFFFE], "int32") == -2
    assert decode_registers([0x0001], "int32") is None


def test_decode_char() -> None:
    assert decode_registers([0x5645, 0x4E55, 0x5300], "char") == "VENUS"


def test_v3_exception_frame_is_patched() -> None:
    broken = bytes([0, 1, 0, 0, 0, 4, 1, 0x83, 2])
    assert fix_v3_exception_frame(False, broken)[5] == 3
    # Outgoing frames and regular responses stay untouched.
    assert fix_v3_exception_frame(True, broken) == broken
    regular = bytes([0, 1, 0, 0, 0, 4, 1, 0x03, 2])
    assert fix_v3_exception_frame(False, regular) == regular


async def test_the_link_reads_on_after_a_v3_exception_frame() -> None:
    """With the real pymodbus client: a refused register is an answer at once,
    the connection stays and the next read gets its value."""

    async def battery(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        refused = False
        while True:
            try:
                transaction_id, _, length, unit_id = struct.unpack(">HHHB", await reader.readexactly(7))
                await reader.readexactly(length - 1)
            except asyncio.IncompleteReadError:
                return
            if not refused:
                refused = True
                # Like the v3 firmware: MBAP length 4 for the 3 bytes of an exception.
                writer.write(struct.pack(">HHHB", transaction_id, 0, 4, unit_id) + bytes([0x83, 2]))
            else:
                writer.write(struct.pack(">HHHBBBH", transaction_id, 0, 5, unit_id, 0x03, 2, 630))
            await writer.drain()

    server = await asyncio.start_server(battery, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    link = ModbusTcpLink("127.0.0.1", port, 1, message_wait_s=0.0, timeout_s=1.0, patch_v3_frames=True)
    try:
        assert await link.connect()
        started = time.monotonic()
        assert await link.read(35010, 1) is None
        assert time.monotonic() - started < 0.5
        assert link.connected
        assert await link.read(34002, 1) == [630]
    finally:
        await link.close()
        server.close()
        await server.wait_closed()


class FakeResult:
    def __init__(self, registers: list[int], error: bool = False) -> None:
        self.registers = registers
        self._error = error

    def isError(self) -> bool:  # noqa: N802 (pymodbus API)
        return self._error


class FakeClient:
    """Stands in for AsyncModbusTcpClient; ``mode`` is shared by all instances."""

    mode = "answer"
    created = 0
    requests = 0

    def __init__(self, **_kwargs: object) -> None:
        FakeClient.created += 1
        self.connected = False

    async def connect(self) -> bool:
        self.connected = FakeClient.mode != "refuse"
        return self.connected

    def close(self) -> None:
        self.connected = False

    async def read_holding_registers(self, address: int, count: int, device_id: int) -> FakeResult:
        FakeClient.requests += 1
        if FakeClient.mode == "silent":
            raise ModbusIOException("no response")
        if FakeClient.mode == "exception":
            return FakeResult([], error=True)
        return FakeResult([address] * count)

    async def write_register(self, address: int, value: int, device_id: int) -> FakeResult:
        FakeClient.requests += 1
        if FakeClient.mode == "silent":
            raise ModbusIOException("no response")
        return FakeResult([])


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    clock = Clock()
    FakeClient.mode, FakeClient.created, FakeClient.requests = "answer", 0, 0
    monkeypatch.setattr(modbus_client, "AsyncModbusTcpClient", FakeClient)
    monkeypatch.setattr(modbus_client.time, "monotonic", clock.monotonic)

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(modbus_client.asyncio, "sleep", no_sleep)
    return clock


def make_link() -> ModbusTcpLink:
    return ModbusTcpLink("host", 502, 1, message_wait_s=0.15, timeout_s=3.0, patch_v3_frames=True)


async def test_a_request_without_answer_drops_the_connection(clock: Clock) -> None:
    link = make_link()
    assert await link.connect()
    FakeClient.mode = "silent"
    assert await link.read(100, 1) is None
    assert not link.connected
    # The rest of a read fails at once instead of waiting for each timeout.
    assert await link.read(101, 1) is None
    assert await link.write(200, 1) is False
    assert FakeClient.requests == 1


async def test_connections_without_answer_back_off(clock: Clock) -> None:
    link = make_link()
    FakeClient.mode = "silent"
    for wait in modbus_client.BACKOFF_S + (modbus_client.BACKOFF_S[-1],):
        # A TCP accept alone does not prove the battery is back.
        assert await link.connect()
        assert await link.read(100, 1) is None
        assert link.retry_in_s == wait
        assert not await link.connect()
        clock.now += wait
    created = FakeClient.created
    assert await link.connect()
    assert FakeClient.created == created + 1


async def test_an_answer_ends_the_back_off(clock: Clock) -> None:
    link = make_link()
    FakeClient.mode = "refuse"
    assert not await link.connect()
    assert link.retry_in_s == modbus_client.BACKOFF_S[0]
    clock.now += modbus_client.BACKOFF_S[0]
    FakeClient.mode = "exception"
    assert await link.connect()
    # An exception response (e.g. a refused block) is an answer as well.
    assert await link.read(100, 2) is None
    assert link.connected
    FakeClient.mode = "silent"
    assert await link.read(100, 1) is None
    # Only the settle time of the TCP slot: the battery answered before.
    assert link.retry_in_s == pytest.approx(1.0)
    assert await link.connect()
