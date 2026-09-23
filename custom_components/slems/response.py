"""Learned timing of the grid meter, the batteries and the consumers.

* Meter cadence: moving average of the interval between two reports of the
  grid meter (also reports without a value change).
* Battery response time: time from a battery command until the grid meter
  shows most of the commanded change. The controller uses it to know which
  commands the current meter value already contains.
* Consumer response time: time from a consumer command until the consumer's
  own power sensor shows most of the change.

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

    def __init__(self, default_s: float, min_step_w: float = MIN_STEP_W) -> None:
        self._default_s = default_s
        self._min_step_w = min_step_w
        self.response_s: float | None = None
        self._pending: _PendingStep | None = None

    @property
    def value(self) -> float:
        return self.response_s if self.response_s is not None else self._default_s

    def command(self, timestamp: float, baseline: float | None, change: float) -> None:
        """A command changes the measured value by ``change`` (sign included)."""
        if baseline is None or abs(change) < self._min_step_w:
            # Small or unmeasurable steps would disturb a pending measurement.
            if self._pending is not None and abs(change) > 0:
                self._pending = None
            return
        self._pending = _PendingStep(timestamp, baseline, change)

    def sample(self, timestamp: float, value: float) -> None:
        """A new measured value arrived."""
        pending = self._pending
        if pending is None:
            return
        elapsed = timestamp - pending.start
        if elapsed > MAX_RESPONSE_S:
            self._pending = None
            return
        moved = (value - pending.baseline) / pending.change
        if moved >= ARRIVED_RATIO:
            self.response_s = _ema(self.response_s, max(0.1, elapsed))
            self._pending = None
