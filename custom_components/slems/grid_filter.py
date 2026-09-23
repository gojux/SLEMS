"""Conservative smoothing of the grid power.

The surplus used for control is based on the grid power averaged over a
configurable window, but the less favourable of average and current value is
used: a sudden PV drop is followed immediately, a sudden PV rise only after the
window. This prevents the control from overshooting with fluctuating PV.

Grid power sign: +import / -export. "Less favourable" is the larger value.
"""

from __future__ import annotations

from collections import deque


class GridPowerFilter:
    """Time weighted moving average of the grid power."""

    def __init__(self, window_s: float) -> None:
        self.window_s = window_s
        # (timestamp in s, value in W); each value holds until the next sample.
        self._samples: deque[tuple[float, float]] = deque()

    def add(self, timestamp: float, grid_power_w: float) -> None:
        """Add a new measurement."""
        self._samples.append((timestamp, grid_power_w))
        self._prune(timestamp)

    def _prune(self, now: float) -> None:
        # Keep the newest sample that started before the window: it defines
        # the value at the beginning of the window.
        start = now - self.window_s
        while len(self._samples) > 1 and self._samples[1][0] <= start:
            self._samples.popleft()

    def average(self, now: float) -> float | None:
        """Time weighted average over the window ending at ``now``."""
        if not self._samples:
            return None
        if self.window_s <= 0:
            return self._samples[-1][1]
        self._prune(now)
        start = now - self.window_s
        weighted = 0.0
        duration = 0.0
        samples = list(self._samples)
        for index, (timestamp, value) in enumerate(samples):
            begin = max(timestamp, start)
            end = samples[index + 1][0] if index + 1 < len(samples) else now
            if end > begin:
                weighted += value * (end - begin)
                duration += end - begin
        return weighted / duration if duration > 0 else samples[-1][1]

    def conservative(self, now: float) -> float | None:
        """Larger (= less surplus) of current value and window average."""
        if not self._samples:
            return None
        current = self._samples[-1][1]
        average = self.average(now)
        return current if average is None else max(current, average)
