"""Hourly PV forecasts as they were, to check the weights of the PV correction.

Once per local hour the raw forecast of the rest of today (as the solar
forecast integrations provide it, without the PV correction) is recorded per
hour in Wh, together with the PV energy produced today until then (the actual
value of the PV correction, None while it is not known since midnight). The
first record of a day holds the forecast of the whole day, the later ones show
how the provider updated it; the production per hour is the difference of two
records. With them the PV correction (``grid_friendly.correction_weight``) can
be replayed for every hour of a recorded day.

Records start with the first hour that still has forecast PV left today, so a
day takes about 2-3 kB; ``KEEP_DAYS`` covers the seasons (about 1 MB).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

from .pv_forecast import hourly

KEEP_DAYS = 400


class PvForecastLog:
    """ISO date -> local time "HH:MM" of the record -> {"produced_wh", "forecast_wh"}.

    ``forecast_wh`` maps the local start hour ("HH") to the forecast Wh of that hour.
    """

    def __init__(self) -> None:
        self.days: dict[str, dict[str, dict[str, Any]]] = {}

    def record(
        self, forecast: Mapping[datetime, float], now: datetime, produced_wh: float | None
    ) -> bool:
        """Record the forecast of the rest of today once per hour; True if recorded."""
        local_now = dt_util.as_local(now)
        records = self.days.get(local_now.date().isoformat(), {})
        hour = f"{local_now.hour:02d}"
        if any(key.startswith(hour + ":") for key in records):
            return False
        start = local_now.replace(minute=0, second=0, microsecond=0)
        end = dt_util.start_of_local_day(local_now) + timedelta(days=1)
        rest = {
            f"{dt_util.as_local(moment).hour:02d}": round(wh)
            for moment, wh in sorted(hourly(forecast).items())
            if start <= moment < end and wh > 0
        }
        if not rest:
            return False
        records[local_now.strftime("%H:%M")] = {
            "produced_wh": None if produced_wh is None else round(produced_wh),
            "forecast_wh": rest,
        }
        self.days[local_now.date().isoformat()] = records
        self._trim()
        return True

    def _trim(self) -> None:
        for key in sorted(self.days)[:-KEEP_DAYS]:
            del self.days[key]

    def as_dict(self) -> dict[str, Any]:
        return self.days

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> PvForecastLog:
        log = cls()
        if data:
            log.days = {day: {at: dict(record) for at, record in records.items()} for day, records in data.items()}
        return log
