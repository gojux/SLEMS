"""The grid phase a battery or consumer is connected to, learned from the smart meter's phases.

All devices behind the meter are learned together: the batteries regulate
against every change of the other loads within seconds, so a step of one
device never shows up alone on the meter. The readings are averaged over
blocks of ``BLOCK_S``; the change of each phase from one block to the next is
explained as the sum of the changes of all devices, each with its share per
phase (least squares over all blocks, older blocks fading with
``HALF_LIFE_S``). The PV power is one more column. Block means also take up
sensors that report up to a minute late (e.g. a heat pump) and the
regulating back and forth of the batteries.

A device's shares are compared with the patterns of one phase (1, 0, 0) and
of three phases (⅓, ⅓, ⅓): the phase is taken once the shares are measured
precisely enough after at least ``MIN_CHANGES`` clear changes of its power
and point clearly to one pattern. Devices whose phase is known count with
their pattern, which makes the shares of the others more exact.

Batteries are columns as well, but their phase is not taken from the
statistics: they follow the loads SLEMS does not measure and the control
moves them together, which mixes up their shares (three batteries on L1,
L2 and L3 look like three phase devices). Their phase comes from the
self-test (see self_test), where the other devices are held.

The statistics start from the recorder's history (see the coordinator) and
go on with the live readings; a phase that differs from the learned one is
reported as a change. The assignment is only shown and warned about; the
control does not use it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
import math
from typing import Any

import numpy as np

# A step smaller than this is lost in the other loads of a phase (self-test).
STEP_W = 300.0
# One phase: its change is this share of the device's step; the others stay below OTHER_SHARE.
SINGLE_RANGE = (0.7, 1.3)
OTHER_SHARE = 0.25
# Three phases: each phase changes by this share of the step.
THREE_RANGE = (0.2, 0.5)

BLOCK_S = 120.0
# A block counts with readings over at least this share of it.
MIN_BLOCK_COVER = 0.5
HALF_LIFE_S = 3 * 86400.0
# Taken from the recorder when the statistics start anew.
HISTORY_S = 2 * 86400.0
# Block changes of a device of at least MIN_CHANGE_W (fading like the statistics) needed for its shares.
MIN_CHANGES = 6
MIN_CHANGE_W = 150.0
# Standard error of a share above this: not measured precisely enough yet.
MAX_ERROR = 0.15
# Cosine between the shares and the best pattern, and its lead over the second.
MIN_SIMILARITY = 0.85
MIN_LEAD = 0.2
# Share of the device's power along the best pattern (1: all of it shows up on the meter)
# below this: it hardly shows up (e.g. a three phase load the batteries on L1, L2 and L3
# take over alike) and the direction is noise. Batteries still estimated freely and late
# sensors take up part of it, so it stays below 1.
MIN_GAIN = 0.4
PV_COLUMN = "pv"


class Phase(StrEnum):
    """Where a device is connected."""

    L1 = "l1"
    L2 = "l2"
    L3 = "l3"
    THREE_PHASE = "three_phase"


SINGLE = (Phase.L1, Phase.L2, Phase.L3)
PATTERNS: dict[Phase, tuple[float, float, float]] = {
    Phase.L1: (1.0, 0.0, 0.0),
    Phase.L2: (0.0, 1.0, 0.0),
    Phase.L3: (0.0, 0.0, 1.0),
    Phase.THREE_PHASE: (1 / 3, 1 / 3, 1 / 3),
}


def label(phase: Phase) -> str:
    """The phase in texts that are not translated (placeholders): L1 … or L1+L2+L3."""
    return "L1+L2+L3" if phase is Phase.THREE_PHASE else phase.value.upper()


def classify(device_step_w: float, phase_steps_w: Sequence[float]) -> Phase | None:
    """The phase a step of ``device_step_w`` shows up on while nothing else changes; None if it
    does not fit one pattern.

    Only magnitudes count, so a meter that reports its phases with the
    opposite sign gives the same result.
    """
    if abs(device_step_w) < STEP_W or len(phase_steps_w) != 3:
        return None
    shares = [abs(step / device_step_w) for step in phase_steps_w]
    for phase, share in zip(SINGLE, shares, strict=True):
        others = [s for p, s in zip(SINGLE, shares, strict=True) if p is not phase]
        if SINGLE_RANGE[0] <= share <= SINGLE_RANGE[1] and all(s < OTHER_SHARE for s in others):
            return phase
    if all(THREE_RANGE[0] <= share <= THREE_RANGE[1] for share in shares):
        return Phase.THREE_PHASE
    return None


def pattern_of(shares: Sequence[float]) -> Phase | None:
    """The pattern the shares of a device point to clearly, None if none does."""
    size = math.sqrt(sum(s * s for s in shares))
    if size == 0:
        return None
    similarity = sorted(
        (
            sum(s * p for s, p in zip(shares, pattern, strict=True))
            / (size * math.sqrt(sum(p * p for p in pattern))),
            phase,
        )
        for phase, pattern in PATTERNS.items()
    )
    (second, _), (best, phase) = similarity[-2], similarity[-1]
    if best < MIN_SIMILARITY or best - second < MIN_LEAD:
        return None
    pattern = PATTERNS[phase]
    gain = sum(s * p for s, p in zip(shares, pattern, strict=True)) / sum(p * p for p in pattern)
    return phase if gain >= MIN_GAIN else None


class PhaseLearner:
    """Phase of one device: the learned or chosen phase and what to ask."""

    def __init__(self) -> None:
        self.phase: Phase | None = None
        # Chosen by the user: kept against the measurement, a contradiction is still reported.
        self.manual = False
        # Latest shares per phase measured for the device (None: not measured precisely enough).
        self.shares: tuple[float, float, float] | None = None
        # Measured another phase than ``phase``: ask whether it changed.
        self.changed_to: Phase | None = None
        # Learned anew (the button): the phase before, to ask if the same comes out again.
        self.relearn_from: Phase | None = None
        self.confirm: Phase | None = None

    def observe(self, measured: Phase) -> bool:
        """A phase measured for the device; returns whether anything changed."""
        before = (self.phase, self.changed_to, self.confirm)
        if self.phase is None:
            self.phase = measured
            if self.relearn_from is not None and measured is self.relearn_from:
                self.confirm = measured
            self.relearn_from = None
        else:
            self.changed_to = None if measured is self.phase else measured
        return before != (self.phase, self.changed_to, self.confirm)

    def relearn(self) -> None:
        """Forget the phase and learn it anew; if the same comes out, ask to confirm it."""
        self.relearn_from = self.phase
        self.phase = None
        self.manual = False
        self.changed_to = None
        self.confirm = None

    def choose(self, phase: Phase, *, manual: bool) -> None:
        """The user's answer: ``phase`` from now on (``manual``: not the one measured)."""
        self.phase = phase
        self.manual = manual
        self.changed_to = None
        self.confirm = None
        self.relearn_from = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase.value if self.phase else None,
            "manual": self.manual,
            "changed_to": self.changed_to.value if self.changed_to else None,
            "relearn_from": self.relearn_from.value if self.relearn_from else None,
            "confirm": self.confirm.value if self.confirm else None,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> PhaseLearner:
        learner = cls()
        if not data:
            return learner

        def phase(key: str) -> Phase | None:
            value = data.get(key)
            return Phase(value) if value in {p.value for p in Phase} else None

        learner.phase = phase("phase")
        learner.manual = bool(data.get("manual"))
        learner.changed_to = phase("changed_to")
        learner.relearn_from = phase("relearn_from")
        learner.confirm = phase("confirm")
        return learner


@dataclass
class _Block:
    start: float
    end: float
    count: int
    x: np.ndarray
    y: np.ndarray


class PhaseModel:
    """The statistics of all devices behind the meter and their phases."""

    def __init__(self) -> None:
        self.learners: dict[str, PhaseLearner] = {}
        self.batteries: frozenset[str] = frozenset()
        # Device ids in the order of the columns; the PV power is the last column.
        self.devices: tuple[str, ...] = ()
        self._reset_statistics()

    @property
    def columns(self) -> tuple[str, ...]:
        return (*self.devices, PV_COLUMN)

    @property
    def empty(self) -> bool:
        """Whether nothing has been learned yet (the recorder's history is taken first)."""
        return self.weight == 0

    def learner(self, device: str) -> PhaseLearner:
        return self.learners.setdefault(device, PhaseLearner())

    def configure(self, devices: Iterable[str], batteries: Iterable[str]) -> bool:
        """The devices behind the meter; other devices start the statistics anew (returns True)."""
        devices = tuple(devices)
        self.batteries = frozenset(batteries)
        self.learners = {d: self.learners.get(d) or PhaseLearner() for d in devices}
        if devices == self.devices:
            return False
        self.devices = devices
        self._reset_statistics()
        return True

    def restart(self) -> None:
        """Start the statistics anew (learning a phase again); the phases stay."""
        self._reset_statistics()

    def _reset_statistics(self) -> None:
        n = len(self.devices) + 1
        self.xx = np.zeros((n, n))
        self.xy = np.zeros((n, 3))
        self.yy = np.zeros(3)
        # Block changes of at least MIN_CHANGE_W per column (fading).
        self.changes = np.zeros(n)
        # Sum of the weights of the block changes (fading).
        self.weight = 0.0
        self._last_x = np.zeros(n)
        self._block: _Block | None = None
        self._previous: _Block | None = None

    def add(
        self,
        now: float,
        readings: Mapping[str, float | None],
        phases_w: Sequence[float | None],
        pv_w: float | None = None,
        three_phase_w: float = 0.0,
    ) -> bool:
        """One reading (every few seconds): the power of each device (+ import: a consumer's
        power, a battery's charge), the phases (+ import) and the PV power. ``three_phase_w``
        is power known to be spread evenly (a wallbox charging on three phases).

        A device without a reading keeps its last value. Returns whether a block change was
        added to the statistics."""
        if len(phases_w) != 3 or any(p is None for p in phases_w):
            return False
        x = self._last_x.copy()
        for i, device in enumerate(self.devices):
            if (value := readings.get(device)) is not None:
                x[i] = value
        if pv_w is not None:
            x[-1] = pv_w
        self._last_x = x
        y = np.array(phases_w, dtype=float) - three_phase_w / 3
        block = self._block
        added = False
        if block is not None and now - block.start >= BLOCK_S:
            added = self._close(block)
            block = None
        if block is None:
            block = self._block = _Block(now, now, 0, np.zeros_like(x), np.zeros(3))
        block.end = now
        block.count += 1
        block.x += x
        block.y += y
        return added

    def _close(self, block: _Block) -> bool:
        previous = self._previous
        self._previous = block if block.end - block.start >= MIN_BLOCK_COVER * BLOCK_S else None
        if previous is None or self._previous is None or block.start - previous.end > BLOCK_S:
            return False
        dx = block.x / block.count - previous.x / previous.count
        dy = block.y / block.count - previous.y / previous.count
        fade = 0.5 ** ((block.end - previous.end) / HALF_LIFE_S)
        self.xx = fade * self.xx + np.outer(dx, dx)
        self.xy = fade * self.xy + np.outer(dx, dy)
        self.yy = fade * self.yy + dy * dy
        self.changes = fade * self.changes + (np.abs(dx) >= MIN_CHANGE_W)
        self.weight = fade * self.weight + 1.0
        return True

    def shares(self, device: str) -> tuple[float, float, float] | None:
        """The shares per phase measured for ``device``; None while not precise enough."""
        fit = self.fit(device)
        if fit is None or max(fit[1]) > MAX_ERROR:
            return None
        return fit[0]

    def fit(self, device: str) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
        """The shares per phase of ``device`` and their standard errors; None without enough
        changes of its power. The other devices with a known phase count with their pattern."""
        if device not in self.devices or self.weight == 0:
            return None
        index = self.devices.index(device)
        fixed: dict[int, tuple[float, float, float]] = {}
        for i, other in enumerate(self.devices):
            learner = self.learners.get(other)
            if other != device and learner is not None and learner.phase is not None:
                fixed[i] = PATTERNS[learner.phase]
        free = [
            i for i in range(len(self.columns)) if i not in fixed and self.changes[i] >= MIN_CHANGES
        ]
        if index not in free:
            return None
        beta = np.zeros((len(self.columns), 3))
        for i, pattern in fixed.items():
            beta[i] = pattern
        # Batteries the control moves alike are columns that cannot be told apart: the pseudo
        # inverse splits them evenly, the other columns stay exact.
        inverse = np.linalg.pinv(self.xx[np.ix_(free, free)])
        right = self.xy[free] - self.xx[np.ix_(free, list(fixed))] @ beta[list(fixed)]
        beta[free] = inverse @ right
        rss = self.yy - 2 * np.einsum("ik,ik->k", beta, self.xy) + np.einsum("ik,ij,jk->k", beta, self.xx, beta)
        dof = self.weight - len(free)
        if dof <= 0:
            return None
        position = free.index(index)
        errors = np.sqrt(np.maximum(rss, 0.0) / dof * inverse[position, position])
        return (
            (float(beta[index, 0]), float(beta[index, 1]), float(beta[index, 2])),
            (float(errors[0]), float(errors[1]), float(errors[2])),
        )

    def diagnostics(self) -> dict[str, Any]:
        """State of the statistics per device (rounded), for the diagnostics download."""
        devices = {}
        for i, device in enumerate(self.devices):
            fit = self.fit(device)
            devices[device] = {
                **self.learner(device).as_dict(),
                "battery": device in self.batteries,
                "changes": round(float(self.changes[i]), 1),
                "shares": None if fit is None else [round(v, 3) for v in fit[0]],
                "errors": None if fit is None else [round(v, 3) for v in fit[1]],
            }
        return {
            "blocks": round(self.weight, 1),
            "pv_changes": round(float(self.changes[-1]), 1),
            "devices": devices,
        }

    def evaluate(self) -> bool:
        """Compare the shares of every consumer with the patterns; returns whether a phase or a
        question changed."""
        changed = False
        for device in self.devices:
            learner = self.learner(device)
            learner.shares = self.shares(device)
            if learner.shares is None or device in self.batteries:
                continue
            measured = pattern_of(learner.shares)
            if measured is not None:
                changed |= learner.observe(measured)
        return changed

    def as_dict(self) -> dict[str, Any]:
        return {
            "learners": {device: learner.as_dict() for device, learner in self.learners.items()},
            "devices": list(self.devices),
            "xx": self.xx.tolist(),
            "xy": self.xy.tolist(),
            "yy": self.yy.tolist(),
            "changes": self.changes.tolist(),
            "weight": self.weight,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> PhaseModel:
        model = cls()
        if not data:
            return model
        if "learners" not in data:
            # Stored before the statistics: only the learners, by device.
            model.learners = {device: PhaseLearner.from_dict(d) for device, d in data.items()}
            return model
        model.learners = {device: PhaseLearner.from_dict(d) for device, d in (data.get("learners") or {}).items()}
        model.devices = tuple(data.get("devices") or ())
        model._reset_statistics()
        n = len(model.columns)
        try:
            xx = np.array(data["xx"], dtype=float)
            xy = np.array(data["xy"], dtype=float)
            yy = np.array(data["yy"], dtype=float)
            changes = np.array(data["changes"], dtype=float)
            weight = float(data["weight"])
        except (KeyError, TypeError, ValueError):
            return model
        if xx.shape == (n, n) and xy.shape == (n, 3) and yy.shape == (3,) and changes.shape == (n,):
            model.xx, model.xy, model.yy, model.changes, model.weight = xx, xy, yy, changes, weight
        return model


# Readings of a time series: (time, value), sorted by time; the value holds until the next one.
Series = Sequence[tuple[float, float | None]]
REPLAY_STEP_S = 5.0


class _Cursor:
    """Walks through a series in time order."""

    def __init__(self, series: Series) -> None:
        self._series = series
        self._index = -1

    def at(self, t: float) -> float | None:
        series, index = self._series, self._index
        while index + 1 < len(series) and series[index + 1][0] <= t:
            index += 1
        self._index = index
        return series[index][1] if index >= 0 else None


def replay(
    model: PhaseModel,
    start: float,
    end: float,
    phases: Sequence[Series],
    devices: Mapping[str, Series],
    pv: Sequence[Series] = (),
    active_phases: Mapping[str, Series] | None = None,
) -> None:
    """Feed recorded series (already in W, + import) into the model as the live readings would be
    (with an evaluation after each block), every ``REPLAY_STEP_S`` from ``start`` to before ``end``. ``active_phases``: the active phases of
    a wallbox; on more than one its power counts as spread evenly."""
    phase_cursors = [_Cursor(series) for series in phases]
    device_cursors = {device: _Cursor(series) for device, series in devices.items()}
    pv_cursors = [_Cursor(series) for series in pv]
    count_cursors = {device: _Cursor(series) for device, series in (active_phases or {}).items()}
    t = start
    while t < end:
        readings: dict[str, float | None] = {device: c.at(t) for device, c in device_cursors.items()}
        three_phase_w = 0.0
        for device, cursor in count_cursors.items():
            count, power = cursor.at(t), readings.get(device)
            if count is not None and count != 1 and power is not None:
                three_phase_w += power
                readings[device] = 0.0
        pv_values = [v for c in pv_cursors if (v := c.at(t)) is not None]
        if model.add(
            t, readings, [c.at(t) for c in phase_cursors], sum(pv_values) if pv_values else None, three_phase_w
        ):
            model.evaluate()
        t += REPLAY_STEP_S
