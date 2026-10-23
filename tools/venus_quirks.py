#!/usr/bin/env python3
"""Check the Modbus TCP quirks of a Marstek Venus that SLEMS' client handles.

Standard library only (uses read_registers.py from this folder). It only
reads, it never writes a register. The battery accepts a single Modbus TCP
connection, so any other integration talking to it must be stopped while this
script runs (in SLEMS: switch on *Communication paused* of the battery).

Tests (``--tests``, default all but ``spacing``):

* ``rest``: the control registers 42000/42010/42020/42021 (e.g. right after a
  restart of the battery: what it reports before anything was written).
* ``exception``: the raw bytes of exception responses (a refused block and an
  unassigned register) with the MBAP length they declare and the bytes that
  follow it; a correct exception frame declares 3 (unit, function, code).
* ``second``: a second connection while the first is open (refused, closed,
  accepted but silent, or answered).
* ``reconnect``: closes the connection and connects again after 0, 0.5, 1,
  2 and 5 s; shows whether the connect works and the first read answers.
* ``spacing``: reads 34002 with gaps shorter than 150 ms (0.15, 0.1, 0.05,
  0.02, 0 s) and counts answers, time-outs and late replies with another
  transaction id. Only on request: the firmware may stop answering for a
  while; a series stops after a time-out.

Examples:
    python3 -B venus_quirks.py 192.168.1.50
    python3 -B venus_quirks.py 192.168.1.50 --tests rest
    python3 -B venus_quirks.py 192.168.1.50 --tests spacing --reads 20 | tee quirks.txt
"""

from __future__ import annotations

import argparse
import socket
import struct
import sys
import time

sys.dont_write_bytecode = True

from read_registers import FRAME_GAP_S  # noqa: E402

TESTS = ("rest", "exception", "second", "reconnect", "spacing")
DEFAULT_TESTS = ("rest", "exception", "second", "reconnect")
CONTROL_REGISTERS = ((42000, "rs485_control"), (42010, "force_mode"), (42020, "set_charge_power"),
                     (42021, "set_discharge_power"))
# A block over unassigned registers (refused, see read_blocks.py) and one unassigned register.
EXCEPTION_READS = ((42000, 11), (42001, 1))
SOC_REGISTER = 34002
RECONNECT_WAITS_S = (0.0, 0.5, 1.0, 2.0, 5.0)
SPACING_GAPS_S = (0.15, 0.1, 0.05, 0.02, 0.0)
TIMEOUT_S = 3.0
# Bytes arriving this long after the last one belong to the same answer.
TRAILING_S = 0.3


class Link:
    """One TCP connection that sends read requests and returns the raw answers."""

    def __init__(self, host: str, port: int, unit: int) -> None:
        self.host, self.port, self.unit = host, port, unit
        self.sock: socket.socket | None = None
        self.transaction = 0

    def connect(self) -> None:
        self.sock = socket.create_connection((self.host, self.port), timeout=TIMEOUT_S)

    def close(self) -> None:
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def send_read(self, address: int, count: int) -> int:
        assert self.sock is not None
        self.transaction = (self.transaction + 1) & 0xFFFF
        self.sock.sendall(struct.pack(">HHHBBHH", self.transaction, 0, 6, self.unit, 3, address, count))
        return self.transaction

    def receive(self, timeout_s: float = TIMEOUT_S, *, complete: bool = False) -> bytes:
        """Everything that arrives until ``TRAILING_S`` after the last byte (b"" on a time-out);
        ``complete``: as soon as the bytes form complete frames (for exact timing)."""
        assert self.sock is not None
        data = b""
        deadline = time.monotonic() + timeout_s
        while True:
            if complete and data and all(frame["function"] is not None for frame in frames(data)):
                return data
            wait = deadline - time.monotonic() if not data or complete else TRAILING_S
            if wait <= 0:
                return data
            self.sock.settimeout(wait)
            try:
                chunk = self.sock.recv(1024)
            except TimeoutError:
                return data
            if not chunk:
                if data:
                    return data
                raise ConnectionError("closed by the battery")
            data += chunk


def frames(data: bytes) -> list[dict]:
    """Split raw bytes into Modbus TCP answers, trusting the declared lengths
    except for exception frames (always 9 bytes)."""
    result = []
    while len(data) >= 8:
        transaction, _protocol, length, _unit = struct.unpack(">HHHB", data[:7])
        function = data[7]
        if function & 0x80:
            size = 9
        else:
            size = 6 + length
        frame, data = data[:size], data[size:]
        result.append({"transaction": transaction, "length": length, "function": function, "raw": frame,
                       "following": len(frame) - 6})
    if data:
        result.append({"transaction": None, "length": None, "function": None, "raw": data, "following": None})
    return result


def words_of(frame: dict) -> list[int] | None:
    raw = frame["raw"]
    if frame["function"] != 3 or len(raw) < 9:
        return None
    count = raw[8] // 2
    return list(struct.unpack(f">{count}H", raw[9 : 9 + count * 2]))


def read_words(link: Link, address: int, count: int) -> list[int] | None:
    transaction = link.send_read(address, count)
    for frame in frames(link.receive(complete=True)):
        if frame["transaction"] == transaction:
            return words_of(frame)
    return None


def test_rest(link: Link) -> None:
    print("rest: control registers")
    for address, name in CONTROL_REGISTERS:
        try:
            words = read_words(link, address, 1)
        except OSError as err:
            print(f"  {address} {name:<20} ERROR {err}")
            continue
        value = "no answer" if words is None else f"{words[0]} ({words[0]:#06x})"
        print(f"  {address} {name:<20} {value}")
        time.sleep(FRAME_GAP_S)


def test_exception(link: Link) -> None:
    print("exception: raw exception responses")
    for address, count in EXCEPTION_READS:
        transaction = link.send_read(address, count)
        start = time.monotonic()
        try:
            data = link.receive()
        except OSError as err:
            print(f"  {address} x{count}: ERROR {err}")
            continue
        elapsed = (time.monotonic() - start) * 1000
        if not data:
            print(f"  {address} x{count}: no answer within {TIMEOUT_S:.0f} s")
            continue
        print(f"  {address} x{count}: {len(data)} bytes in {elapsed:.0f} ms: {data.hex(' ')}")
        for frame in frames(data):
            if frame["function"] is None:
                print(f"    trailing bytes: {frame['raw'].hex(' ')}")
                continue
            if frame["function"] & 0x80:
                code = frame["raw"][8] if len(frame["raw"]) > 8 else None
                verdict = (
                    "correct" if frame["length"] == 3
                    else f"WRONG: declares {frame['length']} for {frame['following']} bytes"
                )
                print(f"    transaction {frame['transaction']} (sent {transaction}), exception {code}, "
                      f"MBAP length {frame['length']}: {verdict}")
            else:
                print(f"    transaction {frame['transaction']}: normal answer {words_of(frame)}")
        time.sleep(FRAME_GAP_S)
    # Whether the connection still works after the exceptions.
    try:
        words = read_words(link, SOC_REGISTER, 1)
        print(f"  afterwards {SOC_REGISTER}: {'no answer' if words is None else words[0]}")
    except OSError as err:
        print(f"  afterwards {SOC_REGISTER}: ERROR {err}")
    time.sleep(FRAME_GAP_S)


def test_second(link: Link) -> None:
    print("second: another connection while the first is open")
    second = Link(link.host, link.port, link.unit)
    start = time.monotonic()
    try:
        second.connect()
    except OSError as err:
        print(f"  connect refused after {(time.monotonic() - start) * 1000:.0f} ms: {err}")
    else:
        print(f"  connect accepted after {(time.monotonic() - start) * 1000:.0f} ms")
        try:
            words = read_words(second, SOC_REGISTER, 1)
            print(f"  read on it: {'no answer (accepted but silent)' if words is None else f'answered {words[0]}'}")
        except OSError as err:
            print(f"  read on it: {err}")
        finally:
            second.close()
    time.sleep(FRAME_GAP_S)
    try:
        words = read_words(link, SOC_REGISTER, 1)
        print(f"  first connection afterwards: {'no answer' if words is None else f'answers {words[0]}'}")
    except OSError as err:
        print(f"  first connection afterwards: ERROR {err}")
    time.sleep(FRAME_GAP_S)


def test_reconnect(link: Link) -> None:
    print("reconnect: close and connect again after a wait")
    for wait in RECONNECT_WAITS_S:
        link.close()
        time.sleep(wait)
        start = time.monotonic()
        try:
            link.connect()
        except OSError as err:
            print(f"  after {wait:>3} s: connect failed ({err})")
            continue
        connect_ms = (time.monotonic() - start) * 1000
        try:
            words = read_words(link, SOC_REGISTER, 1)
            answer = "no answer" if words is None else f"answered {words[0]}"
        except OSError as err:
            answer = f"ERROR {err}"
        total_ms = (time.monotonic() - start) * 1000
        print(f"  after {wait:>3} s: connected in {connect_ms:.0f} ms, first read {answer} ({total_ms:.0f} ms)")
        time.sleep(FRAME_GAP_S)
    if link.sock is None:
        time.sleep(5)
        link.connect()


def test_spacing(link: Link, reads: int) -> None:
    print("spacing: reads of 34002 with shorter gaps")
    for gap in SPACING_GAPS_S:
        answered = timeouts = late = 0
        times: list[float] = []
        pending: set[int] = set()
        for _ in range(reads):
            transaction = link.send_read(SOC_REGISTER, 1)
            pending.add(transaction)
            start = time.monotonic()
            try:
                data = link.receive(complete=True)
            except OSError as err:
                print(f"  gap {gap:.2f} s: connection lost ({err})")
                timeouts += 1
                break
            got_own = False
            for frame in frames(data):
                if frame["transaction"] == transaction:
                    got_own = True
                elif frame["transaction"] in pending:
                    late += 1
                pending.discard(frame["transaction"])
            if got_own:
                answered += 1
                times.append((time.monotonic() - start) * 1000)
            else:
                timeouts += 1
                break
            time.sleep(gap)
        mean = sum(times) / len(times) if times else 0.0
        worst = max(times) if times else 0.0
        print(f"  gap {gap:.2f} s: {answered} answered (mean {mean:.0f} ms, max {worst:.0f} ms), "
              f"{timeouts} time-outs, {late} late replies")
        if timeouts:
            print("  stopped after a time-out; waiting 10 s for late replies, then reconnecting")
            try:
                late_data = link.receive(10.0)
                if late_data:
                    ids = [frame["transaction"] for frame in frames(late_data)]
                    print(f"  arrived late: transactions {ids}")
            except OSError:
                pass
            link.close()
            time.sleep(5)
            link.connect()
            return
        time.sleep(1.0)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("host")
    parser.add_argument("--port", type=int, default=502)
    parser.add_argument("--unit", type=int, default=1)
    parser.add_argument("--tests", nargs="+", choices=TESTS, default=list(DEFAULT_TESTS))
    parser.add_argument("--spacing", action="store_true", help="add the spacing test")
    parser.add_argument("--reads", type=int, default=20, help="reads per gap of the spacing test")
    args = parser.parse_args()
    tests = list(args.tests)
    if args.spacing and "spacing" not in tests:
        tests.append("spacing")

    print(time.strftime("%Y-%m-%d %H:%M:%S"), f"{args.host}:{args.port} unit {args.unit}")
    link = Link(args.host, args.port, args.unit)
    link.connect()
    try:
        for test in TESTS:
            if test not in tests:
                continue
            if test == "spacing":
                test_spacing(link, args.reads)
            else:
                {"rest": test_rest, "exception": test_exception, "second": test_second,
                 "reconnect": test_reconnect}[test](link)
            print()
    finally:
        link.close()


if __name__ == "__main__":
    main()
