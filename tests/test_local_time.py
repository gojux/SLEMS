"""Local periods across the clock changes (Europe/Vienna: 2027-03-28 and 2026-10-25)."""

from datetime import date, datetime, timedelta

from homeassistant.util import dt as dt_util
import pytest

from custom_components.slems.battery_priority import night_energy_wh
from custom_components.slems.consumer_targets import run_load
from custom_components.slems.grid_charge import ChargeBattery, plan_grid_charge
from custom_components.slems.local_time import HOUR, hours_of_day, next_start, real_duration

QUARTER = timedelta(minutes=15)


@pytest.fixture(autouse=True)
def vienna() -> None:
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Vienna"))


def local(*args: int) -> datetime:
    return datetime(*args, tzinfo=dt_util.get_default_time_zone())


def steps(start: datetime, period: timedelta, count: int) -> list[str]:
    result = []
    for _ in range(count):
        result.append(start.strftime("%H:%M%z"))
        start = next_start(start, period)
    return result


def test_next_start_skips_the_hour_the_clock_skips() -> None:
    assert steps(local(2027, 3, 28, 0), HOUR, 4) == ["00:00+0100", "01:00+0100", "03:00+0200", "04:00+0200"]
    assert steps(local(2027, 3, 28, 1, 30), QUARTER, 4) == ["01:30+0100", "01:45+0100", "03:00+0200", "03:15+0200"]


def test_next_start_visits_the_repeated_hour_once() -> None:
    # The local buckets hold both runs of 02:00 in one.
    assert steps(local(2026, 10, 25, 1), HOUR, 3) == ["01:00+0200", "02:00+0200", "03:00+0100"]
    # Starting in the second run it goes on, not back to the first.
    second = dt_util.as_local(datetime(2026, 10, 25, 1, 15, tzinfo=dt_util.UTC))
    assert steps(second, QUARTER, 4) == ["02:15+0100", "02:30+0100", "02:45+0100", "03:00+0100"]


def test_hours_of_day() -> None:
    assert len(hours_of_day(local(2027, 3, 28))) == 23
    assert len(hours_of_day(local(2026, 10, 25))) == 25
    assert len(hours_of_day(local(2026, 11, 3))) == 24
    assert real_duration(local(2027, 3, 28, 1, 30), local(2027, 3, 28, 3)) == timedelta(minutes=30)


def test_grid_charge_plan_goes_on_after_the_skipped_hour() -> None:
    first = datetime(2027, 3, 27, 23, 0, tzinfo=dt_util.UTC)  # 00:00 local
    h = [dt_util.as_local(first + HOUR * i) for i in range(8)]
    prices = dict(zip(h, [10.0] * 5 + [40.0] * 3, strict=True))
    deficits = {hour: 500.0 for hour in h}
    battery = ChargeBattery(
        capacity_wh=5000, stored_wh=500, floor_wh=500, grid_max_wh=4500, max_charge_w=2000,
        max_discharge_w=2000, efficiency=0.95, wear_ct=1.0,
    )
    plan = plan_grid_charge(h[0], battery, deficits, prices, h[-1] + HOUR, 2.0, period=HOUR)
    # Before, the plan ended at 02:00 and never saw the expensive morning.
    assert plan is not None
    assert plan.charge_w and all(hour < h[5] for hour in plan.charge_w)


def _hourly(days: list[date], wh: float, pv_hours: range = range(0)) -> tuple[dict, dict]:
    consumption: dict[datetime, float] = {}
    pv: dict[datetime, float] = {}
    for day in days:
        for start in hours_of_day(local(day.year, day.month, day.day)):
            consumption[start] = wh
            pv[start] = 2000.0 if dt_util.as_local(start).hour in pv_hours else 0.0
    return pv, consumption


@pytest.mark.parametrize(
    ("day", "hours"),
    [
        (date(2027, 3, 27), 17),  # 16:00 CET to 10:00 CEST
        (date(2026, 10, 24), 19),  # 16:00 CEST to 10:00 CET
        (date(2026, 11, 3), 18),
    ],
)
def test_night_energy_counts_the_real_hours(day: date, hours: int) -> None:
    pv, consumption = _hourly([day, day + timedelta(days=1)], 100.0, range(10, 16))
    assert night_energy_wh(day, pv, consumption) == pytest.approx(hours * 100.0)


def test_run_load_across_the_skipped_hour() -> None:
    load = run_load(local(2027, 3, 28, 1, 30), None, energy_wh=2000.0, power_w=1000.0)
    assert load == {
        local(2027, 3, 28, 1): pytest.approx(500.0),
        dt_util.as_local(datetime(2027, 3, 28, 1, tzinfo=dt_util.UTC)): pytest.approx(1000.0),
        dt_util.as_local(datetime(2027, 3, 28, 2, tzinfo=dt_util.UTC)): pytest.approx(500.0),
    }
