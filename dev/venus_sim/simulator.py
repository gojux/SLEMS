"""Minimal Modbus TCP simulator of a Marstek Venus E 3.0 for development.

Plain asyncio without dependencies. It implements the function codes the
driver uses (3 = read holding registers, 6 = write single register,
16 = write multiple registers) and a simple battery model. In RS485 control
mode the force mode and set points drive the AC power; the DC battery power
differs by the conversion losses (15 W + 4 % + 1e-5 * P², like the SLEMS default
model); the state of charge is integrated once per second.

Like the real device it accepts only one TCP connection at a time. A second,
read-only port (``SIM_TAP_PORT``) serves the same registers to any number of
clients; the dev instance reads the AC power there every second to build a
realistic smart meter.

Environment variables:
    SIM_PORT          TCP port (default 502)
    SIM_TAP_PORT      read-only TCP port (default 5020)
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
REG_BATTERY_SOC = 37005
REG_RS485_CONTROL = 42000
REG_FORCE_MODE = 42010
REG_SET_CHARGE_POWER = 42020
REG_SET_DISCHARGE_POWER = 42021

RS485_ENABLE = 0x55AA
MAX_POWER_W = 2500
TICK_S = 1.0


def conversion_loss(ac_power: float) -> float:
    power = abs(ac_power)
    return 0.0 if power == 0 else 15 + 0.04 * power + 1e-5 * power**2

EXC_ILLEGAL_FUNCTION = 1
EXC_ILLEGAL_ADDRESS = 2


class VenusModel:
    """Register store plus battery physics."""

    def __init__(self, capacity_wh: float, soc: float) -> None:
        self.registers: dict[int, int] = {}
        self._capacity_wh = capacity_wh
        self._energy_wh = capacity_wh * soc / 100
        self._charged_wh = 0.0
        self._discharged_wh = 0.0
        self.tick()

    def read(self, address: int, count: int) -> list[int]:
        return [self.registers.get(address + i, 0) for i in range(count)]

    def write(self, address: int, values: list[int]) -> None:
        for offset, value in enumerate(values):
            self.registers[address + offset] = value & 0xFFFF

    def tick(self) -> None:
        power = 0
        if self.registers.get(REG_RS485_CONTROL) == RS485_ENABLE:
            force_mode = self.registers.get(REG_FORCE_MODE, 0)
            if force_mode == 1:
                power = min(self.registers.get(REG_SET_CHARGE_POWER, 0), MAX_POWER_W)
            elif force_mode == 2:
                power = -min(self.registers.get(REG_SET_DISCHARGE_POWER, 0), MAX_POWER_W)

        if (power > 0 and self._energy_wh >= self._capacity_wh) or (
            power < 0 and self._energy_wh <= 0
        ):
            power = 0

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
        self.write(REG_INVERTER_STATE, [2 if power > 0 else 3 if power < 0 else 1])
        self.write(REG_BATTERY_SOC, [round(soc)])
        self.write(REG_TOTAL_CHARGING_ENERGY, _split_32(int(self._charged_wh / 10)))
        self.write(REG_TOTAL_DISCHARGING_ENERGY, _split_32(int(self._discharged_wh / 10)))


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
            return bytes((function, count * 2)) + struct.pack(f">{count}H", *values)
        if function == 6:
            address, value = struct.unpack(">HH", pdu[1:5])
            model.write(address, [value])
            return pdu[:5]
        if function == 16:
            address, count, _byte_count = struct.unpack(">HHB", pdu[1:6])
            values = list(struct.unpack(f">{count}H", pdu[6 : 6 + count * 2]))
            model.write(address, values)
            return pdu[:5]
    except struct.error:
        return bytes((function | 0x80, EXC_ILLEGAL_ADDRESS))
    return bytes((function | 0x80, EXC_ILLEGAL_FUNCTION))


class ModbusServer:
    """Modbus TCP server; the control port accepts a single client like the real battery."""

    def __init__(self, model: VenusModel, *, single_client: bool, read_only: bool) -> None:
        self._model = model
        self._single_client = single_client
        self._read_only = read_only
        self._busy = False

    async def handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        if self._single_client and self._busy:
            _LOGGER.warning("Rejecting second connection from %s", peer)
            writer.close()
            return
        if self._single_client:
            self._busy = True
            _LOGGER.info("Client connected: %s", peer)
        try:
            while True:
                header = await reader.readexactly(7)
                transaction_id, protocol_id, length, unit_id = struct.unpack(">HHHB", header)
                pdu = await reader.readexactly(length - 1)
                response = handle_pdu(self._model, pdu, self._read_only)
                writer.write(
                    struct.pack(">HHHB", transaction_id, protocol_id, len(response) + 1, unit_id)
                    + response
                )
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            if self._single_client:
                self._busy = False
                _LOGGER.info("Client disconnected: %s", peer)


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
    control_server = await asyncio.start_server(control.handle_client, "0.0.0.0", port)
    tap_server = await asyncio.start_server(tap.handle_client, "0.0.0.0", tap_port)
    _LOGGER.info("Venus E 3.0 simulator listening on port %d (read-only tap %d)", port, tap_port)
    async with control_server, tap_server:
        await asyncio.gather(
            control_server.serve_forever(), tap_server.serve_forever(), run_model(model)
        )


if __name__ == "__main__":
    asyncio.run(main())
