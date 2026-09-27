"""Tests for the key figures of the simulation."""

from datetime import datetime, timedelta

import pytest

from homeassistant.util import dt as dt_util

from custom_components.slems.simulation import _metrics


@pytest.fixture(autouse=True)
def vienna() -> None:
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Vienna"))


def rows(grid: dict[int, float], soc: dict[int, float]) -> list[dict]:
    day = datetime(2026, 6, 1, tzinfo=dt_util.get_default_time_zone())
    return [
        {
            "start": (day + timedelta(hours=h)).isoformat(),
            "grid_w": grid.get(h),
            "soc_pct": soc.get(h),
        }
        for h in range(24)
    ]


def test_metrics_of_the_projected_hours() -> None:
    day = datetime(2026, 6, 1, tzinfo=dt_util.get_default_time_zone())
    plan = rows({10: 400, 11: -2000, 12: -5000, 20: 1500}, {11: 60, 23: 40})
    # From 10:30 on: half of the 10 o'clock hour counts.
    result = _metrics(plan, day + timedelta(hours=10, minutes=30), cap_limit_w=4000)
    assert result["import_kwh"] == pytest.approx(0.2 + 1.5)
    assert result["export_kwh"] == pytest.approx(2 + 4)
    assert result["curtailed_kwh"] == pytest.approx(1)
    assert result["max_import_w"] == 1500
    assert result["max_export_w"] == 4000
    assert result["soc_end_pct"] == 40
