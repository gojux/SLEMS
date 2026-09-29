"""Tests for the daily targets of consumers."""

from datetime import datetime, time, timedelta

import pytest

from homeassistant.util import dt as dt_util

from custom_components.slems.const import TargetSource, TargetType
from custom_components.slems.consumer_targets import (
    TargetMode,
    TargetProgress,
    TargetSettings,
    evaluate,
    period_end,
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
    progress.done = True
    assert state(settings, progress, local(12), temperature_c=50).mode is TargetMode.DONE
