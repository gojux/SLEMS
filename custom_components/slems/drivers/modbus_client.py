"""Thin async Modbus TCP client tuned for Marstek Venus firmware quirks.

Handled quirks (based on the Omnibattery project,
https://github.com/ffunes/Omnibattery, GPL-3.0):

* The Venus E v3 firmware answers exception responses with a wrong MBAP length
  byte (4 instead of 3); the packet is patched before pymodbus parses it.
* The v3 MCU needs ~150 ms between frames or it stops answering.
* The battery only has a single TCP connection slot and releases it slowly, so
  reconnects wait before opening a new socket and never happen per request.
* Retries are done inside pymodbus (same transaction id) so late replies from a
  stalled MCU are still matched.
* Some firmware accepts the TCP connection and then ignores every frame. A
  request without an answer therefore drops the connection at once (the
  remaining requests of a read fail without waiting), and only an answer
  proves a connection: until one arrives, reconnects wait ``BACKOFF_S``.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

_LOGGER = logging.getLogger(__name__)

_PYMODBUS_RETRIES = 2
_RECONNECT_SETTLE_S = 1.0
# Wait before the next connect after connections without an answer (s), by
# the number of such connections in a row.
BACKOFF_S = (5.0, 10.0, 20.0, 40.0, 60.0)


def fix_v3_exception_frame(sending: bool, data: bytes) -> bytes:
    """Patch malformed Modbus exception responses sent by Venus v3 firmware."""
    if not sending and len(data) == 9 and data[5] == 4 and (data[7] & 0x80) == 0x80:
        return data[0:5] + b"\x03" + data[6:]
    return data


def decode_registers(words: list[int], data_type: str) -> int | str | None:
    """Decode raw register words into a typed value."""
    if not words:
        return None
    if data_type == "uint16":
        return words[0]
    if data_type == "int16":
        return words[0] - 0x10000 if words[0] >= 0x8000 else words[0]
    if data_type in ("uint32", "int32"):
        if len(words) < 2:
            return None
        value = (words[0] << 16) | words[1]
        if data_type == "int32" and value >= 0x80000000:
            value -= 0x100000000
        return value
    if data_type == "char":
        raw = bytearray()
        for word in words:
            raw += bytes(((word >> 8) & 0xFF, word & 0xFF))
        text = raw.decode("ascii", errors="ignore").rstrip("\x00")
        return "".join(char for char in text if char.isprintable())
    raise ValueError(f"Unsupported data type: {data_type}")


def register_count(data_type: str) -> int:
    """Return how many 16 bit registers a (non-char) data type occupies."""
    return 2 if data_type in ("uint32", "int32") else 1


class ModbusTcpLink:
    """Serialized Modbus TCP link with inter-frame pacing."""

    def __init__(
        self,
        host: str,
        port: int,
        unit_id: int,
        *,
        message_wait_s: float,
        timeout_s: float,
        patch_v3_frames: bool,
    ) -> None:
        self._host = host
        self._port = port
        self._unit_id = unit_id
        self._message_wait_s = message_wait_s
        self._timeout_s = timeout_s
        # Outer safety net: must cover all pymodbus internal attempts.
        self._request_timeout_s = timeout_s * (_PYMODBUS_RETRIES + 1) + 2
        self._patch_v3_frames = patch_v3_frames
        self._client: AsyncModbusTcpClient | None = None
        self._lock = asyncio.Lock()
        self._unit_kwarg = "device_id"
        # Whether the battery answered on the current connection, connections
        # in a row without an answer and the earliest next connect (monotonic).
        self._answered = False
        self._silent_count = 0
        self._connect_at = 0.0

    @property
    def connected(self) -> bool:
        """Return True if the socket is open."""
        return self._client is not None and self._client.connected

    @property
    def retry_in_s(self) -> float:
        """Seconds until the next connect is allowed (0: now)."""
        return max(0.0, self._connect_at - time.monotonic())

    async def connect(self) -> bool:
        """Open a fresh connection, releasing any previous socket first.

        Returns False without trying while connections stay unanswered (see
        ``retry_in_s``); after a closed socket waits until its slot is free.
        """
        async with self._lock:
            if self._client is not None:
                self._drop()
            wait = self.retry_in_s
            if wait > _RECONNECT_SETTLE_S:
                return False
            if wait > 0:
                await asyncio.sleep(wait)
            client = AsyncModbusTcpClient(
                host=self._host,
                port=self._port,
                timeout=self._timeout_s,
                retries=_PYMODBUS_RETRIES,
                reconnect_delay=0,
                reconnect_delay_max=0,
            )
            if self._patch_v3_frames:
                client.trace_packet = fix_v3_exception_frame
            self._unit_kwarg = _detect_unit_kwarg(client)
            self._client = client
            if not await client.connect():
                self._drop()
                return False
            return True

    async def close(self) -> None:
        """Close the connection."""
        async with self._lock:
            if self._client is not None:
                self._drop()

    def _drop(self) -> None:
        """Close the socket; without an answer on it the next connect waits."""
        if self._client is not None:
            self._client.close()
            self._client = None
        if self._answered:
            self._silent_count = 0
            # The single TCP slot is not freed instantly on close.
            self._connect_at = time.monotonic() + _RECONNECT_SETTLE_S
        else:
            self._silent_count += 1
            wait = BACKOFF_S[min(self._silent_count, len(BACKOFF_S)) - 1]
            self._connect_at = time.monotonic() + wait
            _LOGGER.debug(
                "No answer from %s:%d, next connect in %.0f s", self._host, self._port, wait
            )
        self._answered = False

    async def read(self, address: int, count: int, *, input_registers: bool = False) -> list[int] | None:
        """Read holding (or input) registers; return raw words or None on failure."""
        async with self._lock:
            if self._client is None:
                return None
            method = self._client.read_input_registers if input_registers else self._client.read_holding_registers
            try:
                result = await asyncio.wait_for(
                    method(address=address, count=count, **{self._unit_kwarg: self._unit_id}),
                    timeout=self._request_timeout_s,
                )
            except (ModbusException, TimeoutError) as err:
                _LOGGER.debug("Read of register %d failed: %s", address, err)
                self._drop()
                return None
            finally:
                await asyncio.sleep(self._message_wait_s)
            # Also an exception response (e.g. a refused block) is an answer.
            self._answered = True
            if result.isError() or len(result.registers) < count:
                _LOGGER.debug("Read of register %d returned %s", address, result)
                return None
            return list(result.registers)

    async def write(self, address: int, value: int | list[int]) -> bool:
        """Write a single holding register, or several in one frame (a list)."""
        async with self._lock:
            if self._client is None:
                return False
            if isinstance(value, list):
                request = self._client.write_registers(
                    address=address, values=value, **{self._unit_kwarg: self._unit_id}
                )
            else:
                request = self._client.write_register(
                    address=address, value=value, **{self._unit_kwarg: self._unit_id}
                )
            try:
                result = await asyncio.wait_for(request, timeout=self._request_timeout_s)
            except (ModbusException, TimeoutError) as err:
                _LOGGER.debug("Write of register %d failed: %s", address, err)
                self._drop()
                return False
            finally:
                await asyncio.sleep(self._message_wait_s)
            self._answered = True
            return not result.isError()


def _detect_unit_kwarg(client: AsyncModbusTcpClient) -> str:
    """Return the keyword pymodbus uses for the unit id ("slave" before 3.9)."""
    params = inspect.signature(client.read_holding_registers).parameters
    return "device_id" if "device_id" in params else "slave"
