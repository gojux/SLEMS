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


def test_costs_of_the_planned_grid_energy() -> None:
    day = datetime(2026, 6, 1, tzinfo=dt_util.get_default_time_zone())
    plan = rows({10: 400, 11: -2000, 20: 1500}, {})
    hours = {day + timedelta(hours=h): h for h in range(24)}
    import_prices = {hour: 30.0 for hour in hours}
    credits = {hour: 10.0 for hour in hours}
    result = _metrics(plan, day + timedelta(hours=10, minutes=30), None, (import_prices, credits))
    # 0.2 + 1.5 kWh at 30 ct, 2 kWh at 10 ct.
    assert result["import_cost"] == pytest.approx(0.51)
    assert result["export_credit"] == pytest.approx(0.2)
    # A missing price where energy flows: unknown.
    import_prices[day + timedelta(hours=20)] = None
    result = _metrics(plan, day, None, (import_prices, credits))
    assert result["import_cost"] is None and result["export_credit"] == pytest.approx(0.2)
    assert "import_cost" not in _metrics(plan, day, None)
