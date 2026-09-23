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
"""

from __future__ import annotations

import asyncio
import inspect
import logging

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

_LOGGER = logging.getLogger(__name__)

_PYMODBUS_RETRIES = 2
_RECONNECT_SETTLE_S = 1.0


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

    @property
    def connected(self) -> bool:
        """Return True if the socket is open."""
        return self._client is not None and self._client.connected

    async def connect(self) -> bool:
        """Open a fresh connection, releasing any previous socket first."""
        async with self._lock:
            if self._client is not None:
                self._client.close()
                self._client = None
                # The single TCP slot is not freed instantly on close.
                await asyncio.sleep(_RECONNECT_SETTLE_S)
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
            if not await client.connect():
                client.close()
                return False
            self._client = client
            return True

    async def close(self) -> None:
        """Close the connection."""
        async with self._lock:
            if self._client is not None:
                self._client.close()
                self._client = None

    async def read(self, address: int, count: int) -> list[int] | None:
        """Read holding registers; return raw words or None on failure."""
        async with self._lock:
            if self._client is None:
                return None
            try:
                result = await asyncio.wait_for(
                    self._client.read_holding_registers(
                        address=address, count=count, **{self._unit_kwarg: self._unit_id}
                    ),
                    timeout=self._request_timeout_s,
                )
            except (ModbusException, TimeoutError) as err:
                _LOGGER.debug("Read of register %d failed: %s", address, err)
                return None
            finally:
                await asyncio.sleep(self._message_wait_s)
            if result.isError() or len(result.registers) < count:
                _LOGGER.debug("Read of register %d returned %s", address, result)
                return None
            return list(result.registers)

    async def write(self, address: int, value: int) -> bool:
        """Write a single holding register."""
        async with self._lock:
            if self._client is None:
                return False
            try:
                result = await asyncio.wait_for(
                    self._client.write_register(
                        address=address, value=value, **{self._unit_kwarg: self._unit_id}
                    ),
                    timeout=self._request_timeout_s,
                )
            except (ModbusException, TimeoutError) as err:
                _LOGGER.debug("Write of register %d failed: %s", address, err)
                return False
            finally:
                await asyncio.sleep(self._message_wait_s)
            return not result.isError()


def _detect_unit_kwarg(client: AsyncModbusTcpClient) -> str:
    """Return the keyword pymodbus uses for the unit id ("slave" before 3.9)."""
    params = inspect.signature(client.read_holding_registers).parameters
    return "device_id" if "device_id" in params else "slave"
