"""Tests for the recorded hourly PV forecasts."""

from datetime import datetime, timedelta

import pytest

from homeassistant.util import dt as dt_util

from custom_components.slems.pv_forecast_log import KEEP_DAYS, PvForecastLog


@pytest.fixture(autouse=True)
def vienna() -> None:
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Vienna"))


def local(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 6, day, hour, minute, tzinfo=dt_util.get_default_time_zone())


def forecast(day: int) -> dict[datetime, float]:
    """Half hourly periods from 06:00 to 18:00 of a day and of the next day."""
    result = {}
    for offset in (0, 1):
        for half in range(12, 36):
            start = local(day, 0) + timedelta(days=offset, minutes=30 * half)
            result[start] = 400.4
    return result


def test_records_the_rest_of_today_once_per_hour() -> None:
    log = PvForecastLog()
    assert log.record(forecast(1), local(1, 9), 2500.6)
    assert not log.record(forecast(1), local(1, 9, 40), 3100.0)
    record = log.days["2026-06-01"]["09:00"]
    assert record["produced_wh"] == 2501
    # From the current hour to the end of today, summed per hour; not tomorrow.
    assert record["forecast_wh"] == {f"{h:02d}": 801 for h in range(9, 18)}
    assert log.record(forecast(1), local(1, 10, 1), None)
    assert log.days["2026-06-01"]["10:01"]["produced_wh"] is None


def test_nothing_left_today_is_not_recorded() -> None:
    log = PvForecastLog()
    assert not log.record(forecast(1), local(1, 19), 9000.0)
    assert log.days == {}
    # Before sunrise: the whole day.
    assert log.record(forecast(1), local(1, 3), 0.0)
    assert len(log.days["2026-06-01"]["03:00"]["forecast_wh"]) == 12


def test_kept_days_and_restore() -> None:
    log = PvForecastLog()
    start = local(1, 8)
    for day in range(KEEP_DAYS + 2):
        moment = start + timedelta(days=day)
        log.record({moment: 500.0}, moment, 0.0)
    assert len(log.days) == KEEP_DAYS
    assert "2026-06-01" not in log.days
    restored = PvForecastLog.from_dict(log.as_dict())
    assert restored.days == log.days
