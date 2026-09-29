"""Tests for the learned values."""

import pytest

from custom_components.slems.learning import (
    CapacityLearner,
    ConsumerLearner,
    GridTargetLearner,
    auto_timing,
    consumption_underestimate,
    pv_overestimate,
)


def test_pv_overestimate_needs_14_days() -> None:
    days = {f"2026-05-{d:02d}": {"forecast_wh": 10000, "actual_wh": 11000} for d in range(1, 10)}
    assert pv_overestimate(days) == (None, 9)
    for d, actual in zip(range(10, 16), (9500, 9000, 8000, 7000, 6000, 10000), strict=True):
        days[f"2026-05-{d:02d}"] = {"forecast_wh": 10000, "actual_wh": actual}
    share, count = pv_overestimate(days)
    assert count == 15
    # 80 % quantile of 5, 10, 20, 30, 40 % too high.
    assert share == pytest.approx(0.3)


def test_consumption_underestimate() -> None:
    assert consumption_underestimate([(10000, 11000)] * 3) == (None, 3)
    days = [(10000, 9000)] * 5 + [(10000, 10500), (10000, 11000), (10000, 12000)]
    share, count = consumption_underestimate(days)
    assert count == 8
    # 80 % quantile of 5, 10, 20 % too low: the highest of three.
    assert share == pytest.approx(0.2)


def test_capacity_from_a_discharge_leg() -> None:
    learner = CapacityLearner()
    # 5000 Wh battery: 1000 W for 1.5 h is 30 %.
    soc = 90.0
    for step in range(0, 5401, 5):
        learner.update(step, soc, -1000, 5120)
        soc -= 1000 * 5 / 3600 / 5000 * 100
    learner.update(5410, soc, 0, 5120)
    learner.update(6100, soc, 0, 5120)  # rest ends the leg
    assert learner.estimates and learner.estimates[0] == pytest.approx(5000, rel=0.01)
    assert learner.capacity_wh is None  # three legs needed


def test_capacity_jump_discards_the_leg() -> None:
    learner = CapacityLearner()
    soc = 50.0
    for step in range(0, 3600, 5):
        learner.update(step, soc, 1500, 5120)
        soc += 1500 * 5 / 3600 / 5000 * 100
    learner.update(3605, 100.0, 1500, 5120)  # BMS recalibrates to 100 %
    learner.update(4300, 100.0, 0, 5120)
    learner.update(5000, 100.0, 0, 5120)
    assert learner.estimates == []


def test_capacity_median_and_restore() -> None:
    learner = CapacityLearner.from_dict({"estimates": [4900, 5000, 5100]})
    assert learner.capacity_wh == 5000
    assert CapacityLearner.from_dict(learner.as_dict()).estimates == [4900, 5000, 5100]


def test_grid_target_from_import_deviation() -> None:
    learner = GridTargetLearner()
    assert learner.target_w(charging=True) is None
    # Target 100 W export; the grid swings between 200 W export and 150 W import.
    for i in range(400):
        learner.add(True, -200 + (i % 8) * 50, 100)
    # Deviations above the target: 0 … 250 W; 90 % quantile.
    assert learner.target_w(charging=True) == pytest.approx(250)
    assert learner.target_w(charging=False) is None


def test_auto_timing() -> None:
    assert auto_timing(None) is None
    assert auto_timing(1.0) == (0.8, 3)
    assert auto_timing(5.0) == (4.0, 15)


def test_consumer_power_and_thermostat_cycles() -> None:
    learner = ConsumerLearner()
    t = 0
    for _ in range(40):
        learner.update(t, True, 2000 + (t % 3) * 10)
        t += 5
    assert learner.nominal_w == pytest.approx(2010, abs=10)
    for cycle in range(2):
        for _ in range(10):  # 50 s pause of the thermostat
            learner.update(t, True, 0)
            t += 5
        learner.update(t, True, 2000)
        t += 5
    assert learner.thermostat_cycles
    # Commanded off: no pause counted.
    learner.update(t, False, 0)
    assert learner.cycles == 2


def test_morning_gap_and_reserve() -> None:
    from datetime import datetime, timedelta

    from homeassistant.util import dt as dt_util

    from custom_components.slems.learning import MorningGapLearner

    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Vienna"))
    day = datetime(2026, 9, 28, tzinfo=dt_util.get_default_time_zone())
    learner = MorningGapLearner()
    learner.plan(day + timedelta(hours=8), 20000)
    # PV takes over at 9:00 instead of 8:00: 1 h with 500 W deficit.
    t = 0.0
    moment = day + timedelta(hours=8)
    while moment < day + timedelta(hours=10):
        pv = 0 if moment < day + timedelta(hours=9) else 2000
        learner.update(t, moment, pv, 500)
        t += 60
        moment += timedelta(seconds=60)
    # 500 Wh of 20 kWh (integrated from the second sample on).
    assert learner.days["2026-09-28"] == pytest.approx(2.5, abs=0.05)
    assert learner.planned is None

    for d in range(1, 14):
        learner.days[f"2026-09-{d:02d}"] = d * 0.5
    assert learner.reserve_pct(90) == 6.0
    # Above 100 %: the largest gap plus 10 %.
    assert learner.reserve_pct(110) == 7.2
    assert MorningGapLearner.from_dict(learner.as_dict()).days == learner.days


def test_morning_gap_zero_when_pv_is_early() -> None:
    from datetime import datetime, timedelta

    from homeassistant.util import dt as dt_util

    from custom_components.slems.learning import MorningGapLearner

    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Vienna"))
    day = datetime(2026, 9, 28, tzinfo=dt_util.get_default_time_zone())
    learner = MorningGapLearner()
    learner.plan(day + timedelta(hours=8), 20000)
    for minute in range(0, 20):
        learner.update(minute * 60.0, day + timedelta(hours=8, minutes=minute), 3000, 500)
    assert learner.days == {"2026-09-28": 0.0}


def _heat(learner, t, temp, *, minutes, cutoff, wh_per_k=150.0, power=3400.0, full_command=True):
    """Heating run; from ``cutoff`` on the thermostat cycles 60 s off, 60 s on."""
    cycling_since = None
    for _ in range(int(minutes * 60 / 5)):
        if cycling_since is None and temp >= cutoff:
            cycling_since = t
        running = cycling_since is None or (t - cycling_since) % 120 >= 60
        watts = power if running else 0.0
        learner.update(t, True, watts, temp, power, full_command)
        temp += watts * 5 / 3600 / wh_per_k
        t += 5
    learner.update(t, False, 0.0, temp, power)
    return t + 3600, temp


def test_thermal_storage_learns_energy_per_kelvin_and_cycling() -> None:
    from custom_components.slems.learning import ThermalLearner

    learner = ThermalLearner()
    t = 0.0
    for start in (30.0, 35.0, 40.0):
        t, _ = _heat(learner, t, start, minutes=80, cutoff=52.0)
    assert learner.energy_per_k == pytest.approx(150, rel=0.1)
    assert learner.pause_temp == pytest.approx(52, abs=0.3)
    assert learner.cycling_w is not None and learner.cycling_w < 3400
    until_cycling, until_full = learner.capacity(40.0)
    assert until_cycling == pytest.approx(12 * 150, rel=0.1)
    # The full temperature is not learned yet: only the energy until it cycles.
    assert until_full == until_cycling
    assert ThermalLearner.from_dict(learner.as_dict()).wh_per_k == learner.wh_per_k


def test_thermal_storage_full_and_throttled() -> None:
    from custom_components.slems.learning import ThermalLearner

    learner = ThermalLearner()
    t = 0.0
    for _ in range(2):
        # Commanded on, but the thermostat keeps it off: the storage is full.
        for _ in range(int(40 * 60 / 5)):
            learner.update(t, True, 0.0, 58.0, 3400.0)
            t += 5
        learner.update(t, False, 0.0, 58.0, 3400.0)
        t += 3600
    assert learner.full_temp == 58.0
    # Commanded below its full power: the cycling phase is not used.
    throttled = ThermalLearner()
    _heat(throttled, 0.0, 30.0, minutes=80, cutoff=52.0, power=650.0, full_command=False)
    assert throttled.cycling_powers == [] and throttled.full_temps == []


def test_long_pauses_are_no_thermostat_cycles() -> None:
    # A dehumidifier: off for 30 min once the target humidity is reached.
    learner = ConsumerLearner()
    t = 0
    for _ in range(3):
        for _ in range(40):
            learner.update(t, True, 300)
            t += 5
        for _ in range(360):
            learner.update(t, True, 0)
            t += 5
    learner.update(t, True, 300)
    assert learner.cycles == 0
    assert not learner.thermostat_cycles


def test_start_delay_is_no_thermostat_pause() -> None:
    # Switched on, the compressor starts after 2 minutes; twice.
    learner = ConsumerLearner()
    t = 0
    for _ in range(40):
        learner.update(t, True, 300)
        t += 5
    for _ in range(2):
        learner.update(t, False, 0)
        t += 600
        for _ in range(24):  # 2 min start delay
            learner.update(t, True, 0)
            t += 5
        for _ in range(60):
            learner.update(t, True, 300)
            t += 5
    assert learner.cycles == 0
