"""Minimal Modbus TCP simulator of a Huawei SUN2000-8K-MAP0 hybrid inverter
with a LUNA2000-14-S1 battery for development.

Plain asyncio without dependencies. Registers as in Huawei's "Solar Inverter
Modbus Interface Definitions" (Issue 05): protocol addresses, all holding
registers, 32 bit with the high word first. Like the real inverter it knows
only the function codes 3, 6 and 16; a read with function code 4 (input
registers) is refused with exception 1, a register it does not know with
exception 2.

Model (one tick per second, no losses except 3 % in the battery path):

* PV power comes from Home Assistant through the feed port, as does the grid
  power at the meter (import positive) for the inverter's own logic.
* Forcible charge/discharge: writing 47100 = 1 (charge) or 2 (discharge)
  starts it for the period in 47083 (minutes) with the power in 47247 or
  47249 (W); every such write starts the period anew. When the period ends,
  47100 returns to 0. 47084 is stored but has no effect (no source shows that
  47100 uses it). Charging from the grid needs 47087 = 1, otherwise the
  charge is limited to the PV power.
* Otherwise the working mode 47086 applies: 2 (maximise self-consumption)
  moves the battery towards a grid power of 0, charging only from PV; any
  other mode keeps the battery idle.
* The AC output is PV minus the battery power, at most the rated 8000 W in
  either direction (PV is curtailed above it); a full battery passes its PV
  through.

Ports: the main port answers any number of clients; a read-only tap port
serves the same registers (the dev instance reads the AC power there for its
smart meter); the feed port takes writes of register 1 (PV power, W) and 2
(grid power, W, import positive, signed) and answers reads of both.

Environment variables:
    SIM_PORT          TCP port (default 502)
    SIM_TAP_PORT      read-only TCP port (default 5020)
    SIM_FEED_PORT     TCP port for the fed values (default 5021)
    SIM_CAPACITY_WH   usable capacity (default 13800)
    SIM_INITIAL_SOC   initial state of charge in % (default 50)
    SIM_GRID_CHARGE   initial value of 47087 "Charge from grid" (default 1)
    SIM_WORKING_MODE  initial value of 47086 (default 2, self-consumption; e.g. 5
                      keeps the battery idle outside forcible charge/discharge)
"""

from __future__ import annotations

import asyncio
import logging
import os
import struct

_LOGGER = logging.getLogger("huawei_sim")

TICK_S = 1.0
RATED_AC_W = 8000
MAX_BATTERY_W = 7000
BATTERY_LOSS = 0.03

REG_SOC = 37760
REG_UNIT1_SOC = 37004
REG_BATTERY_POWER = 37765
REG_UNIT1_POWER = 37001
REG_BUS_VOLTAGE = 37763
REG_UNIT1_BUS_VOLTAGE = 37003
REG_BUS_CURRENT = 37764
REG_RUNNING_STATUS = 37762
REG_UNIT1_RUNNING_STATUS = 37000
REG_BATTERY_TEMPERATURE = 37022
REG_TOTAL_CHARGE = 37780
REG_TOTAL_DISCHARGE = 37782
REG_PV_POWER = 32064
REG_ACTIVE_POWER = 32080
REG_INTERNAL_TEMPERATURE = 32087

REG_PERIOD = 47083
REG_FORCED_POWER = 47084
REG_WORKING_MODE = 47086
REG_GRID_CHARGE = 47087
REG_FORCIBLE = 47100
REG_SETTING_MODE = 47246
REG_FORCIBLE_CHARGE_POWER = 47247
REG_FORCIBLE_DISCHARGE_POWER = 47249

FEED_PV = 1
FEED_GRID = 2

SELF_CONSUMPTION = 2
STOP, CHARGE, DISCHARGE = 0, 1, 2

# Static device information: (address, registers, text).
TEXTS = (
    (30000, 15, "SUN2000-8K-MAP0"),  # model
    (30015, 10, "HV2350012345"),  # inverter SN
    (30035, 15, "V200R024C00"),  # firmware version
    (30050, 15, "V200R024C00SPC110"),  # software version
    (31025, 15, "V100R001C00SPC127"),  # monitoring software version
    (37036, 10, "V200R002C00"),  # unit 1 BMS version
    (37052, 10, "HV2410054321"),  # unit 1 SN
    (37814, 15, "V100R002C00SPC401"),  # unit 1 software version
    (38210, 15, "V100R002C00SPC401"),  # unit 1 pack 1 firmware version
)
# Writable registers (control and settings) with their initial values; 32-bit
# values span two addresses.
WRITABLE_INITIAL = {
    47075: 0, 47076: MAX_BATTERY_W, 47077: 0, 47078: MAX_BATTERY_W,  # max charge/discharge power (settings)
    REG_PERIOD: 0, REG_FORCED_POWER: 0, REG_FORCED_POWER + 1: 0,
    REG_WORKING_MODE: SELF_CONSUMPTION, REG_GRID_CHARGE: 1, 47088: 500,  # grid charge cutoff SOC (0.1 %)
    REG_FORCIBLE: STOP, REG_SETTING_MODE: 0,
    REG_FORCIBLE_CHARGE_POWER: 0, REG_FORCIBLE_CHARGE_POWER + 1: 0,
    REG_FORCIBLE_DISCHARGE_POWER: 0, REG_FORCIBLE_DISCHARGE_POWER + 1: 0,
}

EXC_ILLEGAL_FUNCTION = 1
EXC_ILLEGAL_ADDRESS = 2


def _chars(text: str, registers: int) -> list[int]:
    raw = text.encode("ascii").ljust(registers * 2, b"\x00")
    return [(raw[i] << 8) | raw[i + 1] for i in range(0, registers * 2, 2)]


def _split_32(value: int) -> list[int]:
    value &= 0xFFFFFFFF
    return [value >> 16, value & 0xFFFF]


def _join_32(high: int, low: int) -> int:
    return (high << 16) | low


def _signed_16(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value


class HuaweiModel:
    """Register store plus inverter and battery physics."""

    def __init__(self, capacity_wh: float, soc: float, grid_charge: int, working_mode: int) -> None:
        self.registers: dict[int, int] = dict(WRITABLE_INITIAL)
        self.registers[REG_GRID_CHARGE] = grid_charge
        self.registers[REG_WORKING_MODE] = working_mode
        for address, count, text in TEXTS:
            self.write(address, _chars(text, count))
        self.write(30070, [428])  # model ID
        self.write(30073, _split_32(RATED_AC_W))  # rated power
        self.write(37758, _split_32(int(capacity_wh)))  # rated capacity (Wh)
        self.write(37046, _split_32(MAX_BATTERY_W))  # maximum charge power
        self.write(37048, _split_32(MAX_BATTERY_W))  # maximum discharge power
        self.pv_w = 0
        self.grid_w: int | None = None
        self._capacity_wh = capacity_wh
        self._energy_wh = capacity_wh * soc / 100
        self._charged_wh = 0.0
        self._discharged_wh = 0.0
        self._battery_w = 0.0
        self._forcible_left_s = 0.0
        self.tick()

    def read(self, address: int, count: int) -> list[int] | None:
        """Register values; None if one of them is unknown (refused)."""
        addresses = range(address, address + count)
        if any(a not in self.registers for a in addresses):
            return None
        return [self.registers[a] for a in addresses]

    def write(self, address: int, values: list[int]) -> None:
        for offset, value in enumerate(values):
            self.registers[address + offset] = value & 0xFFFF

    def command(self, address: int, values: list[int]) -> bool:
        """A write of a client; False if a register is not writable."""
        if any(address + i not in WRITABLE_INITIAL for i in range(len(values))):
            return False
        self.write(address, values)
        if address <= REG_FORCIBLE < address + len(values):
            command = self.registers[REG_FORCIBLE]
            self._forcible_left_s = self.registers[REG_PERIOD] * 60.0 if command in (CHARGE, DISCHARGE) else 0.0
            _LOGGER.info("47100 = %d, period %d min", command, self.registers[REG_PERIOD])
        return True

    def feed(self, address: int, values: list[int]) -> bool:
        """A write on the feed port: PV power and grid power."""
        for offset, value in enumerate(values):
            if address + offset == FEED_PV:
                self.pv_w = value
            elif address + offset == FEED_GRID:
                self.grid_w = _signed_16(value)
            else:
                return False
        return True

    def fed(self, address: int, count: int) -> list[int] | None:
        """The fed values for a read on the feed port; None outside registers 1-2."""
        values = {FEED_PV: self.pv_w, FEED_GRID: (self.grid_w or 0) & 0xFFFF}
        addresses = range(address, address + count)
        if any(a not in values for a in addresses):
            return None
        return [values[a] for a in addresses]

    def _target_w(self) -> float:
        """Battery power the inverter aims at (+charge), before the limits."""
        registers = self.registers
        if self._forcible_left_s > 0:
            self._forcible_left_s -= TICK_S
            if self._forcible_left_s <= 0:
                registers[REG_FORCIBLE] = STOP
                _LOGGER.info("Forcible charge/discharge period over")
        command = registers[REG_FORCIBLE]
        if command == CHARGE:
            power = min(_join_32(*self.read(REG_FORCIBLE_CHARGE_POWER, 2)), MAX_BATTERY_W)
            return power if registers[REG_GRID_CHARGE] == 1 else min(power, self.pv_w)
        if command == DISCHARGE:
            return -min(_join_32(*self.read(REG_FORCIBLE_DISCHARGE_POWER, 2)), MAX_BATTERY_W)
        if registers[REG_WORKING_MODE] == SELF_CONSUMPTION and self.grid_w is not None:
            # Half the remaining grid power per second; charge only from PV.
            return min(self._battery_w - 0.5 * self.grid_w, self.pv_w)
        return 0.0

    def tick(self) -> None:
        power = max(-MAX_BATTERY_W, min(MAX_BATTERY_W, self._target_w()))
        if (power > 0 and self._energy_wh >= self._capacity_wh) or (power < 0 and self._energy_wh <= 0):
            power = 0.0
        # The AC connection stays within the rated power: less discharging,
        # less charging from the grid, then PV curtailed.
        if power < 0:
            power = max(power, min(0.0, self.pv_w - RATED_AC_W))
        power = min(power, self.pv_w + RATED_AC_W)
        pv = min(self.pv_w, RATED_AC_W + power)
        ac_output = pv - power
        self._battery_w = power

        stored = power * (1 - BATTERY_LOSS) if power > 0 else power * (1 + BATTERY_LOSS)
        self._energy_wh = max(0.0, min(self._capacity_wh, self._energy_wh + stored * TICK_S / 3600))
        if power > 0:
            self._charged_wh += power * TICK_S / 3600
        else:
            self._discharged_wh -= power * TICK_S / 3600
        soc = self._energy_wh / self._capacity_wh * 100
        voltage = 360 + soc * 0.4

        for address in (REG_SOC, REG_UNIT1_SOC):
            self.write(address, [round(soc * 10)])
        for address in (REG_BATTERY_POWER, REG_UNIT1_POWER):
            self.write(address, _split_32(round(power)))
        for address in (REG_BUS_VOLTAGE, REG_UNIT1_BUS_VOLTAGE):
            self.write(address, [round(voltage * 10)])
        self.write(REG_BUS_CURRENT, [round(power / voltage * 10)])
        for address in (REG_RUNNING_STATUS, REG_UNIT1_RUNNING_STATUS):
            self.write(address, [2 if power else 1])
        self.write(REG_BATTERY_TEMPERATURE, [250 + round(abs(power) / 500)])
        self.write(REG_TOTAL_CHARGE, _split_32(int(self._charged_wh / 10)))
        self.write(REG_TOTAL_DISCHARGE, _split_32(int(self._discharged_wh / 10)))
        self.write(REG_PV_POWER, _split_32(round(pv)))
        self.write(REG_ACTIVE_POWER, _split_32(round(ac_output)))
        self.write(REG_INTERNAL_TEMPERATURE, [350 + round(abs(ac_output) / 400)])


def handle_pdu(model: HuaweiModel, pdu: bytes, port: str) -> bytes:
    """Process one request PDU (``port``: main, tap or feed) and return the response PDU."""
    function = pdu[0]
    if function not in (3, 6, 16) or (port == "tap" and function != 3):
        return bytes((function | 0x80, EXC_ILLEGAL_FUNCTION))
    try:
        if function == 3:
            address, count = struct.unpack(">HH", pdu[1:5])
            read = model.fed if port == "feed" else model.read
            values = read(address, count) if 1 <= count <= 125 else None
            if values is None:
                return bytes((function | 0x80, EXC_ILLEGAL_ADDRESS))
            return bytes((function, count * 2)) + struct.pack(f">{count}H", *values)
        if function == 6:
            address, value = struct.unpack(">HH", pdu[1:5])
            values = [value]
        else:
            address, count, _byte_count = struct.unpack(">HHB", pdu[1:6])
            values = list(struct.unpack(f">{count}H", pdu[6 : 6 + count * 2]))
        ok = model.feed(address, values) if port == "feed" else model.command(address, values)
        return pdu[:5] if ok else bytes((function | 0x80, EXC_ILLEGAL_ADDRESS))
    except struct.error:
        return bytes((function | 0x80, EXC_ILLEGAL_ADDRESS))


async def serve(model: HuaweiModel, port: int, kind: str) -> None:
    async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                header = await reader.readexactly(7)
                transaction_id, protocol_id, length, unit_id = struct.unpack(">HHHB", header)
                pdu = await reader.readexactly(length - 1)
                response = handle_pdu(model, pdu, kind)
                writer.write(struct.pack(">HHHB", transaction_id, protocol_id, len(response) + 1, unit_id) + response)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    await asyncio.start_server(handle_client, "0.0.0.0", port)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    model = HuaweiModel(
        float(os.environ.get("SIM_CAPACITY_WH", "13800")),
        float(os.environ.get("SIM_INITIAL_SOC", "50")),
        int(os.environ.get("SIM_GRID_CHARGE", "1")),
        int(os.environ.get("SIM_WORKING_MODE", str(SELF_CONSUMPTION))),
    )
    port = int(os.environ.get("SIM_PORT", "502"))
    tap_port = int(os.environ.get("SIM_TAP_PORT", "5020"))
    feed_port = int(os.environ.get("SIM_FEED_PORT", "5021"))
    await serve(model, port, "main")
    await serve(model, tap_port, "tap")
    await serve(model, feed_port, "feed")
    _LOGGER.info("Huawei SUN2000 simulator on port %d (tap %d, feed %d)", port, tap_port, feed_port)
    while True:
        await asyncio.sleep(TICK_S)
        model.tick()


if __name__ == "__main__":
    asyncio.run(main())
