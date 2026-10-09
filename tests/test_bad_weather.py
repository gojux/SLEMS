"""Tests for the bad weather mode."""

from datetime import date, datetime, time, timedelta

import pytest

from homeassistant.util import dt as dt_util

from custom_components.slems.bad_weather import FALLBACK_SUNSET_HOUR, BadWeatherMode


@pytest.fixture(autouse=True)
def vienna() -> None:
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Vienna"))


def at(hour: float, day: int = 0) -> datetime:
    return datetime(2026, 6, 1, tzinfo=dt_util.get_default_time_zone()) + timedelta(days=day, hours=hour)


def sun_at(hour: float):
    return lambda day: datetime.combine(day, time(), tzinfo=dt_util.get_default_time_zone()) + timedelta(hours=hour)


def mode() -> BadWeatherMode:
    return BadWeatherMode(sun_at(5.5), sun_at(21))


def forecasts(days: dict[int, dict[int, float]], load: float = 500) -> tuple[dict, dict]:
    """Hourly forecasts over the given days (PV by day and hour)."""
    pv = {at(h, d): days[d].get(h, 0.0) for d in days for h in range(24)}
    consumption = {hour: load for hour in pv}
    return pv, consumption


SUNNY = {9: 1500, 10: 2000, 15: 900, 18: 300}


def test_evening_and_morning() -> None:
    pv, consumption = forecasts({0: SUNNY, 1: SUNNY})
    bad = mode()
    assert bad.evening_of(at(0).date(), pv, consumption) == at(16)
    assert bad.morning_after(at(16), pv, consumption) == at(9, day=1)
    # Without a surplus hour: the sunset; without a forecast as well.
    pv, consumption = forecasts({0: {10: 300}})
    assert bad.evening_of(at(0).date(), pv, consumption) == at(21)
    assert bad.evening_of(at(0).date(), None, None) == at(21)
    # Without a sunset (polar day): a fixed hour.
    assert BadWeatherMode(sun_at(5.5), lambda day: None).evening_of(
        at(0).date(), None, None
    ) == at(FALLBACK_SUNSET_HOUR)


def test_switched_on_during_the_day_lasts_until_the_next_morning() -> None:
    pv, consumption = forecasts({0: SUNNY, 1: SUNNY})
    bad = mode()
    bad.switch_on(at(12), pv, consumption)
    assert bad.evening == at(16) and bad.until == at(9, day=1)
    assert bad.active(at(23)) and not bad.active(at(9, day=1))
    restored = mode()
    restored.restore(bad.as_dict())
    assert restored.evening == at(16) and restored.until == at(9, day=1)


def test_switched_on_at_night_lasts_until_the_morning_after_next() -> None:
    pv, consumption = forecasts({0: SUNNY, 1: SUNNY, 2: SUNNY})
    bad = mode()
    # In the evening and after midnight the same night is covered.
    bad.switch_on(at(22), pv, consumption)
    assert bad.until == at(9, day=2)
    bad.switch_on(at(2, day=1), pv, consumption)
    assert bad.until == at(9, day=2)


def test_goes_on_without_a_surplus_hour() -> None:
    pv, consumption = forecasts({0: SUNNY, 1: {12: 400}, 2: {}})
    bad = mode()
    bad.switch_on(at(12), pv, consumption)
    # No surplus in the forecast: until the sunrise after its end.
    assert bad.until == at(5.5, day=3)
    # A newer forecast with a surplus on the third day.
    pv, consumption = forecasts({1: {12: 400}, 2: {}, 3: SUNNY})
    assert bad.update(at(8, day=1), pv, consumption) is False
    assert bad.until == at(9, day=3)
    # Without a forecast: the sunrise after the evening.
    bad.switch_on(at(12), None, None)
    assert bad.evening == at(21) and bad.until == at(5.5, day=1)


def test_update_follows_the_forecast_and_ends_the_mode() -> None:
    pv, consumption = forecasts({0: SUNNY, 1: SUNNY})
    bad = mode()
    bad.switch_on(at(12), pv, consumption)
    pv[at(9, day=1)] = 300  # the morning gets worse
    assert bad.update(at(23), pv, consumption) is False
    assert bad.until == at(10, day=1)
    # Once the morning has come, a forecast without the past hours does not move it.
    future = {hour: wh for hour, wh in pv.items() if hour >= at(11, day=1)}
    assert bad.update(at(10, day=1), future, consumption) is True
    assert not bad.active(at(10, day=1)) and bad.as_dict() is None
    # Without a forecast the end stays.
    bad.switch_on(at(12), pv, consumption)
    assert bad.update(at(13), None, None) is False and bad.until == at(10, day=1)


def test_old_stored_state_is_ignored() -> None:
    bad = mode()
    bad.restore({"day": date(2026, 6, 1).isoformat(), "until": at(16).isoformat()})
    assert bad.until is None
