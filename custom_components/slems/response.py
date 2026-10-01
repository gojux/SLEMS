"""Learned timing of the grid meter, the batteries and the consumers.

* Meter cadence: moving average of the interval between two reports of the
  grid meter (also reports without a value change).
* Battery response time: time from a battery command until the grid meter
  shows most of the commanded change. The controller uses it to know which
  commands the current meter value already contains. Learned for all
  batteries together and for each battery from the steps it makes mostly
  alone (``BatteryResponses``); the batteries' own telemetry is read too
  rarely for that.
* Consumer response times: time from a consumer command until the consumer's
  own power sensor (and the grid meter) shows most of the change, separately
  for switching on and off (``DirectionalResponse``): switching on includes
  the start delay of the device (e.g. a compressor), switching off is usually
  only the reporting delay.

All values are exponential moving averages of individual measurements; a
measurement is dropped when another command interferes or nothing happens
within ``MAX_RESPONSE_S``.
"""

from __future__ import annotations

from dataclasses import dataclass

EMA_ALPHA = 0.3
# Share of the commanded change that counts as "arrived".
ARRIVED_RATIO = 0.6
MIN_STEP_W = 300.0
MAX_RESPONSE_S = 30.0
# Consumers may start several minutes after the command (compressor delay).
CONSUMER_MAX_RESPONSE_S = 300.0

# Share of a step one battery must carry for the step to count as its own.
OWN_STEP_SHARE = 0.8

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


class StepResponse:
    """Learns how long a commanded step takes to show up in a measurement."""

    def __init__(
        self, default_s: float, min_step_w: float = MIN_STEP_W, max_response_s: float = MAX_RESPONSE_S
    ) -> None:
        self._default_s = default_s
        self._min_step_w = min_step_w
        self._max_response_s = max_response_s
        self.response_s: float | None = None
        self._pending: _PendingStep | None = None

    @property
    def value(self) -> float:
        return self.response_s if self.response_s is not None else self._default_s

    def command(self, timestamp: float, baseline: float | None, change: float) -> None:
        """A command changes the measured value by ``change`` (sign included)."""
        if baseline is None or abs(change) < self._min_step_w:
            # A small correction the same way (the damped control follows a
            # step with those) keeps a pending measurement; one the other way
            # would hide the step.
            pending = self._pending
            if pending is not None and change * pending.change < 0:
                self._pending = None
            return
        self._pending = _PendingStep(timestamp, baseline, change)

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
            return
        moved = (value - pending.baseline) / pending.change
        if moved >= ARRIVED_RATIO:
            self.response_s = _ema(self.response_s, max(0.1, elapsed))
            self._pending = None


class BatteryResponses:
    """Response time of each battery, learned at the grid meter.

    A step counts for a battery when it carries at least ``OWN_STEP_SHARE`` of
    the moved power; a larger step shared by several batteries ends all
    pending measurements. Most steps are shared, so a battery can stay
    unlearned for a long time.
    """

    def __init__(self) -> None:
        self.learners: dict[str, StepResponse] = {}

    def command(self, timestamp: float, baseline: float | None, changes: dict[str, float]) -> None:
        """The batteries' commanded powers changed by ``changes`` (battery id -> W)."""
        total = sum(changes.values())
        moved = sum(abs(change) for change in changes.values())
        if moved < MIN_STEP_W:
            # Small corrections: each pending measurement decides by direction.
            for learner in self.learners.values():
                learner.command(timestamp, baseline, total)
            return
        own = next(
            (
                battery_id
                for battery_id, change in changes.items()
                if abs(change) >= OWN_STEP_SHARE * moved and change * total > 0
            ),
            None,
        )
        for battery_id, learner in self.learners.items():
            if battery_id != own:
                learner.cancel()
        if own is not None:
            self.learners.setdefault(own, StepResponse(DEFAULT_BATTERY_RESPONSE_S)).command(
                timestamp, baseline, total
            )

    def sample(self, timestamp: float, value: float) -> None:
        for learner in self.learners.values():
            learner.sample(timestamp, value)

    def learned(self, battery_id: str) -> float | None:
        learner = self.learners.get(battery_id)
        return learner.response_s if learner else None

    def as_dict(self) -> dict[str, float]:
        return {
            battery_id: learner.response_s
            for battery_id, learner in self.learners.items()
            if learner.response_s is not None
        }

    def restore(self, data: dict[str, float]) -> None:
        for battery_id, response_s in data.items():
            learner = self.learners.setdefault(battery_id, StepResponse(DEFAULT_BATTERY_RESPONSE_S))
            learner.response_s = response_s


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
