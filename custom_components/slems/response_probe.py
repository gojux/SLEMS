"""Probe step of a battery: the earliest time the grid meter can show its command.

The response time learner (see response) drops a measurement whose grid moved
by half the step before the earliest possible response: the grid was moved by
something else then. This module measures that limit for each battery with a
small step of its own while the other batteries and the consumers are held
(like in the self-test, see self_test):

1. WAIT: until the house load (grid minus the batteries' seen power) varied
   by less than ``QUIET_STD_W`` (standard deviation) over ``QUIET_S``, no
   battery command moved since ``SETTLED_S`` and no consumer command since
   ``CONSUMER_QUIET_S``, and while the battery runs; given up after
   ``WAIT_MAX_S`` (``not_quiet`` / ``not_running``).
2. SETTLE: the others are held from now on; ``SETTLE_S`` and at least
   ``MIN_SETTLE_REPORTS`` meter reports are the reference. Spreading by more
   than ``NOISE_W`` makes the attempt unclear.
3. STEP: the battery's held set point plus the offset (``PROBE_W``, at least
   ``MIN_PROBE_W``) for ``STEP_S``.
4. BACK: the held set point again, watched for ``BACK_S``; then the others
   are released, and after ``PAUSE_S`` the next attempt begins (``ATTEMPTS``).

Only a running battery is probed, and it keeps running: the offset never
reverses its direction and leaves at least ``RUNNING_MIN_W``. The offset goes
the way of the house (more charging with a surplus, more discharging with a
deficit); the allowed power (state of charge, BMS, power limits) comes first,
so a battery at a limit is probed with a reduction.

Every meter report counts. The step and the step back are each one
measurement: the dead time is the time of the last report that did not move
by ``MOVED_SHARE`` of the step yet (a lower bound, as the meter reports only
every half second or so), the response time the first report with
``ARRIVED_RATIO`` of it. A measurement moving the wrong way by
``AGAINST_SHARE``, beyond ``JUMP_RATIO`` or not arriving is unclear. The
result is the median of the dead times of all clear measurements; the
learner drops a measurement whose grid moved before it.

With the smart meter's power per phase reported as fast as the grid power,
the step also shows the battery's phase (see phases.classify): the phases at
rest against the last ``PHASE_WINDOW_S`` of the step.

``ProbeRecord`` keeps the result per battery and decides when a probe is due:
a new battery, a changed configuration, firmware, smart meter or controlling
integration (``fingerprint``; the learned response times are dropped then),
more than ``EARLY_SHARE`` of the last ``EARLY_COUNT`` measurements dropped as
too early, ``MAX_AGE_S`` after the last one, or by hand. A probe that ends
unclear or stopped is tried again after ``RETRY_S``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import json
import statistics
from typing import Any

from .const import (
    CONF_BATTERY_CONTROL,
    CONF_CHARGE_ENTITY,
    CONF_DEVICE,
    CONF_DISCHARGE_ENTITY,
    CONF_GRID_MODBUS,
    CONF_GRID_MODBUS_HOST,
    CONF_GRID_MODBUS_INVERTED,
    CONF_GRID_MODBUS_PORT,
    CONF_GRID_MODBUS_REGISTER,
    CONF_GRID_MODBUS_SIGN,
    CONF_GRID_MODBUS_UNIT_ID,
    CONF_GRID_PHASE_ENTITIES,
    CONF_GRID_POWER_ENTITY,
    CONF_GRID_POWER_INVERTED,
    CONF_HOST,
    CONF_INPUT_ENTITY,
    CONF_INPUT_SETPOINT_ENTITY,
    CONF_MODE_AUTO,
    CONF_MODE_CHARGE,
    CONF_MODE_DISCHARGE,
    CONF_MODE_ENTITY,
    CONF_MODE_STANDBY,
    CONF_MODEL,
    CONF_OUTPUT_ENTITY,
    CONF_OUTPUT_SETPOINT_ENTITY,
    CONF_PORT,
    CONF_POWER_ENTITY,
    CONF_POWER_INVERTED,
    CONF_POWER_SCRIPT,
    CONF_PROFILE_ID,
    CONF_PROFILE_YAML,
    CONF_RELEASE_SCRIPT,
    CONF_REMOTE_ENTITY,
    CONF_REMOTE_OFF,
    CONF_REMOTE_ON,
    CONF_SETPOINT_ENTITY,
    CONF_SETPOINT_INVERTED,
    CONF_TRANSPORT,
    CONF_UNIT_ID,
)
from .phases import Phase as GridPhase, classify
from .response import ARRIVED_RATIO, JUMP_RATIO

PROBE_W = 500.0
MIN_PROBE_W = 300.0
# Power a reduction leaves at least (the battery keeps running).
RUNNING_MIN_W = 100.0
STEP_S = 3.0
BACK_S = 3.0
SETTLE_S = 1.0
MIN_SETTLE_REPORTS = 2
NOISE_W = 50.0
# A write not done this long after the step began ends the attempt.
WRITE_MAX_S = 5.0
ATTEMPTS = 3
PAUSE_S = 10.0
MOVED_SHARE = 0.1
AGAINST_SHARE = 0.5
PHASE_WINDOW_S = 1.0

QUIET_S = 10.0
QUIET_STD_W = 50.0
# No battery command beyond the deadband for this long before an attempt, no consumer command for CONSUMER_QUIET_S.
SETTLED_S = 3.0
CONSUMER_QUIET_S = 60.0
WAIT_MAX_S = 900.0

RETRY_S = 3600.0
MAX_AGE_S = 90 * 86400.0
EARLY_COUNT = 20
EARLY_SHARE = 0.5

# Subentry data of a battery whose change is a new setup for the probe.
CONFIG_KEYS = (
    CONF_MODEL, CONF_HOST, CONF_PORT, CONF_UNIT_ID, CONF_TRANSPORT, CONF_PROFILE_ID, CONF_PROFILE_YAML,
    CONF_DEVICE, CONF_BATTERY_CONTROL, CONF_SETPOINT_ENTITY, CONF_SETPOINT_INVERTED, CONF_CHARGE_ENTITY,
    CONF_DISCHARGE_ENTITY, CONF_MODE_ENTITY, CONF_MODE_CHARGE, CONF_MODE_DISCHARGE, CONF_MODE_STANDBY,
    CONF_MODE_AUTO, CONF_REMOTE_ENTITY, CONF_REMOTE_ON, CONF_REMOTE_OFF, CONF_POWER_SCRIPT,
    CONF_RELEASE_SCRIPT, CONF_POWER_ENTITY, CONF_POWER_INVERTED, CONF_OUTPUT_ENTITY,
    CONF_OUTPUT_SETPOINT_ENTITY, CONF_INPUT_ENTITY, CONF_INPUT_SETPOINT_ENTITY,
)

# The entity SLEMS writes for a battery from entities, the first one configured.
CONTROL_ENTITY_KEYS = (
    CONF_SETPOINT_ENTITY, CONF_CHARGE_ENTITY, CONF_OUTPUT_SETPOINT_ENTITY, CONF_INPUT_SETPOINT_ENTITY, CONF_MODE_ENTITY,
)

# Configuration of the smart meter whose change counts like a new meter.
METER_KEYS = (
    CONF_GRID_POWER_ENTITY, CONF_GRID_POWER_INVERTED, *CONF_GRID_PHASE_ENTITIES, CONF_GRID_MODBUS,
    CONF_GRID_MODBUS_HOST, CONF_GRID_MODBUS_PORT, CONF_GRID_MODBUS_UNIT_ID, CONF_GRID_MODBUS_REGISTER,
    CONF_GRID_MODBUS_SIGN, CONF_GRID_MODBUS_INVERTED,
)

# Why a probe is due, by the part of the fingerprint that changed.
FINGERPRINT_REASONS = {"config": "config", "device": "firmware", "meter": "meter", "control": "integration"}


def digest(data: Mapping[str, Any], keys: tuple[str, ...] | None = None) -> str:
    """A short fingerprint of ``data`` (only ``keys`` if given, e.g. ``CONFIG_KEYS``)."""
    picked = {key: data[key] for key in sorted(data) if keys is None or key in keys}
    return hashlib.sha256(json.dumps(picked, sort_keys=True, default=str).encode()).hexdigest()[:16]


def probe_offset_w(base_w: float, house_w: float, charge_w: float, discharge_w: float) -> float | None:
    """Offset of the probe step for a battery running at ``base_w`` (+charge) with a
    house at ``house_w`` (+import, without batteries) and these allowed powers;
    None if the battery cannot be probed now."""
    if abs(base_w) < RUNNING_MIN_W:
        return None

    def room(sign: int) -> float:
        if base_w * sign > 0:
            # More of the same direction, within the allowed power.
            return max(0.0, (charge_w if sign > 0 else discharge_w) - abs(base_w))
        return abs(base_w) - RUNNING_MIN_W

    preferred = 1 if house_w < 0 else -1
    for sign in (preferred, -preferred):
        size = min(PROBE_W, room(sign))
        if size >= MIN_PROBE_W:
            return sign * size
    return None


@dataclass(frozen=True)
class Report:
    """One report of the grid meter (+import) and, if as fast, its phases."""

    t: float
    grid_w: float
    phases_w: tuple[float, float, float] | None = None


def measure(written: float, baseline: float, change_w: float, reports: list[Report]) -> dict[str, Any]:
    """One measurement: the step ``change_w`` written at ``written`` from the grid
    at ``baseline``; the reports after the write."""
    result: dict[str, Any] = {"change_w": round(change_w)}
    last_still = written
    moved = False
    arrived: float | None = None
    for report in reports:
        if report.t < written:
            continue
        along = (report.grid_w - baseline) / change_w
        if along <= -AGAINST_SHARE:
            return {**result, "reason": "against"}
        if along > JUMP_RATIO:
            return {**result, "reason": "jump"}
        if not moved:
            if along >= MOVED_SHARE:
                moved = True
            else:
                last_still = report.t
        if arrived is None and along >= ARRIVED_RATIO:
            arrived = report.t
    if arrived is None:
        return {**result, "reason": "none"}
    return {**result, "dead_s": round(last_still - written, 3), "arrived_s": round(arrived - written, 3)}


class Stage(StrEnum):
    WAIT = "wait"
    SETTLE = "settle"
    STEP = "step"
    BACK = "back"
    PAUSE = "pause"
    DONE = "done"


_HOLDING = (Stage.SETTLE, Stage.STEP, Stage.BACK)


class ResponseProbe:
    """The attempts of one probe and their evaluation (monotonic time ``now``)."""

    def __init__(self, reason: str, now: float) -> None:
        self.reason = reason
        self.stage = Stage.WAIT
        self.stage_start = now
        self.attempts = 0
        self.base_w = 0.0
        self.offset_w = 0.0
        self.measurements: list[dict[str, Any]] = []
        self.phases: list[GridPhase | None] = []
        # "ok", "unclear" or why it stopped early.
        self.result: str | None = None
        # What the next attempt waits for: "quiet" (the grid) or "running" (the battery).
        self.waiting_for = "quiet"
        self._reports: list[Report] = []
        self._baseline: float | None = None
        self._step_written: float | None = None
        self._back_written: float | None = None

    @property
    def holds_others(self) -> bool:
        """Whether the other batteries and the consumers keep their set points."""
        return self.stage in _HOLDING

    @property
    def target_w(self) -> float | None:
        """Set point of the battery while it is probed, None otherwise."""
        if self.stage is Stage.STEP:
            return self.base_w + self.offset_w
        return self.base_w if self.stage in (Stage.SETTLE, Stage.BACK) else None

    def ready(self, now: float) -> bool:
        """Whether the next attempt may begin (once the grid is quiet)."""
        if self.stage is Stage.WAIT:
            return True
        return self.stage is Stage.PAUSE and now - self.stage_start >= PAUSE_S

    def waited_too_long(self, now: float) -> bool:
        """No attempt could begin for ``WAIT_MAX_S`` (the grid not quiet)."""
        return self.stage in (Stage.WAIT, Stage.PAUSE) and now - self.stage_start >= WAIT_MAX_S

    def begin(self, now: float, base_w: float, offset_w: float) -> None:
        """An attempt begins: the battery runs at ``base_w``, the step is ``offset_w``."""
        self.attempts += 1
        self.base_w = base_w
        self.offset_w = offset_w
        self._reports = []
        self._baseline = None
        self._step_written = None
        self._back_written = None
        self._enter(Stage.SETTLE, now)

    def report(self, report: Report) -> None:
        if self.holds_others:
            self._reports.append(report)
        self.advance(report.t)

    def written(self, t: float, target_w: float) -> None:
        """The controller wrote ``target_w`` to the battery at ``t``."""
        if self.stage is Stage.STEP and self._step_written is None and round(target_w) == round(self.base_w + self.offset_w):
            self._step_written = t
        elif self.stage is Stage.BACK and self._back_written is None and round(target_w) == round(self.base_w):
            self._back_written = t

    def advance(self, now: float) -> None:
        if self.stage is Stage.SETTLE:
            if now - self.stage_start < SETTLE_S or len(self._reports) < MIN_SETTLE_REPORTS:
                return
            values = [r.grid_w for r in self._reports]
            if max(values) - min(values) > NOISE_W:
                self.measurements.append({"change_w": round(self.offset_w), "reason": "noisy"})
                self._end_attempt(now)
                return
            self._baseline = statistics.fmean(values)
            self._enter(Stage.STEP, now)
        elif self.stage is Stage.STEP:
            if self._step_written is None:
                if now - self.stage_start >= WRITE_MAX_S:
                    self.measurements.append({"change_w": round(self.offset_w), "reason": "not_written"})
                    self._end_attempt(now)
                return
            if now - self._step_written >= STEP_S:
                self._enter(Stage.BACK, now)
        elif self.stage is Stage.BACK:
            if self._back_written is None:
                if now - self.stage_start >= WRITE_MAX_S:
                    self._evaluate()
                    self._end_attempt(now)
                return
            if now - self._back_written >= BACK_S:
                self._evaluate()
                self._end_attempt(now)

    def stop(self, reason: str) -> None:
        """End early (e.g. blocked meanwhile); the measurements so far stay."""
        self.stage = Stage.DONE
        self.result = reason

    @property
    def dead_time_s(self) -> float | None:
        """Median of the dead times of the clear measurements."""
        times = [m["dead_s"] for m in self.measurements if "dead_s" in m]
        return statistics.median(times) if times else None

    @property
    def arrived_s(self) -> list[float]:
        return [m["arrived_s"] for m in self.measurements if "arrived_s" in m]

    @property
    def phase(self) -> GridPhase | None:
        """The phase all attempts that showed one agree on."""
        found = {phase for phase in self.phases if phase is not None}
        return found.pop() if len(found) == 1 else None

    def _enter(self, stage: Stage, now: float) -> None:
        self.stage = stage
        self.stage_start = now

    def _end_attempt(self, now: float) -> None:
        if self.attempts >= ATTEMPTS:
            self.stage = Stage.DONE
            self.result = "ok" if self.dead_time_s is not None else "unclear"
        else:
            self._enter(Stage.PAUSE, now)

    def _evaluate(self) -> None:
        """The step and the step back of the attempt that just ended."""
        baseline, step_written = self._baseline, self._step_written
        assert baseline is not None and step_written is not None
        end = self._back_written if self._back_written is not None else self._reports[-1].t if self._reports else step_written
        step_reports = [r for r in self._reports if step_written <= r.t < end]
        step = measure(step_written, baseline, self.offset_w, step_reports)
        self.measurements.append(step)
        plateau = [r for r in step_reports if r.t >= end - PHASE_WINDOW_S]
        if "dead_s" in step and plateau:
            self.phases.append(self._phase(plateau))
        if self._back_written is None:
            self.measurements.append({"change_w": round(-self.offset_w), "reason": "not_written"})
            return
        if not plateau:
            self.measurements.append({"change_w": round(-self.offset_w), "reason": "none"})
            return
        back_from = statistics.fmean(r.grid_w for r in plateau)
        back_reports = [r for r in self._reports if r.t >= self._back_written]
        self.measurements.append(measure(self._back_written, back_from, -self.offset_w, back_reports))

    def _phase(self, plateau: list[Report]) -> GridPhase | None:
        settle = [r for r in self._reports if self._step_written is not None and r.t < self._step_written]
        if not settle or any(r.phases_w is None for r in settle + plateau):
            return None

        def means(reports: list[Report]) -> list[float]:
            return [statistics.fmean(r.phases_w[k] for r in reports) for k in range(3)]  # type: ignore[index]

        before, after = means(settle), means(plateau)
        step = statistics.fmean(r.grid_w for r in plateau) - statistics.fmean(r.grid_w for r in settle)
        return classify(step, [a - b for a, b in zip(after, before, strict=True)])

    def as_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "stage": self.stage.value,
            "attempts": self.attempts,
            "offset_w": round(self.offset_w),
            "result": self.result,
            "measurements": self.measurements,
        }


class QuietWatch:
    """The house load of the last ``QUIET_S``: quiet enough for a probe?"""

    def __init__(self) -> None:
        self._values: list[tuple[float, float]] = []

    def add(self, t: float, house_w: float) -> None:
        self._values.append((t, house_w))
        while self._values and self._values[0][0] < t - QUIET_S:
            self._values.pop(0)

    def quiet(self, now: float) -> bool:
        values = [w for t, w in self._values if t >= now - QUIET_S]
        if len(values) < 2 or now - self._values[0][0] < QUIET_S * 0.8:
            return False
        return statistics.pstdev(values) < QUIET_STD_W


@dataclass
class ProbeRecord:
    """Result of the last probe of a battery and when the next one is due (wall clock)."""

    at: float | None = None
    reason: str | None = None
    result: str | None = None
    dead_time_s: float | None = None
    measurements: list[dict[str, Any]] = field(default_factory=list)
    phase: str | None = None
    fingerprint: dict[str, dict[str, str]] = field(default_factory=dict)
    # Result of the last probe that did not succeed since the last success.
    failed: str | None = None
    # Why a probe is due (None: only after MAX_AGE_S).
    requested: str | None = None
    # Start of the last probe that did not end with a result.
    tried_at: float | None = None

    def check(self, fingerprint: Mapping[str, Mapping[str, str]]) -> list[str]:
        """Compare the current fingerprint (part -> values); returns the parts that
        changed (their learned response times are outdated). Only values present
        on both sides are compared (e.g. a firmware version not read once)."""
        if not self.fingerprint:
            self.fingerprint = {part: dict(values) for part, values in fingerprint.items()}
            self.requested = self.requested or "new"
            return []
        changed = [
            part
            for part, values in fingerprint.items()
            if any(
                key in self.fingerprint.get(part, {}) and self.fingerprint[part][key] != value
                for key, value in values.items()
            )
        ]
        for part, values in fingerprint.items():
            self.fingerprint[part] = {**self.fingerprint.get(part, {}), **values}
        if changed:
            self.requested = FINGERPRINT_REASONS.get(changed[0], changed[0])
            self.dead_time_s = None
        return changed

    def request(self, reason: str) -> None:
        """Ask for a probe; one already asked for keeps its reason. By hand at once."""
        self.requested = self.requested or reason
        if reason == "manual":
            self.tried_at = None

    def due(self, wall: float) -> str | None:
        """Why a probe is to run now, None if none."""
        if self.requested is not None:
            if self.tried_at is not None and wall - self.tried_at < RETRY_S:
                return None
            return self.requested
        if self.at is not None and wall - self.at >= MAX_AGE_S:
            return "age"
        return None

    def finished(self, probe: ResponseProbe, wall: float) -> None:
        if probe.result == "ok":
            self.at = wall
            self.reason = probe.reason
            self.result = probe.result
            self.dead_time_s = probe.dead_time_s
            self.measurements = probe.measurements
            phase = probe.phase
            self.phase = phase.value if phase is not None else None
            self.requested = None
            self.tried_at = None
            self.failed = None
            return
        self.tried_at = wall
        self.failed = probe.result
        if probe.reason == "manual":
            self.requested = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "reason": self.reason,
            "result": self.result,
            "dead_time_s": self.dead_time_s,
            "measurements": self.measurements,
            "phase": self.phase,
            "fingerprint": self.fingerprint,
            "failed": self.failed,
            "requested": self.requested,
            "tried_at": self.tried_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> ProbeRecord:
        if not data:
            return cls()
        return cls(
            at=data.get("at"),
            reason=data.get("reason"),
            result=data.get("result"),
            dead_time_s=data.get("dead_time_s"),
            measurements=list(data.get("measurements") or []),
            phase=data.get("phase"),
            fingerprint={part: dict(values) for part, values in (data.get("fingerprint") or {}).items()},
            failed=data.get("failed"),
            requested=data.get("requested"),
            tried_at=data.get("tried_at"),
        )
