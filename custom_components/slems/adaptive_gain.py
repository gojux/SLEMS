"""Automatic reduction of the control gain when the control swings.

The controller moves the total battery power by ``gain`` of the remaining
difference towards its target per cycle. The target already takes the learned
battery response times into account, so the corrections only smooth the
commands: after a step of the target they shrink by ``1 - gain`` per cycle
however fast the battery is. A higher gain does not make a battery faster; it
only makes the control react harder to errors of the target, e.g. a wrong
learned response time. The set gain is therefore the upper limit, and the
adaptation only lowers it:

* Oscillation is seen at the grid meter: the grid power crosses the grid
  target of the batteries (the charging or discharging target, by the
  direction of the commanded total) from one side of a band of ``BAND_W`` to
  the other, and the crossing was caused by the control itself. That is the
  case when the direction of the corrections (rising or falling total
  battery power) reversed before: at least ``EARLY_SHARE`` of the response
  time of the fastest battery commanded (not before its earliest response)
  and at most the loop time earlier (``LOOP_RESPONSES`` × the longest
  response time plus ``LOOP_CYCLES`` control cycles; generous, because a
  wrongly learned response time is the usual cause and a crossing comes up to
  half a period after the reversal). ``OSCILLATION_CROSSINGS`` such crossings
  in a row within ``WINDOW_LOOPS`` loop times, whose swings do not die out
  (the largest deviation of each half swing at least ``NOT_DECAYING_RATIO``
  of the previous one), lower the gain by ``DECREASE_FACTOR``. A control that
  overshoots once after a load change and settles is no oscillation.
* Load changes from outside do not count: they cross the target first and
  the commands follow, so no reversal comes before them in the window. A
  load that switches on and off while the commands still follow the last
  switch (a cycling stove) does have a reversal before it; it moves the grid
  at once by more than ``JUMP_RATIO`` × the largest correction of the loop
  time, which the batteries cannot do. A crossing or jump from outside ends
  the series: a load pulse shorter than the response time (a coffee machine)
  is followed by an overshoot the control caused, but each one starts with
  the next pulse, while a swinging control causes every crossing itself.
* Recovery: after ``RECOVERY_S`` without a change the gain rises by
  ``INCREASE_STEP``, at most up to the limit.

After a reduction the history is cleared and nothing changes for
``COOLDOWN_S`` so the effect of the new gain becomes visible first.

Not seen: a control that swings around another point than the grid target
(feed-in cap, peak shaving, batteries at their power limit) or while the
batteries stand still.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
import logging
from statistics import median

_LOGGER = logging.getLogger(__name__)

# Lowest gain the reduction goes to (a lower set gain stays as set).
MIN_GAIN = 0.2
DECREASE_FACTOR = 0.8
INCREASE_STEP = 0.05
# Corrections below this size carry no direction information.
SIGNIFICANT_W = 50.0
# Deviation from the grid target that counts as one side.
BAND_W = 100.0
OSCILLATION_CROSSINGS = 3
NOT_DECAYING_RATIO = 0.7
EARLY_SHARE = 0.7
LOOP_RESPONSES = 4.0
LOOP_CYCLES = 2
WINDOW_LOOPS = 8
# A grid jump larger than this share of the largest recent correction comes from outside.
JUMP_RATIO = 1.5
# Corrections and reversals are kept at least this long.
KEEP_S = 60.0
CYCLE_INTERVALS = 20
COOLDOWN_S = 30.0
RECOVERY_S = 300.0


class GainAdjustment(StrEnum):
    """Last automatic change of the gain."""

    NONE = "none"
    DECREASED = "decreased"
    # Recovered a step towards the limit.
    INCREASED = "increased"


@dataclass
class _Reversal:
    timestamp: float
    earliest_s: float
    response_s: float


class AdaptiveGain:
    """Keeps the current gain and adapts it below a limit."""

    def __init__(self, gain: float) -> None:
        self.gain = gain
        self.last_adjustment = GainAdjustment.NONE
        self.last_adjustment_at: float | None = None
        self._cooldown_until = 0.0
        self._steady_since: float | None = None
        self._cycles: deque[float] = deque(maxlen=CYCLE_INTERVALS + 1)
        # (time, size) of the significant corrections, direction of the last one.
        self._corrections: deque[tuple[float, float]] = deque()
        self._direction = 0
        self._reversals: deque[_Reversal] = deque()
        self._previous_grid_w: float | None = None
        self._side = 0
        # Largest deviation from the target on the current side.
        self._peak_w = 0.0
        # Crossings caused by the control in a row: (time, loop time, peak of the half swing before).
        self._crossings: deque[tuple[float, float, float]] = deque()

    def reset(self, gain: float) -> None:
        """Start again from ``gain`` (e.g. after the user changed it)."""
        self.gain = gain
        self._clear()
        self._cooldown_until = 0.0
        self._steady_since = None
        self.last_adjustment = GainAdjustment.NONE
        self.last_adjustment_at = None

    def _clear(self) -> None:
        self._corrections.clear()
        self._direction = 0
        self._reversals.clear()
        self._side = 0
        self._crossings.clear()

    def current(self, limit: float) -> float:
        """Gain to use with the set gain ``limit``."""
        return min(self.gain, limit)

    @property
    def cycle_interval_s(self) -> float | None:
        """Median time between the last control cycles."""
        if len(self._cycles) < 2:
            return None
        cycles = list(self._cycles)
        return median(b - a for a, b in zip(cycles, cycles[1:]))

    def observe(
        self,
        timestamp: float,
        correction_w: float,
        limit: float,
        responses: Iterable[tuple[float, float]] = (),
    ) -> None:
        """A control cycle changed the total battery power by ``correction_w``
        (0 if nothing was sent); ``limit`` is the set gain. ``responses``: per
        battery commanded in this cycle the earliest and the usual time the
        grid meter shows its command."""
        self.gain = self.current(limit)
        self._cycles.append(timestamp)
        if self._steady_since is None:
            self._steady_since = timestamp
        elif timestamp - self._steady_since >= RECOVERY_S and self.gain < limit:
            self._steady_since = timestamp
            self._change(min(limit, self.gain + INCREASE_STEP), GainAdjustment.INCREASED, timestamp)
        if abs(correction_w) < SIGNIFICANT_W:
            return
        self._corrections.append((timestamp, correction_w))
        direction = 1 if correction_w > 0 else -1
        responses = list(responses)
        if self._direction and direction != self._direction and responses:
            self._reversals.append(
                _Reversal(
                    timestamp,
                    min(max(earliest, EARLY_SHARE * response) for earliest, response in responses),
                    max(response for _, response in responses),
                )
            )
        self._direction = direction

    def sample(self, timestamp: float, grid_w: float, target_w: float | None) -> None:
        """The grid meter reported ``grid_w``; ``target_w`` is the grid power the
        batteries control to (None while they stand still)."""
        previous = self._previous_grid_w
        self._previous_grid_w = grid_w
        loop = self._loop_s()
        oldest = timestamp - max(WINDOW_LOOPS * loop if loop is not None else 0.0, KEEP_S)
        for items in (self._corrections, self._crossings):
            while items and items[0][0] < oldest:
                items.popleft()
        while self._reversals and self._reversals[0].timestamp < oldest:
            self._reversals.popleft()
        if target_w is None:
            self._side = 0
            self._crossings.clear()
            return
        if timestamp < self._cooldown_until:
            return
        deviation = grid_w - target_w
        side = 1 if deviation > BAND_W else -1 if deviation < -BAND_W else self._side
        crossed = self._side != 0 and side != self._side
        self._side = side
        if crossed:
            peak, self._peak_w = self._peak_w, abs(deviation)
        else:
            self._peak_w = max(self._peak_w, abs(deviation))
        if previous is not None and self._crossings:
            span = loop if loop is not None else KEEP_S
            largest = max((abs(size) for at, size in self._corrections if timestamp - at <= span), default=0.0)
            if abs(grid_w - previous) > max(BAND_W, JUMP_RATIO * largest):
                # A load from outside: the series of crossings caused by the control ends.
                _LOGGER.debug(
                    "Grid jump of %.0f W from outside (largest correction %.0f W), crossings reset",
                    grid_w - previous, largest,
                )
                self._crossings.clear()
                return
        if not crossed:
            return
        reversal = self._cause(timestamp)
        if reversal is None:
            # From outside: the series of crossings caused by the control ends.
            self._crossings.clear()
            return
        # Each reversal explains one crossing.
        self._reversals.remove(reversal)
        loop = self._reversal_loop_s(reversal)
        self._crossings.append((timestamp, loop, peak))
        recent = [crossing for crossing in self._crossings if timestamp - crossing[0] <= WINDOW_LOOPS * loop]
        peaks = [crossing[2] for crossing in recent[-OSCILLATION_CROSSINGS:]]
        decaying = any(later < NOT_DECAYING_RATIO * earlier for earlier, later in zip(peaks, peaks[1:]))
        _LOGGER.debug(
            "Grid crossing at %.0f W caused by the control (%.2f s after a reversal, half swing before %.0f W), "
            "%d in %.1f s",
            grid_w, timestamp - reversal.timestamp, peak, len(recent), WINDOW_LOOPS * loop,
        )
        if len(recent) >= OSCILLATION_CROSSINGS and not decaying:
            self._clear()
            self._cooldown_until = timestamp + COOLDOWN_S
            self._steady_since = timestamp
            self._change(
                min(self.gain, max(MIN_GAIN, self.gain * DECREASE_FACTOR)), GainAdjustment.DECREASED, timestamp
            )

    def _reversal_loop_s(self, reversal: _Reversal) -> float:
        cycle = self.cycle_interval_s or 0.0
        return LOOP_RESPONSES * reversal.response_s + LOOP_CYCLES * cycle

    def _loop_s(self) -> float | None:
        if not self._reversals:
            return None
        return max(self._reversal_loop_s(reversal) for reversal in self._reversals)

    def _cause(self, timestamp: float) -> _Reversal | None:
        """The latest reversal that can have caused a crossing at ``timestamp``."""
        for reversal in reversed(self._reversals):
            elapsed = timestamp - reversal.timestamp
            if reversal.earliest_s <= elapsed <= self._reversal_loop_s(reversal):
                return reversal
        return None

    def _change(self, gain: float, adjustment: GainAdjustment, timestamp: float) -> None:
        if gain == self.gain:
            return
        _LOGGER.debug("Gain %s from %.2f to %.2f", adjustment, self.gain, gain)
        self.gain = gain
        self.last_adjustment = adjustment
        self.last_adjustment_at = timestamp
