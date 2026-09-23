"""Tests for the night discharge planning."""

from datetime import datetime, timedelta

import pytest

from homeassistant.util import dt as dt_util

from custom_components.slems.night_discharge import plan_night_discharge
from custom_components.slems.pv_forecast import hourly


@pytest.fixture(autouse=True)
def vienna() -> None:
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Vienna"))


def _day(offset: int = 0) -> datetime:
    return datetime(2026, 6, 1, tzinfo=dt_util.get_default_time_zone()) + timedelta(days=offset)


def _forecasts(pv_per_hour: float) -> tuple[dict, dict]:
    """400 Wh consumption every hour; PV 08:00-16:00 of both days."""
    consumption = {}
    pv = {}
    for day in (0, 1):
        for hour in range(24):
            start = _day(day).replace(hour=hour)
            consumption[start] = 400
            pv[start] = pv_per_hour if 8 <= hour < 16 else 0
    return pv, consumption


def plan(soc: float, pv_per_hour: float, now: datetime | None = None):
    pv, consumption = _forecasts(pv_per_hour)
    return plan_night_discharge(
        now or _day().replace(hour=22),
        soc_pct=soc,
        capacity_wh=10000,
        charge_efficiency=1.0,
        discharge_efficiency=1.0,
        pv_forecast=pv,
        consumption_forecast=consumption,
        reserve_pct_of_consumption=25,
        buffer_wh=1000,
    )


def test_discharge_to_reserve_when_pv_refills() -> None:
    # Tomorrow: 9.6 kWh consumption -> reserve 2.4 kWh. Surplus 8 h x 2 kWh = 16 kWh.
    result = plan(soc=80, pv_per_hour=2400)
    assert result.until == _day(1).replace(hour=8)
    assert result.target_wh == pytest.approx(2400)
    # (8000 - 2400) Wh over 10 h
    assert result.power_w == pytest.approx(560)


def test_higher_reserve_when_pv_is_not_enough() -> None:
    # Surplus 8 h x 600 Wh = 4.8 kWh, minus 1 kWh buffer -> 3.8 kWh rechargeable.
    result = plan(soc=80, pv_per_hour=1000)
    assert result.target_wh == pytest.approx(6200)
    assert result.power_w == pytest.approx(180)


def test_no_discharge_below_target() -> None:
    assert plan(soc=20, pv_per_hour=2400).power_w == 0


def test_not_applicable_while_pv_exceeds_consumption() -> None:
    assert plan(soc=80, pv_per_hour=2400, now=_day().replace(hour=12, minute=30)) is None


def test_half_hour_periods_are_summed() -> None:
    start = _day().replace(hour=10)
    assert hourly({start: 100, start + timedelta(minutes=30): 150}) == {start: 250}
