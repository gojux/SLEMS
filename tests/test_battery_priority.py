"""Tests for the automatic battery priority."""

from datetime import datetime, timedelta

import pytest

from homeassistant.util import dt as dt_util

from custom_components.slems.allocation import BatteryGroup
from custom_components.slems.battery_priority import (
    BatteryPriorityDay,
    night_energy_wh,
    priority_soc_pct,
    takeover,
)
from custom_components.slems.battery_support import BatterySupport
from custom_components.slems.const import ConsumerType, ControlMode
from custom_components.slems.consumers import ConsumerConfig
from custom_components.slems.coordinator import ControlSettings, SlemsCoordinator
from custom_components.slems.forecast import ConsumptionForecast


@pytest.fixture(autouse=True)
def vienna() -> None:
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Vienna"))


def at(hour: int, day: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, 6, 1, tzinfo=dt_util.get_default_time_zone()) + timedelta(
        days=day, hours=hour, minutes=minute
    )


def forecasts(days: tuple[int, ...] = (0, 1), pv_days: tuple[int, ...] | None = None) -> tuple[dict, dict]:
    """400 Wh consumption every hour; PV above it 08:00-17:00."""
    pv, consumption = {}, {}
    for day in days:
        for hour in range(24):
            consumption[at(hour, day)] = 400.0
    for day in days if pv_days is None else pv_days:
        for hour in range(24):
            pv[at(hour, day)] = 1500.0 if 8 <= hour < 17 else 0.0
    return pv, consumption


def test_night_from_the_end_of_the_surplus_until_the_takeover_tomorrow() -> None:
    pv, consumption = forecasts()
    assert takeover(at(0).date(), pv, consumption) == at(8)
    # 17:00-08:00: 15 h × 400 Wh.
    assert night_energy_wh(at(0).date(), pv, consumption) == pytest.approx(6000)


def test_without_tomorrows_forecasts_the_night_ends_at_todays_takeover_time() -> None:
    pv, consumption = forecasts(days=(0,))
    for hour in range(8):
        consumption[at(hour)] = 300.0
    # 17:00-24:00 today, then 00:00-08:00 tomorrow from today's hours.
    assert night_energy_wh(at(0).date(), pv, consumption) == pytest.approx(7 * 400 + 8 * 300)


def test_a_later_takeover_tomorrow_makes_the_night_longer() -> None:
    pv, consumption = forecasts()
    pv[at(8, 1)] = 0.0
    pv[at(9, 1)] = 0.0
    assert night_energy_wh(at(0).date(), pv, consumption) == pytest.approx(17 * 400)


def test_no_night_without_a_surplus_on_the_day() -> None:
    pv, consumption = forecasts(pv_days=(1,))
    assert night_energy_wh(at(0).date(), pv, consumption) is None


def test_threshold_between_its_bounds() -> None:
    # 10 % + 6 kWh ÷ 0.95 of 10 kWh.
    assert priority_soc_pct(6000, 10, 10000, 0.95) == pytest.approx(10 + 6000 / 0.95 / 10000 * 100)
    # At least 10 % above the minimum SoC, at most 90 %.
    assert priority_soc_pct(100, 10, 10000, 0.95) == 20
    assert priority_soc_pct(20000, 10, 10000, 0.95) == 90
    assert priority_soc_pct(100, 85, 10000, 0.95) == 90
    assert priority_soc_pct(6000, 10, 0, 0.95) == 20


def test_computed_once_at_the_takeover_and_kept_over_the_day() -> None:
    pv, consumption = forecasts(days=(0, 1, 2))
    day = BatteryPriorityDay()
    # Nothing kept yet: computed at once, again at the takeover.
    day.update(at(3), pv, consumption)
    assert day.night_wh == pytest.approx(6000) and day.day is None
    assert not day.due(at(7, minute=59), pv, consumption)
    assert day.due(at(8), pv, consumption)
    consumption[at(20)] = 1400.0
    day.update(at(8, minute=1), pv, consumption)
    assert day.night_wh == pytest.approx(7000) and day.day == at(0).date()
    # Kept over the day, whatever the forecast does.
    consumption[at(21)] = 1400.0
    assert not day.due(at(12), pv, consumption)
    day.update(at(12), pv, consumption)
    assert day.night_wh == pytest.approx(7000)
    # The next takeover computes the next night.
    day.update(at(8, day=1), pv, consumption)
    assert day.day == at(0, 1).date() and day.night_wh == pytest.approx(6000)


def test_a_day_without_takeover_keeps_the_last_value() -> None:
    pv, consumption = forecasts(days=(0, 1), pv_days=(0,))
    day = BatteryPriorityDay(at(0).date(), 6000.0)
    assert not day.due(at(14, day=1), pv, consumption)
    day.update(at(14, day=1), pv, consumption)
    assert day.day == at(0).date() and day.night_wh == 6000.0


def test_restart_after_the_takeover_computes_at_once() -> None:
    pv, consumption = forecasts(days=(0, 1, 2))
    day = BatteryPriorityDay.from_dict(BatteryPriorityDay(at(0).date(), 5000.0).as_dict())
    assert day == BatteryPriorityDay(at(0).date(), 5000.0)
    day.update(at(14, day=1), pv, consumption)
    assert day.day == at(0, 1).date() and day.night_wh == pytest.approx(6000)


def test_restore_without_or_with_broken_data() -> None:
    assert BatteryPriorityDay.from_dict(None) == BatteryPriorityDay()
    assert BatteryPriorityDay.from_dict({"day": "x", "night_wh": 1}) == BatteryPriorityDay()


def _consumer(subentry_id: str, consumer_type: ConsumerType = ConsumerType.OTHER) -> ConsumerConfig:
    return ConsumerConfig(
        subentry_id=subentry_id,
        name=subentry_id,
        consumer_type=consumer_type,
        power_entity_id=f"sensor.{subentry_id}",
        energy_entity_id=None,
        included_in_meter=True,
        control_mode=ControlMode.SWITCH,
        control_entity_id=f"switch.{subentry_id}",
        nominal_power_w=2000,
        min_power_w=None,
        max_power_w=None,
        block_entity_id=None,
        priority=5,
    )


def coordinator(consumers: list[ConsumerConfig], support: dict[str, BatterySupport]) -> SlemsCoordinator:
    c = SlemsCoordinator.__new__(SlemsCoordinator)
    c.consumers = consumers
    c.battery_support = support
    c.target_load = {}
    c.target_load_grid = {}
    c.battery_priority_day = BatteryPriorityDay()
    return c


def test_night_consumption_without_consumers_the_batteries_do_not_cover() -> None:
    c = coordinator(
        [_consumer("always"), _consumer("never"), _consumer("auto")],
        {"always": BatterySupport.ALWAYS, "never": BatterySupport.NEVER, "auto": BatterySupport.AUTO},
    )
    hour = at(22)
    # Forced runs of the daily targets; those from the grid per consumer.
    c.target_load = {hour: 3000.0}
    c.target_load_grid = {"always": {hour: 1000.0}, "never": {hour: 1000.0}, "auto": {hour: 1000.0}}
    forecast = ConsumptionForecast(total={hour: 500.0}, heat_pump={hour: 200.0})
    # 500 Wh forecast + 3000 Wh runs − the runs on never and automatic; no heat pump.
    assert c._night_consumption(forecast)[hour] == pytest.approx(1500)


def test_night_consumption_without_the_heat_pump_only_if_none_has_always() -> None:
    hour = at(22)
    forecast = ConsumptionForecast(total={hour: 500.0}, heat_pump={hour: 200.0})
    pumps = [_consumer("hp1", ConsumerType.HEAT_PUMP), _consumer("hp2", ConsumerType.HEAT_PUMP)]
    without = coordinator(pumps, {"hp1": BatterySupport.NEVER, "hp2": BatterySupport.AUTO})
    assert without._night_consumption(forecast)[hour] == pytest.approx(300)
    mixed = coordinator(pumps, {"hp1": BatterySupport.NEVER})
    assert mixed._night_consumption(forecast)[hour] == pytest.approx(500)


def test_threshold_in_effect() -> None:
    c = coordinator([], {})
    group = BatteryGroup(
        soc_pct=50, capacity_wh=10000, max_charge_w=5000, max_discharge_w=5000,
        charge_efficiency=1.0, min_soc_pct=10,
    )
    settings = ControlSettings(battery_priority_soc_pct=30.0)
    # Not computed yet: the set value.
    assert c.battery_priority_soc_pct(settings, group) == 30.0
    c.battery_priority_day = BatteryPriorityDay(at(0).date(), 4000.0)
    assert c.battery_priority_soc_pct(settings, group) == pytest.approx(50)
    # The simulation: other consumption and batteries.
    assert c.battery_priority_soc_pct(settings, group, 1.5) == pytest.approx(70)
    assert c.battery_priority_soc_pct(settings, None) == 30.0
    # Switched off: the set value.
    off = ControlSettings(battery_priority_soc_pct=30.0, battery_priority_auto=False)
    assert c.battery_priority_soc_pct(off, group) == 30.0
