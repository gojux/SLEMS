"""Regular full charge of the batteries.

LFP batteries recalibrate their state of charge only at the top of the charge,
where the BMS also balances the cells passively. A battery whose last full
charge (``CellMonitor.observe_full``) is longer ago than the interval, or not
known yet, is *due*. One due battery at a time, the one whose last full charge
is the oldest (unknown first, then by name), is preferred:

* Charging: it gets the charge power first, the other batteries the rest
  (``BatteryDistributor``). The split between batteries and consumers does
  not change.
* It may charge above its maximum SoC once; the limit applies again as soon
  as it was full.
* Discharging: it is spared while all other batteries have more than
  ``SPARE_OTHERS_MIN_SOC_PCT`` and can deliver the power, so it starts the
  next day higher if it did not get full.
* Once full it rests ``REST_S`` without power, so the cell delta at the top is
  measured (``CellMonitor``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

SPARE_OTHERS_MIN_SOC_PCT = 50.0
# Longer than the rest the cell monitor needs for a measurement (60 s).
REST_S = 90.0
DAY_S = 86400.0


@dataclass(frozen=True)
class FullChargeCandidate:
    """A battery that can be preferred: controllable and in the planning."""

    battery_id: str
    name: str
    # Wall clock time of the last full charge, None if not known.
    last_full: float | None


def is_due(last_full: float | None, now: float, interval_days: float) -> bool:
    return last_full is None or now - last_full > interval_days * DAY_S


def due_battery(
    candidates: Sequence[FullChargeCandidate], now: float, interval_days: float
) -> str | None:
    """The battery to prefer: the oldest last full charge among the due ones."""
    due = [c for c in candidates if is_due(c.last_full, now, interval_days)]
    if not due:
        return None
    due.sort(
        key=lambda c: (c.last_full if c.last_full is not None else float("-inf"), c.name.casefold())
    )
    return due[0].battery_id
