"""Whether the smart meter's energy counters net the phases like the utility meter.

The instantaneous grid power is the sum of the phases, so it is netted. Some
meters count the energy per phase instead (e.g. the Shelly Pro 3EM): while one
phase draws and another feeds in, the import and the export counter both run.
In a quarter hour that shows as both counters above the import and the export
added up from the grid power, by about the same amount (the energy that
flowed in on one phase and out on another). Counter readings that are late
only shift energy between quarter hours and never raise both.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .grid_quarters import SLOT_S, GridQuarters

# A reading of the counters counts for a quarter hour boundary up to this long after it.
READING_WINDOW_S = 30.0
# Both counters above the grid power by at least this much, and alike within the share.
MIN_EXTRA_WH = 30.0
ALIKE_SHARE = 0.3
ALIKE_WH = 10.0
# Quarter hours with that pattern within KEEP_S before the counters count as not netted.
NOT_NETTED_QUARTERS = 4
KEEP_S = 7 * 86400


def per_phase_pattern(imported_wh: float, exported_wh: float, grid_import_wh: float, grid_export_wh: float) -> bool:
    """Whether a quarter hour's counters look like counting per phase."""
    extra_import = imported_wh - grid_import_wh
    extra_export = exported_wh - grid_export_wh
    if extra_import < MIN_EXTRA_WH or extra_export < MIN_EXTRA_WH:
        return False
    return abs(extra_import - extra_export) <= ALIKE_SHARE * max(extra_import, extra_export) + ALIKE_WH


class NettingCheck:
    """Compares the counters with the netted grid power per quarter hour."""

    def __init__(self) -> None:
        # Quarter hour start -> counter readings (kWh) at its start.
        self._readings: dict[int, tuple[float, float]] = {}
        # Quarter hours that looked like counting per phase.
        self.per_phase: list[int] = []
        # The counters checked (another pair starts anew).
        self.counters: tuple[str, str] | None = None

    def observe(
        self, now: float, counters: tuple[str, str], imported_kwh: float | None, exported_kwh: float | None,
        quarters: GridQuarters,
    ) -> None:
        """A reading of the import and export counter at unix time ``now``."""
        if counters != self.counters:
            self._readings = {}
            self.per_phase = []
            self.counters = counters
        if imported_kwh is None or exported_kwh is None:
            return
        slot = int(now - now % SLOT_S)
        if slot not in self._readings and now - slot <= READING_WINDOW_S:
            self._readings[slot] = (imported_kwh, exported_kwh)
            previous = slot - SLOT_S
            if previous in self._readings and quarters.complete(previous):
                start, end = self._readings[previous], self._readings[slot]
                imported = (end[0] - start[0]) * 1000
                exported = (end[1] - start[1]) * 1000
                # A counter reset or a replaced meter tells nothing.
                if imported >= 0 and exported >= 0 and per_phase_pattern(
                    imported, exported, quarters.imported.get(previous, 0.0), quarters.exported.get(previous, 0.0)
                ):
                    self.per_phase.append(previous)
        self._readings = {s: r for s, r in self._readings.items() if s >= slot - SLOT_S}
        self.per_phase = [s for s in self.per_phase if s >= now - KEEP_S]

    @property
    def not_netted(self) -> bool:
        return len(self.per_phase) >= NOT_NETTED_QUARTERS

    def as_dict(self) -> dict[str, Any]:
        return {"counters": list(self.counters) if self.counters else None, "per_phase": self.per_phase}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> NettingCheck:
        check = cls()
        if data and data.get("counters"):
            check.counters = tuple(data["counters"])  # type: ignore[assignment]
            check.per_phase = [int(s) for s in data.get("per_phase") or ()]
        return check
