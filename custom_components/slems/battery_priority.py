"""Battery priority automatic: the state of charge below which the batteries
get the whole surplus, from the consumption of the coming night.

The batteries should hold what the house needs from the end of the PV surplus
today until PV takes over again tomorrow (as for the morning reserve, see
night_discharge.pv_takeover):

    priority = minimum SoC + night consumption ÷ discharge efficiency ÷ capacity

between the minimum SoC + ``MIN_ABOVE_PCT`` and ``MAX_PCT`` (100 % would never
leave the surplus to grid friendly charging).

The night consumption is the consumption forecast with the forced runs of the
daily targets, without the runs of consumers whose battery support is never
or automatic (the grid covers them, or the batteries only with what they can
spare) and without the heat pump part of the forecast while no heat pump has
the battery support always.

It is computed once a day when PV takes over (the start of the first hour of
the day in which the PV forecast exceeds the consumption forecast) and kept
until the next takeover, so the threshold does not move during the day. Kept
is the night consumption in Wh, not the percentage: the control and the
simulation derive the percentage with their battery values.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

from .local_time import next_start
from .pv_forecast import hourly

PERIOD = timedelta(hours=1)
MIN_ABOVE_PCT = 10.0
MAX_PCT = 90.0


def surplus_hours(day: date, pv: Mapping[datetime, float], consumption: Mapping[datetime, float]) -> list[datetime]:
    """Starts of the hours of the local ``day`` in which PV exceeds the consumption.

    Both forecasts are hourly (period start -> Wh)."""
    pv = hourly(pv)
    consumption = hourly(consumption)
    return sorted(
        start for start, wh in pv.items() if start.date() == day and wh > consumption.get(start, 0.0)
    )


def takeover(day: date, pv: Mapping[datetime, float], consumption: Mapping[datetime, float]) -> datetime | None:
    """Start of the first hour of the local ``day`` in which PV exceeds the consumption."""
    hours = surplus_hours(day, pv, consumption)
    return hours[0] if hours else None


def night_energy_wh(
    day: date, pv: Mapping[datetime, float], consumption: Mapping[datetime, float]
) -> float | None:
    """Consumption from the end of the PV surplus of the local ``day`` until PV
    takes over on the next day; None without a surplus hour on ``day``.

    Without a takeover in the forecast of the next day (none, or no forecast)
    the night ends at the time of the takeover on ``day``. An hour without a
    consumption forecast (beyond its end) takes the one a day earlier.
    """
    hours = surplus_hours(day, pv, consumption)
    if not hours:
        return None
    start = hours[-1] + PERIOD
    next_day = day + timedelta(days=1)
    end = takeover(next_day, pv, consumption) or dt_util.as_local(
        datetime.combine(next_day, hours[0].time(), tzinfo=hours[0].tzinfo)
    )
    consumption = hourly(consumption)
    total = 0.0
    hour = start
    while hour < end:
        wh = consumption.get(hour)
        if wh is None:
            wh = consumption.get(dt_util.as_local(hour - timedelta(days=1)), 0.0)
        total += wh
        hour = next_start(hour, PERIOD)
    return total


def priority_soc_pct(night_wh: float, min_soc_pct: float, capacity_wh: float, efficiency: float) -> float:
    """The battery priority threshold (%) that holds ``night_wh`` above the minimum SoC."""
    low = min(MAX_PCT, min_soc_pct + MIN_ABOVE_PCT)
    if capacity_wh <= 0:
        return low
    pct = min_soc_pct + night_wh / (efficiency or 1.0) / capacity_wh * 100
    return min(MAX_PCT, max(low, pct))


@dataclass
class BatteryPriorityDay:
    """The night consumption computed at the last takeover, with its day."""

    day: date | None = None
    night_wh: float | None = None

    def due(self, now: datetime, pv: Mapping[datetime, float], consumption: Mapping[datetime, float]) -> bool:
        """Whether to compute now: nothing kept yet, or not for today once PV took over today."""
        local = dt_util.as_local(now)
        if self.night_wh is None:
            return True
        if self.day == local.date():
            return False
        start = takeover(local.date(), pv, consumption)
        return start is not None and start <= local

    def update(self, now: datetime, pv: Mapping[datetime, float], consumption: Mapping[datetime, float]) -> None:
        """Compute the night of today if due; a day without PV surplus keeps the
        last value. Computed before today's takeover (nothing kept yet), the
        value is computed again at the takeover."""
        if not self.due(now, pv, consumption):
            return
        local = dt_util.as_local(now)
        night = night_energy_wh(local.date(), pv, consumption)
        if night is None:
            return
        start = takeover(local.date(), pv, consumption)
        self.night_wh = round(night, 1)
        self.day = local.date() if start is not None and start <= local else None

    def as_dict(self) -> dict[str, Any]:
        return {"day": self.day.isoformat() if self.day else None, "night_wh": self.night_wh}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> BatteryPriorityDay:
        if not data:
            return cls()
        try:
            day = date.fromisoformat(data["day"]) if data.get("day") else None
            night = data.get("night_wh")
            return cls(day, float(night) if night is not None else None)
        except (TypeError, ValueError, KeyError):
            return cls()
