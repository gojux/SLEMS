"""Tests for the daily targets of consumers."""

from datetime import datetime, time, timedelta

import pytest

from homeassistant.util import dt as dt_util

from custom_components.slems.const import TargetSensor, TargetSource, TargetType
from custom_components.slems.consumer_targets import (
    TargetMode,
    TargetProgress,
    TargetSettings,
    energy_to_target,
    evaluate,
    forced_load,
    period_end,
    surplus_demand,
)


@pytest.fixture(autouse=True)
def vienna() -> None:
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Vienna"))


def local(hour: int, minute: int = 0, day: int = 1) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=dt_util.get_default_time_zone())


def state(settings, progress, now, **changes):
    values = {
        "power_w": 300.0,
        "temperature_c": None,
        "wh_per_k": None,
        "expected_surplus_wh": 10000.0,
        "battery_need_wh": 0.0,
        "battery_can_supply": True,
    }
    values.update(changes)
    return evaluate(settings, progress, now, **values)


def test_period_from_deadline_to_deadline() -> None:
    assert period_end(local(10), time(22, 0)) == local(22)
    assert period_end(local(23), time(22, 0)) == local(22, day=2)
    # Across midnight: until 06:00 the next morning.
    assert period_end(local(20), time(6, 0)) == local(6, day=2)


def test_counting_and_rollover() -> None:
    settings = TargetSettings(type=TargetType.RUNTIME, hours=1.0)
    progress = TargetProgress()
    t = 0.0
    moment = local(20)
    # 1.5 h: first hour running at 300 W, then off.
    for step in range(0, 5400, 60):
        power = 300.0 if step < 3600 else 0.0
        assert progress.update(t, moment, settings, power, commanded_on=step < 3600) is None
        t += 60
        moment += timedelta(seconds=60)
    assert progress.runtime_s == pytest.approx(3600)
    assert progress.enabled_s == pytest.approx(3600)
    assert progress.energy_wh == pytest.approx(300)
    # The deadline passes: the period ends met and counting starts again.
    moment = local(22, 1)
    assert progress.update(t, moment, settings, 0.0, False) == "met"
    assert progress.runtime_s == 0 and progress.end == local(22, day=2)
    # Stored and restored.
    assert TargetProgress.from_dict(progress.as_dict()).end == progress.end


def test_latest_start_and_sources() -> None:
    settings = TargetSettings(type=TargetType.RUNTIME, hours=2.0, deadline=time(22, 0))
    progress = TargetProgress(end=local(22))
    # 2 h missing: latest start 22:00 − 2.4 h − 10 min = 19:26.
    early = state(settings, progress, local(19))
    assert early.mode is TargetMode.SURPLUS
    assert early.latest_start == local(19, 26)
    # Only surplus: never forced.
    assert state(settings, progress, local(20)).mode is TargetMode.SURPLUS
    settings.source = TargetSource.BATTERY
    assert state(settings, progress, local(20)).mode is TargetMode.FORCED
    # Batteries empty: not forced with "battery", forced with "grid".
    assert state(settings, progress, local(20), battery_can_supply=False).mode is TargetMode.SURPLUS
    settings.source = TargetSource.GRID
    assert state(settings, progress, local(20), battery_can_supply=False).mode is TargetMode.FORCED
    # Met: off until the next period.
    progress.runtime_s = 7200
    assert state(settings, progress, local(20)).mode is TargetMode.DONE


def test_priority_only_when_short() -> None:
    settings = TargetSettings(type=TargetType.ENERGY, energy_kwh=3.0, priority=True)
    progress = TargetProgress(end=local(22))
    # 3 kWh missing plus 5 kWh for the batteries.
    plenty = state(settings, progress, local(10), expected_surplus_wh=12000, battery_need_wh=5000)
    assert plenty.mode is TargetMode.SURPLUS
    short = state(settings, progress, local(10), expected_surplus_wh=6000, battery_need_wh=5000)
    assert short.mode is TargetMode.BOOST
    settings.priority = False
    assert state(settings, progress, local(10), expected_surplus_wh=6000, battery_need_wh=5000).mode is TargetMode.SURPLUS


def test_temperature_target() -> None:
    settings = TargetSettings(
        type=TargetType.TEMPERATURE, min_temp_c=40, target_temp_c=55,
        deadline=time(18, 0), source=TargetSource.GRID,
    )
    progress = TargetProgress(end=local(18))
    # Below the minimum: priority over the batteries, forced from the latest start.
    # 10 K × 150 Wh/K at 3000 W = 30 min × 1.2 = 36 min, minus 10 min: 17:14.
    below = state(settings, progress, local(12), temperature_c=30, wh_per_k=150, power_w=3000)
    assert below.mode is TargetMode.BOOST
    assert below.latest_start == local(17, 14)
    assert state(settings, progress, local(17, 20), temperature_c=30, wh_per_k=150, power_w=3000).mode is TargetMode.FORCED
    # Without a learned energy per kelvin: 2 h before the deadline.
    assert state(settings, progress, local(12), temperature_c=30).latest_start == local(15, 50)
    # Between minimum and target: surplus only; at the target: done.
    assert state(settings, progress, local(12), temperature_c=45).mode is TargetMode.SURPLUS
    assert state(settings, progress, local(12), temperature_c=55).mode is TargetMode.DONE
    progress.mark_done(55)
    assert state(settings, progress, local(12), temperature_c=50).mode is TargetMode.DONE
    # A higher target temperature set later in the period: not reached yet.
    settings.target_temp_c = 60
    assert state(settings, progress, local(12), temperature_c=50).mode is TargetMode.SURPLUS
    # Lower again: still reached.
    settings.target_temp_c = 52
    assert state(settings, progress, local(12), temperature_c=50).mode is TargetMode.DONE


def test_below_the_minimum_the_target_is_open_again() -> None:
    settings = TargetSettings(type=TargetType.TEMPERATURE, min_temp_c=40, target_temp_c=55)
    progress = TargetProgress(end=local(18))
    progress.track_temperature(settings, 55)
    assert progress.done and progress.min_reached
    # Cooling a little: still reached, nothing needed.
    progress.track_temperature(settings, 50)
    assert state(settings, progress, local(12), temperature_c=50).mode is TargetMode.DONE
    assert energy_to_target(settings, progress, power_w=3000, temperature_c=50, wh_per_k=200) == 0
    # Hot water drawn: below the minimum it heats with priority, and the target is open again.
    below = state(settings, progress, local(12), temperature_c=28, wh_per_k=200, power_w=3000)
    assert below.mode is TargetMode.BOOST
    progress.track_temperature(settings, 28)
    assert not progress.done and progress.min_reached
    assert energy_to_target(settings, progress, power_w=3000, temperature_c=28, wh_per_k=200) == 5400
    # Above the minimum again: the surplus fills it up to the target.
    assert state(settings, progress, local(12), temperature_c=41).mode is TargetMode.SURPLUS


def test_another_sensor_starts_open() -> None:
    settings = TargetSettings(type=TargetType.TEMPERATURE, min_temp_c=40, target_temp_c=55)
    progress = TargetProgress(end=local(18))
    progress.use_sensor(settings.sensor)
    progress.mark_done(55)
    progress.min_reached = True
    assert progress.done_for(settings)
    other = TargetSettings(
        type=TargetType.TEMPERATURE, min_temp_c=40, target_temp_c=55, sensor=TargetSensor.SECOND
    )
    # Not reached for the new choice right away, and the flags are reset with the next poll.
    assert not progress.done_for(other)
    progress.use_sensor(other.sensor)
    assert not progress.done and not progress.min_reached
    assert TargetProgress.from_dict(progress.as_dict()).temperature_sensor == "second"


def test_temperature_target_waits_after_the_deadline_until_midnight() -> None:
    settings = TargetSettings(
        type=TargetType.TEMPERATURE, min_temp_c=40, target_temp_c=55,
        deadline=time(16, 0), source=TargetSource.GRID,
    )
    # After 16:00 the period runs until 16:00 tomorrow, but starts at midnight.
    progress = TargetProgress(end=local(16, day=2))
    evening = state(settings, progress, local(17), temperature_c=30)
    assert evening.mode is TargetMode.WAITING
    assert state(settings, progress, local(1, day=2), temperature_c=30).mode is TargetMode.BOOST
    assert state(settings, progress, local(1, day=2), temperature_c=45).mode is TargetMode.SURPLUS
    # Deadline at midnight: the whole day.
    settings.deadline = time(0, 0)
    whole_day = TargetProgress(end=local(0, day=2))
    assert state(settings, whole_day, local(23), temperature_c=45).mode is TargetMode.SURPLUS



def test_forced_load_for_the_planning() -> None:
    settings = TargetSettings(type=TargetType.RUNTIME, hours=2.0, source=TargetSource.BATTERY)
    progress = TargetProgress(end=local(22))
    now = local(12)
    target = state(settings, progress, now)
    # 2 h at 300 W from the latest start 19:26 until 22:00.
    load = forced_load(settings, target, now, power_w=300, wh_per_k=None)
    assert load[local(19)] == pytest.approx(300 * 34 / 60)
    assert load[local(20)] == pytest.approx(300)
    assert sum(load.values()) == pytest.approx(600)
    # Only surplus: nothing is planned.
    settings.source = TargetSource.SURPLUS
    assert forced_load(settings, state(settings, progress, now), now, power_w=300, wh_per_k=None) == {}
    # Temperature: missing kelvin × Wh/K, from the latest start.
    temperature = TargetSettings(type=TargetType.TEMPERATURE, min_temp_c=40, target_temp_c=55,
                                 deadline=time(18, 0), source=TargetSource.GRID)
    progress = TargetProgress(end=local(18))
    below = state(temperature, progress, now, temperature_c=30, wh_per_k=150, power_w=3000)
    load = forced_load(temperature, below, now, power_w=3000, wh_per_k=150)
    assert sum(load.values()) == pytest.approx(1500)
    assert min(load) == local(17)


def test_temperature_of_the_chosen_sensor() -> None:
    from custom_components.slems.const import TargetSensor
    from custom_components.slems.consumer_targets import target_temperature

    settings = TargetSettings(type=TargetType.TEMPERATURE)
    readings = (60.0, 42.0)  # at the heating rod, higher up
    assert target_temperature(settings, 51.0, readings) == 51.0
    settings.sensor = TargetSensor.FIRST
    assert target_temperature(settings, 51.0, readings) == 60.0
    settings.sensor = TargetSensor.SECOND
    assert target_temperature(settings, 51.0, readings) == 42.0
    # Only one sensor configured: the mean is that sensor.
    assert target_temperature(settings, 55.0, (55.0,)) == 55.0



def test_earliest_start() -> None:
    settings = TargetSettings(
        type=TargetType.ENABLED, hours=2.0, deadline=time(22, 0), source=TargetSource.GRID,
        earliest_enabled=True, earliest=time(10, 0),
    )
    progress = TargetProgress(end=local(22))
    # Before 10:00: off, also with surplus.
    assert state(settings, progress, local(9)).mode is TargetMode.WAITING
    assert state(settings, progress, local(11)).mode is TargetMode.SURPLUS
    # Deadline across midnight: the window starts the evening before.
    overnight = TargetSettings(
        type=TargetType.RUNTIME, hours=1.0, deadline=time(6, 0),
        earliest_enabled=True, earliest=time(20, 0),
    )
    night = TargetProgress(end=local(6, day=2))
    assert state(overnight, night, local(19)).mode is TargetMode.WAITING
    assert state(overnight, night, local(23)).mode is TargetMode.SURPLUS
    # The latest start is never before the earliest one.
    short = TargetSettings(
        type=TargetType.RUNTIME, hours=10.0, deadline=time(22, 0), source=TargetSource.GRID,
        earliest_enabled=True, earliest=time(16, 0),
    )
    late = state(short, TargetProgress(end=local(22)), local(16, 5))
    assert late.latest_start == local(16) and late.mode is TargetMode.FORCED
    # Switched off: no restriction.
    settings.earliest_enabled = False
    assert state(settings, progress, local(9)).mode is TargetMode.SURPLUS


def test_energy_to_target() -> None:
    runtime = TargetSettings(type=TargetType.RUNTIME, hours=2.0)
    progress = TargetProgress(end=local(22), runtime_s=1800)
    assert energy_to_target(runtime, progress, power_w=2000, temperature_c=None, wh_per_k=None) == 3000
    energy = TargetSettings(type=TargetType.ENERGY, energy_kwh=3.0)
    assert energy_to_target(
        energy, TargetProgress(end=local(22), energy_wh=1000), power_w=0, temperature_c=None, wh_per_k=None
    ) == 2000
    temperature = TargetSettings(type=TargetType.TEMPERATURE, min_temp_c=40, target_temp_c=55)
    progress = TargetProgress(end=local(18))
    # Up to the target temperature with the learned energy per kelvin.
    assert energy_to_target(temperature, progress, power_w=2000, temperature_c=45, wh_per_k=200) == 2000
    # Not known before the energy per kelvin is learned, 0 once reached.
    assert energy_to_target(temperature, progress, power_w=2000, temperature_c=45, wh_per_k=None) is None
    done = TargetProgress(end=local(18), done=True)
    assert energy_to_target(temperature, done, power_w=2000, temperature_c=45, wh_per_k=None) == 0


def test_surplus_demand_is_what_the_forced_run_leaves() -> None:
    settings = TargetSettings(type=TargetType.RUNTIME, hours=2.0)
    progress = TargetProgress(end=local(22))
    now = local(9)
    current = state(settings, progress, now, power_w=2000)
    demand = surplus_demand(settings, current, now, energy_wh=4000, forced_wh=0, power_w=2000)
    assert (demand.energy_wh, demand.start, demand.end) == (4000, now, local(22))
    # A forced run covering everything leaves nothing for the surplus.
    assert surplus_demand(settings, current, now, energy_wh=4000, forced_wh=4000, power_w=2000) is None
    earliest = TargetSettings(type=TargetType.RUNTIME, hours=2.0, earliest_enabled=True, earliest=time(11, 0))
    demand = surplus_demand(
        earliest, state(earliest, progress, now, power_w=2000), now, energy_wh=4000, forced_wh=0, power_w=2000
    )
    assert demand.start == local(11)


def test_missed_period_remembers_whether_slems_could_control() -> None:
    settings = TargetSettings(type=TargetType.RUNTIME, hours=1.0)
    progress = TargetProgress()
    # Control switched off for a while in the period: missed, but not controlled.
    progress.update(0.0, local(20), settings, 0.0, False)
    progress.update(60.0, local(20, 1), settings, 0.0, False, controlled=False)
    progress.update(120.0, local(20, 2), settings, 0.0, False)
    assert progress.update(180.0, local(22, 1), settings, 0.0, False) == "missed"
    assert progress.last_controlled is False
    # The next period under control all the time: a missed target counts.
    assert progress.update(240.0, local(22, 1, day=2), settings, 0.0, False) == "missed"
    assert progress.last_controlled is True
    restored = TargetProgress.from_dict(progress.as_dict())
    assert restored.last_controlled is True and restored.uncontrolled is False


def test_blocked_time_of_the_period() -> None:
    settings = TargetSettings(type=TargetType.RUNTIME, hours=1.0)
    progress = TargetProgress()
    t, moment = 0.0, local(20)
    # 30 minutes blocked, then free.
    for step in range(0, 3600, 60):
        progress.update(t, moment, settings, 0.0, False, blocked=step < 1800)
        t += 60
        moment += timedelta(seconds=60)
    assert progress.blocked_s == pytest.approx(1800)
    assert progress.update(t, local(22, 1), settings, 0.0, False) == "missed"
    assert progress.last_blocked_s == pytest.approx(1800) and progress.blocked_s == 0


def test_declined_power_is_remembered_for_the_period() -> None:
    settings = TargetSettings(type=TargetType.RUNTIME, hours=1.0)
    progress = TargetProgress()
    progress.update(0.0, local(20), settings, 0.0, True)
    progress.update(60.0, local(20, 1), settings, 0.0, True, declined=True)
    assert progress.update(120.0, local(22, 1), settings, 0.0, False) == "missed"
    assert progress.last_declined is True and progress.declined is False
    assert progress.update(180.0, local(22, 1, day=2), settings, 0.0, False) == "missed"
    assert progress.last_declined is False


def _no_power_day(watch, day: int, commanded_minutes: int, power: float = 0.0) -> None:
    """One day: switched on for ``commanded_minutes`` from 10:00, polled every minute."""
    base = (day - 1) * 86400.0
    for minute in range(0, 120):
        moment = local(10, day=day) + timedelta(minutes=minute)
        watch.update(base + 36000 + minute * 60, moment, minute < commanded_minutes, power, 1800)


def test_no_power_for_three_days_although_switched_on() -> None:
    from custom_components.slems.consumer_targets import NoPowerWatch

    watch = NoPowerWatch()
    for day in (1, 2, 3):
        _no_power_day(watch, day, 60)
    assert not watch.active  # day 3 is not over yet
    # A day it was hardly switched on does not count either way.
    _no_power_day(watch, 4, 10)
    assert watch.days == 3 and watch.active and watch.since == local(0, day=1).date()
    # Drawing power again ends it at once.
    _no_power_day(watch, 5, 60, power=500.0)
    assert not watch.active and watch.days == 0
    assert NoPowerWatch.from_dict(watch.as_dict()).days == 0


def test_no_power_threshold_follows_a_short_target() -> None:
    from custom_components.slems.consumer_targets import no_power_threshold_s

    assert no_power_threshold_s(TargetSettings(type=TargetType.RUNTIME, hours=0.25), 500) == 900
    assert no_power_threshold_s(TargetSettings(type=TargetType.RUNTIME, hours=2), 500) == 1800
    assert no_power_threshold_s(TargetSettings(type=TargetType.ENERGY, energy_kwh=0.1), 1000) == 360


def test_target_window_and_fit() -> None:
    from custom_components.slems.consumer_targets import no_power_threshold_s, target_fits

    # Enabled time 3 h, only from 14:00 to 16:00: does not fit.
    settings = TargetSettings(
        type=TargetType.ENABLED, hours=3.0, deadline=time(16, 0), earliest_enabled=True, earliest=time(14, 0)
    )
    end = local(16)
    assert not target_fits(settings, end, 500)
    assert target_fits(TargetSettings(type=TargetType.ENABLED, hours=1.5, deadline=time(16, 0),
                                      earliest_enabled=True, earliest=time(14, 0)), end, 500)
    # A 20 minute window caps the time that makes a no-power day count.
    short = TargetSettings(type=TargetType.ENABLED, hours=2.0, deadline=time(16, 0),
                           earliest_enabled=True, earliest=time(15, 40))
    assert no_power_threshold_s(short, 500, end) == pytest.approx(1200)
    # Energy: 3 kWh at 1 kW need 3 h.
    energy = TargetSettings(type=TargetType.ENERGY, energy_kwh=3.0, deadline=time(16, 0),
                            earliest_enabled=True, earliest=time(14, 0))
    assert not target_fits(energy, end, 1000) and target_fits(energy, end, 2000)


def test_daily_energy_estimate_from_the_last_days() -> None:
    from custom_components.slems.consumer_targets import daily_energy_estimate

    today = local(12, day=8).date()
    hourly = {}
    # Days 1–7: 3 kWh on days 5 and 6 (in two hours each), nothing else; day 8 (today) not counted.
    for day, wh in ((5, 1500.0), (6, 1500.0), (8, 9999.0)):
        for hour in (10, 11):
            hourly[local(hour, day=day)] = wh
    assert daily_energy_estimate(hourly, today) == pytest.approx(3000)
    # One day with consumption is not enough.
    assert daily_energy_estimate({local(10, day=5): 2000.0}, today) is None
