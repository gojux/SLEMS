"""Starts of the batteries out of standby, counted per day for the diagnostics.

A Marstek Venus needs about 2.7 s until a command out of standby shows at the
grid meter, however short the stop before it was (see developers.md, Marstek
Venus E 3.0, timing). Holding a small power instead of 0 W avoids that delay
but costs conversion losses all the time. The restarts shortly after a stop
are the ones such a hold could avoid; their counts show whether it would pay
off.
"""

from __future__ import annotations

# Restarts counted apart when they follow the battery's last stop this soon (s).
RESTART_WINDOWS_S = (60, 300)
DAYS_KEPT = 7


def _empty() -> dict[str, int]:
    return {"starts": 0} | {f"within_{window}_s": 0 for window in RESTART_WINDOWS_S}


class BatteryStarts:
    """Per battery and day: starts out of standby and quick restarts after a stop."""

    def __init__(self) -> None:
        # battery id -> day (ISO date) -> counts
        self.days: dict[str, dict[str, dict[str, int]]] = {}
        # battery id -> monotonic time of its last command to 0 W
        self._stopped_at: dict[str, float] = {}

    def stopped(self, battery_id: str, timestamp: float) -> None:
        """The battery got 0 W after a power."""
        self._stopped_at[battery_id] = timestamp

    def started(self, battery_id: str, timestamp: float, day: str) -> None:
        """The battery got a power after 0 W."""
        days = self.days.setdefault(battery_id, {})
        counts = days.setdefault(day, _empty())
        counts["starts"] += 1
        stopped = self._stopped_at.pop(battery_id, None)
        if stopped is not None:
            for window in RESTART_WINDOWS_S:
                if timestamp - stopped <= window:
                    counts[f"within_{window}_s"] += 1
        for old in sorted(days)[:-DAYS_KEPT]:
            del days[old]

    def as_dict(self) -> dict[str, dict[str, dict[str, int]]]:
        return {battery_id: dict(days) for battery_id, days in self.days.items()}

    def restore(self, data: dict[str, dict[str, dict[str, int]]]) -> None:
        for battery_id, days in data.items():
            self.days[battery_id] = {day: _empty() | counts for day, counts in days.items()}
