"""The grid phase a battery or consumer is connected to, learned from the smart meter's phases.

A step of a device's power (at least ``STEP_W``, steady before and after) shows
up at the meter on its phase: on one phase with about the same size, or a third
on each for a three phase device. Each step is a vote; other devices switching
at the same moment make a step inconclusive, so a phase is only taken after
``MIN_VOTES`` agreeing votes, and a change only after ``CHANGE_VOTES`` votes in
a row for another phase. The assignment is only shown and warned about; the
control does not use it.
"""

from __future__ import annotations

from collections import Counter, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

# A step smaller than this is lost in the other loads of a phase.
STEP_W = 300.0
# Steady before a step (the mean of this window is the level before).
BEFORE_S = 15.0
# After a step: the meter and the device's sensor may report later (start, end of the window).
AFTER_START_S = 5.0
AFTER_END_S = 20.0
# Steady: the values of a window spread by at most this share of the step.
STEADY_SHARE = 0.2
# One phase: its change is this share of the device's step; the others stay below OTHER_SHARE.
SINGLE_RANGE = (0.7, 1.3)
OTHER_SHARE = 0.25
# Three phases: each phase changes by this share of the step.
THREE_RANGE = (0.2, 0.5)
MIN_VOTES = 3
AGREEMENT = 0.75
CHANGE_VOTES = 3
KEEP_VOTES = 8


class Phase(StrEnum):
    """Where a device is connected."""

    L1 = "l1"
    L2 = "l2"
    L3 = "l3"
    THREE_PHASE = "three_phase"


SINGLE = (Phase.L1, Phase.L2, Phase.L3)


def label(phase: Phase) -> str:
    """The phase in texts that are not translated (placeholders): L1 … or L1+L2+L3."""
    return "L1+L2+L3" if phase is Phase.THREE_PHASE else phase.value.upper()


def classify(device_step_w: float, phase_steps_w: Sequence[float]) -> Phase | None:
    """The phase a step of ``device_step_w`` shows up on; None if it does not fit one pattern.

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


@dataclass
class _Sample:
    time: float
    device_w: float
    phases_w: tuple[float, float, float]


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


class PhaseLearner:
    """Phase of one device: votes from its power steps, the learned or chosen phase and what to ask."""

    def __init__(self) -> None:
        self.phase: Phase | None = None
        # Chosen by the user: kept against the votes, a contradiction is still reported.
        self.manual = False
        self.votes: deque[Phase] = deque(maxlen=KEEP_VOTES)
        # Votes in a row for another phase than ``phase``: ask whether it changed.
        self.changed_to: Phase | None = None
        # Learned anew (the button): the phase before, to ask if the same comes out again.
        self.relearn_from: Phase | None = None
        self.confirm: Phase | None = None
        self._samples: deque[_Sample] = deque()
        # A step being watched: (time, device and phase means before it).
        self._step: tuple[float, float, tuple[float, float, float]] | None = None

    def add(self, now: float, device_w: float | None, phases_w: Sequence[float | None]) -> Phase | None:
        """A reading of the device and the three phases; returns the vote of a step that just ended."""
        if device_w is None or len(phases_w) != 3 or any(p is None for p in phases_w):
            # A gap: no step can be judged across it.
            self._samples.clear()
            self._step = None
            return None
        sample = _Sample(now, device_w, tuple(phases_w))  # type: ignore[arg-type]
        while self._samples and self._samples[0].time < now - BEFORE_S - AFTER_END_S:
            self._samples.popleft()
        vote = None
        if self._step is None:
            before = [s for s in self._samples if s.time >= now - BEFORE_S]
            if len(before) >= 2:
                level = _mean([s.device_w for s in before])
                spread = max(s.device_w for s in before) - min(s.device_w for s in before)
                if abs(device_w - level) >= STEP_W and spread <= STEADY_SHARE * abs(device_w - level):
                    means = tuple(_mean([s.phases_w[i] for s in before]) for i in range(3))
                    self._step = (now, level, means)  # type: ignore[assignment]
        elif now >= self._step[0] + AFTER_END_S:
            vote = self._judge(sample)
            self._step = None
        self._samples.append(sample)
        if vote is not None:
            self._vote(vote)
        return vote

    def _judge(self, sample: _Sample) -> Phase | None:
        assert self._step is not None
        start, level, phases_before = self._step
        after = [
            s for s in (*self._samples, sample) if start + AFTER_START_S <= s.time <= start + AFTER_END_S
        ]
        if len(after) < 2:
            return None
        device_after = [s.device_w for s in after]
        step = _mean(device_after) - level
        if abs(step) < STEP_W or max(device_after) - min(device_after) > STEADY_SHARE * abs(step):
            return None
        steps = [_mean([s.phases_w[i] for s in after]) - phases_before[i] for i in range(3)]
        return classify(step, steps)

    def _vote(self, vote: Phase) -> None:
        self.votes.append(vote)
        if self.phase is None:
            phase, count = Counter(self.votes).most_common(1)[0]
            if count >= MIN_VOTES and count >= AGREEMENT * len(self.votes):
                self.phase = phase
                if self.relearn_from is not None and phase is self.relearn_from:
                    self.confirm = phase
                self.relearn_from = None
            return
        recent = list(self.votes)[-CHANGE_VOTES:]
        if len(recent) == CHANGE_VOTES and len(set(recent)) == 1 and recent[0] is not self.phase:
            self.changed_to = recent[0]

    def relearn(self) -> None:
        """Forget the phase and learn it anew; if the same comes out, ask to confirm it."""
        self.relearn_from = self.phase
        self.phase = None
        self.manual = False
        self.votes.clear()
        self.changed_to = None
        self.confirm = None

    def choose(self, phase: Phase, *, manual: bool) -> None:
        """The user's answer: ``phase`` from now on (``manual``: not the one measured)."""
        self.phase = phase
        self.manual = manual
        self.changed_to = None
        self.confirm = None
        self.relearn_from = None
        # Only new votes may report a change again.
        self.votes.clear()

    def as_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase.value if self.phase else None,
            "manual": self.manual,
            "votes": [vote.value for vote in self.votes],
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
        learner.votes.extend(Phase(v) for v in data.get("votes") or () if v in {p.value for p in Phase})
        learner.changed_to = phase("changed_to")
        learner.relearn_from = phase("relearn_from")
        learner.confirm = phase("confirm")
        return learner
