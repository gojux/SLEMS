#!/usr/bin/env python3
"""Test whether a Marstek Venus answers reads of several registers in one frame.

Standard library only (uses read_registers.py from this folder). The battery
accepts a single Modbus TCP connection, so any other integration talking to it
must be stopped while this script runs (in SLEMS: switch on *Communication
paused* of the battery).

Each block is read as ADDRESS:COUNT; the first register alone is read for
comparison. An "exception 2" (illegal address) means the firmware refuses the
range, e.g. because it contains unused registers.

Examples:
    python3 read_blocks.py 192.168.1.50
    python3 read_blocks.py 192.168.1.50 --blocks 30001:6 42000:11 --rounds 5
"""

from __future__ import annotations

import argparse
import socket
import sys
import time

sys.dont_write_bytecode = True

from read_registers import FRAME_GAP_S, ModbusError, read_holding  # noqa: E402

# Adjacent registers SLEMS reads one by one (see drivers/marstek_venus_e3.py).
DEFAULT_BLOCKS = ("30001:6", "34002:2", "37007:2", "33000:4", "42000:11")


def parse_block(text: str) -> tuple[int, int]:
    try:
        address, count = (int(part) for part in text.split(":"))
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected ADDRESS:COUNT, got {text}") from None
    if not 0 <= address <= 0xFFFF or not 1 <= count <= 125:
        raise argparse.ArgumentTypeError(f"invalid block {text}")
    return address, count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("host")
    parser.add_argument("--port", type=int, default=502)
    parser.add_argument("--unit", type=int, default=1)
    parser.add_argument("--blocks", type=parse_block, nargs="+", default=[parse_block(b) for b in DEFAULT_BLOCKS])
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()

    transaction = 0
    with socket.create_connection((args.host, args.port), timeout=3) as sock:
        for round_ in range(1, args.rounds + 1):
            print(f"Round {round_}")
            for address, count in args.blocks:
                for size in (1, count) if count > 1 else (1,):
                    transaction += 1
                    start = time.monotonic()
                    try:
                        words = read_holding(sock, args.unit, transaction, address, size)
                        result = f"{words}"
                    except (ModbusError, OSError) as err:
                        result = f"ERROR {err}"
                    elapsed = (time.monotonic() - start) * 1000
                    print(f"  {address} x{size:<3} {elapsed:5.0f} ms  {result}")
                    time.sleep(FRAME_GAP_S)
            print()


if __name__ == "__main__":
    main()
