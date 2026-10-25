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
6. RELEASE: back to its own logic straight from the last step; the meter
   shows whether it really left the test power (up to ``RELEASE_OBSERVE_S``,
   the others still held). The result is final then.

A step whose meter runs the wrong way, or by more than ``TOO_LARGE_FACTOR``
times the test power, for ``ABORT_S`` ends the whole test at once: the
battery is released instead of running on with a wrong set point.

The smart meter is the independent reference: the grid power minus the
reported power of the other batteries (``meter``) changes by the AC power of
the tested battery as long as house and PV stay as they are. Means are taken
over the last ``MEAN_S`` of a phase. The checks:

* effect: the meter moves in the commanded direction by at least half of the
  test power (no movement: wrong register or remote control not enabled;
  the other way round: the set point is inverted; more than twice: a wrong
  factor of the set point),
* power_sign / power_scale: the reported battery power changes like the meter
  (inverted sign, a factor such as kW for W),
* response: time until the meter shows half of the command,
* counters: the charge counter rises while charging, the discharge counter
  while discharging, not the other one and not by a multiple,
* soc: within 0-100 %, without jumps, not against the direction,
* release: the battery was handed back to its own logic. The driver's read
  back alone is not enough (a wrong profile confirms itself): released to
  standby, battery and meter go back to the rest; released to its own logic
  (auto), it leaves the test power. A battery whose own logic happens to hold
  the same power is unclear, one that stays there in standby failed.

With the smart meter's power per phase the test also shows the battery's
phase (``grid_phase``): its step shows up on one phase alone (see
phases.classify, against the battery's own reported step, which the house
load does not move). With both directions the charge is compared with the
discharge directly: twice the step, and a change before them (e.g. another
battery handing over during the rest) cancels out. If another battery's
reported power moved between the compared windows, the comparison shows
nothing.

A meter that moves a lot while the battery rests (house or PV changing) makes
the meter based checks *unclear* instead of failed (unless the meter matches
the battery's own reported change), and such a step does not count for the
power checks either; a step below ``MIN_POWER_W`` (a nearly full
or empty battery) is marked ``small``. A test whose worst check is unclear
ends *unclear*: nothing found, only not all of it checkable.

The trial of a battery with a Modbus profile (``TrialSpec``, see trial.py) is
the same test with more phases and checks of the profile, reported apart
(``trial_checks``). The registers the profile writes are read with the
samples whenever ``registers_due`` asks for them:

* QUIET before anything is written: the registers are read every
  ``REGISTER_READ_S`` for ``QUIET_S``. One changing by itself is likely a
  measured value, not a set point (a shifted address); one outside its range
  at rest cannot be taken over. Either ends the test without any write and
  without a release (the release would write the same registers). The state
  of charge and the probe register jumping by more than ``SOC_JUMP_PCT``
  (of the probe's range) between two reads meanwhile is a warning: likely a
  shifted read address (e.g. a power below 1000 W read as 0.1 %).
* A small first step (``TrialSpec.small_w``) before each direction, then
  ``SETTLE_S`` at 0 W, then the test power: a step running the wrong way or
  beyond ``SMALL_TOO_LARGE_FACTOR`` times for ``SMALL_ABORT_S`` ends the test
  before the full power is sent (a wrong factor of the set point).
* Late read back: once a full step shows its effect, it is written once more
  completely (``wants_refresh``) and then nothing at all is written for
  ``TrialSpec.late_s`` (``writes_paused``, no keep-alive) while the registers
  are read. A register falling back to a value of a release state (the
  firmware went back to its own logic, its time-out is shorter than the
  keep-alive) or changing otherwise (another system writes it) fails, a power
  cut to a lower value (a limit of the firmware) is a warning.
* After the release the registers are read at once and after
  ``RELEASE_LATE_S``: those the release writes hold its values, the others
  are within their range at rest.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
import statistics
from typing import Any

from .phases import OTHER_SHARE, Phase as GridPhase, classify

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
# A meter this many times the test power along the command: wrong factor.
TOO_LARGE_FACTOR = 2.0
# How long a wrong direction or a too large effect lasts before the test ends.
ABORT_S = 10.0
# Watching the meter after the release: at most this long, decided on the
# mean of the last RELEASE_MEAN_S (and not before).
RELEASE_OBSERVE_S = 60.0
RELEASE_MEAN_S = 15.0
SOC_JUMP_PCT = 5.0
SOC_AGAINST_PCT = 1.0
# A counter rising by more than this multiple of the energy moved: wrong unit.
COUNTER_FACTOR = 10.0
# Change of the other counter counted as "not moving", as share of the right one.
COUNTER_OTHER_SHARE = 0.2

# Trial of a profile (see the module docstring).
QUIET_S = 60.0
REGISTER_READ_S = 10.0
SMALL_W = 100.0
# The small step ends this long after it showed, at the latest after SMALL_MAX_S.
SMALL_HOLD_S = 10.0
SMALL_MAX_S = 60.0
SMALL_TOO_LARGE_FACTOR = 3.0
SMALL_ABORT_S = 5.0
SETTLE_S = 15.0
LATE_S = 90.0
# The late read back ends this long before a keep-alive the profile needs.
LATE_MARGIN_S = 5.0
RELEASE_LATE_S = 90.0


class Phase(StrEnum):
    PREPARE = "prepare"
    QUIET = "quiet"
    IDLE = "idle"
    SMALL_CHARGE = "small_charge"
    SETTLE = "settle"
    CHARGE = "charge"
    REST = "rest"
    SMALL_DISCHARGE = "small_discharge"
    DISCHARGE = "discharge"
    RELEASE = "release"
    RELEASE_LATE = "release_late"
    DONE = "done"


class Outcome(StrEnum):
    OK = "ok"
    WARNING = "warning"
    FAILED = "failed"
    UNCLEAR = "unclear"
    SKIPPED = "skipped"


_MEASURING = (
    Phase.IDLE, Phase.SMALL_CHARGE, Phase.SETTLE, Phase.CHARGE, Phase.REST, Phase.SMALL_DISCHARGE, Phase.DISCHARGE,
)
_SMALL = (Phase.SMALL_CHARGE, Phase.SMALL_DISCHARGE)
_SEVERITY = {Outcome.FAILED: 3, Outcome.WARNING: 2, Outcome.UNCLEAR: 1, Outcome.OK: 0, Outcome.SKIPPED: -1}

# Raw values of the write registers by address (None: not read).
Registers = Mapping[int, float | None]


@dataclass(frozen=True)
class TrialRegister:
    """A register a profile writes, as the trial checks it."""

    address: int
    rests_at: Callable[[float], bool]
    # Value the release to the battery's release state writes (None: not written).
    released: float | None = None
    # Values of all release states: a register returning to one fell back.
    fallback: frozenset[float] = frozenset()
    # Holds a power set point (one raw unit = ``scale`` W).
    power: bool = False
    scale: float = 1.0


@dataclass(frozen=True)
class TrialSpec:
    """What the trial of a profile checks."""

    registers: tuple[TrialRegister, ...]
    small_w: float = SMALL_W
    keepalive_s: float | None = None
    # Raw range of the probe register (its value is read at rest, see ``reads_probe``).
    probe_range: tuple[float, float] | None = None

    @property
    def late_s(self) -> float:
        """Duration of the late read back: up to shortly before a keep-alive."""
        if self.keepalive_s is None:
            return LATE_S
        return max(0.0, min(LATE_S, self.keepalive_s - LATE_MARGIN_S))


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
    # The smart meter's power per phase (+import), None without it.
    phases_w: tuple[float | None, float | None, float | None] | None = None
    # Reported power of the other batteries, always in the same order.
    others_w: tuple[float | None, ...] = ()
    # The write registers of a trial, read when ``registers_due`` asked for them.
    registers: Registers | None = None
    # Raw value of the probe register, read with them while ``reads_probe``.
    probe: float | None = None


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
    # The meter ran the commanded way; since when it runs the wrong way or too far (sample time).
    ran_along: bool = False
    against_since: float | None = None
    excess_since: float | None = None
    # Why the test ended in this step ("reversed", "too_large").
    aborted: str | None = None
    # Trial: start of the late read back (monotonic) and the registers read in it.
    late_start: float | None = None
    late_reads: list[tuple[float, Registers]] = field(default_factory=list)

    def window(self, span: float = MEAN_S) -> list[Sample]:
        if not self.samples:
            return []
        end = self.samples[-1].t
        return [s for s in self.samples if s.t >= end - span]

    def mean(self, attribute: str, span: float = MEAN_S) -> float | None:
        values = [v for s in self.window(span) if (v := getattr(s, attribute)) is not None]
        return statistics.fmean(values) if values else None

    def phase_means(self) -> list[float] | None:
        means = []
        for k in range(3):
            values = [v for s in self.window() if s.phases_w is not None and (v := s.phases_w[k]) is not None]
            if not values:
                return None
            means.append(statistics.fmean(values))
        return means

    def others_means(self) -> list[float | None]:
        window = self.window()
        count = max((len(s.others_w) for s in window), default=0)
        means: list[float | None] = []
        for i in range(count):
            values = [v for s in window if i < len(s.others_w) and (v := s.others_w[i]) is not None]
            means.append(statistics.fmean(values) if values else None)
        return means

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

    def __init__(
        self,
        charge_w: float,
        discharge_w: float,
        started_at: float,
        release_state: str = "auto",
        trial: TrialSpec | None = None,
    ) -> None:
        self.charge_w = charge_w
        self.discharge_w = discharge_w
        # State the battery is released to ("auto" or "standby", see ReleaseState).
        self.release_state = release_state
        # The trial of a profile (None: a plain self-test).
        self.trial = trial
        # Wall clock (UNIX time) of the start and the end.
        self.started_at = started_at
        self.finished_at: float | None = None
        self.phase = Phase.PREPARE
        self.phase_start: float | None = None
        self.checks: list[Check] = []
        self.trial_checks: list[Check] = []
        self.result: str | None = None
        self._records: dict[Phase, _Record] = {}
        self._current: _Record | None = None
        # The last step before the release and the samples after it (None: not released yet).
        self._last_step: _Record | None = None
        self._released: _Record | None = None
        # Trial: the full step following the settling, the register reads at
        # rest and after the release, and the time of the last read.
        self._after_settle: Phase | None = None
        self._quiet_reads: list[tuple[float, Registers]] = []
        # Trial: state of charge and probe register at rest (time, value; None: not read).
        self._quiet_values: list[tuple[float, float | None, float | None]] = []
        self._release_reads: list[tuple[float, Registers]] = []
        self._read_at: float | None = None
        self._release_failed = False

    @property
    def commanding(self) -> bool:
        """Whether SLEMS sends the test powers (the battery is out of normal operation)."""
        return self.phase in _MEASURING

    @property
    def holds_others(self) -> bool:
        """Whether the other batteries and the consumers are held meanwhile."""
        return self.phase in _MEASURING or self.phase is Phase.RELEASE

    @property
    def awaiting_release(self) -> bool:
        """Whether the battery is to be released and that has not happened yet."""
        return self.phase is Phase.RELEASE and self._released is None

    @property
    def wants_refresh(self) -> bool:
        """Trial: the full step showed its effect; it is to be written once more
        completely, then the late read back begins (``refreshed``)."""
        record = self._current
        return (
            self.trial is not None
            and self.trial.late_s >= REGISTER_READ_S
            and self.phase in (Phase.CHARGE, Phase.DISCHARGE)
            and record is not None
            and record.late_start is None
            and record.reached_meter is not None
            and record.reached_power is not None
        )

    def refreshed(self, now: float) -> None:
        """The complete write ``wants_refresh`` asked for succeeded."""
        if self.wants_refresh and self._current is not None:
            self._current.late_start = now

    def writes_paused(self, now: float) -> bool:
        """Trial: nothing at all is to be written (late read back, no keep-alive)."""
        return self._late_running(now)

    @property
    def reads_probe(self) -> bool:
        """Trial: whether the probe register is to be read with the write registers."""
        return self.trial is not None and self.trial.probe_range is not None and self.phase is Phase.QUIET

    def registers_due(self, now: float) -> bool:
        """Trial: whether the next sample is to carry the write registers."""
        if self.trial is None:
            return False
        due = self._read_at is None or now - self._read_at >= REGISTER_READ_S
        if self.phase is Phase.QUIET:
            return due
        if self._late_running(now):
            return due or not self._current.late_reads  # type: ignore[union-attr]
        if self.phase in (Phase.RELEASE, Phase.RELEASE_LATE) and self._released is not None:
            if not self._release_reads:
                return True
            return now - self._released.start >= RELEASE_LATE_S and len(self._release_reads) < 2
        return False

    def _late_running(self, now: float) -> bool:
        record = self._current
        return (
            self.trial is not None
            and record is not None
            and record.late_start is not None
            and now < record.late_start + self.trial.late_s
        )

    def step(self, now: float, sample: Sample | None, ready: bool, wall: float | None = None) -> float | None:
        """Next power (+charge / -discharge), None while nothing is sent; ``wall`` (UNIX
        time) ends a test whose release was watched long enough."""
        wall = now if wall is None else wall
        if sample is not None and sample.registers is not None:
            self._read_at = sample.t
        if self.phase in (Phase.RELEASE, Phase.RELEASE_LATE):
            self._step_release(now, sample, wall)
            return None
        if self.phase is Phase.PREPARE:
            if not ready:
                return None
            if self.trial is not None:
                self.phase = Phase.QUIET
                self.phase_start = now
            else:
                self._enter(Phase.IDLE, now, 0.0)
        if self.phase is Phase.QUIET:
            self._step_quiet(now, sample, wall)
            return self._current.target_w if self.commanding and self._current else None
        record = self._current
        if record is None or not self.commanding:
            return None
        if sample is not None:
            record.samples.append(sample)
            if sample.registers is not None and record.late_start is not None and sample.t >= record.late_start:
                if sample.t <= record.late_start + self.trial.late_s:  # type: ignore[union-attr]
                    record.late_reads.append((sample.t, sample.registers))
            if record.target_w:
                self._update_reached(record, sample)
                wrong = (
                    self._small_runs_wrong(record, sample)
                    if record.phase in _SMALL
                    else self._runs_wrong(record, sample)
                )
                if wrong is not None:
                    record.aborted = wrong
                    self._release(now)
                    return None
        elapsed = now - record.start
        if record.target_w == 0:
            if elapsed >= (SETTLE_S if record.phase is Phase.SETTLE else IDLE_S):
                self._next(now)
        elif record.phase in _SMALL:
            shown = [t for t in (record.reached_meter, record.reached_power) if t is not None]
            if (shown and now - min(shown) >= SMALL_HOLD_S) or elapsed >= SMALL_MAX_S:
                self._next(now)
        elif not self._late_running(now):
            reached = (record.reached_meter, record.reached_power)
            late_done = self.trial is None or self.trial.late_s < REGISTER_READ_S or record.late_start is not None
            if (
                all(t is not None for t in reached)
                and now - max(reached) >= HOLD_S  # type: ignore[type-var]
                and self._counter_moved(record)
                and late_done
            ) or elapsed >= STEP_MAX_S:
                self._next(now)
        return self._current.target_w if self.commanding and self._current else None

    def _step_quiet(self, now: float, sample: Sample | None, wall: float) -> None:
        """Trial: the registers at rest before anything is written."""
        if sample is None:
            return
        self._quiet_values.append((sample.t, sample.soc_pct, sample.probe))
        if sample.registers is None:
            return
        self._quiet_reads.append((sample.t, sample.registers))
        check = self._quiet_check(final=sample.t - self._quiet_reads[0][0] >= QUIET_S)
        if check is None:
            return
        self.trial_checks.append(check)
        self.trial_checks.append(quiet_values_check(self.trial, self._quiet_values))  # type: ignore[arg-type]
        if check.outcome is Outcome.OK:
            self._enter(Phase.IDLE, now, 0.0)
        else:
            # Nothing was written: no release either.
            self._finish(wall)

    def _step_release(self, now: float, sample: Sample | None, wall: float) -> None:
        if self._released is None or sample is None:
            return
        self._released.samples.append(sample)
        if sample.registers is not None:
            self._release_reads.append((sample.t, sample.registers))
        if self.phase is Phase.RELEASE and (check := self._release_check(now)) is not None:
            self.checks.append(check)
            if self.trial is None:
                self._finish(wall)
                return
            self.phase = Phase.RELEASE_LATE
            self.phase_start = now
        if self.phase is Phase.RELEASE_LATE and (
            len(self._release_reads) >= 2 or now - self._released.start >= 2 * RELEASE_LATE_S
        ):
            self.trial_checks.append(self._trial_release_check())
            self._finish(wall)

    def phase_steps(self) -> dict[str, dict[str, Any]]:
        """The compared windows: charge against discharge if both ran, else each step against its
        rest. Per comparison the battery's step (its reported power, else the meter), each phase's
        change and the largest change of another battery's reported power."""
        charge, discharge = self._records.get(Phase.CHARGE), self._records.get(Phase.DISCHARGE)
        if charge is not None and discharge is not None:
            pairs = {"both": (charge, discharge)}
        else:
            pairs = {
                phase.value: (record, reference)
                for phase in (Phase.CHARGE, Phase.DISCHARGE)
                if (record := self._records.get(phase)) is not None
                and (reference := self._reference(phase)) is not None
            }
        steps: dict[str, dict[str, Any]] = {}
        for key, (record, reference) in pairs.items():
            meter, power, _ = self._deltas(record, reference)
            after, before = record.phase_means(), reference.phase_means()
            step = power if power is not None else meter
            if step is None or after is None or before is None:
                continue
            others = [
                abs(a - b)
                for a, b in zip(record.others_means(), reference.others_means())
                if a is not None and b is not None
            ]
            steps[key] = {
                "step_w": step,
                "phases_w": [a - b for a, b in zip(after, before, strict=True)],
                "others_w": max(others, default=0.0),
            }
        return steps

    def grid_phase(self) -> GridPhase | None:
        """The phase the steps showed up on; None if no step shows one, they disagree or the
        phases are not measured."""
        found = {
            phase
            for step in self.phase_steps().values()
            if step["others_w"] <= OTHER_SHARE * abs(step["step_w"])
            and (phase := classify(step["step_w"], step["phases_w"])) is not None
        }
        return found.pop() if len(found) == 1 else None

    def released(self, ok: bool | None, wall: float, now: float) -> None:
        """The driver handed the battery back (None: nothing had to be released; False:
        not confirmed). After a step the meter is watched before the test ends."""
        if not self.awaiting_release:
            return
        if ok is None:
            self._finish(wall)
        elif not ok:
            self.checks.append(Check("release", Outcome.FAILED, {"state": self.release_state, "reason": "readback"}))
            self._release_failed = True
            if self.trial is not None:
                self.trial_checks.append(self._trial_release_check())
            self._finish(wall)
        elif self._last_step is None:
            self.checks.append(Check("release", Outcome.OK, {"state": self.release_state}))
            if self.trial is None:
                self._finish(wall)
            else:
                self.phase = Phase.RELEASE_LATE
                self._released = _Record(Phase.RELEASE, now, 0.0)
        else:
            self._released = _Record(Phase.RELEASE, now, 0.0)

    def stop(self, reason: str, wall: float) -> None:
        """End early: cancelled by the user or no longer possible (e.g. not active)."""
        self.phase = Phase.DONE
        self.result = reason
        self.finished_at = wall

    def as_dict(self) -> dict[str, Any]:
        trial = None
        if self.trial is not None:
            trial = {
                "result": _worst([check.outcome for check in self.trial_checks]).value if self.trial_checks else None,
                "checks": [check.as_dict() for check in self.trial_checks],
                # The self-test part on its own (None: it did not run, e.g. the registers changed at rest).
                "self_test_result": _worst([check.outcome for check in self.checks]).value if self.checks else None,
            }
        return {
            "result": self.result,
            "trial": trial,
            "phase": self.phase.value,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "charge_w": self.charge_w,
            "discharge_w": self.discharge_w,
            "checks": [check.as_dict() for check in self.checks],
            "phase_steps": {
                key: {
                    "step_w": round(step["step_w"]),
                    "phases_w": [round(w) for w in step["phases_w"]],
                    "others_w": round(step["others_w"]),
                }
                for key, step in self.phase_steps().items()
            },
            "grid_phase": None if (phase := self.grid_phase()) is None else phase.value,
        }

    def register_reads(self) -> dict[str, Any] | None:
        """The write registers read so far in a trial (diagnostics; times from the start of each part)."""
        if self.trial is None:
            return None

        def reads(items: list[tuple[float, Registers]], start: float | None) -> list[dict[str, Any]]:
            return [
                {"t_s": round(t - (start if start is not None else items[0][0]), 1), **{str(a): v for a, v in read.items()}}
                for t, read in items
            ]

        late = {
            phase.value: reads(record.late_reads, record.late_start)
            for phase in (Phase.CHARGE, Phase.DISCHARGE)
            if (record := self._records.get(phase)) is not None and record.late_reads
        }
        return {
            "quiet": reads(self._quiet_reads, None),
            "late": late,
            "release": reads(self._release_reads, self._released.start if self._released else None),
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
            self._start_step(now, Phase.CHARGE)
        elif phase is Phase.IDLE and self.discharge_w:
            self._start_step(now, Phase.DISCHARGE)
        elif phase in _SMALL:
            self._after_settle = Phase.CHARGE if phase is Phase.SMALL_CHARGE else Phase.DISCHARGE
            self._enter(Phase.SETTLE, now, 0.0)
        elif phase is Phase.SETTLE:
            full = self._after_settle or Phase.CHARGE
            self._enter(full, now, self.charge_w if full is Phase.CHARGE else -self.discharge_w)
        elif phase is Phase.CHARGE and self.discharge_w:
            self._enter(Phase.REST, now, 0.0)
        elif phase is Phase.REST:
            self._start_step(now, Phase.DISCHARGE)
        else:
            self._release(now)

    def _start_step(self, now: float, full: Phase) -> None:
        """A step of a direction, in a trial the small one first."""
        power = self.charge_w if full is Phase.CHARGE else self.discharge_w
        sign = 1 if full is Phase.CHARGE else -1
        if self.trial is not None:
            small = Phase.SMALL_CHARGE if full is Phase.CHARGE else Phase.SMALL_DISCHARGE
            self._enter(small, now, sign * min(self.trial.small_w, power))
        else:
            self._enter(full, now, sign * power)

    def _release(self, now: float) -> None:
        """Evaluate the steps and hand the battery back (see ``released``)."""
        if self._current is not None and self._current.target_w:
            self._last_step = self._current
        self.phase = Phase.RELEASE
        self.phase_start = now
        self._current = None
        self.checks = self._evaluate()
        if self.trial is not None:
            self.trial_checks += self._evaluate_trial()

    def _runs_wrong(self, record: _Record, sample: Sample) -> str | None:
        """Why the step has to end the test now: the meter runs the wrong way (and never
        ran the right one) or too far for ``ABORT_S``, confirmed by the battery's own
        power if it reports one; None while the house moves the meter as much."""
        reference = self._reference(record.phase)
        if reference is None or sample.meter_w is None:
            return None
        meter_ref = reference.mean("meter_w")
        spread = reference.spread()
        size = abs(record.target_w)
        if meter_ref is None or (spread is not None and spread > NOISE_SHARE * size):
            return None
        sign = 1 if record.target_w > 0 else -1
        along = (sample.meter_w - meter_ref) * sign
        power_ref = reference.mean("power_w")
        power = None
        if sample.power_w is not None and power_ref is not None:
            power = (sample.power_w - power_ref) * sign
        if along >= REACHED_SHARE * size:
            record.ran_along = True
        against = (
            not record.ran_along
            and along <= -REACHED_SHARE * size
            and (power is None or power <= -REACHED_SHARE * size)
        )
        excess = along >= TOO_LARGE_FACTOR * size and (power is None or power >= TOO_LARGE_FACTOR * size)
        if not against:
            record.against_since = None
        elif record.against_since is None:
            record.against_since = sample.t
        if not excess:
            record.excess_since = None
        elif record.excess_since is None:
            record.excess_since = sample.t
        if record.against_since is not None and sample.t - record.against_since >= ABORT_S:
            return "reversed"
        if record.excess_since is not None and sample.t - record.excess_since >= ABORT_S:
            return "too_large"
        return None

    def _small_runs_wrong(self, record: _Record, sample: Sample) -> str | None:
        """Why the small step has to end the test now: beyond ``SMALL_TOO_LARGE_FACTOR``
        times (the meter while the rest was quiet enough for that, else the battery's own
        power) or the wrong way (the meter while the rest was quiet, confirmed by the
        battery's power if it reports one) for ``SMALL_ABORT_S``."""
        reference = self._reference(record.phase)
        if reference is None:
            return None
        size = abs(record.target_w)
        limit = SMALL_TOO_LARGE_FACTOR * size
        sign = 1 if record.target_w > 0 else -1
        meter_ref, power_ref, spread = reference.mean("meter_w"), reference.mean("power_w"), reference.spread()
        meter = None if meter_ref is None or sample.meter_w is None else (sample.meter_w - meter_ref) * sign
        power = None if power_ref is None or sample.power_w is None else (sample.power_w - power_ref) * sign
        def quiet_for(share: float) -> bool:
            return meter is not None and (spread is None or spread <= share)

        if quiet_for(NOISE_SHARE * limit):
            excess = meter >= limit and (power is None or power >= limit)  # type: ignore[operator]
        else:
            excess = power is not None and power >= limit
        if meter is not None and meter >= REACHED_SHARE * size:
            record.ran_along = True
        against = (
            not record.ran_along
            and quiet_for(NOISE_SHARE * size)
            and meter <= -REACHED_SHARE * size  # type: ignore[operator]
            and (power is None or power <= -REACHED_SHARE * size)
        )
        if not against:
            record.against_since = None
        elif record.against_since is None:
            record.against_since = sample.t
        if not excess:
            record.excess_since = None
        elif record.excess_since is None:
            record.excess_since = sample.t
        if record.against_since is not None and sample.t - record.against_since >= SMALL_ABORT_S:
            return "reversed"
        if record.excess_since is not None and sample.t - record.excess_since >= SMALL_ABORT_S:
            return "too_large"
        return None

    def _quiet_check(self, *, final: bool) -> Check | None:
        """Trial: the registers at rest; None while watching and nothing found."""
        assert self.trial is not None
        return quiet_check(self.trial, self._quiet_reads, final=final)

    def _evaluate_trial(self) -> list[Check]:
        """Trial: the small steps and the late read back, per direction that ran."""
        checks = []
        for small, full in ((Phase.SMALL_CHARGE, Phase.CHARGE), (Phase.SMALL_DISCHARGE, Phase.DISCHARGE)):
            if (record := self._records.get(small)) is not None:
                checks.append(self._small_check(record))
            if (record := self._records.get(full)) is not None:
                checks.append(self._late_check(record))
        return checks

    def _small_check(self, record: _Record) -> Check:
        key = f"trial_{record.phase.value}"
        size = abs(record.target_w)
        reference = self._reference(record.phase)
        values: dict[str, Any] = {"expected_w": round(record.target_w)}
        if record.aborted is not None:
            return Check(key, Outcome.FAILED, {**values, "reason": record.aborted, "aborted": True})
        if reference is None:
            return Check(key, Outcome.UNCLEAR, values)

        def delta(attribute: str) -> float | None:
            now, before = record.mean(attribute, SMALL_HOLD_S), reference.mean(attribute)
            return None if now is None or before is None else now - before

        meter, power, spread = delta("meter_w"), delta("power_w"), reference.spread()
        values |= {
            "meter_w": None if meter is None else round(meter),
            "battery_w": None if power is None else round(power),
            "spread_w": None if spread is None else round(spread),
        }
        sign = 1 if record.target_w > 0 else -1
        limit = SMALL_TOO_LARGE_FACTOR * size
        meter_counts = meter is not None and (spread is None or spread <= NOISE_SHARE * limit)
        along = meter * sign if meter_counts else (power * sign if power is not None else None)  # type: ignore[operator]
        if along is None:
            return Check(key, Outcome.UNCLEAR, {**values, "reason": "noisy"})
        if along > limit:
            return Check(key, Outcome.FAILED, {**values, "reason": "too_large"})
        if along >= REACHED_SHARE * size:
            return Check(key, Outcome.OK, values)
        if along <= -REACHED_SHARE * size and power is not None and power * sign <= -REACHED_SHARE * size:
            return Check(key, Outcome.FAILED, {**values, "reason": "reversed"})
        return Check(key, Outcome.UNCLEAR, {**values, "reason": "none"})

    def _late_check(self, record: _Record) -> Check:
        """Trial: the registers while nothing was written after the step showed."""
        assert self.trial is not None
        key = f"trial_late_{record.phase.value}"
        window = self.trial.late_s
        values: dict[str, Any] = {"window_s": round(window)}
        if window < REGISTER_READ_S:
            return Check(key, Outcome.SKIPPED, {**values, "reason": "keepalive"})
        if record.late_start is None:
            return Check(key, Outcome.UNCLEAR, {**values, "reason": "not_reached"})
        reads = record.late_reads
        if len(reads) < 2:
            return Check(key, Outcome.UNCLEAR, {**values, "reason": "unreadable"})
        base = reads[0][1]
        changes = []
        unread = False
        for register in self.trial.registers:
            before = base.get(register.address)
            if before is None:
                unread = True
                continue
            for t, read in reads[1:]:
                after = read.get(register.address)
                if after is None:
                    unread = True
                    continue
                if after == before:
                    continue
                kind = change_kind(register, before, after)
                change = {
                    "address": register.address, "before": _raw(before), "after": _raw(after),
                    "after_s": round(t - record.late_start), "kind": kind,
                }
                if kind == "clamped":
                    change["limit_w"] = round(after * register.scale)
                changes.append(change)
                break
        values["changes"] = changes
        kinds = {change["kind"] for change in changes}
        if kinds - {"clamped"}:
            return Check(key, Outcome.FAILED, {**values, "reason": "fallback" if "fallback" in kinds else "changed"})
        if kinds:
            return Check(key, Outcome.WARNING, {**values, "reason": "clamped"})
        if unread:
            return Check(key, Outcome.UNCLEAR, {**values, "reason": "unreadable"})
        return Check(key, Outcome.OK, values)

    def _trial_release_check(self) -> Check:
        """Trial: the registers at once and ``RELEASE_LATE_S`` after the release."""
        assert self.trial is not None
        values: dict[str, Any] = {"state": self.release_state}
        if self._release_failed:
            return Check("trial_release", Outcome.FAILED, {**values, "reason": "readback"})
        start = self._released.start if self._released is not None else 0.0
        wrong, unread = release_problems(self.trial, self._release_reads, start)
        unread = unread or len(self._release_reads) < 2
        if wrong:
            return Check("trial_release", Outcome.FAILED, {**values, "reason": "wrong", "registers": wrong})
        if unread:
            return Check("trial_release", Outcome.UNCLEAR, {**values, "reason": "unreadable"})
        return Check("trial_release", Outcome.OK, values)

    def _release_check(self, now: float) -> Check | None:
        """The release judged at the meter (see the module docstring), None while watching."""
        observed = self._released
        last = self._last_step
        reference = self._reference(last.phase) if last is not None else None
        if observed is None or last is None or reference is None:
            return None
        elapsed = now - observed.start
        final = elapsed >= RELEASE_OBSERVE_S
        values: dict[str, Any] = {"state": self.release_state}
        size = abs(last.target_w)
        # The meter unless the house moved it as much as the step, then the battery's own power.
        attribute = "meter_w"
        if (spread := reference.spread()) is not None and spread > NOISE_SHARE * size:
            attribute = "power_w"
        step, rest = last.mean(attribute), reference.mean(attribute)
        after = observed.mean(attribute, RELEASE_MEAN_S)
        if step is None or rest is None or after is None:
            return Check("release", Outcome.UNCLEAR, {**values, "reason": "noisy"}) if final else None
        change = step - rest
        values |= {"step_w": round(change), "change_w": round(after - step)}
        if abs(change) < NO_EFFECT_SHARE * size:
            # The step did not show: nothing to compare the release with.
            return Check("release", Outcome.UNCLEAR, {**values, "reason": "no_step"})
        if elapsed < RELEASE_MEAN_S:
            return None
        back = (step - after) / change
        if self.release_state == "standby":
            if back >= 1 - REACHED_SHARE:
                return Check("release", Outcome.OK, values)
            if not final:
                return None
            if back < NO_EFFECT_SHARE:
                return Check("release", Outcome.FAILED, {**values, "reason": "held"})
            return Check("release", Outcome.WARNING, {**values, "reason": "partial"})
        # Its own logic often only covers the house (zero export): a smaller change counts.
        if abs(after - step) >= NO_EFFECT_SHARE * size:
            return Check("release", Outcome.OK, values)
        return Check("release", Outcome.UNCLEAR, {**values, "reason": "same"}) if final else None

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
        if phase in (Phase.DISCHARGE, Phase.SMALL_DISCHARGE) and Phase.REST in self._records:
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
        if record.aborted is not None:
            return Check(key, Outcome.FAILED, {**values, "reason": record.aborted, "aborted": True})
        if meter is None:
            return Check(key, Outcome.UNCLEAR, values)
        along = meter * (1 if target > 0 else -1)
        size = abs(target)
        if spread is not None and spread > NOISE_SHARE * size:
            # The house moves the meter as much: only a change that matches the battery's own counts.
            if along >= REACHED_SHARE * size and power is not None and abs(meter - power) <= NO_EFFECT_SHARE * size:
                return Check(key, Outcome.OK, values)
            if size < MIN_POWER_W:
                values["reason"] = "small"
            return Check(key, Outcome.UNCLEAR, values)
        if along > TOO_LARGE_FACTOR * size:
            return Check(key, Outcome.FAILED, {**values, "reason": "too_large"})
        if along >= REACHED_SHARE * size:
            return Check(key, Outcome.OK, values)
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
            meter, power, spread = self._deltas(record, reference)
            size = abs(record.target_w)
            if meter is None or power is None or abs(meter) < REACHED_SHARE * size:
                continue
            if spread is not None and spread > NOISE_SHARE * size:
                # The meter moved with the house, not (only) with the battery.
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
        self.result = _worst([check.outcome for check in self.checks + self.trial_checks]).value


def _worst(outcomes: list[Outcome]) -> Outcome:
    relevant = [o for o in outcomes if o is not Outcome.SKIPPED]
    return max(relevant, key=_SEVERITY.__getitem__) if relevant else Outcome.OK


def quiet_check(spec: TrialSpec, reads: list[tuple[float, Registers]], *, final: bool) -> Check | None:
    """The write registers read at rest, nothing written meanwhile: one changing by
    itself or outside its range at rest fails, an unread one is unclear; None
    while nothing was found and the reads are not ``final``."""
    first_t, first = reads[0]
    t, latest = reads[-1]
    values: dict[str, Any] = {"reads": len(reads)}
    unread = [r.address for r in spec.registers if latest.get(r.address) is None]
    if unread:
        return Check("trial_quiet", Outcome.UNCLEAR, {**values, "reason": "unreadable", "registers": unread})
    outside = [
        {"address": r.address, "value": _raw(latest[r.address])}  # type: ignore[arg-type]
        for r in spec.registers
        if not r.rests_at(latest[r.address])  # type: ignore[arg-type]
    ]
    if outside:
        return Check("trial_quiet", Outcome.FAILED, {**values, "reason": "outside_rest", "registers": outside})
    changed = [
        {"address": r.address, "before": _raw(first[r.address]), "after": _raw(latest[r.address]),  # type: ignore[arg-type]
         "after_s": round(t - first_t)}
        for r in spec.registers
        if first.get(r.address) is not None and latest[r.address] != first[r.address]
    ]
    if changed:
        return Check("trial_quiet", Outcome.FAILED, {**values, "reason": "changing", "registers": changed})
    return Check("trial_quiet", Outcome.OK, values) if final else None


def quiet_values_check(spec: TrialSpec, values: list[tuple[float, float | None, float | None]]) -> Check:
    """State of charge and probe register at rest: a jump by more than
    ``SOC_JUMP_PCT`` (of the probe's range) between two reads is a warning."""
    spans = {"soc_pct": 100.0}
    if spec.probe_range is not None and spec.probe_range[1] > spec.probe_range[0]:
        spans["probe"] = spec.probe_range[1] - spec.probe_range[0]
    jumps = []
    for index, key in enumerate(("soc_pct", "probe"), start=1):
        if key not in spans:
            continue
        reads = [(item[0], item[index]) for item in values if item[index] is not None]
        for (t1, before), (t2, after) in zip(reads, reads[1:]):
            if abs(after - before) > SOC_JUMP_PCT / 100 * spans[key]:  # type: ignore[operator]
                jumps.append({
                    "address": key, "before": _raw(round(before, 1)), "after": _raw(round(after, 1)),  # type: ignore[arg-type]
                    "after_s": round(t2 - t1),
                })
                break
    if jumps:
        return Check("trial_quiet_values", Outcome.WARNING, {"reason": "jump", "registers": jumps})
    if not any(item[1] is not None or item[2] is not None for item in values):
        return Check("trial_quiet_values", Outcome.SKIPPED)
    return Check("trial_quiet_values", Outcome.OK)


def change_kind(register: TrialRegister, before: float, after: float) -> str:
    """How a register differs from what was written: "fallback" to a value of a
    release state, "clamped" (a power cut to a lower value) or "changed"."""
    if after in register.fallback and before not in register.fallback:
        return "fallback"
    if register.power and 0 < after < before:
        return "clamped"
    return "changed"


def release_problems(
    spec: TrialSpec, reads: list[tuple[float, Registers]], start: float
) -> tuple[list[dict[str, Any]], bool]:
    """Registers not as the release left them (those it writes hold its values,
    the others are within their range at rest), and whether one was not read."""
    wrong = []
    unread = False
    for register in spec.registers:
        for t, read in reads:
            value = read.get(register.address)
            if value is None:
                unread = True
                continue
            ok = value == register.released if register.released is not None else register.rests_at(value)
            if not ok:
                wrong.append({
                    "address": register.address, "value": _raw(value),
                    "expected": None if register.released is None else _raw(register.released),
                    "after_s": round(t - start),
                })
                break
    return wrong, unread


def _raw(value: float) -> int | float:
    """A raw register value as read (an integer stays one)."""
    return int(value) if float(value).is_integer() else value
