"""Tests for the requests to a unit of Home Assistant's Modbus backend.

``MockModbusUnit`` of modbus-connection does not model reconnects or timeouts,
so a fake unit raises a given sequence of errors and counts ``disconnect()``.
"""

from unittest.mock import patch

from modbus_connection import (
    ClientClosedError,
    IllegalDataAddressError,
    ModbusConnectionError,
    ModbusDesyncError,
    ModbusTimeoutError,
)
import pytest

from custom_components.slems import ha_modbus


class SequenceUnit:
    """Raises the given outcomes in turn (an exception, or a value to return)."""

    def __init__(self, *outcomes, disconnect_error: Exception | None = None) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[tuple] = []
        self.disconnects = 0
        self.disconnect_error = disconnect_error

    async def disconnect(self) -> None:
        self.disconnects += 1
        if self.disconnect_error is not None:
            raise self.disconnect_error

    async def _next(self, call: tuple):
        self.requests.append(call)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def read_holding_registers(self, address: int, count: int) -> list[int]:
        return await self._next(("read", address, count))

    async def write_register(self, address: int, value: int) -> None:
        await self._next(("write", address, value))


@pytest.fixture(autouse=True)
def waits():
    """The waits between attempts, without waiting."""
    seconds: list[float] = []

    async def sleep(wait: float) -> None:
        seconds.append(wait)

    with patch.object(ha_modbus.asyncio, "sleep", sleep):
        yield seconds


def read(unit: SequenceUnit):
    return ha_modbus.request(unit, lambda: unit.read_holding_registers(7, 2), "test read")


async def test_timeout_then_success_drops_once(waits) -> None:
    # The failed request is not repeated: the next poll is the retry, on a new connection.
    unit = SequenceUnit(ModbusTimeoutError("no answer"), [1, 2])
    with pytest.raises(ModbusTimeoutError):
        await read(unit)
    assert await read(unit) == [1, 2]
    assert unit.disconnects == 1 and waits == []


async def test_three_timeouts_fail_and_drop_each_time() -> None:
    unit = SequenceUnit(*(ModbusTimeoutError("no answer") for _ in range(3)))
    for _ in range(3):
        with pytest.raises(ModbusTimeoutError):
            await read(unit)
    assert unit.disconnects == 3


async def test_late_answer_drops_like_a_timeout() -> None:
    unit = SequenceUnit(ModbusDesyncError("transaction id 4 instead of 5"))
    with pytest.raises(ModbusDesyncError):
        await read(unit)
    assert unit.disconnects == 1


async def test_timeouts_then_lost_connection_then_success(waits) -> None:
    unit = SequenceUnit(
        ModbusTimeoutError("no answer"), ModbusTimeoutError("no answer"),
        ModbusConnectionError("Connection lost before response was received"), [5, 6],
    )
    for _ in range(2):
        with pytest.raises(ModbusTimeoutError):
            await read(unit)
    assert await read(unit) == [5, 6]
    assert unit.disconnects == 2 and waits == [ha_modbus.RETRY_WAIT_S]


async def test_lost_connection_is_asked_again_without_dropping(waits) -> None:
    unit = SequenceUnit(ModbusConnectionError("lost"), ModbusConnectionError("lost"), [3, 4])
    assert await read(unit) == [3, 4]
    assert unit.disconnects == 0 and len(unit.requests) == 3
    assert waits == [ha_modbus.RETRY_WAIT_S] * 2


async def test_three_lost_connections_fail_without_dropping() -> None:
    unit = SequenceUnit(*(ModbusConnectionError("lost") for _ in range(3)))
    with pytest.raises(ModbusConnectionError):
        await read(unit)
    assert unit.disconnects == 0 and len(unit.requests) == 3


async def test_closed_client_is_final() -> None:
    unit = SequenceUnit(ClientClosedError("connection is closed"))
    with pytest.raises(ClientClosedError):
        await read(unit)
    assert unit.disconnects == 0 and len(unit.requests) == 1


async def test_exception_answer_is_final_without_dropping() -> None:
    unit = SequenceUnit(IllegalDataAddressError())
    with pytest.raises(IllegalDataAddressError):
        await read(unit)
    assert unit.disconnects == 0 and len(unit.requests) == 1


async def test_failing_disconnect_is_ignored() -> None:
    unit = SequenceUnit(ModbusTimeoutError("no answer"), disconnect_error=ModbusConnectionError("already gone"))
    with pytest.raises(ModbusTimeoutError):
        await read(unit)
    assert unit.disconnects == 1


async def test_write_is_asked_again_on_a_lost_connection() -> None:
    unit = SequenceUnit(ModbusConnectionError("lost"), None)
    await ha_modbus.request(unit, lambda: unit.write_register(9, 100), "test write")
    assert unit.requests == [("write", 9, 100), ("write", 9, 100)]


async def test_write_with_timeout_drops_and_the_next_write_succeeds() -> None:
    unit = SequenceUnit(ModbusTimeoutError("no answer"), None)
    with pytest.raises(ModbusTimeoutError):
        await ha_modbus.request(unit, lambda: unit.write_register(9, 100), "test write")
    await ha_modbus.request(unit, lambda: unit.write_register(9, 100), "test write")
    assert unit.disconnects == 1 and len(unit.requests) == 2


async def test_holding_reader() -> None:
    unit = SequenceUnit(ModbusConnectionError("lost"), [8])
    assert await ha_modbus.holding_reader(unit, "Grid meter")(40100, 1) == [8]
    assert unit.requests == [("read", 40100, 1)] * 2
