"""Bad weather mode: keep as much energy as possible until PV takes over again.

Switched on by the user (or an automation) when bad weather is coming. Until
``until`` the batteries charge every surplus at once (no grid friendly
charging) and the night discharge is off; the target grid surplus, the
feed-in cap and the battery limits stay.

The mode covers the next night: it lasts until the morning after the next
evening. Switched on during the day this is the next morning; switched on at
night (after the evening or before the morning) the morning after next.

The evening is the end of the last hour of the day in which the PV forecast
is above the consumption forecast, without such an hour the sunset. The
morning is the start of the first hour after the evening in which the PV
forecast is above the consumption forecast, as the night discharge plans
until then. Without such an hour in the forecast the mode goes on: until the
sunrise after the end of the forecast, recomputed with every new forecast.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime, time, timedelta

from homeassistant.util import dt as dt_util

PERIOD = timedelta(hours=1)
# Used where the sun neither rises nor sets on a day.
FALLBACK_SUNRISE_HOUR = 6
FALLBACK_SUNSET_HOUR = 20

# Sunrise or sunset of a day (None if there is none).
SunEvent = Callable[[date], datetime | None]


def _surplus_hours(
    pv: Mapping[datetime, float] | None,
    consumption: Mapping[datetime, float] | None,
) -> list[datetime]:
    if pv is None or consumption is None:
        return []
    return sorted(hour for hour, wh in pv.items() if wh > consumption.get(hour, 0.0))


def _local(day: date, hour: int) -> datetime:
    return datetime.combine(day, time(hour), tzinfo=dt_util.get_default_time_zone())


class BadWeatherMode:
    """State of the bad weather mode (stored with the learned data)."""

    def __init__(self, sunrise: SunEvent, sunset: SunEvent) -> None:
        self._sunrise = sunrise
        self._sunset = sunset
        # Evening before the night the mode covers, and its end; None when off.
        self.evening: datetime | None = None
        self.until: datetime | None = None

    def sunrise(self, day: date) -> datetime:
        return self._sunrise(day) or _local(day, FALLBACK_SUNRISE_HOUR)

    def sunset(self, day: date) -> datetime:
        return self._sunset(day) or _local(day, FALLBACK_SUNSET_HOUR)

    def evening_of(
        self,
        day: date,
        pv: Mapping[datetime, float] | None,
        consumption: Mapping[datetime, float] | None,
    ) -> datetime:
        """End of the last hour of ``day`` with PV above consumption (hourly Wh), else the sunset."""
        surplus = [hour for hour in _surplus_hours(pv, consumption) if dt_util.as_local(hour).date() == day]
        return surplus[-1] + PERIOD if surplus else self.sunset(day)

    def morning_after(
        self,
        evening: datetime,
        pv: Mapping[datetime, float] | None,
        consumption: Mapping[datetime, float] | None,
    ) -> datetime:
        """Start of the first hour after ``evening`` with PV above consumption.

        Without one the sunrise after the end of the forecast (after the
        evening without a forecast).
        """
        if surplus := [hour for hour in _surplus_hours(pv, consumption) if hour >= evening]:
            return surplus[0]
        last = max(evening, max(pv) + PERIOD) if pv else evening
        day = dt_util.as_local(last).date()
        sunrise = self.sunrise(day)
        return sunrise if sunrise >= last else self.sunrise(day + timedelta(days=1))

    def active(self, now: datetime) -> bool:
        return self.until is not None and now < self.until

    def switch_on(
        self,
        now: datetime,
        pv: Mapping[datetime, float] | None,
        consumption: Mapping[datetime, float] | None,
    ) -> None:
        day = dt_util.as_local(now).date()
        evening = self.evening_of(day, pv, consumption)
        if evening <= now:
            evening = self.evening_of(day + timedelta(days=1), pv, consumption)
        self.evening, self.until = evening, self.morning_after(evening, pv, consumption)

    def switch_off(self) -> None:
        self.evening = self.until = None

    def update(
        self,
        now: datetime,
        pv: Mapping[datetime, float] | None,
        consumption: Mapping[datetime, float] | None,
    ) -> bool:
        """Follow a new forecast; switches off once the morning has come.

        Returns True if the mode was switched off.
        """
        if self.evening is None:
            return False
        # Checked first: once the morning has come, a forecast without the past
        # hours must not move it to the next surplus.
        if now < self.until and pv is not None and consumption is not None:
            self.until = self.morning_after(self.evening, pv, consumption)
        if now >= self.until:
            self.switch_off()
            return True
        return False

    def as_dict(self) -> dict | None:
        if self.evening is None:
            return None
        return {"evening": self.evening.isoformat(), "until": self.until.isoformat()}

    def restore(self, data: dict | None) -> None:
        if not data or "evening" not in data:
            return
        self.evening = datetime.fromisoformat(data["evening"])
        self.until = datetime.fromisoformat(data["until"])
