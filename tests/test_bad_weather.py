"""Tests for the bad weather mode."""

from datetime import datetime, timedelta

import pytest

from homeassistant.util import dt as dt_util

from custom_components.slems.bad_weather import FALLBACK_HOUR, BadWeatherMode, evening


@pytest.fixture(autouse=True)
def vienna() -> None:
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Vienna"))


def at(hour: int, day: int = 0) -> datetime:
    return datetime(2026, 6, 1, tzinfo=dt_util.get_default_time_zone()) + timedelta(days=day, hours=hour)


def forecasts(pv_by_hour: dict[int, float], load: float = 500) -> tuple[dict, dict]:
    pv = {at(h, d): pv_by_hour.get(h, 0.0) for d in (0, 1) for h in range(24)}
    consumption = {at(h, d): load for d in (0, 1) for h in range(24)}
    return pv, consumption


def test_evening_is_the_end_of_the_last_surplus_hour() -> None:
    pv, consumption = forecasts({8: 300, 10: 2000, 15: 900, 17: 400, 18: 100})
    assert evening(at(0).date(), pv, consumption) == at(16)
    # Without a surplus hour: the end of the production; without forecast: fallback.
    pv, consumption = forecasts({10: 300, 13: 200})
    assert evening(at(0).date(), pv, consumption) == at(14)
    assert evening(at(0).date(), None, None) == at(FALLBACK_HOUR)


def test_switched_on_in_the_morning_and_at_night() -> None:
    pv, consumption = forecasts({10: 2000, 15: 900})
    mode = BadWeatherMode()
    mode.switch_on(at(9), pv, consumption)
    assert mode.until == at(16) and mode.active(at(12))
    # After the evening: until the evening of the next day.
    mode.switch_on(at(21), pv, consumption)
    assert mode.until == at(16, day=1)
    restored = BadWeatherMode()
    restored.restore(mode.as_dict())
    assert restored.until == at(16, day=1) and restored.day == at(0, 1).date()


def test_update_follows_the_forecast_and_ends_the_mode() -> None:
    pv, consumption = forecasts({10: 2000, 15: 900})
    mode = BadWeatherMode()
    mode.switch_on(at(9), pv, consumption)
    pv[at(15)] = 300  # the afternoon gets worse
    assert mode.update(at(10), pv, consumption) is False
    assert mode.until == at(11)
    # A forecast without the past hours (no surplus hour left) keeps the end.
    future = {hour: wh for hour, wh in pv.items() if hour >= at(11)}
    assert mode.update(at(10) + timedelta(minutes=30), future, consumption) is False
    assert mode.until == at(11)
    assert mode.update(at(11), future, consumption) is True
    assert not mode.active(at(11)) and mode.as_dict() is None
