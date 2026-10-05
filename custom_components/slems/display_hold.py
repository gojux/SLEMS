"""Holds a displayed value until a new value has lasted for a while.

Used for sensors that only show what the control does, so that changes
lasting a few seconds do not fill the history and the logbook. The control
itself always works with the current value.
"""

from __future__ import annotations

from collections.abc import Hashable

# How long (s) a new strategy has to last before the sensor shows it.
STRATEGY_HOLD_S = 30.0


class DisplayHold:
    """Shows a new value only after it lasted ``hold_s`` seconds.

    The first value and ``None`` (nothing known) are shown at once.
    """

    def __init__(self, hold_s: float) -> None:
        self.hold_s = hold_s
        self._shown: Hashable | None = None
        self._candidate: Hashable | None = None
        self._candidate_since: float | None = None

    def update(self, value: Hashable | None, now: float) -> Hashable | None:
        """Return the value to show for the current ``value`` at ``now``."""
        if value is None or self._shown is None or value == self._shown:
            self._shown = value
            self._candidate = None
            self._candidate_since = None
            return self._shown
        if value != self._candidate:
            self._candidate = value
            self._candidate_since = now
        elif now - self._candidate_since >= self.hold_s:
            self._shown = value
            self._candidate = None
            self._candidate_since = None
        return self._shown
