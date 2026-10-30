"""Automatic reduction of the control gain when the control swings.

The controller moves the total battery power by ``gain`` of the remaining
difference towards its target per cycle. The target already takes the learned
battery response times into account, so the corrections only smooth the
commands: after a step of the target they shrink by ``1 - gain`` per cycle
however fast the battery is. A higher gain does not make a battery faster; it
only makes the control react harder to errors of the target, e.g. a wrong
learned response time. The set gain is therefore the upper limit, and the
adaptation only lowers it:

* Oscillation: the last ``OSCILLATION_REVERSALS`` + 1 significant corrections
  alternate in direction and do not die out (each at least
  ``NOT_DECAYING_RATIO`` of the previous one). The loop overshoots, so the gain
  is reduced by ``DECREASE_FACTOR``.
* Recovery: after ``RECOVERY_S`` without a change the gain rises by
  ``INCREASE_STEP``, at most up to the limit.

Load changes from outside start with corrections in one direction and are not
mistaken for oscillation.

After a reduction the history is cleared and nothing changes for
``COOLDOWN_S`` so the effect of the new gain becomes visible first.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import logging

_LOGGER = logging.getLogger(__name__)

# Lowest gain the reduction goes to (a lower set gain stays as set).
MIN_GAIN = 0.2
DECREASE_FACTOR = 0.8
INCREASE_STEP = 0.05
# Corrections below this size carry no direction information.
SIGNIFICANT_W = 50.0
OSCILLATION_REVERSALS = 3
NOT_DECAYING_RATIO = 0.7
# Corrections further apart than this do not belong to the same movement.
MAX_GAP_S = 15.0
COOLDOWN_S = 30.0
RECOVERY_S = 300.0


class GainAdjustment(StrEnum):
    """Last automatic change of the gain."""

    NONE = "none"
    DECREASED = "decreased"
    # Recovered a step towards the limit.
    INCREASED = "increased"


@dataclass
class _Correction:
    timestamp: float
    power_w: float


class AdaptiveGain:
    """Keeps the current gain and adapts it below a limit."""

    def __init__(self, gain: float) -> None:
        self.gain = gain
        self.last_adjustment = GainAdjustment.NONE
        self.last_adjustment_at: float | None = None
        self._history: list[_Correction] = []
        self._cooldown_until = 0.0
        self._steady_since: float | None = None

    def reset(self, gain: float) -> None:
        """Start again from ``gain`` (e.g. after the user changed it)."""
        self.gain = gain
        self._history.clear()
        self._cooldown_until = 0.0
        self._steady_since = None
        self.last_adjustment = GainAdjustment.NONE
        self.last_adjustment_at = None

    def current(self, limit: float) -> float:
        """Gain to use with the set gain ``limit``."""
        return min(self.gain, limit)

    def observe(self, timestamp: float, correction_w: float, limit: float) -> None:
        """A control cycle changed the total battery power by ``correction_w``;
        ``limit`` is the set gain."""
        self.gain = self.current(limit)
        if self._steady_since is None:
            self._steady_since = timestamp
        elif timestamp - self._steady_since >= RECOVERY_S and self.gain < limit:
            self._steady_since = timestamp
            self._change(min(limit, self.gain + INCREASE_STEP), GainAdjustment.INCREASED, timestamp)
        if abs(correction_w) < SIGNIFICANT_W:
            return
        if timestamp < self._cooldown_until:
            return
        if self._history and timestamp - self._history[-1].timestamp > MAX_GAP_S:
            self._history.clear()
        self._history.append(_Correction(timestamp, correction_w))
        del self._history[: -(OSCILLATION_REVERSALS + 1)]

        if self._oscillating():
            self._history.clear()
            self._cooldown_until = timestamp + COOLDOWN_S
            self._steady_since = timestamp
            self._change(min(self.gain, max(MIN_GAIN, self.gain * DECREASE_FACTOR)), GainAdjustment.DECREASED, timestamp)

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

    def _change(self, gain: float, adjustment: GainAdjustment, timestamp: float) -> None:
        if gain == self.gain:
            return
        _LOGGER.debug("Gain %s from %.2f to %.2f", adjustment, self.gain, gain)
        self.gain = gain
        self.last_adjustment = adjustment
        self.last_adjustment_at = timestamp

