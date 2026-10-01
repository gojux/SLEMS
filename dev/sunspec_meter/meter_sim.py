"""SunSpec meter simulator (layout of a SolarEdge inverter with two meters).

Serves the SunSpec model chain at 40000: the inverter's common block and
model 103, then meter 1 (export+import, model 203, total power at 40206) and
meter 2 (consumption, model 203, total power at 40380). Like SolarEdge, the
meter reports export as positive.

The power is fed by Home Assistant (dev automation) through a second port
(``SIM_FEED_PORT``), which accepts writes of single registers; the main port
(``SIM_PORT``) only answers reads, for any number of clients.

Environment variables:
    SIM_PORT       TCP port of the meter (default 502)
    SIM_FEED_PORT  TCP port for the fed values (default 5021)
"""

from __future__ import annotations

import asyncio
import logging
import os
import struct

_LOGGER = logging.getLogger("sunspec_meter")

EXC_ILLEGAL_FUNCTION = 1
EXC_ILLEGAL_ADDRESS = 2


def _text(text: str, registers: int) -> list[int]:
    raw = text.encode().ljust(registers * 2, b"\0")
    return [(raw[i] << 8) | raw[i + 1] for i in range(0, len(raw), 2)]


def _common(address: int, model: str, option: str) -> dict[int, int]:
    words = [1, 65, *_text("SolarEdge", 16), *_text(model, 16), *_text(option, 8)]
    return dict(enumerate(words, address))


def initial_registers() -> dict[int, int]:
    registers = {40000: 0x5375, 40001: 0x6E53}
    registers |= _common(40002, "SE10K-SIM", "")
    registers |= {40069: 103, 40070: 50}
    registers |= _common(40121, "WND-3Y-400-MB", "Export+Import")
    registers |= {40188: 203, 40189: 105, 40206: 0, 40210: 0}
    registers |= _common(40295, "WND-3Y-400-MB", "Consumption")
    registers |= {40362: 203, 40363: 105, 40380: 600, 40384: 0}
    registers |= {40469: 0xFFFF, 40470: 0}
    return registers


class Meter:
    def __init__(self) -> None:
        self.registers = initial_registers()

    def handle(self, pdu: bytes, writable: bool) -> bytes:
        function = pdu[0]
        try:
            if function == 3:
                address, count = struct.unpack(">HH", pdu[1:5])
                if not 1 <= count <= 125:
                    return bytes((function | 0x80, EXC_ILLEGAL_ADDRESS))
                values = [self.registers.get(address + i, 0) for i in range(count)]
                return bytes((function, count * 2)) + struct.pack(f">{count}H", *values)
            if function == 6 and writable:
                address, value = struct.unpack(">HH", pdu[1:5])
                self.registers[address] = value
                return pdu[:5]
        except struct.error:
            return bytes((function | 0x80, EXC_ILLEGAL_ADDRESS))
        return bytes((function | 0x80, EXC_ILLEGAL_FUNCTION))

    def server(self, writable: bool):
        async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                while True:
                    header = await reader.readexactly(7)
                    transaction_id, protocol_id, length, unit_id = struct.unpack(">HHHB", header)
                    pdu = await reader.readexactly(length - 1)
                    response = self.handle(pdu, writable)
                    writer.write(
                        struct.pack(">HHHB", transaction_id, protocol_id, len(response) + 1, unit_id)
                        + response
                    )
                    await writer.drain()
            except (asyncio.IncompleteReadError, ConnectionError):
                pass
            finally:
                writer.close()

        return handle_client


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    port = int(os.environ.get("SIM_PORT", "502"))
    feed_port = int(os.environ.get("SIM_FEED_PORT", "5021"))
    meter = Meter()
    server = await asyncio.start_server(meter.server(False), "0.0.0.0", port)
    feed = await asyncio.start_server(meter.server(True), "0.0.0.0", feed_port)
    _LOGGER.info("SunSpec meter simulator on port %d (feed %d)", port, feed_port)
    async with server, feed:
        await asyncio.gather(server.serve_forever(), feed.serve_forever())


if __name__ == "__main__":
    asyncio.run(main())
