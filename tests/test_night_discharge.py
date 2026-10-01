"""Tests for the night discharge planning."""

from datetime import datetime, timedelta

import pytest

from homeassistant.util import dt as dt_util

from custom_components.slems.consumer_targets import SurplusDemand
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


def plan(
    soc: float,
    pv_per_hour: float,
    now: datetime | None = None,
    min_wh: float = 0.0,
    max_target=None,
    full_wh: float | None = None,
    **extra,
):
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
        min_wh=min_wh,
        max_target=max_target,
        full_wh=full_wh,
        **extra,
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


def test_target_from_the_maximum_soc() -> None:
    # Maximum SoC 90 %: 3.8 kWh rechargeable up to 9 kWh, not up to 10 kWh.
    result = plan(soc=80, pv_per_hour=1000, full_wh=9000)
    assert result.target_wh == pytest.approx(5200)


def test_reserve_comes_on_top_of_the_minimum_soc() -> None:
    # 12 % of 10 kWh cannot be used: reserve 2.4 kWh above 1.2 kWh.
    result = plan(soc=80, pv_per_hour=2400, min_wh=1200)
    assert result.target_wh == pytest.approx(3600)
    assert result.power_w == pytest.approx(440)


def test_feed_in_cap_lowers_the_target() -> None:
    # The cap needs 9 kWh free space when PV takes over: at most 1 kWh stored,
    # but never below the minimum SoC.
    result = plan(soc=80, pv_per_hour=2400, min_wh=1200, max_target=lambda _: 1000)
    assert result.target_wh == pytest.approx(1200)
    result = plan(soc=80, pv_per_hour=2400, max_target=lambda _: 2000)
    assert result.target_wh == pytest.approx(2000)


def test_no_discharge_below_target() -> None:
    assert plan(soc=20, pv_per_hour=2400).power_w == 0


def test_not_applicable_while_pv_exceeds_consumption() -> None:
    assert plan(soc=80, pv_per_hour=2400, now=_day().replace(hour=12, minute=30)) is None


def test_half_hour_periods_are_summed() -> None:
    start = _day().replace(hour=10)
    assert hourly({start: 100, start + timedelta(minutes=30): 150}) == {start: 250}


def test_refill_limited_by_charge_power_and_daily_targets() -> None:
    # Surplus 8 h x 1 kWh = 8 kWh, minus 1 kWh buffer: 7 kWh -> target 3 kWh.
    assert plan(80, 1400).target_wh == pytest.approx(3000)
    # The batteries take only 500 W: 4 kWh - 1 kWh -> target 7 kWh.
    assert plan(80, 1400, max_charge_w=500).target_wh == pytest.approx(7000)
    # A daily target takes 3 kWh of the surplus that day: 5 kWh - 1 kWh -> 6 kWh.
    demand = SurplusDemand(3000, 2000, _day(1).replace(hour=8), _day(1).replace(hour=18))
    assert plan(80, 1400, demands=[demand]).target_wh == pytest.approx(6000)
    # One the surplus covers besides the refill changes nothing.
    small = SurplusDemand(500, 2000, _day(1).replace(hour=8), _day(1).replace(hour=18))
    assert plan(80, 1400, max_charge_w=500, demands=[small]).target_wh == pytest.approx(7000)
