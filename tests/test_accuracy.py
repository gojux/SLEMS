"""Tests for the accuracy of the forecasts."""

from datetime import date, datetime, timedelta

import pytest

from homeassistant.util import dt as dt_util

from custom_components.slems.forecast.accuracy import (
    Accuracy,
    DayResult,
    PvAccuracyTracker,
    backtest_consumption,
    history_days,
)


@pytest.fixture(autouse=True)
def vienna() -> None:
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Vienna"))


def midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=dt_util.get_default_time_zone())


def hourly(days: int, end: datetime, value) -> dict[datetime, float]:
    start = end - timedelta(days=days)
    return {
        start + timedelta(hours=h): value(start + timedelta(hours=h))
        for h in range(days * 24)
    }


def test_steady_consumption_is_forecast_exactly() -> None:
    now = midnight(date(2026, 6, 20)) + timedelta(hours=10)
    history = hourly(40, now, lambda _: 500.0)
    result = backtest_consumption(history, None, {}, now)
    assert len(result.days) == 14
    assert result.daily_error == pytest.approx(0, abs=1e-6)
    assert result.hourly_error == pytest.approx(0, abs=1e-6)
    assert result.history_days >= 39


def test_backtest_uses_only_the_history_before_the_day() -> None:
    now = midnight(date(2026, 6, 20)) + timedelta(hours=10)
    jump = midnight(date(2026, 6, 19))
    # The consumption doubles on the last complete day: that day is missed.
    history = hourly(40, now, lambda start: 1000.0 if start >= jump else 500.0)
    result = backtest_consumption(history, None, {}, now)
    last = result.days[-1]
    assert last.day == date(2026, 6, 19)
    assert last.error == pytest.approx(-0.5, abs=0.05)


def test_expected_error_prefers_the_same_day_type() -> None:
    accuracy = Accuracy(
        days=[
            DayResult(date(2026, 6, 15), 11000, 10000),  # Monday +10 %
            DayResult(date(2026, 6, 16), 11000, 10000),  # Tuesday +10 %
            DayResult(date(2026, 6, 20), 7000, 10000),  # Saturday -30 %
            DayResult(date(2026, 6, 21), 7000, 10000),  # Sunday -30 %
        ]
    )
    assert accuracy.expected_error(date(2026, 6, 22)) == pytest.approx(0.1)  # Monday
    assert accuracy.expected_error(date(2026, 6, 27)) == pytest.approx(0.3)  # Saturday
    assert accuracy.bias == pytest.approx(-0.1)


def test_pv_tracker_records_the_forecast_at_the_start_of_the_day() -> None:
    tracker = PvAccuracyTracker()
    day = date(2026, 6, 20)
    tracker.record_forecast(day, 20000, midnight(day) + timedelta(hours=8))
    assert not tracker.days  # too late: already an intraday forecast
    tracker.record_forecast(day, 20000, midnight(day) + timedelta(minutes=5))
    tracker.record_forecast(day, 25000, midnight(day) + timedelta(hours=1))
    tracker.record_actual(day, 16000)
    accuracy = tracker.accuracy()
    assert accuracy.days[0].forecast_wh == 20000
    assert accuracy.bias == pytest.approx(0.25)
    restored = PvAccuracyTracker()
    restored.restore(tracker.as_dict())
    assert restored.accuracy().daily_error == pytest.approx(0.25)


def test_history_days_counts_complete_days() -> None:
    now = midnight(date(2026, 6, 20))
    history = hourly(5, now, lambda _: 1.0)
    del history[now - timedelta(hours=3)]
    del history[now - timedelta(hours=4)]
    del history[now - timedelta(hours=5)]
    assert history_days(history, now, 56) == 4
