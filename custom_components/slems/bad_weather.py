"""Bad weather mode: store as much of the PV surplus as possible until the evening.

Switched on by the user (or an automation) when bad weather is coming. Until
``until`` the batteries charge every surplus at once (no grid friendly
charging) and the night discharge is off; the target grid surplus, the
feed-in cap and the battery limits stay.

The evening is the end of the last hour of the day in which the PV forecast
is above the consumption forecast; without such an hour the end of the PV
production, without a forecast ``FALLBACK_HOUR``. Switched on after that
moment, the mode lasts until the evening of the next day (the night
discharge of this night is off as well). The end is recomputed with every
new forecast for the day chosen when switching on.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time, timedelta

from homeassistant.util import dt as dt_util

PERIOD = timedelta(hours=1)
FALLBACK_HOUR = 20


def _producing(day: date, pv: Mapping[datetime, float] | None) -> list[datetime]:
    return sorted(
        hour for hour in (pv or {}) if dt_util.as_local(hour).date() == day and pv[hour] > 0
    )


def surplus_end(
    day: date,
    pv: Mapping[datetime, float] | None,
    consumption: Mapping[datetime, float] | None,
) -> datetime | None:
    """End of the last hour of ``day`` with PV above consumption (hourly Wh)."""
    if consumption is None:
        return None
    surplus = [hour for hour in _producing(day, pv) if pv[hour] > consumption.get(hour, 0.0)]
    return surplus[-1] + PERIOD if surplus else None


def evening(
    day: date,
    pv: Mapping[datetime, float] | None,
    consumption: Mapping[datetime, float] | None,
) -> datetime:
    """The evening of ``day``: end of the surplus, else of the PV production."""
    if (end := surplus_end(day, pv, consumption)) is not None:
        return end
    if hours := _producing(day, pv):
        return hours[-1] + PERIOD
    return datetime.combine(day, time(FALLBACK_HOUR), tzinfo=dt_util.get_default_time_zone())


class BadWeatherMode:
    """State of the bad weather mode (stored with the learned data)."""

    def __init__(self) -> None:
        # Day whose evening ends the mode, and that evening; None when off.
        self.day: date | None = None
        self.until: datetime | None = None

    def active(self, now: datetime) -> bool:
        return self.until is not None and now < self.until

    def switch_on(
        self,
        now: datetime,
        pv: Mapping[datetime, float] | None,
        consumption: Mapping[datetime, float] | None,
    ) -> None:
        today = dt_util.as_local(now).date()
        until = evening(today, pv, consumption)
        if until <= now:
            today += timedelta(days=1)
            until = evening(today, pv, consumption)
        self.day, self.until = today, until

    def switch_off(self) -> None:
        self.day = self.until = None

    def update(
        self,
        now: datetime,
        pv: Mapping[datetime, float] | None,
        consumption: Mapping[datetime, float] | None,
    ) -> bool:
        """Follow a new forecast; switches off once the evening has passed.

        Returns True if the mode was switched off.
        """
        if self.day is None:
            return False
        # Only a surplus hour moves the end: a forecast without the past hours
        # of the day must not stretch it to the end of the production.
        if (end := surplus_end(self.day, pv, consumption)) is not None:
            self.until = end
        if now >= self.until:
            self.switch_off()
            return True
        return False

    def as_dict(self) -> dict | None:
        if self.day is None:
            return None
        return {"day": self.day.isoformat(), "until": self.until.isoformat()}

    def restore(self, data: dict | None) -> None:
        if not data:
            return
        self.day = date.fromisoformat(data["day"])
        self.until = datetime.fromisoformat(data["until"])
