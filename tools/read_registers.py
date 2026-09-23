#!/usr/bin/env python3
"""Read and compare holding registers of a Marstek Venus via Modbus TCP.

Standard library only, runs on any machine with Python 3.10+ that can reach
the battery. The battery accepts a single Modbus TCP connection, so any other
integration talking to it must be stopped while this script runs.

Registers are given as ADDRESS or ADDRESS:TYPE with TYPE one of u16 (default),
i16, u32, i32. Every value is shown raw and scaled by 0.1 and 0.01.

Examples:
    python3 read_registers.py 192.168.1.50
    python3 read_registers.py 192.168.1.50 --registers 37005 34002 32104 --samples 20
    python3 read_registers.py 192.168.1.50 --registers 30001:i16 30006:i16 33000:u32
"""

from __future__ import annotations

import argparse
import socket
import struct
import time

FRAME_GAP_S = 0.15
TYPES = {"u16": 1, "i16": 1, "u32": 2, "i32": 2}


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


def read_holding(
    sock: socket.socket, unit_id: int, transaction_id: int, address: int, count: int
) -> list[int]:
    """Read ``count`` holding registers and return the raw 16 bit words."""
    request = struct.pack(">HHHBBHH", transaction_id, 0, 6, unit_id, 3, address, count)
    sock.sendall(request)
    header = recv_exactly(sock, 7)
    rx_transaction, _protocol, _length, _unit = struct.unpack(">HHHB", header)
    function = recv_exactly(sock, 1)[0]
    if function & 0x80:
        # The declared length of exception frames is wrong on v3 firmware, so
        # only the exception code byte is read.
        code = recv_exactly(sock, 1)[0]
        raise ModbusError(f"exception {code}")
    byte_count = recv_exactly(sock, 1)[0]
    payload = recv_exactly(sock, byte_count)
    if rx_transaction != transaction_id:
        raise ModbusError("transaction id mismatch")
    return list(struct.unpack(f">{count}H", payload[: count * 2]))


def decode(words: list[int], data_type: str) -> int:
    value = words[0] if len(words) == 1 else (words[0] << 16) | words[1]
    bits = 16 * len(words)
    if data_type.startswith("i") and value >= 1 << (bits - 1):
        value -= 1 << bits
    return value


def parse_register(text: str) -> tuple[int, str]:
    address, _, data_type = text.partition(":")
    data_type = data_type or "u16"
    if data_type not in TYPES:
        raise argparse.ArgumentTypeError(f"unknown type {data_type}")
    if not address.isdigit() or not 0 <= int(address) <= 0xFFFF:
        raise argparse.ArgumentTypeError(f"invalid register address {address}")
    return int(address), data_type


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("host")
    parser.add_argument("--port", type=int, default=502)
    parser.add_argument("--unit", type=int, default=1)
    parser.add_argument(
        "--registers",
        type=parse_register,
        nargs="+",
        default=[(37005, "u16"), (34002, "u16")],
    )
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--interval", type=float, default=3.0, help="seconds between samples")
    args = parser.parse_args()

    with socket.create_connection((args.host, args.port), timeout=5) as sock:
        transaction_id = 0
        for sample in range(args.samples):
            print(time.strftime("%H:%M:%S"))
            values: list[int | None] = []
            for address, data_type in args.registers:
                transaction_id = (transaction_id + 1) & 0xFFFF
                label = f"  {address}:{data_type}"
                try:
                    words = read_holding(
                        sock, args.unit, transaction_id, address, TYPES[data_type]
                    )
                    value = decode(words, data_type)
                    values.append(value)
                    scaled = f"x0.1={value / 10:<10g} x0.01={value / 100:g}"
                    print(f"{label:<14} {value:>12}   {scaled}")
                except ModbusError as err:
                    values.append(None)
                    print(f"{label:<14} {err}")
                time.sleep(FRAME_GAP_S)
            valid = [v for v in values if v is not None]
            if len(values) > 1:
                identical = len(valid) == len(values) and len(set(valid)) == 1
                print(f"  identical: {'yes' if identical else 'no'}")
            if sample < args.samples - 1:
                time.sleep(args.interval)


if __name__ == "__main__":
    main()
