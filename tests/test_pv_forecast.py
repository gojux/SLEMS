"""Tests for merging solar forecasts of several providers."""

from datetime import date, datetime, timedelta

from homeassistant.util import dt as dt_util

from custom_components.slems.pv_forecast import (
    energy_on_day,
    keyed_by_start,
    mean_power,
    merge_forecasts,
    power_lookup,
)


def test_merge_sums_planes_and_sorts() -> None:
    east = {"2026-06-01T10:00:00+02:00": 400, "2026-06-01T09:00:00+02:00": 300}
    west = {"2026-06-01T10:00:00+02:00": 100, "2026-06-01T16:00:00+02:00": 500}
    merged = merge_forecasts([east, west])
    assert list(merged.values()) == [300, 500, 500]


def test_energy_on_local_day() -> None:
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Vienna"))
    forecast = merge_forecasts(
        [
            {
                # 23:30 UTC on May 31 is already June 1 in Vienna.
                "2026-05-31T23:30:00+00:00": 10,
                "2026-06-01T12:00:00+02:00": 2000,
                "2026-06-02T12:00:00+02:00": 1500,
            }
        ]
    )
    assert energy_on_day(forecast, date(2026, 6, 1)) == 2010
    assert energy_on_day(forecast, date(2026, 6, 2)) == 1500


def test_period_ends_are_rekeyed_to_starts() -> None:
    # Forecast.Solar: sunrise 07:25 (0 Wh), hours, sunset 19:07.
    ends = {
        "2026-09-27T07:25:00+02:00": 0,
        "2026-09-27T08:00:00+02:00": 208,
        "2026-09-27T09:00:00+02:00": 1235,
        "2026-09-27T19:00:00+02:00": 789,
        "2026-09-27T19:07:00+02:00": 5,
    }
    starts = keyed_by_start(ends)
    assert starts == {
        "2026-09-27T06:25:00+02:00": 0,
        "2026-09-27T07:25:00+02:00": 208,
        "2026-09-27T08:00:00+02:00": 1235,
        "2026-09-27T18:00:00+02:00": 789,
        "2026-09-27T19:00:00+02:00": 5,
    }


def test_mean_power_per_half_hour() -> None:
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Vienna"))
    forecast = merge_forecasts(
        [keyed_by_start({"2026-09-27T08:00:00+02:00": 208, "2026-09-27T09:00:00+02:00": 1235,
                          "2026-09-27T07:25:00+02:00": 0})]
    )
    power = power_lookup(forecast)
    start = datetime(2026, 9, 27, 7, tzinfo=dt_util.get_default_time_zone())
    half = timedelta(minutes=30)
    # 208 Wh between 07:25 and 08:00: 356.6 W; 5 of the first 30 minutes.
    assert round(mean_power(power, start, half)) == 59
    assert round(mean_power(power, start + half, half)) == 357
    assert mean_power(power, start + 2 * half, half) == 1235
