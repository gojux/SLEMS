#!/usr/bin/env python3
"""Measure how fast a Marstek Venus follows power steps, via Modbus TCP.

Standard library only (Python 3.10+). The battery accepts a single Modbus TCP
connection: pause the communication of this battery in SLEMS (battery menu ⋮)
and stop any other integration talking to it while the script runs.

The script plays a fixed sequence of set points several times:

* ``--plan steps`` (default): starts out of standby (after a short and a long
  standby, with full and small power), steps up and down in the same
  direction, reversals (charging <-> discharging) and stops.
* ``--plan warm``: whether a battery that stopped only shortly, or that holds
  a small power instead of 0 W, starts faster than one out of standby:
  restarts after stops of ``--gaps`` seconds and steps from ``--warm-power``
  watts held for ``--warm-hold`` seconds, both directions, with a start out
  of a long standby for comparison.

Set points are written like SLEMS does: only registers whose value
changed, set points before the force mode, everything again every
``REFRESH_S`` seconds.

After each command the AC power (30006) and DC power (30001) are read as fast
as the firmware allows, the inverter state (35100) every other read. A step
has "arrived" when the AC power moved by 60 % of the commanded change (the
threshold SLEMS uses at the grid meter) and "settled" at 90 %. All times count
from the start of the first register write, like the command time in SLEMS.

With ``--meter`` a SunSpec grid meter (e.g. behind a Modbus proxy) is read in
a second thread, the same register block SLEMS reads (total real power and
its scale factor). Its "arrived" time is what the SLEMS controller learns.
The sign of the meter is taken from the first large step. Nothing else may
regulate on the grid meter meanwhile: set SLEMS to "Observe only".

Every sample goes to a CSV file (meter samples to a second one); a summary
per kind of step is printed at the end. At the end, or on Ctrl+C, the battery
is handed back to its own logic.

Examples:
    python3 step_response.py 192.168.1.50
    python3 step_response.py 192.168.1.50 --meter 127.0.0.1:503
    python3 step_response.py 192.168.1.50 --power 2000 --cycles 2 --long-idle 120
    python3 step_response.py 192.168.1.50 --meter 127.0.0.1:503 --plan warm --cycles 2
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field
import socket
import statistics
import threading
import time
from typing import Any

from read_registers import FRAME_GAP_S, ModbusError, decode, read_holding
from set_power import (
    FORCE_CHARGE,
    FORCE_DISCHARGE,
    FORCE_NONE,
    MAX_POWER_W,
    REG_FORCE_MODE,
    REG_RS485_CONTROL,
    REG_SET_CHARGE_POWER,
    REG_SET_DISCHARGE_POWER,
    RS485_ENABLE,
    STATES,
    Link,
    release,
)

# Same keepalive as the SLEMS controller.
REFRESH_S = 60.0
ARRIVED_RATIO = 0.6
SETTLED_RATIO = 0.9
REG_POWER_BLOCK = 30001  # 30001 DC power ... 30006 AC power
REG_SOC = 34002
REG_STATE = 35100
# SunSpec meter: total real power, three phase powers, scale factor.
METER_BLOCK = 5
NOT_IMPLEMENTED = 0x8000
# Meter baseline before the command and final value at the end of a hold.
METER_WINDOW_S = 1.0
# A set point held shorter has not settled: the next step is measured from
# the last settled state instead of the values right before its command.
SETTLED_HOLD_S = 2.0


@dataclass
class Segment:
    target_w: int  # +charge / -discharge
    hold_s: float
    kind: str


@dataclass
class Step:
    kind: str
    previous_w: int
    target_w: int
    state_before: str
    writes: int
    write_s: float
    arrived_s: float | None = None
    settled_s: float | None = None
    state_s: float | None = None
    # Steps in the same direction do not change the inverter state.
    state_tracked: bool = True
    meter_arrived_s: float | None = None
    # Share of the commanded change the meter showed at the end of the hold.
    meter_share: float | None = None
    # AC power of the battery during the last second of the set point.
    end_w: float | None = None
    samples: list[tuple[float, int, int, str]] = field(default_factory=list)


class Battery:
    """Writes set points like the SLEMS driver and reads the power block."""

    def __init__(self, link: Link) -> None:
        self.link = link
        self.written: dict[int, int] = {}
        self.last_refresh = 0.0

    def apply(self, net_w: int, *, refresh: bool = False) -> int:
        """Write the changed registers; return the number of writes."""
        if refresh:
            self.written.clear()
            self.last_refresh = time.monotonic()
        charge = max(net_w, 0)
        discharge = max(-net_w, 0)
        mode = FORCE_CHARGE if net_w > 0 else FORCE_DISCHARGE if net_w < 0 else FORCE_NONE
        writes = 0
        for register, value in (
            (REG_RS485_CONTROL, RS485_ENABLE),
            (REG_SET_DISCHARGE_POWER, discharge),
            (REG_SET_CHARGE_POWER, charge),
            (REG_FORCE_MODE, mode),
        ):
            if self.written.get(register) == value:
                continue
            self.link.write(register, value)
            self.written[register] = value
            writes += 1
        return writes

    def power(self) -> tuple[int, int]:
        """DC power (+charge) and AC power converted to +charge."""
        words = read_holding(self.link.sock, self.link.unit, self.link._next(), REG_POWER_BLOCK, 6)
        time.sleep(FRAME_GAP_S)
        return decode(words[:1], "i16"), -decode(words[5:6], "i16")

    def state(self) -> str:
        value = self.link.read(REG_STATE)
        return STATES.get(value, str(value))


def connect(what: str, host: str, port: int) -> socket.socket:
    """Open a Modbus TCP connection or end the script with a readable reason."""
    try:
        return socket.create_connection((host, port), timeout=5)
    except ConnectionRefusedError:
        reason = "connection refused (wrong port, or another client holds the only connection?)"
    except TimeoutError:
        reason = "no answer within 5 s (wrong address or not reachable?)"
    except OSError as err:
        reason = str(err)
    raise SystemExit(f"{what} {host}:{port} not reachable: {reason}")


class Meter(threading.Thread):
    """Polls the grid meter's total real power in the background."""

    def __init__(self, host: str, port: int, unit: int, register: int, interval: float) -> None:
        super().__init__(daemon=True)
        self.sock = connect("Grid meter", host, port)
        self.unit = unit
        self.register = register
        self.interval = interval
        self.samples: list[tuple[float, float]] = []
        self.errors = 0
        self.last_error = ""
        self._lock = threading.Lock()
        self._halt = threading.Event()
        # +1: the meter rises with charging, -1: it falls; None until known.
        self.sign: int | None = None
        # Meter value without the battery, from the end of the last settled set point.
        self.without_battery: float | None = None

    def read(self, transaction: int) -> float | None:
        words = read_holding(self.sock, self.unit, transaction, self.register, METER_BLOCK)
        if words[0] == NOT_IMPLEMENTED or words[4] == NOT_IMPLEMENTED:
            return None
        return decode(words[:1], "i16") * 10.0 ** decode(words[4:5], "i16")

    def run(self) -> None:
        transaction = 0
        while not self._halt.is_set():
            transaction = (transaction + 1) & 0xFFFF
            try:
                value = self.read(transaction)
            except (ModbusError, OSError) as err:
                self.errors += 1
                self.last_error = str(err)
                if isinstance(err, OSError):
                    return
            else:
                if value is not None:
                    with self._lock:
                        self.samples.append((time.monotonic(), value))
            self._halt.wait(self.interval)

    def stop(self) -> None:
        self._halt.set()
        self.join(timeout=2)
        self.sock.close()

    def between(self, start: float, end: float) -> list[tuple[float, float]]:
        with self._lock:
            return [sample for sample in self.samples if start <= sample[0] <= end]

    def evaluate(self, step: Step, command: float, end: float, settled_before: bool) -> None:
        """Fill the meter times of a step that ended at ``end``."""
        change = step.target_w - step.previous_w
        before = [value for _, value in self.between(command - METER_WINDOW_S, command)]
        after = [value for _, value in self.between(end - METER_WINDOW_S, end)]
        if not after:
            return
        settled = end - command >= SETTLED_HOLD_S
        if not settled_before and self.sign is not None and self.without_battery is not None:
            baseline = self.without_battery + self.sign * step.previous_w
        elif before:
            baseline = statistics.median(before)
        else:
            return
        if settled and self.sign is not None:
            self.without_battery = statistics.median(after) - self.sign * step.target_w
        if not change:
            return
        moved = statistics.median(after) - baseline
        if self.sign is None:
            if abs(change) < 1000 or abs(moved) < 0.5 * abs(change):
                return
            self.sign = 1 if moved * change > 0 else -1
            self.without_battery = statistics.median(after) - self.sign * step.target_w
        step.meter_share = self.sign * moved / change
        for timestamp, value in self.between(command, end):
            if self.sign * (value - baseline) / change >= ARRIVED_RATIO:
                step.meter_arrived_s = timestamp - command
                break


def plan(power: int, small: int, hold: float, short_idle: float, long_idle: float) -> list[Segment]:
    third = power // 3
    return [
        Segment(0, long_idle, "stop discharge"),
        Segment(power, hold, f"start charge after {long_idle:g} s"),
        Segment(third, hold, "down charge"),
        Segment(power, hold, "up charge"),
        Segment(-power, hold, "reversal charge -> discharge"),
        Segment(-third, hold, "down discharge"),
        Segment(-power, hold, "up discharge"),
        Segment(power, hold, "reversal discharge -> charge"),
        Segment(0, short_idle, "stop charge"),
        Segment(-power, hold, f"start discharge after {short_idle:g} s"),
        Segment(0, short_idle, "stop discharge"),
        Segment(small, hold, f"start charge {small} W after {short_idle:g} s"),
        Segment(0, long_idle, "stop charge"),
        Segment(-power, hold, f"start discharge after {long_idle:g} s"),
        Segment(0, short_idle, "stop discharge"),
        Segment(power, hold, f"start charge after {short_idle:g} s"),
        Segment(0, short_idle, "stop charge"),
        Segment(-small, hold, f"start discharge {small} W after {short_idle:g} s"),
    ]


def plan_warm(
    power: int,
    gaps: list[float],
    warm_powers: list[int],
    hold: float,
    warm_hold: float,
    short_idle: float,
    long_idle: float,
) -> list[Segment]:
    segments = [
        Segment(0, long_idle, "stop charge"),
        Segment(power, hold, f"start charge after {long_idle:g} s"),
    ]
    for gap in gaps:
        segments += [
            Segment(0, gap, f"stop charge for {gap:g} s"),
            Segment(power, hold, f"restart charge after {gap:g} s stop"),
        ]
    segments += [
        Segment(0, short_idle, "stop charge"),
        Segment(-power, hold, f"start discharge after {short_idle:g} s"),
    ]
    for gap in gaps:
        segments += [
            Segment(0, gap, f"stop discharge for {gap:g} s"),
            Segment(-power, hold, f"restart discharge after {gap:g} s stop"),
        ]
    for warm in warm_powers:
        segments += [
            Segment(-warm, warm_hold, f"down discharge to {warm} W"),
            Segment(-power, hold, f"up discharge from {warm} W"),
        ]
    for warm in warm_powers:
        segments += [
            Segment(warm, warm_hold, f"to charge {warm} W"),
            Segment(power, hold, f"up charge from {warm} W"),
        ]
    return segments


def run_segment(
    battery: Battery,
    meter: Meter | None,
    previous_w: int,
    settled_before: bool,
    segment: Segment,
    writer: Any,
    index: int,
    started: float,
) -> Step:
    state_before = battery.state()
    _dc, baseline = battery.power()
    if not settled_before:
        # The battery's own value lags behind: measured from the set point before.
        baseline = previous_w
    command = time.monotonic()
    writes = battery.apply(segment.target_w)
    step = Step(segment.kind, previous_w, segment.target_w, state_before, writes, time.monotonic() - command)
    change = segment.target_w - previous_w
    active = ("charging", "discharging")
    expected_state = active[0] if segment.target_w > 0 else active[1] if segment.target_w < 0 else None
    step.state_tracked = state_before != expected_state
    read_state = False
    while (now := time.monotonic()) - command < segment.hold_s:
        if now - battery.last_refresh >= REFRESH_S:
            battery.apply(segment.target_w, refresh=True)
        dc, ac = battery.power()
        elapsed = time.monotonic() - command
        state = ""
        if read_state:
            state = battery.state()
            reached = state == expected_state if expected_state else state not in active
            if step.state_s is None and step.state_tracked and reached:
                step.state_s = elapsed
        read_state = not read_state
        step.samples.append((elapsed, dc, ac, state))
        writer.writerow(
            [f"{time.monotonic() - started:.3f}", index, segment.kind, segment.target_w, f"{elapsed:.3f}", dc, ac, state]
        )
        moved = (ac - baseline) / change if change else 0.0
        if step.arrived_s is None and moved >= ARRIVED_RATIO:
            step.arrived_s = elapsed
        if step.settled_s is None and moved >= SETTLED_RATIO:
            step.settled_s = elapsed
    if meter is not None:
        meter.evaluate(step, command, time.monotonic(), settled_before)
    last = [ac for elapsed, _dc, ac, _state in step.samples if elapsed >= segment.hold_s - 1.0]
    if last and segment.hold_s >= SETTLED_HOLD_S:
        step.end_w = statistics.median(last)
    return step


def seconds(value: float | None) -> str:
    return f"{value:5.2f} s" if value is not None else "   –   "


def summary(steps: list[Step]) -> None:
    print("\nSummary (median / min / max over all cycles, from the first write)")
    kinds: dict[str, list[Step]] = {}
    for step in steps:
        kinds.setdefault(step.kind, []).append(step)
    for kind, group in kinds.items():
        print(f"\n  {kind}  ({len(group)}x, {statistics.median(s.writes for s in group):g} writes, "
              f"{statistics.median(s.write_s for s in group):.2f} s writing)")
        for label, values in (
            ("60 % arrived", [s.arrived_s for s in group]),
            ("90 % settled", [s.settled_s for s in group]),
            ("state changed", [s.state_s for s in group]),
            ("meter 60 %", [s.meter_arrived_s for s in group]),
        ):
            measured = [v for v in values if v is not None]
            if label == "meter 60 %" and all(s.meter_share is None for s in group):
                continue
            if label == "state changed" and not any(s.state_tracked for s in group):
                continue
            missing = len(values) - len(measured)
            if not measured:
                print(f"    {label:<14} not reached")
                continue
            text = (
                f"{statistics.median(measured):5.2f} s / {min(measured):5.2f} s / {max(measured):5.2f} s"
            )
            if missing:
                text += f"  ({missing}x not reached)"
            print(f"    {label:<14} {text}")
        ends = [s.end_w for s in group if s.end_w is not None]
        if ends:
            print(f"    {'battery at end':<14} {statistics.median(ends):5.0f} W (AC, own measurement)")
        shares = [s.meter_share for s in group if s.meter_share is not None]
        if shares:
            print(f"    {'meter share':<14} {statistics.median(shares):5.0%} of the commanded change at the end")


def write_meter(meter: Meter, path: str, started: float) -> None:
    meter_path = path.removesuffix(".csv") + "_meter.csv"
    with open(meter_path, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["time_s", "meter_w"])
        for timestamp, value in meter.samples:
            writer.writerow([f"{timestamp - started:.3f}", f"{value:g}"])
    count = len(meter.samples)
    rate = count / (meter.samples[-1][0] - meter.samples[0][0]) if count > 1 else 0.0
    errors = f", {meter.errors} errors (last: {meter.last_error})" if meter.errors else ""
    sign = {1: "rises", -1: "falls", None: "unknown"}[meter.sign]
    print(f"\nGrid meter: {count} samples ({rate:.1f}/s){errors}; with charging it {sign}. Samples: {meter_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("host")
    parser.add_argument("--port", type=int, default=502)
    parser.add_argument("--unit", type=int, default=1)
    parser.add_argument("--power", type=int, default=1500, help="W of the large steps (default 1500)")
    parser.add_argument("--plan", choices=("steps", "warm"), default="steps", help="sequence (default steps)")
    parser.add_argument("--small", type=int, default=300, help="W of the small starts (default 300)")
    parser.add_argument(
        "--gaps", type=float, nargs="+", default=[0.5, 1.0, 2.0, 5.0, 10.0], help="warm: stops before a restart (s)"
    )
    parser.add_argument(
        "--warm-power", type=int, nargs="+", default=[30, 50], help="warm: small powers held instead of 0 W"
    )
    parser.add_argument("--warm-hold", type=float, default=30.0, help="warm: seconds of the small power (default 30)")
    parser.add_argument("--hold", type=float, default=10.0, help="seconds per set point (default 10)")
    parser.add_argument("--short-idle", type=float, default=10.0, help="short standby in seconds (default 10)")
    parser.add_argument("--long-idle", type=float, default=90.0, help="long standby in seconds (default 90)")
    parser.add_argument("--cycles", type=int, default=3, help="repetitions of the sequence (default 3)")
    parser.add_argument("--min-soc", type=float, default=30.0, help="refuse to start below this SoC (default 30)")
    parser.add_argument("--max-soc", type=float, default=85.0, help="refuse to start above this SoC (default 85)")
    parser.add_argument("--yes", action="store_true", help="start without asking for confirmation")
    parser.add_argument("--csv", help="sample file (default step_response_<host>_<time>.csv)")
    parser.add_argument("--meter", metavar="HOST:PORT", help="also read this SunSpec grid meter")
    parser.add_argument("--meter-unit", type=int, default=1)
    parser.add_argument("--meter-register", type=int, default=40206, help="total real power (default 40206)")
    parser.add_argument("--meter-interval", type=float, default=0.2, help="seconds between meter reads (default 0.2)")
    args = parser.parse_args()

    power = min(max(args.power, 300), MAX_POWER_W)
    small = min(max(args.small, 100), power)
    if args.plan == "warm":
        warm_powers = [min(max(w, 10), power // 2) for w in args.warm_power]
        segments = plan_warm(
            power, args.gaps, warm_powers, args.hold, args.warm_hold, args.short_idle, args.long_idle
        )
    else:
        segments = plan(power, small, args.hold, args.short_idle, args.long_idle)
    # About one second per set point for the reads and writes around the command.
    duration = sum(s.hold_s + 1 for s in segments) * args.cycles
    path = args.csv or f"step_response_{args.host}_{time.strftime('%Y%m%d-%H%M%S')}.csv"
    if not args.yes:
        print("Before the test, in SLEMS:")
        if args.meter:
            print('  1. Set the operating mode to "Observe only" (nothing else may regulate on the grid meter).')
        else:
            print('  1. Set the operating mode to "Observe only" (or keep the other batteries controlled).')
        print(f"  2. Pause the communication of the battery {args.host} (battery menu ⋮), long enough")
        print(f"     for about {duration / 60:.0f} min; pausing again extends the pause.")
        print("  Afterwards set the operating mode back and end the pause.")
        try:
            input("Press Enter to start, Ctrl+C to cancel. ")
        except (KeyboardInterrupt, EOFError):
            print("\nCancelled, nothing done.")
            return

    with connect("Battery", args.host, args.port) as sock:
        link = Link(sock, args.unit)
        soc = link.read(REG_SOC) / 10
        print(f"State of charge {soc:g} %")
        if not args.min_soc <= soc <= args.max_soc:
            print(f"Outside {args.min_soc:g}-{args.max_soc:g} %: the BMS could limit the steps. Nothing done.")
            return
        meter: Meter | None = None
        if args.meter:
            meter_host, _, meter_port = args.meter.rpartition(":")
            meter = Meter(meter_host, int(meter_port), args.meter_unit, args.meter_register, args.meter_interval)
            try:
                value = meter.read(0)
            except (ModbusError, OSError) as err:
                raise SystemExit(f"Grid meter {args.meter} does not answer: {err}") from None
            if value is None:
                print("The grid meter does not report a total power at this register. Nothing done.")
                return
            print(f"Grid meter {value:g} W")
            meter.start()
        print(f"{len(segments)} set points x {args.cycles} cycles, about {duration / 60:.0f} min "
              f"(Ctrl+C stops early). Samples: {path}")
        battery = Battery(link)
        steps: list[Step] = []
        with open(path, "w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(["time_s", "step", "kind", "target_w", "since_command_s", "dc_w", "ac_w", "state"])
            started = time.monotonic()
            try:
                battery.apply(0, refresh=True)
                previous = 0
                settled_before = True
                index = 0
                for cycle in range(args.cycles):
                    print(f"\nCycle {cycle + 1}/{args.cycles}")
                    for segment in segments:
                        step = run_segment(
                            battery, meter, previous, settled_before, segment, writer, index, started
                        )
                        settled_before = segment.hold_s >= SETTLED_HOLD_S
                        index += 1
                        if previous == segment.target_w:
                            continue
                        steps.append(step)
                        print(
                            f"  {previous:>6} -> {segment.target_w:>6} W  {segment.kind:<34} "
                            f"from {step.state_before:<11} {step.writes} writes {step.write_s:.2f} s  "
                            f"60 % {seconds(step.arrived_s)}  90 % {seconds(step.settled_s)}  "
                            f"state {seconds(step.state_s)}"
                            + (f"  meter {seconds(step.meter_arrived_s)}" if meter else "")
                            + (f"  end {step.end_w:.0f} W" if step.end_w is not None else "")
                        )
                        previous = segment.target_w
            except KeyboardInterrupt:
                print("Stopped.")
            except (ModbusError, OSError) as err:
                print(f"Communication error: {err}")
            finally:
                try:
                    release(link)
                except (ModbusError, OSError) as err:
                    print(f"Release failed ({err}): run set_power.py {args.host} --release")
        if meter is not None:
            meter.stop()
            write_meter(meter, path, started)
        summary(steps)


if __name__ == "__main__":
    main()
