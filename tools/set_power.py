#!/usr/bin/env python3
"""Charge or discharge a Marstek Venus with a fixed power via Modbus TCP.

Standard library only (Python 3.10+). The battery accepts a single Modbus TCP
connection: pause the communication of this battery in SLEMS (battery menu ⋮)
and stop any other integration talking to it while the script runs.

The script enables RS485 control, writes the set point, reads it back and
then shows every few seconds what the battery reports: DC power (30001), AC
power (30006), state of charge (34002), inverter state (35100) and the set
point registers. The set point is written again every ``REFRESH_S`` seconds.
At the end, or on Ctrl+C, the battery is handed back to its own logic
(set points 0, force mode none, RS485 control off).

Examples:
    python3 set_power.py 192.168.1.50 --charge 1000
    python3 set_power.py 192.168.1.50 --discharge 800 --duration 300
    python3 set_power.py 192.168.1.50 --release
"""

from __future__ import annotations

import argparse
import socket
import struct
import time

from read_registers import FRAME_GAP_S, ModbusError, decode, read_holding, recv_exactly

REG_RS485_CONTROL = 42000
REG_FORCE_MODE = 42010
REG_SET_CHARGE_POWER = 42020
REG_SET_DISCHARGE_POWER = 42021
RS485_ENABLE = 0x55AA
RS485_DISABLE = 0x55BB
FORCE_NONE, FORCE_CHARGE, FORCE_DISCHARGE = 0, 1, 2
# Hardware ceiling of the set point registers.
MAX_POWER_W = 2500
REFRESH_S = 30.0

# (label, address, type, scale, unit)
MONITOR = (
    ("DC power 30001", 30001, "i16", 1, "W"),
    ("AC power 30006", 30006, "i16", 1, "W"),
    ("SoC 34002", 34002, "u16", 0.1, "%"),
    ("state 35100", 35100, "u16", 1, ""),
    ("force mode 42010", 42010, "u16", 1, ""),
    ("charge set 42020", 42020, "u16", 1, "W"),
    ("discharge set 42021", 42021, "u16", 1, "W"),
)
STATES = {0: "sleep", 1: "standby", 2: "charging", 3: "discharging", 4: "backup", 5: "ota upgrade", 6: "bypass"}
WORDS = {"u16": 1, "i16": 1, "u32": 2, "i32": 2}


class Link:
    def __init__(self, sock: socket.socket, unit: int) -> None:
        self.sock = sock
        self.unit = unit
        self.transaction = 0

    def _next(self) -> int:
        self.transaction = (self.transaction + 1) & 0xFFFF
        return self.transaction

    def read(self, address: int, data_type: str = "u16") -> int:
        words = read_holding(self.sock, self.unit, self._next(), address, WORDS[data_type])
        time.sleep(FRAME_GAP_S)
        return decode(words, data_type)

    def write(self, address: int, value: int) -> None:
        """Write a single holding register (function 6)."""
        transaction = self._next()
        self.sock.sendall(struct.pack(">HHHBBHH", transaction, 0, 6, self.unit, 6, address, value))
        header = recv_exactly(self.sock, 7)
        function = recv_exactly(self.sock, 1)[0]
        if function & 0x80:
            code = recv_exactly(self.sock, 1)[0]
            raise ModbusError(f"write {address}: exception {code}")
        echo = recv_exactly(self.sock, 4)
        if struct.unpack(">HHHB", header)[0] != transaction or struct.unpack(">HH", echo) != (address, value):
            raise ModbusError(f"write {address}: unexpected answer")
        time.sleep(FRAME_GAP_S)


def apply(link: Link, charge: int, discharge: int) -> None:
    mode = FORCE_CHARGE if charge else FORCE_DISCHARGE if discharge else FORCE_NONE
    # Set points before the force mode, so a new direction starts with valid values.
    link.write(REG_RS485_CONTROL, RS485_ENABLE)
    link.write(REG_SET_DISCHARGE_POWER, discharge)
    link.write(REG_SET_CHARGE_POWER, charge)
    link.write(REG_FORCE_MODE, mode)


def release(link: Link) -> None:
    link.write(REG_SET_DISCHARGE_POWER, 0)
    link.write(REG_SET_CHARGE_POWER, 0)
    link.write(REG_FORCE_MODE, FORCE_NONE)
    link.write(REG_RS485_CONTROL, RS485_DISABLE)
    print("Battery handed back to its own logic.")


def show(link: Link) -> None:
    print(time.strftime("%H:%M:%S"))
    for label, address, data_type, scale, unit in MONITOR:
        try:
            value = link.read(address, data_type)
        except ModbusError as err:
            print(f"  {label:<20} {err}")
            continue
        text = f"{value * scale:g} {unit}".strip()
        if address == 35100:
            text += f" ({STATES.get(value, '?')})"
        print(f"  {label:<20} {text}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("host")
    parser.add_argument("--port", type=int, default=502)
    parser.add_argument("--unit", type=int, default=1)
    direction = parser.add_mutually_exclusive_group(required=True)
    direction.add_argument("--charge", type=int, metavar="W")
    direction.add_argument("--discharge", type=int, metavar="W")
    direction.add_argument("--release", action="store_true", help="only hand the battery back")
    parser.add_argument("--duration", type=float, default=120.0, help="seconds (default 120)")
    parser.add_argument("--interval", type=float, default=5.0, help="seconds between readings")
    args = parser.parse_args()

    charge = min(max(args.charge or 0, 0), MAX_POWER_W)
    discharge = min(max(args.discharge or 0, 0), MAX_POWER_W)
    with socket.create_connection((args.host, args.port), timeout=5) as sock:
        link = Link(sock, args.unit)
        if args.release:
            release(link)
            return
        print(f"Charge cycles 34003: {link.read(34003)}")
        what = f"charge {charge} W" if charge else f"discharge {discharge} W"
        print(f"Setting {what} for {args.duration:g} s (Ctrl+C stops early).")
        try:
            apply(link, charge, discharge)
            started = last_write = time.monotonic()
            while time.monotonic() - started < args.duration:
                show(link)
                time.sleep(args.interval)
                if time.monotonic() - last_write >= REFRESH_S:
                    apply(link, charge, discharge)
                    last_write = time.monotonic()
        except KeyboardInterrupt:
            print("Stopped.")
        finally:
            release(link)
            show(link)


if __name__ == "__main__":
    main()
