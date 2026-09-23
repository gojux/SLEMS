#!/usr/bin/env python3
"""Read and compare holding registers of a Marstek Venus via Modbus TCP.

Standard library only, runs on any machine with Python 3.10+ that can reach
the battery. The battery accepts a single Modbus TCP connection, so any other
integration talking to it must be stopped while this script runs.

Examples:
    python3 read_registers.py 192.168.1.50
    python3 read_registers.py 192.168.1.50 --registers 37005 34002 32104 --samples 20
"""

from __future__ import annotations

import argparse
import socket
import struct
import time

FRAME_GAP_S = 0.15


class ModbusError(Exception):
    """Exception response or broken frame."""


def recv_exactly(sock: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError("Connection closed by the battery")
        data += chunk
    return data


def read_holding(sock: socket.socket, unit_id: int, transaction_id: int, address: int) -> int:
    """Read one holding register and return its raw 16 bit value."""
    request = struct.pack(">HHHBBHH", transaction_id, 0, 6, unit_id, 3, address, 1)
    sock.sendall(request)
    header = recv_exactly(sock, 7)
    rx_transaction, _protocol, _length, _unit = struct.unpack(">HHHB", header)
    function = recv_exactly(sock, 1)[0]
    if function & 0x80:
        # The declared length of exception frames is wrong on v3 firmware, so
        # only the exception code byte is read.
        code = recv_exactly(sock, 1)[0]
        raise ModbusError(f"exception code {code}")
    byte_count = recv_exactly(sock, 1)[0]
    payload = recv_exactly(sock, byte_count)
    if rx_transaction != transaction_id:
        raise ModbusError("transaction id mismatch")
    return struct.unpack(">H", payload[:2])[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("host")
    parser.add_argument("--port", type=int, default=502)
    parser.add_argument("--unit", type=int, default=1)
    parser.add_argument("--registers", type=int, nargs="+", default=[37005, 34002])
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--interval", type=float, default=3.0, help="seconds between samples")
    args = parser.parse_args()

    columns = "".join(f"{reg:>16}" for reg in args.registers)
    print(f"{'time':<10}{columns}   identical")
    print(f"{'':<10}" + "".join(f"{'raw (x0.1)':>16}" for _ in args.registers))

    with socket.create_connection((args.host, args.port), timeout=5) as sock:
        transaction_id = 0
        for sample in range(args.samples):
            values: list[int | None] = []
            cells: list[str] = []
            for register in args.registers:
                transaction_id = (transaction_id + 1) & 0xFFFF
                try:
                    value = read_holding(sock, args.unit, transaction_id, register)
                    values.append(value)
                    cells.append(f"{value} ({value / 10:g})")
                except ModbusError as err:
                    values.append(None)
                    cells.append(str(err))
                time.sleep(FRAME_GAP_S)
            valid = [v for v in values if v is not None]
            identical = "yes" if len(valid) == len(values) and len(set(valid)) == 1 else "no"
            print(f"{time.strftime('%H:%M:%S'):<10}" + "".join(f"{c:>16}" for c in cells) + f"   {identical}")
            if sample < args.samples - 1:
                time.sleep(args.interval)


if __name__ == "__main__":
    main()
