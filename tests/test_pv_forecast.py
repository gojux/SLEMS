"""Tests for merging solar forecasts of several providers."""

from datetime import date

from homeassistant.util import dt as dt_util

from custom_components.slems.pv_forecast import energy_on_day, merge_forecasts


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
