"""Tests for the consumption forecast models."""

from datetime import date, datetime, timedelta

import pytest

from homeassistant.util import dt as dt_util

from custom_components.slems.allocation import expected_surplus_wh
from custom_components.slems.forecast import (
    ForecastSources,
    _mean_temperature,
    build_house_history,
)
from custom_components.slems.forecast.models import (
    BaseLoadProfile,
    HeatPumpModel,
    daily_mean_temperature,
)


@pytest.fixture(autouse=True)
def vienna() -> None:
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Vienna"))


# Monday
NOW = datetime(2026, 3, 2, 0, 0, tzinfo=dt_util.get_time_zone("Europe/Vienna"))


def hours(days: int):
    start = NOW - timedelta(days=days)
    for index in range(days * 24):
        yield start + timedelta(hours=index)


def test_base_profile_separates_workdays_and_weekends() -> None:
    history = {
        start: (600 if start.weekday() >= 5 else 300) + (200 if start.hour == 19 else 0)
        for start in hours(28)
    }
    model = BaseLoadProfile.fit(history, NOW)
    assert model.predict(NOW.replace(hour=19)) == pytest.approx(500)
    assert model.predict(NOW.replace(hour=3)) == pytest.approx(300)
    saturday = NOW + timedelta(days=5)
    assert model.predict(saturday.replace(hour=3)) == pytest.approx(600)


def test_base_profile_follows_recent_change() -> None:
    # Consumption doubled two weeks ago: the profile moves halfway (half-life),
    # the short-term correction lifts the prediction close to the new level.
    history = {
        start: 800 if start >= NOW - timedelta(days=14) else 400 for start in hours(56)
    }
    model = BaseLoadProfile.fit(history, NOW)
    assert 550 < model.raw(NOW.replace(hour=12)) < 700
    assert model.predict(NOW.replace(hour=12)) > 700


def test_short_term_correction_is_limited() -> None:
    history = {start: 300 for start in hours(28)}
    for start in hours(3):
        history[start] = 900
    model = BaseLoadProfile.fit(history, NOW)
    assert model.correction == pytest.approx(1.25)


def test_vacation_uses_night_level() -> None:
    history = {start: 200 if start.hour < 6 else 700 for start in hours(28)}
    model = BaseLoadProfile.fit(history, NOW)
    assert model.predict(NOW.replace(hour=18), vacation=True) == pytest.approx(200)


def _heat_pump_history(temperature_of_day):
    history = {}
    temperatures = {}
    for start in hours(60):
        day = start.date()
        temperature = temperature_of_day(day)
        temperatures[start] = temperature
        daily = 3000 + 800 * max(0.0, 15 - temperature)
        history[start] = daily / 24
    return history, temperatures


def test_heat_pump_model_recovers_parameters() -> None:
    history, temperatures = _heat_pump_history(lambda day: (day.toordinal() % 20) - 5)
    model = HeatPumpModel.fit(history, daily_mean_temperature(temperatures), NOW)
    assert model.temperature_based
    assert model.heating_limit_c == 15
    assert model.base_wh == pytest.approx(3000, rel=0.01)
    assert model.slope_wh_per_k == pytest.approx(800, rel=0.01)
    # A warm day in the heating season needs only hot water.
    assert model.daily(20) == pytest.approx(3000, rel=0.02)
    assert model.daily(0) == pytest.approx(15000, rel=0.02)


def test_heat_pump_without_temperatures_uses_recent_mean() -> None:
    history, _ = _heat_pump_history(lambda day: 5)
    model = HeatPumpModel.fit(history, {}, NOW)
    assert not model.temperature_based
    assert model.daily(None) == pytest.approx(11000)


def test_house_history_prefers_explicit_sources() -> None:
    hour_a = NOW - timedelta(hours=2)
    hour_b = NOW - timedelta(hours=1)
    sources = ForecastSources(
        house=("sensor.slems_house", "sensor.legacy_house"),
        grid="sensor.grid", grid_inverted=False, pv="sensor.pv",
        batteries=("sensor.battery",), heat_pumps=(), controllable=(),
        temperature=None, weather=None,
    )
    means = {
        "sensor.grid": {hour_a: 100, hour_b: 100},
        "sensor.pv": {hour_a: 1000, hour_b: 1000},
        "sensor.battery": {hour_a: 500, hour_b: 500},
        "sensor.legacy_house": {hour_a: 700},
        "sensor.slems_house": {hour_b: 650},
    }
    house = build_house_history(sources, means)
    assert house == {hour_a: 700, hour_b: 650}
    del means["sensor.legacy_house"]
    assert build_house_history(sources, means)[hour_a] == 600  # 100 + 1000 - 500


def test_mean_temperature_combines_measured_and_forecast() -> None:
    day = NOW.date()
    measured = {NOW + timedelta(hours=h): 2.0 for h in range(10)}
    forecast = {NOW + timedelta(hours=h): 8.0 for h in range(24)}
    # 10 measured hours at 2 °C, 14 forecast hours at 8 °C
    assert _mean_temperature(day, measured, forecast, {}) == pytest.approx(5.5)
    assert _mean_temperature(day, {}, {}, {date(2026, 3, 1): 4.0}) == 4.0


def test_expected_surplus_with_consumption_forecast() -> None:
    day = NOW.replace(hour=0)
    pv = {day.replace(hour=h): 2000 for h in (11, 12)}
    consumption = {day.replace(hour=h): 500 for h in range(24)}
    consumption[day.replace(hour=12)] = 2500
    # 11:00 -> 1500 Wh surplus, 12:00 -> none; night consumption does not count.
    assert expected_surplus_wh(pv, day.replace(hour=6), 400, consumption) == pytest.approx(1500)


def test_forecast_days_after_local_midnight() -> None:
    """00:30 in Vienna is still the previous day in UTC."""
    from datetime import timezone
    from types import SimpleNamespace

    from custom_components.slems.forecast import ConsumptionForecaster

    now_utc = datetime(2026, 9, 23, 22, 0, tzinfo=timezone.utc)  # 00:00 local, Sep 24
    history = {now_utc - timedelta(hours=h): 400.0 for h in range(1, 28 * 24)}
    forecaster = ConsumptionForecaster(None, SimpleNamespace(heat_pumps=()))
    result = forecaster._compute(now_utc, history, {}, {}, {}, False)
    days = sorted({dt_util.as_local(start).date() for start in result.total})
    assert days == [date(2026, 9, 24), date(2026, 9, 25)]
