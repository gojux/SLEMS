"""Minimal Modbus TCP simulator of a Marstek Venus E 3.0 for development.

Plain asyncio without dependencies. It implements the function codes the
driver uses (3 = read holding registers, 6 = write single register,
16 = write multiple registers) and a simple battery model. In RS485 control
mode the force mode and set points drive the AC power; the DC battery power
differs by the conversion losses (15 W + 4 % + 1e-5 * P², like the SLEMS default
model); the state of charge is integrated once per second.

Firmware quirks of a real Venus E 3.0 (EMS 148, measured 2026-10-23 with
``tools/venus_quirks.py``):

* only one TCP connection at a time: the port does not listen while it is
  taken and ``RECONNECT_BLOCK_S`` after it closed (connection refused),
* one answer every ``FRAME_S`` per connection: a request sent sooner waits,
* reads that touch an unassigned register (e.g. 42000-42010) and writes
  outside the control registers are refused with exception 2,
* exception responses declare an MBAP length of 4 for their 3 bytes,
* switched off (``SIM_OFF_FILE``), the connection is accepted but nothing is
  answered and no power flows.

A second, read-only port (``SIM_TAP_PORT``) serves the same registers to any
number of clients, without the connection and timing quirks; the dev instance
reads the AC power there every second to build a realistic smart meter.

Cell voltages follow an LFP curve. One cell is ahead by
``SIM_CELL_OFFSET_PCT`` of SoC, so the cell delta is tiny in the middle and
large near the top. Above 3.45 V the BMS bleeds the high cell
(``SIM_BLEED_PCT_PER_MIN``, much faster than reality to test balancing) and
refuses charging while the highest cell is at 3.65 V.

Environment variables:
    SIM_PORT          TCP port (default 502)
    SIM_CELL_OFFSET_PCT  SoC lead of the highest cell (default 2.0)
    SIM_BLEED_PCT_PER_MIN  balancing speed of the BMS (default 0.2)
    SIM_TAP_PORT      read-only TCP port (default 5020)
    SIM_OTA_FILE      while this file exists, the battery reports a firmware
                      update (state 5; default /tmp/venus_ota)
    SIM_FAULT_FILE    while this file exists, commands are accepted but no power
                      flows (test of the non-responding detection; default
                      /tmp/venus_fault, e.g. ``docker compose exec venus-sim-1
                      touch /tmp/venus_fault``)
    SIM_OFF_FILE      while this file exists, the battery is switched off: the
                      control port accepts the connection but answers nothing
                      (default /tmp/venus_off)
    SIM_CAPACITY_WH   usable capacity (default 5120)
    SIM_INITIAL_SOC   initial state of charge in % (default 50)
"""

from __future__ import annotations

import asyncio
import logging
import os
import struct

_LOGGER = logging.getLogger("venus_sim")

REG_BATTERY_POWER = 30001
REG_AC_POWER = 30006
REG_BATTERY_VOLTAGE = 30100
REG_TOTAL_CHARGING_ENERGY = 33000
REG_TOTAL_DISCHARGING_ENERGY = 33002
REG_INTERNAL_TEMPERATURE = 35000
REG_INVERTER_STATE = 35100
FAULT_FILE = os.environ.get("SIM_FAULT_FILE", "/tmp/venus_fault")
OFF_FILE = os.environ.get("SIM_OFF_FILE", "/tmp/venus_off")
OTA_FILE = os.environ.get("SIM_OTA_FILE", "/tmp/venus_ota")
# Static device information (register -> words), as a Venus E 3.0 reports it.
DEVICE_INFO = {
    31000: "VNSE3-SIM",  # device name, 10 registers
    30350: "V1.0.9",  # communication module firmware, 6 registers
    30304: "AABBCCDDEEFF",  # MAC address, 6 registers
}
DEVICE_VERSIONS = {30200: 148, 30202: 125, 30204: 214}  # EMS, VMS, BMS
REG_BATTERY_SOC = 37005
REG_BATTERY_SOC_FINE = 34002
REG_CYCLE_COUNT = 34003
REG_MAX_CELL_VOLTAGE = 37007
REG_MIN_CELL_VOLTAGE = 37008
REG_RS485_CONTROL = 42000
REG_FORCE_MODE = 42010
REG_SET_CHARGE_POWER = 42020
REG_SET_DISCHARGE_POWER = 42021

MAX_POWER_W = 2500
RS485_ENABLE = 0x55AA
RS485_DISABLE = 0x55BB
# Registers a client may write (control registers, highest charge/discharge power).
WRITABLE = frozenset({REG_RS485_CONTROL, REG_FORCE_MODE, REG_SET_CHARGE_POWER, REG_SET_DISCHARGE_POWER, 44002, 44003})
# Assigned registers the model does not compute (30002-30005 lie in a block a
# real Venus answers in one frame).
STATIC_REGISTERS = {
    REG_RS485_CONTROL: RS485_DISABLE, REG_FORCE_MODE: 0, REG_SET_CHARGE_POWER: 0, REG_SET_DISCHARGE_POWER: 0,
    30002: 0, 30003: 0, 30004: 0, 30005: 0, 44002: MAX_POWER_W, 44003: MAX_POWER_W,
}
FRAME_S = 0.15
RECONNECT_BLOCK_S = 0.5
TICK_S = 1.0


def lfp_cell_voltage(soc: float) -> float:
    """Open circuit voltage of an LFP cell at a state of charge (%)."""
    points = ((0, 2.90), (10, 3.20), (90, 3.30), (97, 3.45), (99, 3.55), (100, 3.65))
    soc = max(0.0, min(100.0, soc))
    for (s0, v0), (s1, v1) in zip(points, points[1:]):
        if soc <= s1:
            return v0 + (v1 - v0) * (soc - s0) / (s1 - s0)
    return points[-1][1]


def conversion_loss(ac_power: float) -> float:
    power = abs(ac_power)
    return 0.0 if power == 0 else 15 + 0.04 * power + 1e-5 * power**2

EXC_ILLEGAL_FUNCTION = 1
EXC_ILLEGAL_ADDRESS = 2


class VenusModel:
    """Register store plus battery physics."""

    def __init__(self, capacity_wh: float, soc: float) -> None:
        self.registers: dict[int, int] = dict(STATIC_REGISTERS)
        self._cell_offset = float(os.environ.get("SIM_CELL_OFFSET_PCT", "2.0"))
        self._bleed_per_tick = float(os.environ.get("SIM_BLEED_PCT_PER_MIN", "0.2")) * TICK_S / 60
        self._capacity_wh = capacity_wh
        self._energy_wh = capacity_wh * soc / 100
        self._charged_wh = 0.0
        self._discharged_wh = 0.0
        self.tick()

    def read(self, address: int, count: int) -> list[int] | None:
        """Register values; None if one of them is unassigned (refused)."""
        addresses = range(address, address + count)
        if any(a not in self.registers for a in addresses):
            return None
        return [self.registers[a] for a in addresses]

    def write(self, address: int, values: list[int]) -> None:
        for offset, value in enumerate(values):
            self.registers[address + offset] = value & 0xFFFF

    def tick(self) -> None:
        power = 0
        if self.registers.get(REG_RS485_CONTROL) == RS485_ENABLE and not os.path.exists(OFF_FILE):
            force_mode = self.registers.get(REG_FORCE_MODE, 0)
            if force_mode == 1:
                power = min(self.registers.get(REG_SET_CHARGE_POWER, 0), MAX_POWER_W)
            elif force_mode == 2:
                power = -min(self.registers.get(REG_SET_DISCHARGE_POWER, 0), MAX_POWER_W)
        if os.path.exists(FAULT_FILE):
            power = 0

        soc_now = self._energy_wh / self._capacity_wh * 100
        high_cell = lfp_cell_voltage(soc_now + self._cell_offset)
        # BMS: no charging while the highest cell is at its limit.
        if (power > 0 and (self._energy_wh >= self._capacity_wh or high_cell >= 3.65)) or (
            power < 0 and self._energy_wh <= 0
        ):
            power = 0
        if high_cell > 3.45:
            self._cell_offset = max(0.0, self._cell_offset - self._bleed_per_tick)

        # power is the AC power (+charge); the DC side gets the losses less
        # when charging and delivers them additionally when discharging.
        dc_power = power - conversion_loss(power)
        if power > 0:
            self._charged_wh += power * TICK_S / 3600
        else:
            self._discharged_wh -= power * TICK_S / 3600
        self._energy_wh += dc_power * TICK_S / 3600
        self._energy_wh = max(0.0, min(self._capacity_wh, self._energy_wh))
        soc = self._energy_wh / self._capacity_wh * 100

        self.write(REG_BATTERY_POWER, [round(dc_power)])
        self.write(REG_AC_POWER, [-power])
        self.write(REG_BATTERY_VOLTAGE, [int((48 + soc * 0.06) * 100)])
        self.write(REG_INTERNAL_TEMPERATURE, [250 + abs(power) // 100])
        state = 5 if os.path.exists(OTA_FILE) else 2 if power > 0 else 3 if power < 0 else 1
        self.write(REG_INVERTER_STATE, [state])
        for address, text in DEVICE_INFO.items():
            self.write(address, _chars(text, 10 if address == 31000 else 6))
        for address, version in DEVICE_VERSIONS.items():
            self.write(address, [version])
        self.write(REG_BATTERY_SOC, [round(soc)])
        self.write(REG_BATTERY_SOC_FINE, [round(soc * 10)])
        self.write(REG_CYCLE_COUNT, [int(self._charged_wh / self._capacity_wh)])
        charge_lift = 0.02 if power > 0 else (-0.02 if power < 0 else 0.0)
        self.write(REG_MAX_CELL_VOLTAGE, [round((lfp_cell_voltage(soc + self._cell_offset) + charge_lift) * 1000)])
        self.write(REG_MIN_CELL_VOLTAGE, [round((lfp_cell_voltage(soc) + charge_lift) * 1000)])
        self.write(REG_TOTAL_CHARGING_ENERGY, _split_32(int(self._charged_wh / 10)))
        self.write(REG_TOTAL_DISCHARGING_ENERGY, _split_32(int(self._discharged_wh / 10)))


def _chars(text: str, registers: int) -> list[int]:
    raw = text.encode("ascii").ljust(registers * 2, b"\x00")
    return [(raw[i] << 8) | raw[i + 1] for i in range(0, registers * 2, 2)]


def _split_32(value: int) -> list[int]:
    return [(value >> 16) & 0xFFFF, value & 0xFFFF]


def handle_pdu(model: VenusModel, pdu: bytes, read_only: bool = False) -> bytes:
    """Process one request PDU and return the response PDU."""
    function = pdu[0]
    if read_only and function != 3:
        return bytes((function | 0x80, EXC_ILLEGAL_FUNCTION))
    try:
        if function == 3:
            address, count = struct.unpack(">HH", pdu[1:5])
            if not 1 <= count <= 125:
                return bytes((function | 0x80, EXC_ILLEGAL_ADDRESS))
            values = model.read(address, count)
            if values is None:
                return bytes((function | 0x80, EXC_ILLEGAL_ADDRESS))
            return bytes((function, count * 2)) + struct.pack(f">{count}H", *values)
        if function == 6:
            address, value = struct.unpack(">HH", pdu[1:5])
            if address not in WRITABLE:
                return bytes((function | 0x80, EXC_ILLEGAL_ADDRESS))
            model.write(address, [value])
            return pdu[:5]
        if function == 16:
            address, count, _byte_count = struct.unpack(">HHB", pdu[1:6])
            values = list(struct.unpack(f">{count}H", pdu[6 : 6 + count * 2]))
            if any(address + i not in WRITABLE for i in range(count)):
                return bytes((function | 0x80, EXC_ILLEGAL_ADDRESS))
            model.write(address, values)
            return pdu[:5]
    except struct.error:
        return bytes((function | 0x80, EXC_ILLEGAL_ADDRESS))
    return bytes((function | 0x80, EXC_ILLEGAL_FUNCTION))


def frame(transaction_id: int, protocol_id: int, unit_id: int, response: bytes) -> bytes:
    """Modbus TCP answer; exception responses declare one byte too many like the v3 firmware."""
    length = len(response) + 1 + (1 if response[0] & 0x80 else 0)
    return struct.pack(">HHHB", transaction_id, protocol_id, length, unit_id) + response


class ModbusServer:
    """Modbus TCP server; the control port behaves like the real battery (see above)."""

    def __init__(self, model: VenusModel, *, single_client: bool, read_only: bool) -> None:
        self._model = model
        self._single_client = single_client
        self._read_only = read_only
        self._busy = False
        self._port = 0
        self._server: asyncio.Server | None = None

    async def listen(self, port: int) -> None:
        self._port = port
        self._server = await asyncio.start_server(self.handle_client, "0.0.0.0", port)

    async def handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        loop = asyncio.get_running_loop()
        if self._single_client and self._busy:
            writer.close()
            return
        if self._single_client:
            self._busy = True
            # Stop listening: further connections are refused like on the device.
            if self._server is not None:
                self._server.close()
                self._server = None
            _LOGGER.info("Client connected: %s", peer)
        next_answer = 0.0
        try:
            while True:
                header = await reader.readexactly(7)
                transaction_id, protocol_id, length, unit_id = struct.unpack(">HHHB", header)
                pdu = await reader.readexactly(length - 1)
                if self._single_client and os.path.exists(OFF_FILE):
                    continue
                response = handle_pdu(self._model, pdu, self._read_only)
                if self._single_client:
                    await asyncio.sleep(max(0.0, next_answer - loop.time()))
                    next_answer = loop.time() + FRAME_S
                writer.write(frame(transaction_id, protocol_id, unit_id, response))
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            if self._single_client:
                _LOGGER.info("Client disconnected: %s", peer)
                await asyncio.sleep(RECONNECT_BLOCK_S)
                self._busy = False
                await self.listen(self._port)


async def run_model(model: VenusModel) -> None:
    while True:
        await asyncio.sleep(TICK_S)
        model.tick()


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    port = int(os.environ.get("SIM_PORT", "502"))
    model = VenusModel(
        float(os.environ.get("SIM_CAPACITY_WH", "5120")),
        float(os.environ.get("SIM_INITIAL_SOC", "50")),
    )
    tap_port = int(os.environ.get("SIM_TAP_PORT", "5020"))
    control = ModbusServer(model, single_client=True, read_only=False)
    tap = ModbusServer(model, single_client=False, read_only=True)
    await control.listen(port)
    await tap.listen(tap_port)
    _LOGGER.info("Venus E 3.0 simulator listening on port %d (read-only tap %d)", port, tap_port)
    await run_model(model)


if __name__ == "__main__":
    asyncio.run(main())
