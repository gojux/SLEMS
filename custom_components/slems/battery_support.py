"""How far the batteries may cover a consumer (battery support per consumer).

* *Always*: the batteries cover the consumer like any other load.
* *Never*: its power never comes from the batteries; in a deficit the grid
  covers it (the house is covered as before).
* *Automatic*: the batteries cover it only with the energy they can spare,
  the *budget*:

      budget = lowest stored energy until the next charge from PV
             − (minimum SoC + morning reserve + safety buffer)

  The lowest stored energy comes from a SoC projection without night
  discharge and without the loads this budget is for: what the night
  discharge would feed in may go to such a consumer instead. "Until the next
  charge" is the next hour in which PV exceeds the consumption after the
  next deficit (at night the coming morning; during a surplus the morning
  after the coming night). The budget is computed anew from the current state
  of charge in every update, so it shrinks by itself while the batteries
  cover the consumer. Once used up, it counts again from ``RESUME_WH``.

With import peak shaving the batteries are kept for peaks below its SoC
threshold; the budget keeps that threshold as well.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from enum import StrEnum

from homeassistant.util import dt as dt_util

PERIOD = timedelta(hours=1)
LOOKAHEAD = timedelta(hours=48)
RESUME_WH = 200.0


class BatterySupport(StrEnum):
    ALWAYS = "always"
    AUTO = "auto"
    NEVER = "never"


def next_refill(
    now: datetime,
    pv: Mapping[datetime, float],
    consumption: Mapping[datetime, float],
    needed_wh: float = 0.0,
    max_charge_w: float | None = None,
) -> datetime | None:
    """Start of the next hour with PV above consumption after the next deficit,
    on a day whose PV surplus refills the batteries.

    ``pv`` and ``consumption`` are hourly local buckets (Wh). The surplus of
    the hours of that day with PV above consumption from that hour on (per
    hour at most ``max_charge_w``) must reach ``needed_wh`` (AC energy from
    the minimum to the maximum SoC); a smaller surplus only slows the
    discharge and the search goes on with the next day. None without such a
    day within ``LOOKAHEAD``.
    """
    hour = dt_util.as_local(now).replace(minute=0, second=0, microsecond=0)
    end = hour + LOOKAHEAD
    deficit_seen = False
    while hour < end:
        surplus = pv.get(hour, 0.0) > consumption.get(hour, 0.0)
        if not surplus:
            deficit_seen = True
        elif deficit_seen:
            next_day = dt_util.start_of_local_day(hour) + timedelta(days=1)
            if _day_surplus(hour, next_day, pv, consumption, max_charge_w) >= needed_wh:
                return hour
            hour = next_day
            continue
        hour += PERIOD
    return None


def _day_surplus(
    start: datetime,
    end: datetime,
    pv: Mapping[datetime, float],
    consumption: Mapping[datetime, float],
    max_charge_w: float | None,
) -> float:
    total = 0.0
    while start < end:
        surplus = max(0.0, pv.get(start, 0.0) - consumption.get(start, 0.0))
        total += surplus if max_charge_w is None else min(surplus, max_charge_w * PERIOD.total_seconds() / 3600)
        start += PERIOD
    return total


def support_budget_wh(
    stored_wh: float,
    projected_soc_pct: Mapping[datetime, float],
    capacity_wh: float,
    until: datetime | None,
    floor_wh: float,
) -> float:
    """Energy the batteries can spare for consumers on *automatic*.

    ``projected_soc_pct`` holds the SoC at the end of each hour (projection
    without night discharge and without those consumers' loads); hours from
    ``until`` on do not count. Without ``until`` the whole projection counts.
    """
    lowest = stored_wh
    for hour, soc in projected_soc_pct.items():
        if until is None or hour < until:
            lowest = min(lowest, soc / 100 * capacity_wh)
    return max(0.0, lowest - floor_wh)


class SupportBudget:
    """The budget with its hysteresis: used up at 0, available again from ``RESUME_WH``."""

    def __init__(self) -> None:
        self.budget_wh: float | None = None
        self.used_up = False

    def update(self, budget_wh: float | None) -> None:
        self.budget_wh = budget_wh
        if budget_wh is None:
            self.used_up = False
        elif budget_wh <= 0:
            self.used_up = True
        elif budget_wh >= RESUME_WH:
            self.used_up = False

    @property
    def available(self) -> bool:
        """Whether consumers on *automatic* may draw from the batteries now.

        Without a budget (no forecast or no batteries) they are treated like
        *always*.
        """
        return self.budget_wh is None or not self.used_up
