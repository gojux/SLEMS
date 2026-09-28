"""Tests for the feed-in cap planning."""

from datetime import datetime, timedelta

import pytest

from homeassistant.util import dt as dt_util

from custom_components.slems.allocation import BatteryGroup
from custom_components.slems.feed_in_cap import CapConsumer, CapSettings, auto_buffer, plan_cap

LIMIT = 3000.0


@pytest.fixture(autouse=True)
def vienna() -> None:
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Vienna"))


def midnight(day: int = 0) -> datetime:
    return datetime(2026, 6, 1, tzinfo=dt_util.get_default_time_zone()) + timedelta(days=day)


def pv_hours(powers: dict[tuple[int, int], float]) -> dict[datetime, float]:
    """Hourly PV periods from {(day, hour): W}."""
    return {midnight(day) + timedelta(hours=hour): w for (day, hour), w in powers.items()}


def pv_quarters(day: int, powers: dict[int, list[float]]) -> dict[datetime, float]:
    """15 minute PV periods from {hour: [W, W, W, W]}."""
    result = {}
    for hour, values in powers.items():
        for quarter, w in enumerate(values):
            start = midnight(day) + timedelta(hours=hour, minutes=15 * quarter)
            result[start] = w / 4
    return result


def consumption(load_w: float = 400.0) -> dict[datetime, float]:
    return {midnight(day) + timedelta(hours=h): load_w for day in (0, 1) for h in range(24)}


def battery(soc: float = 50.0, capacity: float = 10000.0) -> BatteryGroup:
    return BatteryGroup(
        soc_pct=soc, capacity_wh=capacity, max_charge_w=5000, max_discharge_w=5000,
        charge_efficiency=1.0,
    )


def settings(**changes) -> CapSettings:
    values = {"limit_w": LIMIT, "buffer_pct": 0.0, "min_buffer_wh": 0.0}
    values.update(changes)
    return CapSettings(**values)


def plan(now, pv, *, bat=None, load=400.0, cap_settings=None, charge=5000.0, **kwargs):
    return plan_cap(
        now, bat or battery(), charge, 5000.0, pv, consumption(load),
        cap_settings or settings(), **kwargs,
    )


def test_short_peaks_are_not_averaged_away() -> None:
    # Hourly mean 3600 W would give only 200 Wh above the limit.
    pv = pv_quarters(1, {12: [2400, 2400, 4800, 4800]})
    result = plan(midnight(0) + timedelta(hours=20), pv)
    assert len(result.blocks) == 1
    assert result.blocks[0].excess_wh == pytest.approx(700)
    assert result.blocks[0].start == midnight(1) + timedelta(hours=12, minutes=30)
    hour = result.hourly[midnight(1) + timedelta(hours=12)]
    assert hour.excess_wh == pytest.approx(700)


def test_irregular_last_period_is_not_a_peak() -> None:
    # Sunset timestamps a minute apart at the end of the forecast.
    pv = pv_hours({(1, 17): 500, (1, 18): 300})
    pv[midnight(1) + timedelta(hours=18, minutes=58)] = 40
    pv[midnight(1) + timedelta(hours=18, minutes=59)] = 60
    result = plan(midnight(0) + timedelta(hours=20), pv)
    assert result.blocks == []


def test_buffer_and_minimum_buffer() -> None:
    pv = pv_hours({(1, 12): 4400})
    result = plan(
        midnight(0) + timedelta(hours=20), pv,
        cap_settings=settings(buffer_pct=20, min_buffer_wh=500),
    )
    assert result.required_space_wh == pytest.approx(1000 * 1.2 + 500)


def test_two_peaks_with_a_cloud_dip() -> None:
    # Two blocks of 1000 Wh; the dip in between drains 800 Wh.
    pv = pv_hours({(1, 10): 4400, (1, 11): 0, (1, 12): 0, (1, 13): 4400})
    result = plan(midnight(0) + timedelta(hours=20), pv)
    assert len(result.blocks) == 2
    assert result.required_space_wh == pytest.approx(1200)
    assert result.space_needed_at(midnight(1) + timedelta(hours=13)) == pytest.approx(1000)


def test_peaks_today_and_tomorrow() -> None:
    pv = pv_hours({(0, 12): 5050, (1, 12): 5050})
    result = plan(midnight(0) + timedelta(hours=8), pv, load=50.0)
    assert len(result.blocks) == 2
    # Tomorrow's 2000 Wh minus 23 h at 50 W are still needed after today's peak.
    assert result.space_needed_at(midnight(0) + timedelta(hours=13)) == pytest.approx(850)
    assert result.required_space_wh == pytest.approx(2850)
    assert [b.start.date() for b in result.day_blocks()] == [midnight(0).date()]


def test_charge_power_too_low() -> None:
    pv = pv_hours({(1, 12): 4400})
    result = plan(midnight(0) + timedelta(hours=20), pv, charge=500.0)
    assert result.blocks[0].absorbed_wh == pytest.approx(500)
    assert result.curtailed_wh == pytest.approx(500)
    assert "charge_power_too_low" in result.problems
    helped = plan(
        midnight(0) + timedelta(hours=20), pv, charge=500.0, normal=[CapConsumer(500.0)]
    )
    assert helped.curtailed_wh == pytest.approx(0)
    assert helped.problems == []


def test_battery_too_small() -> None:
    pv = pv_hours({(1, 12): 15400})
    result = plan(midnight(0) + timedelta(hours=20), pv, charge=20000.0)
    assert result.battery_too_small
    assert "battery_too_small" in result.problems
    assert result.required_space_wh == pytest.approx(10000)


def test_export_as_late_as_possible() -> None:
    # 6000 Wh above the limit at noon; 90 % SoC leaves 1000 Wh free.
    pv = pv_hours({(0, 12): 9400})
    early = plan(midnight(0) + timedelta(hours=6), pv, bat=battery(90), charge=10000.0)
    # The morning consumption drains 2400 Wh until noon.
    assert early.export_needed_wh == pytest.approx(2600)
    assert early.hold_charging
    assert early.export_power_w == 0
    # 70 % of 2900 W per quarter hour, done at 11:00: start at 09:30.
    assert early.export_start == midnight(0) + timedelta(hours=9, minutes=30)
    assert not early.too_late

    later = plan(midnight(0) + timedelta(hours=10), pv, bat=battery(90), charge=10000.0)
    assert later.export_needed_wh == pytest.approx(4200)
    assert later.export_power_w == pytest.approx(2900)
    assert not later.too_late

    too_late = plan(
        midnight(0) + timedelta(hours=10, minutes=45), pv, bat=battery(90), charge=10000.0
    )
    assert too_late.too_late
    assert "too_late" in too_late.problems


def test_no_peak_no_need() -> None:
    pv = pv_hours({(1, 12): 3000})
    result = plan(midnight(0) + timedelta(hours=20), pv)
    assert result.blocks == []
    assert not result.hold_charging
    assert result.export_needed_wh == 0
    assert result.problems == []


def test_pv_factor_raises_the_excess() -> None:
    pv = pv_hours({(1, 12): 4400})
    result = plan(midnight(0) + timedelta(hours=20), pv, cap_settings=settings(pv_factor=1.1))
    assert result.blocks[0].excess_wh == pytest.approx(4840 - 400 - 3000)


def test_auto_buffer() -> None:
    days = {f"2026-05-{d:02d}": {"forecast_wh": 10000, "actual_wh": 9000} for d in range(1, 11)}
    assert auto_buffer(days) == (None, 10)
    for d, actual in zip(range(11, 16), (10500, 11000, 11500, 12000, 13000), strict=True):
        days[f"2026-05-{d:02d}"] = {"forecast_wh": 10000, "actual_wh": actual}
    days["2026-05-16"] = {"forecast_wh": 10000}
    value, count = auto_buffer(days)
    assert count == 15
    # 80 % quantile of +5, +10, +15, +20, +30 %.
    assert value == pytest.approx(0.2)


def test_forecast_fits_but_the_buffer_does_not() -> None:
    # 9 kWh above the limit fit into 10 kWh; with +20 % they do not.
    pv = pv_hours({(1, 11): 6400, (1, 12): 6400, (1, 13): 6400})
    result = plan(midnight(0) + timedelta(hours=20), pv, load=400.0,
                  cap_settings=settings(buffer_pct=20))
    assert not result.battery_too_small
    assert result.buffer_short
    assert "battery_too_small" not in result.problems
    # Without buffer it fits without a note.
    plain = plan(midnight(0) + timedelta(hours=20), pv, load=400.0)
    assert not plain.buffer_short and not plain.battery_too_small


def test_supporting_consumers_run_from_the_start_of_the_peak() -> None:
    # 12 kWh above the limit in one hour, 10 kWh space: 2 kWh do not fit.
    pv = pv_hours({(1, 12): 15400})
    now = midnight(0) + timedelta(hours=20)
    weak = plan(now, pv, charge=20000.0, support=[CapConsumer(1000)])
    assert weak.battery_too_small
    covered = plan(now, pv, charge=20000.0, support=[CapConsumer(3000)])
    assert not covered.battery_too_small
    assert covered.problems == []
    assert covered.early_wh == pytest.approx(2000)
    assert covered.takeover_wh == pytest.approx(2000)
    # Planned in the first steps of the peak, not after the batteries are full.
    first = covered.hourly[midnight(1) + timedelta(hours=12)]
    assert first.absorbed_wh == pytest.approx(10000)


def test_supporting_consumer_limited_by_its_capacity() -> None:
    pv = pv_hours({(1, 12): 15400})
    now = midnight(0) + timedelta(hours=20)
    full = plan(now, pv, charge=20000.0, support=[CapConsumer(3000, capacity_wh=1000)])
    assert full.early_wh == pytest.approx(1000)
    assert full.battery_too_small
    # At full power until the thermostat cycles, then its mean cycling power.
    cycling = CapConsumer(4000, capacity_wh=5000, full_power_wh=1000, cycling_power_w=2000)
    assert cycling.power_after(500) == 4000
    assert cycling.power_after(1500) == 2000
    assert cycling.power_after(5000) == 0


def test_supporting_consumers_now() -> None:
    # The peak starts now and does not fit: the consumer runs right away.
    pv = pv_hours({(0, 12): 15400})
    result = plan(midnight(0) + timedelta(hours=12), pv, charge=20000.0, support=[CapConsumer(3000)])
    assert result.support_w == pytest.approx(3000)
    fits = plan(midnight(0) + timedelta(hours=12), pv_hours({(0, 12): 8000}), charge=20000.0,
                support=[CapConsumer(3000)])
    assert fits.support_w == 0


def test_consumers_cover_a_late_export() -> None:
    pv = pv_hours({(0, 12): 9400})
    now = midnight(0) + timedelta(hours=10, minutes=45)
    covered = plan(now, pv, bat=battery(90), charge=10000.0, support=[CapConsumer(5000)])
    assert not covered.too_late
    assert covered.problems == []
    assert covered.takeover_wh > 0


def test_switch_consumers_only_with_their_full_power() -> None:
    # 12 kWh above the limit in one hour, 2 kWh do not fit.
    pv = pv_hours({(1, 12): 15400})
    now = midnight(0) + timedelta(hours=20)
    # 3 kW on/off: fits into the 12 kW above the limit, runs with its full power.
    fits = plan(now, pv, charge=20000.0, support=[CapConsumer(3000, switch=True)])
    assert not fits.battery_too_small
    # 2.5 kW above the limit are not enough for a 3 kW on/off consumer.
    small = plan(now, pv_hours({(1, 12): 5900}), charge=2000.0,
                 support=[CapConsumer(3000, switch=True)])
    assert small.curtailed_wh == pytest.approx(500)
    assert "charge_power_too_low" in small.problems
    power = plan(now, pv_hours({(1, 12): 5900}), charge=2000.0, support=[CapConsumer(3000)])
    assert power.curtailed_wh == pytest.approx(0)
