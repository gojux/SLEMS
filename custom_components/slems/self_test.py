"""Self-test of a battery: does it do what SLEMS commands, and are its values right?

SLEMS takes the battery out of the normal operation for a few minutes (like a
cell balancing run) and holds the other batteries and the consumers at their
last set points meanwhile, so the smart meter shows the effect of the tested
battery alone:

1. PREPARE: a discharging battery ramps out first.
2. IDLE: 0 W for ``IDLE_S``, the reference of the meter and the battery values.
3. CHARGE: the test power until meter and battery show at least half of it,
   then ``HOLD_S`` more for the means and until its energy counter moved; at
   most ``STEP_MAX_S``. Skipped if the battery may not charge now (e.g. full).
4. REST: 0 W for ``IDLE_S``, the reference of the discharge.
5. DISCHARGE: the test power the other way, like the charge.
6. RELEASE: back to its own logic; the result is final once that is confirmed.

The smart meter is the independent reference: the grid power minus the
reported power of the other batteries (``meter``) changes by the AC power of
the tested battery as long as house and PV stay as they are. Means are taken
over the last ``MEAN_S`` of a phase. The checks:

* effect: the meter moves in the commanded direction by at least half of the
  test power (no movement: wrong register or remote control not enabled;
  the other way round: the set point is inverted),
* power_sign / power_scale: the reported battery power changes like the meter
  (inverted sign, a factor such as kW for W),
* response: time until the meter shows half of the command,
* counters: the charge counter rises while charging, the discharge counter
  while discharging, not the other one and not by a multiple,
* soc: within 0-100 %, without jumps, not against the direction,
* release: the battery was handed back to its own logic.

A meter that moves a lot while the battery rests (house or PV changing) makes
the meter based checks *unclear* instead of failed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import statistics
from typing import Any

IDLE_S = 60.0
STEP_MAX_S = 240.0
HOLD_S = 45.0
MEAN_S = 30.0
# Test power: a share of the smaller maximum power, within these bounds.
POWER_SHARE = 0.25
MIN_POWER_W = 300.0
MAX_POWER_W = 800.0
# Below this a direction is skipped (e.g. charging a full battery).
MIN_STEP_POWER_W = 100.0
REACHED_SHARE = 0.5
NO_EFFECT_SHARE = 0.2
# Meter spread while resting above this share of the test power: unclear.
NOISE_SHARE = 0.3
SCALE_OK = (0.75, 1.33)
SCALE_WARNING = (0.5, 2.0)
SLOW_RESPONSE_S = 60.0
SOC_JUMP_PCT = 5.0
SOC_AGAINST_PCT = 1.0
# A counter rising by more than this multiple of the energy moved: wrong unit.
COUNTER_FACTOR = 10.0
# Change of the other counter counted as "not moving", as share of the right one.
COUNTER_OTHER_SHARE = 0.2


class Phase(StrEnum):
    PREPARE = "prepare"
    IDLE = "idle"
    CHARGE = "charge"
    REST = "rest"
    DISCHARGE = "discharge"
    RELEASE = "release"
    DONE = "done"


class Outcome(StrEnum):
    OK = "ok"
    WARNING = "warning"
    FAILED = "failed"
    UNCLEAR = "unclear"
    SKIPPED = "skipped"


_MEASURING = (Phase.IDLE, Phase.CHARGE, Phase.REST, Phase.DISCHARGE)
_SEVERITY = {Outcome.FAILED: 3, Outcome.WARNING: 2, Outcome.UNCLEAR: 1, Outcome.OK: 0, Outcome.SKIPPED: -1}


@dataclass(frozen=True)
class Sample:
    """One reading during the test."""

    t: float
    # Grid power minus the other batteries (+import).
    meter_w: float | None
    # Reported power of the tested battery, AC if known (+charge).
    power_w: float | None
    soc_pct: float | None
    charged_kwh: float | None = None
    discharged_kwh: float | None = None


@dataclass
class Check:
    key: str
    outcome: Outcome
    values: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "outcome": self.outcome.value, **self.values}


@dataclass
class _Record:
    phase: Phase
    start: float
    target_w: float
    samples: list[Sample] = field(default_factory=list)
    reached_meter: float | None = None
    reached_power: float | None = None

    def window(self) -> list[Sample]:
        if not self.samples:
            return []
        end = self.samples[-1].t
        return [s for s in self.samples if s.t >= end - MEAN_S]

    def mean(self, attribute: str) -> float | None:
        values = [v for s in self.window() if (v := getattr(s, attribute)) is not None]
        return statistics.fmean(values) if values else None

    def spread(self) -> float | None:
        values = [s.meter_w for s in self.window() if s.meter_w is not None]
        return statistics.pstdev(values) if len(values) >= 2 else None

    def counter(self, attribute: str) -> float | None:
        values = [v for s in self.samples if (v := getattr(s, attribute)) is not None]
        return values[-1] - values[0] if len(values) >= 2 else None


def self_test_power_w(max_charge_w: float, max_discharge_w: float) -> float:
    """Power of the test steps for a battery with these maximum powers."""
    limit = min(max_charge_w, max_discharge_w)
    return min(limit, max(MIN_POWER_W, min(MAX_POWER_W, POWER_SHARE * limit)))


def step_power_w(power_w: float, allowed_w: float) -> float:
    """Power of one direction: the test power within what is allowed now, 0 to skip it."""
    power = min(power_w, max(0.0, allowed_w))
    return power if power >= MIN_STEP_POWER_W else 0.0


class SelfTest:
    """The steps of one self-test and their evaluation."""

    def __init__(self, charge_w: float, discharge_w: float, started_at: float) -> None:
        self.charge_w = charge_w
        self.discharge_w = discharge_w
        # Wall clock (UNIX time) of the start and the end.
        self.started_at = started_at
        self.finished_at: float | None = None
        self.phase = Phase.PREPARE
        self.phase_start: float | None = None
        self.checks: list[Check] = []
        self.result: str | None = None
        self._records: dict[Phase, _Record] = {}
        self._current: _Record | None = None

    @property
    def commanding(self) -> bool:
        """Whether SLEMS sends the test powers (the battery is out of normal operation)."""
        return self.phase in _MEASURING

    @property
    def holds_others(self) -> bool:
        """Whether the other batteries and the consumers are held meanwhile."""
        return self.phase in _MEASURING

    def step(self, now: float, sample: Sample | None, ready: bool) -> float | None:
        """Next power (+charge / -discharge), None while nothing is sent."""
        if self.phase is Phase.PREPARE:
            if not ready:
                return None
            self._enter(Phase.IDLE, now, 0.0)
        record = self._current
        if record is None or not self.commanding:
            return None
        if sample is not None:
            record.samples.append(sample)
            if record.target_w:
                self._update_reached(record, sample)
        elapsed = now - record.start
        if record.target_w == 0:
            if elapsed >= IDLE_S:
                self._next(now)
        else:
            reached = (record.reached_meter, record.reached_power)
            if (
                all(t is not None for t in reached)
                and now - max(reached) >= HOLD_S  # type: ignore[type-var]
                and self._counter_moved(record)
            ) or elapsed >= STEP_MAX_S:
                self._next(now)
        return self._current.target_w if self.commanding and self._current else None

    def released(self, ok: bool | None, wall: float) -> None:
        """The battery was handed back (None: nothing had to be released)."""
        if self.phase is not Phase.RELEASE:
            return
        if ok is not None:
            self.checks.append(Check("release", Outcome.OK if ok else Outcome.FAILED))
        self._finish(wall)

    def stop(self, reason: str, wall: float) -> None:
        """End early: cancelled by the user or no longer possible (e.g. not active)."""
        self.phase = Phase.DONE
        self.result = reason
        self.finished_at = wall

    def as_dict(self) -> dict[str, Any]:
        return {
            "result": self.result,
            "phase": self.phase.value,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "charge_w": self.charge_w,
            "discharge_w": self.discharge_w,
            "checks": [check.as_dict() for check in self.checks],
        }

    # --- steps -----------------------------------------------------------------

    def _enter(self, phase: Phase, now: float, target_w: float) -> None:
        self.phase = phase
        self.phase_start = now
        self._current = _Record(phase, now, target_w)
        self._records[phase] = self._current

    def _next(self, now: float) -> None:
        phase = self.phase
        if phase is Phase.IDLE and self.charge_w:
            self._enter(Phase.CHARGE, now, self.charge_w)
        elif phase is Phase.IDLE and self.discharge_w:
            self._enter(Phase.DISCHARGE, now, -self.discharge_w)
        elif phase is Phase.CHARGE and self.discharge_w:
            self._enter(Phase.REST, now, 0.0)
        elif phase is Phase.REST:
            self._enter(Phase.DISCHARGE, now, -self.discharge_w)
        else:
            self.phase = Phase.RELEASE
            self._current = None
            self.checks = self._evaluate()

    @staticmethod
    def _counter_moved(record: _Record) -> bool:
        """Whether the counter of the direction moved (always without counters):
        a coarse counter (e.g. 0.01 kWh) needs a while at a small test power."""
        attribute = "charged_kwh" if record.target_w > 0 else "discharged_kwh"
        if all(getattr(s, attribute) is None for s in record.samples):
            return True
        change = record.counter(attribute)
        return change is not None and change != 0

    def _reference(self, phase: Phase) -> _Record | None:
        if phase is Phase.DISCHARGE and Phase.REST in self._records:
            return self._records[Phase.REST]
        return self._records.get(Phase.IDLE)

    def _update_reached(self, record: _Record, sample: Sample) -> None:
        reference = self._reference(record.phase)
        if reference is None:
            return
        half = REACHED_SHARE * abs(record.target_w)
        meter_ref = reference.mean("meter_w")
        if record.reached_meter is None and sample.meter_w is not None and meter_ref is not None:
            if abs(sample.meter_w - meter_ref) >= half:
                record.reached_meter = sample.t
        power_ref = reference.mean("power_w")
        if record.reached_power is None and sample.power_w is not None and power_ref is not None:
            # Either direction: a wrong sign is judged later.
            if abs(sample.power_w - power_ref) >= half:
                record.reached_power = sample.t
        if record.reached_power is None and all(s.power_w is None for s in record.samples):
            # No power sensor: only the meter decides.
            record.reached_power = record.reached_meter

    # --- evaluation ------------------------------------------------------------

    def _evaluate(self) -> list[Check]:
        steps = [
            (record, reference)
            for phase in (Phase.CHARGE, Phase.DISCHARGE)
            if (record := self._records.get(phase)) is not None
            and (reference := self._reference(phase)) is not None
        ]
        checks = [self._effect(record, reference) for record, reference in steps]
        checks += self._power(steps)
        checks.append(self._response(steps))
        checks.append(self._counters(steps))
        checks.append(self._soc())
        return checks

    @staticmethod
    def _deltas(record: _Record, reference: _Record) -> tuple[float | None, float | None, float | None]:
        """Change of meter and reported power against the reference, and the meter spread."""

        def delta(attribute: str) -> float | None:
            now, before = record.mean(attribute), reference.mean(attribute)
            return None if now is None or before is None else now - before

        return delta("meter_w"), delta("power_w"), reference.spread()

    def _effect(self, record: _Record, reference: _Record) -> Check:
        meter, power, spread = self._deltas(record, reference)
        target = record.target_w
        values = {
            "expected_w": round(target),
            "meter_w": None if meter is None else round(meter),
            "battery_w": None if power is None else round(power),
            "spread_w": None if spread is None else round(spread),
        }
        key = f"effect_{record.phase.value}"
        if meter is None:
            return Check(key, Outcome.UNCLEAR, values)
        along = meter * (1 if target > 0 else -1)
        size = abs(target)
        if along >= REACHED_SHARE * size:
            return Check(key, Outcome.OK, values)
        if spread is not None and spread > NOISE_SHARE * size:
            return Check(key, Outcome.UNCLEAR, values)
        if along <= -REACHED_SHARE * size:
            return Check(key, Outcome.FAILED, {**values, "reason": "reversed"})
        if abs(meter) < NO_EFFECT_SHARE * size:
            return Check(key, Outcome.FAILED, {**values, "reason": "none"})
        return Check(key, Outcome.WARNING, {**values, "reason": "partial"})

    def _power(self, steps: list[tuple[_Record, _Record]]) -> list[Check]:
        """Sign and scale of the reported power against the meter."""
        if all(s.power_w is None for record, _ in steps for s in record.samples):
            return [Check("power_sign", Outcome.SKIPPED), Check("power_scale", Outcome.SKIPPED)]
        ratios = []
        for record, reference in steps:
            meter, power, _ = self._deltas(record, reference)
            if meter is None or power is None or abs(meter) < REACHED_SHARE * abs(record.target_w):
                continue
            ratios.append(power / meter)
        if not ratios:
            return [Check("power_sign", Outcome.UNCLEAR), Check("power_scale", Outcome.UNCLEAR)]
        values = {"ratio": round(statistics.fmean(ratios), 2)}
        sign = Check("power_sign", Outcome.FAILED if any(r < 0 for r in ratios) else Outcome.OK, values)
        size = statistics.fmean(abs(r) for r in ratios)
        if SCALE_OK[0] <= size <= SCALE_OK[1]:
            scale = Outcome.OK
        elif SCALE_WARNING[0] <= size <= SCALE_WARNING[1]:
            scale = Outcome.WARNING
        else:
            scale = Outcome.FAILED
        return [sign, Check("power_scale", scale, {"factor": round(size, 2)})]

    @staticmethod
    def _response(steps: list[tuple[_Record, _Record]]) -> Check:
        values: dict[str, Any] = {}
        times = []
        for record, _ in steps:
            seconds = None if record.reached_meter is None else round(record.reached_meter - record.start, 1)
            values[f"{record.phase.value}_s"] = seconds
            if seconds is not None:
                times.append(seconds)
            if record.reached_power is not None:
                values[f"{record.phase.value}_battery_s"] = round(record.reached_power - record.start, 1)
        if not times:
            return Check("response", Outcome.UNCLEAR, values)
        return Check("response", Outcome.WARNING if max(times) > SLOW_RESPONSE_S else Outcome.OK, values)

    @staticmethod
    def _counters(steps: list[tuple[_Record, _Record]]) -> Check:
        samples = [s for record, _ in steps for s in record.samples]
        if all(s.charged_kwh is None and s.discharged_kwh is None for s in samples):
            return Check("counters", Outcome.SKIPPED)
        outcomes: list[Outcome] = []
        values: dict[str, Any] = {}
        for record, _ in steps:
            charging = record.target_w > 0
            right = record.counter("charged_kwh" if charging else "discharged_kwh")
            other = record.counter("discharged_kwh" if charging else "charged_kwh")
            duration_h = (record.samples[-1].t - record.start) / 3600 if record.samples else 0.0
            moved_kwh = abs(record.target_w) * duration_h / 1000
            values[f"{record.phase.value}_kwh"] = None if right is None else round(right, 3)
            values[f"{record.phase.value}_other_kwh"] = None if other is None else round(other, 3)
            values[f"{record.phase.value}_expected_kwh"] = round(moved_kwh, 3)
            if right is None:
                outcomes.append(Outcome.SKIPPED)
            elif other is not None and other > 0 and other > max(right, 0.0):
                outcomes.append(Outcome.FAILED)
                values["reason"] = "swapped"
            elif right < 0:
                outcomes.append(Outcome.FAILED)
                values["reason"] = "falling"
            elif right == 0:
                # Below the resolution of the counter: not checkable in the short test.
                outcomes.append(Outcome.SKIPPED)
                values.setdefault("reason", "resolution")
            elif moved_kwh > 0 and right > COUNTER_FACTOR * moved_kwh:
                outcomes.append(Outcome.FAILED)
                values["reason"] = "unit"
            elif other is not None and other > COUNTER_OTHER_SHARE * right:
                outcomes.append(Outcome.WARNING)
                values["reason"] = "both"
            else:
                outcomes.append(Outcome.OK)
        if all(o is Outcome.SKIPPED for o in outcomes):
            return Check("counters", Outcome.SKIPPED, values)
        return Check("counters", _worst(outcomes), values)

    def _soc(self) -> Check:
        records = [r for phase in _MEASURING if (r := self._records.get(phase)) is not None]
        socs = [s.soc_pct for r in records for s in r.samples if s.soc_pct is not None]
        if not socs:
            return Check("soc", Outcome.UNCLEAR)
        values: dict[str, Any] = {"min_pct": round(min(socs), 1), "max_pct": round(max(socs), 1)}
        if min(socs) < 0 or max(socs) > 100:
            return Check("soc", Outcome.FAILED, {**values, "reason": "range"})
        jump = max((abs(b - a) for a, b in zip(socs, socs[1:])), default=0.0)
        if jump > SOC_JUMP_PCT:
            return Check("soc", Outcome.WARNING, {**values, "reason": "jump", "jump_pct": round(jump, 1)})
        for record in records:
            if not record.target_w:
                continue
            values_of = [s.soc_pct for s in record.samples if s.soc_pct is not None]
            if len(values_of) < 2:
                continue
            change = (values_of[-1] - values_of[0]) * (1 if record.target_w > 0 else -1)
            if change < -SOC_AGAINST_PCT:
                return Check("soc", Outcome.WARNING, {**values, "reason": "against"})
        return Check("soc", Outcome.OK, values)

    def _finish(self, wall: float) -> None:
        self.phase = Phase.DONE
        self.finished_at = wall
        self.result = _worst([check.outcome for check in self.checks]).value
        if self.result == Outcome.UNCLEAR.value:
            self.result = Outcome.WARNING.value


def _worst(outcomes: list[Outcome]) -> Outcome:
    relevant = [o for o in outcomes if o is not Outcome.SKIPPED]
    return max(relevant, key=_SEVERITY.__getitem__) if relevant else Outcome.OK
