"""Conservative smoothing of the grid power without the batteries.

The control distributes what the house and the PV do on their own: the grid
power minus the battery power the meter shows at the same moment. Averaging
the grid power itself would keep the effect of the controller's own
corrections in the average and count them twice (the corrected battery power
already counts in the energy balance).

Of that power, the less favourable of two values is used:

* The value that held for the whole hold time (its lowest value): a load
  switching on or a PV drop counts only once it lasted ``hold_s``. A shorter
  pulse (e.g. a coffee machine keeping its heater warm) is over before the
  batteries could follow it; following it would only feed the same energy
  into the grid afterwards.
* The average of that held value over the window: a sudden PV rise or a load
  switching off is followed only after the window, so the control does not
  overshoot with fluctuating PV.

Grid power sign: +import / -export. "Less favourable" is the larger value.
"""

from __future__ import annotations

from collections import deque

# Upper bound of the hold time: a load lasting longer is followed.
MAX_HOLD_S = 2.0


class GridPowerFilter:
    """Time weighted moving average of the grid power without the batteries."""

    def __init__(self, window_s: float, hold_s: float = 0.0) -> None:
        self.window_s = window_s
        self.hold_s = hold_s
        # (timestamp in s, grid power minus battery power in W); each value
        # holds until the next sample.
        self._samples: deque[tuple[float, float]] = deque()

    def add(self, timestamp: float, grid_power_w: float, battery_power_w: float = 0.0) -> None:
        """Add a measurement of the grid meter and the battery power it shows."""
        self._samples.append((timestamp, grid_power_w - battery_power_w))
        self._prune(timestamp)

    def _prune(self, now: float) -> None:
        # Keep the newest sample that started before the window and its hold
        # time: it defines the value at the beginning.
        start = now - max(0.0, self.window_s) - max(0.0, self.hold_s)
        while len(self._samples) > 1 and self._samples[1][0] <= start:
            self._samples.popleft()

    def held(self, now: float) -> float | None:
        """Lowest value during the hold time ending at ``now``."""
        samples = self._samples
        start = now - max(0.0, self.hold_s)
        values = [
            value
            for index, (timestamp, value) in enumerate(samples)
            if timestamp <= now and (index + 1 == len(samples) or samples[index + 1][0] > start)
        ]
        return min(values) if values else None

    def average(self, now: float) -> float | None:
        """Time weighted average of the held value over the window ending at ``now``."""
        if not self._samples:
            return None
        self._prune(now)
        if self.window_s <= 0:
            return self.held(now)
        start = now - self.window_s
        hold = max(0.0, self.hold_s)
        # The held value changes when a sample starts and when one leaves the hold time.
        times = sorted(
            {start, now}
            | {t for timestamp, _ in self._samples for t in (timestamp, timestamp + hold) if start < t < now}
        )
        weighted = 0.0
        duration = 0.0
        for begin, end in zip(times, times[1:]):
            value = self.held(begin)
            if value is not None:
                weighted += value * (end - begin)
                duration += end - begin
        return weighted / duration if duration > 0 else self.held(now)

    def conservative(self, now: float) -> float | None:
        """Larger (= less surplus) of held value and window average."""
        held = self.held(now)
        if held is None:
            return None
        average = self.average(now)
        return held if average is None else max(held, average)
