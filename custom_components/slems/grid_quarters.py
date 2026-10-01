"""Grid import and export per quarter hour, added up from every grid value.

Each value counts until the next one (at most ``MAX_GAP_S``, a longer gap is
not bridged), split at the quarter hour boundaries of the wall clock. A
quarter hour counts as recorded when values cover ``COMPLETE_SHARE`` of it.

Unlike the hourly mean of the grid power, import and export within the same
quarter hour do not cancel out; unlike the hourly statistics of an energy
counter, the energy is known per quarter hour, as dynamic prices are.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from homeassistant.util import dt as dt_util

SLOT_S = 900
MAX_GAP_S = 300
COMPLETE_SHARE = 0.9
KEEP_DAYS = 400


class GridQuarters:
    """Recorded quarter hours (unix start -> Wh) and how much of each is covered."""

    def __init__(self) -> None:
        self.imported: dict[int, float] = {}
        self.exported: dict[int, float] = {}
        self.covered: dict[int, float] = {}
        self._last: tuple[float, float] | None = None

    def add(self, grid_w: float | None, now: float) -> None:
        """A grid value (+ import / − export) at unix time ``now``."""
        if self._last is not None:
            last_time, last_grid = self._last
            if 0 < now - last_time <= MAX_GAP_S:
                self._spread(last_time, now, last_grid)
        if grid_w is None:
            self._last = None
        elif self._last is None or now >= self._last[0]:
            self._last = (now, grid_w)

    def _spread(self, start: float, end: float, grid_w: float) -> None:
        while start < end:
            slot = int(start - start % SLOT_S)
            part_end = min(end, slot + SLOT_S)
            seconds = part_end - start
            self.covered[slot] = self.covered.get(slot, 0.0) + seconds
            energy = abs(grid_w) * seconds / 3600
            target = self.imported if grid_w > 0 else self.exported
            target[slot] = target.get(slot, 0.0) + energy
            start = part_end

    def complete(self, slot: int) -> bool:
        return self.covered.get(slot, 0.0) >= COMPLETE_SHARE * SLOT_S

    def prune(self, now: float) -> None:
        oldest = now - KEEP_DAYS * 86400
        for series in (self.imported, self.exported, self.covered):
            for slot in [slot for slot in series if slot < oldest]:
                del series[slot]

    def as_dict(self) -> dict[str, Any]:
        if not self.covered:
            return {}
        first, last = min(self.covered), max(self.covered)
        slots = range(first, last + SLOT_S, SLOT_S)
        return {
            "start": first,
            "import": [round(self.imported.get(slot, 0.0), 2) for slot in slots],
            "export": [round(self.exported.get(slot, 0.0), 2) for slot in slots],
            "covered": [round(self.covered.get(slot, 0.0)) for slot in slots],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> GridQuarters:
        quarters = cls()
        if not data:
            return quarters
        try:
            start = int(data["start"])
            for index, (imported, exported, covered) in enumerate(
                zip(data["import"], data["export"], data["covered"], strict=True)
            ):
                if covered:
                    slot = start + index * SLOT_S
                    quarters.covered[slot] = float(covered)
                    if imported:
                        quarters.imported[slot] = float(imported)
                    if exported:
                        quarters.exported[slot] = float(exported)
        except (KeyError, TypeError, ValueError):
            return cls()
        return quarters

    def hour(self, hour_start: int) -> tuple[list[float], list[float]] | None:
        """Import and export (Wh) of the four quarters of an hour, None if one is not recorded."""
        slots = range(hour_start, hour_start + 3600, SLOT_S)
        if not all(self.complete(slot) for slot in slots):
            return None
        return (
            [self.imported.get(slot, 0.0) for slot in slots],
            [self.exported.get(slot, 0.0) for slot in slots],
        )


def _scaled(parts: list[float], total: float | None) -> list[float]:
    """The quarter values scaled to the hourly total of a counter (evenly if they are all 0)."""
    if total is None:
        return parts
    recorded = sum(parts)
    if recorded > 0:
        return [part * total / recorded for part in parts]
    return [total / len(parts)] * len(parts)


def merge_quarters(
    imported: Mapping[datetime, float],
    exported: Mapping[datetime, float],
    quarters: GridQuarters,
    start: datetime,
    end: datetime,
    *,
    scale_to_hours: bool,
) -> tuple[dict[datetime, float], dict[datetime, float], dict[datetime, int]]:
    """Hourly energy with the recorded quarter hours where all four of an hour are there.

    With ``scale_to_hours`` (hourly values from energy counters) the quarters
    keep their shares but sum up to the counter of the hour. Returns import,
    export and the length (s) of every period.
    """
    out_import: dict[datetime, float] = {}
    out_export: dict[datetime, float] = {}
    lengths: dict[datetime, int] = {}
    first = int(start.timestamp())
    first -= first % 3600
    for hour_start in range(first, int(end.timestamp()), 3600):
        hour = dt_util.utc_from_timestamp(hour_start)
        recorded = quarters.hour(hour_start)
        if recorded is None:
            if hour in imported or hour in exported:
                out_import[hour] = imported.get(hour, 0.0)
                out_export[hour] = exported.get(hour, 0.0)
                lengths[hour] = 3600
            continue
        parts_import, parts_export = recorded
        if scale_to_hours:
            parts_import = _scaled(parts_import, imported.get(hour))
            parts_export = _scaled(parts_export, exported.get(hour))
        for index in range(4):
            slot = dt_util.utc_from_timestamp(hour_start + index * SLOT_S)
            out_import[slot] = parts_import[index]
            out_export[slot] = parts_export[index]
            lengths[slot] = SLOT_S
    return out_import, out_export, lengths
