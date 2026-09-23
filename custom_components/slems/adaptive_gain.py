"""Automatic adaptation of the control gain from the observed behaviour.

The controller moves the total battery power by ``gain`` of the remaining
difference per cycle. This module watches those corrections:

* Oscillation: the last ``OSCILLATION_REVERSALS`` + 1 significant corrections
  alternate in direction and do not die out (each at least
  ``NOT_DECAYING_RATIO`` of the previous one). The loop overshoots, so the gain
  is reduced by ``DECREASE_FACTOR``.
* Sluggishness: ``SLUGGISH_STEPS`` significant corrections in a row go in the
  same direction and shrink (an approach to a fixed target that takes many
  cycles). The gain is raised by ``INCREASE_STEP``.

A slowly moving target (e.g. rising PV) produces same-direction corrections of
similar size; they do not shrink and do not raise the gain. Load changes from
outside start with corrections in one direction and are not mistaken for
oscillation.

After every adjustment the history is cleared and nothing changes for
``COOLDOWN_S`` so the effect of the new gain becomes visible first. The gain
drops quickly and rises slowly, because oscillation costs more than a
somewhat slower reaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

MIN_GAIN = 0.2
MAX_GAIN = 0.9
DECREASE_FACTOR = 0.8
INCREASE_STEP = 0.05
# Corrections below this size carry no direction information.
SIGNIFICANT_W = 50.0
OSCILLATION_REVERSALS = 3
NOT_DECAYING_RATIO = 0.7
SLUGGISH_STEPS = 5
# Corrections further apart than this do not belong to the same movement.
MAX_GAP_S = 15.0
COOLDOWN_S = 30.0


class GainAdjustment(StrEnum):
    """Last automatic change of the gain."""

    NONE = "none"
    DECREASED = "decreased"
    INCREASED = "increased"


@dataclass
class _Correction:
    timestamp: float
    power_w: float


class AdaptiveGain:
    """Keeps the current gain and adapts it."""

    def __init__(self, gain: float) -> None:
        self.gain = _bounded(gain)
        self.last_adjustment = GainAdjustment.NONE
        self.last_adjustment_at: float | None = None
        self._history: list[_Correction] = []
        self._cooldown_until = 0.0

    def reset(self, gain: float) -> None:
        """Start again from ``gain`` (e.g. after the user changed it)."""
        self.gain = _bounded(gain)
        self._history.clear()
        self.last_adjustment = GainAdjustment.NONE
        self.last_adjustment_at = None

    def observe(self, timestamp: float, correction_w: float) -> None:
        """A control cycle changed the total battery power by ``correction_w``."""
        if abs(correction_w) < SIGNIFICANT_W:
            return
        if timestamp < self._cooldown_until:
            return
        if self._history and timestamp - self._history[-1].timestamp > MAX_GAP_S:
            self._history.clear()
        self._history.append(_Correction(timestamp, correction_w))
        del self._history[: -max(OSCILLATION_REVERSALS + 1, SLUGGISH_STEPS)]

        if self._oscillating():
            self._adjust(max(MIN_GAIN, self.gain * DECREASE_FACTOR), GainAdjustment.DECREASED, timestamp)
        elif self._sluggish():
            self._adjust(min(MAX_GAIN, self.gain + INCREASE_STEP), GainAdjustment.INCREASED, timestamp)

    def _oscillating(self) -> bool:
        recent = self._history[-(OSCILLATION_REVERSALS + 1) :]
        if len(recent) < OSCILLATION_REVERSALS + 1:
            return False
        for previous, current in zip(recent, recent[1:]):
            if (previous.power_w > 0) == (current.power_w > 0):
                return False
            if abs(current.power_w) < NOT_DECAYING_RATIO * abs(previous.power_w):
                return False
        return True

    def _sluggish(self) -> bool:
        recent = self._history[-SLUGGISH_STEPS:]
        if len(recent) < SLUGGISH_STEPS:
            return False
        for previous, current in zip(recent, recent[1:]):
            if (previous.power_w > 0) != (current.power_w > 0):
                return False
            if abs(current.power_w) >= abs(previous.power_w):
                return False
        return True

    def _adjust(self, gain: float, adjustment: GainAdjustment, timestamp: float) -> None:
        self._history.clear()
        self._cooldown_until = timestamp + COOLDOWN_S
        if gain == self.gain:
            return
        self.gain = gain
        self.last_adjustment = adjustment
        self.last_adjustment_at = timestamp


def _bounded(gain: float) -> float:
    return min(MAX_GAIN, max(MIN_GAIN, gain))
