"""Learned timing of the grid meter, the batteries and the consumers.

* Meter cadence: moving average of the interval between two reports of the
  grid meter (also reports without a value change).
* Battery response time: time from a battery command until the grid meter
  shows most of the commanded change. The controller uses it to know which
  commands the current meter value already contains. Learned for all
  batteries together and for each battery from the steps it makes mostly
  alone (``BatteryResponses``); the batteries' own telemetry is read too
  rarely for that (and reports its own power more than a second late).
  Steps that reverse the direction (charging to discharging or back) are
  learned apart: a reversal stops at once and only then starts the other
  direction, so the time until the *new direction* shows is learned. Steps
  out of standby (from 0 W) are learned apart as well: the inverter of a
  Marstek Venus does nothing for about 2 s before it ramps up. The controller
  expects these steps to take their own times.
* Consumer response times: time from a consumer command until the consumer's
  own power sensor (and the grid meter) shows most of the change, separately
  for switching on and off (``DirectionalResponse``): switching on includes
  the start delay of the device (e.g. a compressor), switching off is usually
  only the reporting delay.

All values are medians of the last ``SAMPLES_KEPT`` measurements; a
measurement is dropped when another command interferes or nothing happens
within ``MAX_RESPONSE_S``. Learners with a label log every measurement at
debug level, also the dropped ones with the reason.

The commands of the control loop answer a movement of the grid, which often
goes on or back by itself (a stove switching, a cloud edge), and the damped
control follows a step with smaller ones the same way. Battery measurements
are therefore cleaned up:

* Commands still on their way (issued within ``IN_FLIGHT_FACTOR`` × the
  learned time before, the same way) are added to the expected change, so
  their arrival does not end the measurement of the later step early.
* A grid that moves by half the step before the earliest possible response
  (``earliest_s``: the dead time of the battery's probe step, see
  response_probe, else from the driver or half the learned time) was moved
  by something else: the measurement is dropped. The share dropped this way
  per battery (``early_share``) asks for a new probe step when it is high.
* So is one whose grid moves more than ``JUMP_RATIO`` × the expected change
  when it arrives (a foreign load jumped along).
* The step time is learned from steps only, not from starts or reversals; a
  start counts only after a stop of a while (see the controller).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
import logging
from statistics import median

_LOGGER = logging.getLogger(__name__)

EMA_ALPHA = 0.3
# Share of the commanded change that counts as "arrived".
ARRIVED_RATIO = 0.6
MIN_STEP_W = 300.0
MAX_RESPONSE_S = 30.0
# Consumers may start several minutes after the command (compressor delay).
CONSUMER_MAX_RESPONSE_S = 300.0

# Share of a step one battery must carry for the step to count as its own.
OWN_STEP_SHARE = 0.8

# Measurements kept per learner; the learned value is their median.
SAMPLES_KEPT = 9
# Share of a step the grid may move before the earliest possible response.
EARLY_RATIO = 0.5
# Battery measurements: a grid moving more than this multiple of the expected
# change at the arrival was moved by a foreign load too.
JUMP_RATIO = 1.5
# Commands issued within this multiple of the learned time are still on their way.
IN_FLIGHT_FACTOR = 1.5
# Outcomes of the last measurements kept per battery (see BatteryResponses.early_share).
OUTCOMES_KEPT = 20

DEFAULT_METER_INTERVAL_S = 2.0
DEFAULT_BATTERY_RESPONSE_S = 3.0
DEFAULT_CONSUMER_RESPONSE_S = 10.0


def _ema(previous: float | None, value: float) -> float:
    return value if previous is None else previous + EMA_ALPHA * (value - previous)


class MeterCadence:
    """Typical interval between two reports of the grid meter."""

    def __init__(self) -> None:
        self._last: float | None = None
        self.interval_s: float | None = None

    def report(self, timestamp: float) -> None:
        if self._last is not None:
            interval = timestamp - self._last
            if 0.05 < interval < 300:
                self.interval_s = _ema(self.interval_s, interval)
        self._last = timestamp

    @property
    def value(self) -> float:
        return self.interval_s if self.interval_s is not None else DEFAULT_METER_INTERVAL_S


@dataclass
class _PendingStep:
    start: float
    baseline: float
    change: float
    # Commands still on their way the same way (W, same sign as ``change``).
    in_flight: float = 0.0
    earliest_s: float = 0.0


class StepResponse:
    """Learns how long a commanded step takes to show up in a measurement."""

    def __init__(
        self,
        default_s: float,
        min_step_w: float = MIN_STEP_W,
        max_response_s: float = MAX_RESPONSE_S,
        *,
        label: str | None = None,
        jump_ratio: float | None = None,
        track_in_flight: bool = False,
        on_outcome: Callable[[str], None] | None = None,
    ) -> None:
        self._default_s = default_s
        self._min_step_w = min_step_w
        self._max_response_s = max_response_s
        self._label = label
        self._jump_ratio = jump_ratio
        self._track_in_flight = track_in_flight
        # Told "learned", "early" or "dropped" for every measurement that ended.
        self._on_outcome = on_outcome
        self._samples: deque[float] = deque(maxlen=SAMPLES_KEPT)
        self._pending: _PendingStep | None = None
        # Recent commands (time, change) for the ones still on their way.
        self._recent: deque[tuple[float, float]] = deque(maxlen=16)

    @property
    def response_s(self) -> float | None:
        """Median of the kept measurements; None until measured."""
        return median(self._samples) if self._samples else None

    @response_s.setter
    def response_s(self, value: float | None) -> None:
        """Start from a single value (e.g. stored by an older version)."""
        self._samples.clear()
        if value is not None:
            self._samples.append(value)

    @property
    def samples(self) -> list[float]:
        return list(self._samples)

    def restore_samples(self, samples: Iterable[float]) -> None:
        """Take stored measurements; none keeps the current value."""
        values = [float(value) for value in samples]
        if values:
            self._samples.clear()
            self._samples.extend(values)

    def add(self, value: float) -> None:
        """A measurement taken elsewhere (e.g. a probe step, see response_probe)."""
        self._samples.append(max(0.1, value))

    @property
    def value(self) -> float:
        learned = self.response_s
        return learned if learned is not None else self._default_s

    def in_flight_w(self, timestamp: float, change: float, recent: Iterable[tuple[float, float]]) -> float:
        """Sum of the ``recent`` commands the same way as ``change`` that may not
        have shown yet."""
        window = IN_FLIGHT_FACTOR * self.value
        return sum(
            earlier
            for issued, earlier in recent
            if timestamp - issued < window and earlier * change > 0
        )

    def command(
        self,
        timestamp: float,
        baseline: float | None,
        change: float,
        *,
        in_flight: float | None = None,
        earliest_s: float = 0.0,
    ) -> None:
        """A command changes the measured value by ``change`` (sign included).

        ``in_flight``: change of earlier commands still on their way (None:
        from the commands this learner saw, if it tracks them);
        ``earliest_s``: the measured value cannot follow the command earlier.
        """
        if in_flight is None:
            in_flight = self.in_flight_w(timestamp, change, self._recent) if self._track_in_flight else 0.0
        self._recent.append((timestamp, change))
        if baseline is None or abs(change) < self._min_step_w:
            # A small correction the same way (the damped control follows a
            # step with those) keeps a pending measurement; one the other way
            # would hide the step.
            pending = self._pending
            if pending is not None and change * pending.change < 0:
                self._pending = None
            return
        self._pending = _PendingStep(timestamp, baseline, change, in_flight, earliest_s)

    def cancel(self) -> None:
        """Drop a measurement still waiting (another command interfered)."""
        self._pending = None

    def sample(self, timestamp: float, value: float) -> None:
        """A new measured value arrived."""
        pending = self._pending
        if pending is None:
            return
        elapsed = timestamp - pending.start
        if elapsed > self._max_response_s:
            self._pending = None
            if self._label is not None:
                _LOGGER.debug(
                    "Response %s: step %+.0f W from %.0f W not arrived within %.0f s",
                    self._label,
                    pending.change,
                    pending.baseline,
                    self._max_response_s,
                )
            return
        # In shares of the step; the commands on their way may move it up to ``ahead`` first.
        moved = (value - pending.baseline) / pending.change
        ahead = pending.in_flight / pending.change
        if elapsed < pending.earliest_s and (moved >= ahead + EARLY_RATIO or moved <= -EARLY_RATIO):
            self._drop(pending, elapsed, f"moved before {pending.earliest_s:.2f} s", value, "early")
            return
        if moved < ahead + ARRIVED_RATIO:
            return
        if self._jump_ratio is not None and moved > self._jump_ratio * (1 + ahead):
            self._drop(pending, elapsed, f"moved {moved / (1 + ahead):.1f} times the expected change", value)
            return
        self._samples.append(max(0.1, elapsed))
        self._pending = None
        if self._on_outcome is not None:
            self._on_outcome("learned")
        if self._label is not None:
            _LOGGER.debug(
                "Response %s: step %+.0f W from %.0f W%s arrived after %.2f s (grid %.0f W), learned %.2f s",
                self._label,
                pending.change,
                pending.baseline,
                f" ({pending.in_flight:+.0f} W on the way)" if pending.in_flight else "",
                elapsed,
                value,
                self.response_s,
            )

    def _drop(self, pending: _PendingStep, elapsed: float, reason: str, value: float, outcome: str = "dropped") -> None:
        self._pending = None
        if self._on_outcome is not None:
            self._on_outcome(outcome)
        if self._label is not None:
            _LOGGER.debug(
                "Response %s: step %+.0f W from %.0f W dropped after %.2f s (grid %.0f W): %s",
                self._label,
                pending.change,
                pending.baseline,
                elapsed,
                value,
                reason,
            )


class BatteryResponses:
    """Response time of each battery, learned at the grid meter.

    A step counts for a battery when it carries at least ``OWN_STEP_SHARE`` of
    the moved power; a larger step shared by several batteries ends all
    pending measurements. Most steps are shared, so a battery can stay
    unlearned for a long time. Starts and reversals are learned apart, not as
    steps; a start right after a stop (``restarted_ids``) is not learned.
    """

    def __init__(
        self,
        names: Callable[[str], str] | None = None,
        earliest: Callable[[str, str], float] | None = None,
    ) -> None:
        # Battery name for the debug log.
        self._names = names
        # Earliest response of a battery by kind (step, start, reversal).
        self._earliest = earliest
        self.learners: dict[str, StepResponse] = {}
        # Steps of a battery that reverse its direction, until the new direction shows.
        self.reversals: dict[str, StepResponse] = {}
        # Steps of a battery out of standby (from 0 W).
        self.starts: dict[str, StepResponse] = {}
        # Recent own changes (time, W) of each battery.
        self._recent: dict[str, deque[tuple[float, float]]] = {}
        # Outcomes of the last measurements of each battery (all kinds).
        self._outcomes: dict[str, deque[str]] = {}

    def command(
        self,
        timestamp: float,
        baseline: float | None,
        changes: dict[str, float],
        reversed_from: dict[str, float] | None = None,
        started_ids: frozenset[str] = frozenset(),
        *,
        restarted_ids: frozenset[str] = frozenset(),
        issued: dict[str, float] | None = None,
    ) -> None:
        """The batteries' commanded powers changed by ``changes`` (battery id -> W);
        the commands of ``reversed_from`` changed their direction (battery id ->
        power before), the ones of ``started_ids`` left standby, the ones of
        ``restarted_ids`` started again right after a stop. ``issued``: when
        each battery was written (default ``timestamp``)."""
        reversed_from = reversed_from or {}
        issued = issued or {}
        total = sum(changes.values())
        moved = sum(abs(change) for change in changes.values())
        recent_before = {battery_id: list(recent) for battery_id, recent in self._recent.items()}
        for battery_id, change in changes.items():
            self._recent.setdefault(battery_id, deque(maxlen=16)).append(
                (issued.get(battery_id, timestamp), change)
            )
        if moved < MIN_STEP_W:
            # Small corrections: each pending measurement decides by direction.
            for learner in self._all():
                learner.command(timestamp, baseline, total, in_flight=0.0)
            return
        own = next(
            (
                battery_id
                for battery_id, change in changes.items()
                if abs(change) >= OWN_STEP_SHARE * moved and change * total > 0
            ),
            None,
        )
        special = own in reversed_from or own in started_ids or own in restarted_ids
        for battery_id, learner in self.learners.items():
            if battery_id != own or special:
                learner.cancel()
        for battery_id, learner in self.reversals.items():
            if battery_id != own or own not in reversed_from:
                learner.cancel()
        for battery_id, learner in self.starts.items():
            if battery_id != own or own not in started_ids:
                learner.cancel()
        if own is None:
            return
        start = issued.get(own, timestamp)
        if own in restarted_ids:
            if self._names is not None:
                _LOGGER.debug("Response start %s: not measured, started right after a stop", self._names(own))
            return
        if own in reversed_from:
            # Until the new direction shows: the stop of the old power
            # moves the grid first.
            stopped = reversed_from[own]
            self._learner(self.reversals, "reversal", own).command(
                start,
                None if baseline is None else baseline - stopped,
                total + stopped,
                in_flight=0.0,
                earliest_s=self._earliest_s(own, "reversal"),
            )
        elif own in started_ids:
            self._learner(self.starts, "start", own).command(
                start, baseline, total, in_flight=0.0, earliest_s=self._earliest_s(own, "start")
            )
        else:
            learner = self._learner(self.learners, "step", own)
            learner.command(
                start,
                baseline,
                total,
                in_flight=learner.in_flight_w(start, total, recent_before.get(own, [])),
                earliest_s=self._earliest_s(own, "step"),
            )

    def _earliest_s(self, battery_id: str, kind: str) -> float:
        return self._earliest(battery_id, kind) if self._earliest is not None else 0.0

    def _learner(self, learners: dict[str, StepResponse], kind: str, battery_id: str) -> StepResponse:
        if (learner := learners.get(battery_id)) is None:
            name = self._names(battery_id) if self._names is not None else battery_id
            outcomes = self._outcomes.setdefault(battery_id, deque(maxlen=OUTCOMES_KEPT))
            learner = learners[battery_id] = StepResponse(
                DEFAULT_BATTERY_RESPONSE_S, label=f"{kind} {name}", jump_ratio=JUMP_RATIO, on_outcome=outcomes.append
            )
        return learner

    def add(self, kind: str, battery_id: str, value: float) -> None:
        """A measurement of ``kind`` (step, start, reversal) taken elsewhere."""
        self._learner(self._kinds()[kind], kind, battery_id).add(value)

    def clear(self, battery_id: str) -> None:
        """Forget everything learned of a battery (e.g. after a firmware update)."""
        for learners in self._kinds().values():
            learners.pop(battery_id, None)
        self._recent.pop(battery_id, None)
        self._outcomes.pop(battery_id, None)

    def cancel(self) -> None:
        """End all pending measurements (another command interfered)."""
        for learner in self._all():
            learner.cancel()

    def early_share(self, battery_id: str) -> float | None:
        """Share of the last ``OUTCOMES_KEPT`` measurements of a battery dropped as
        too early; None until that many ended."""
        outcomes = self._outcomes.get(battery_id)
        if outcomes is None or len(outcomes) < OUTCOMES_KEPT:
            return None
        return sum(outcome == "early" for outcome in outcomes) / len(outcomes)

    def reset_outcomes(self, battery_id: str) -> None:
        if (outcomes := self._outcomes.get(battery_id)) is not None:
            outcomes.clear()

    def _all(self) -> list[StepResponse]:
        return [*self.learners.values(), *self.reversals.values(), *self.starts.values()]

    def sample(self, timestamp: float, value: float) -> None:
        for learner in self._all():
            learner.sample(timestamp, value)

    def learned(self, battery_id: str) -> float | None:
        learner = self.learners.get(battery_id)
        return learner.response_s if learner else None

    def learned_reversal(self, battery_id: str) -> float | None:
        """Response time of a step that reverses the battery's direction."""
        learner = self.reversals.get(battery_id)
        return learner.response_s if learner else None

    def learned_start(self, battery_id: str) -> float | None:
        """Response time of a step out of standby (from 0 W)."""
        learner = self.starts.get(battery_id)
        return learner.response_s if learner else None

    def reversals_as_dict(self) -> dict[str, float]:
        return _as_dict(self.reversals)

    def restore_reversals(self, data: dict[str, float]) -> None:
        self._restore(self.reversals, "reversal", data)

    def starts_as_dict(self) -> dict[str, float]:
        return _as_dict(self.starts)

    def restore_starts(self, data: dict[str, float]) -> None:
        self._restore(self.starts, "start", data)

    def as_dict(self) -> dict[str, float]:
        return _as_dict(self.learners)

    def restore(self, data: dict[str, float]) -> None:
        self._restore(self.learners, "step", data)

    def samples_as_dict(self) -> dict[str, dict[str, list[float]]]:
        """The kept measurements of every learner, by kind and battery."""
        return {
            kind: {battery_id: learner.samples for battery_id, learner in learners.items() if learner.samples}
            for kind, learners in self._kinds().items()
        }

    def restore_samples(self, data: dict[str, dict[str, list[float]]]) -> None:
        for kind, learners in self._kinds().items():
            for battery_id, samples in (data.get(kind) or {}).items():
                if samples:
                    self._learner(learners, kind, battery_id).restore_samples(samples)

    def _kinds(self) -> dict[str, dict[str, StepResponse]]:
        return {"step": self.learners, "reversal": self.reversals, "start": self.starts}

    def _restore(self, learners: dict[str, StepResponse], kind: str, data: dict[str, float]) -> None:
        for battery_id, response_s in data.items():
            self._learner(learners, kind, battery_id).response_s = response_s


def _as_dict(learners: dict[str, StepResponse]) -> dict[str, float]:
    return {
        battery_id: learner.response_s
        for battery_id, learner in learners.items()
        if learner.response_s is not None
    }


class DirectionalResponse:
    """Response times of switching on (more power) and off (less power), learned apart."""

    def __init__(self, default_s: float, min_step_w: float = MIN_STEP_W) -> None:
        self.on = StepResponse(default_s, min_step_w, CONSUMER_MAX_RESPONSE_S)
        self.off = StepResponse(default_s, min_step_w, CONSUMER_MAX_RESPONSE_S)

    def command(self, timestamp: float, baseline: float | None, change: float) -> None:
        """A command changes the measured value by ``change`` (+: more power)."""
        (self.on if change > 0 else self.off).command(timestamp, baseline, change)
        # A step the other way ends a measurement still waiting.
        (self.off if change > 0 else self.on).cancel()

    def sample(self, timestamp: float, value: float) -> None:
        self.on.sample(timestamp, value)
        self.off.sample(timestamp, value)

    def learned(self, increase: bool) -> float | None:
        """Learned response time of a step in this direction, None until measured."""
        return (self.on if increase else self.off).response_s

    def value(self, increase: bool) -> float:
        return (self.on if increase else self.off).value
