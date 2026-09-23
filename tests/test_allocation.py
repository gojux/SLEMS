"""Tests for the distribution of power between batteries and consumers."""

from datetime import datetime, timedelta

import pytest

from homeassistant.util import dt as dt_util

from custom_components.slems.allocation import (
    AllocationSettings,
    BatteryGroup,
    ConsumerRequest,
    Strategy,
    allocate,
    expected_surplus_wh,
    remaining_pv_wh,
)
from custom_components.slems.const import ControlMode

SETTINGS = AllocationSettings(
    battery_priority_soc_pct=30,
    battery_share_when_secured_pct=75,
    charge_secured_margin_pct=120,
    peak_shaving=False,
    peak_shaving_grid_limit_w=3000,
    peak_shaving_soc_threshold_pct=20,
)

ROD = ConsumerRequest(
    subentry_id="rod", priority=1, control_mode=ControlMode.POWER,
    min_power_w=300, max_power_w=3000,
)
PUMP = ConsumerRequest(
    subentry_id="pump", priority=2, control_mode=ControlMode.SWITCH, nominal_power_w=500,
)


def battery(soc: float) -> BatteryGroup:
    return BatteryGroup(
        soc_pct=soc, capacity_wh=10000, max_charge_w=5000, max_discharge_w=5000,
        charge_efficiency=0.95,
    )


def test_battery_has_priority_below_soc_threshold() -> None:
    result = allocate(4000, battery(20), [ROD], SETTINGS, expected_surplus_wh=50000)
    assert result.strategy is Strategy.BATTERY_PRIORITY
    assert result.battery_power_w == 4000
    assert result.consumer_power_w["rod"] == 0


def test_battery_priority_when_forecast_is_not_enough() -> None:
    # 60 % missing of 10 kWh / 0.95 = 6.3 kWh, with margin 7.6 kWh needed.
    result = allocate(4000, battery(40), [ROD], SETTINGS, expected_surplus_wh=7000)
    assert not result.charge_secured
    assert result.battery_power_w == 4000


def test_unknown_forecast_is_not_secured() -> None:
    result = allocate(4000, battery(40), [ROD], SETTINGS, expected_surplus_wh=None)
    assert result.strategy is Strategy.BATTERY_PRIORITY


def test_split_when_charge_is_secured() -> None:
    result = allocate(4000, battery(40), [ROD, PUMP], SETTINGS, expected_surplus_wh=20000)
    assert result.strategy is Strategy.SHARED
    assert result.consumer_power_w == {"rod": 1000, "pump": 0}
    assert result.battery_power_w == 3000


def test_battery_limit_gives_rest_to_consumers() -> None:
    result = allocate(8000, battery(20), [ROD, PUMP], SETTINGS, expected_surplus_wh=None)
    assert result.battery_power_w == 5000
    assert result.consumer_power_w == {"rod": 3000, "pump": 0}


def test_consumer_leftover_goes_back_to_battery() -> None:
    # Share for consumers is 1000 W, but the only consumer needs 1500 W to start.
    big_pump = ConsumerRequest(
        subentry_id="pump", priority=1, control_mode=ControlMode.SWITCH, nominal_power_w=1500,
    )
    result = allocate(4000, battery(40), [big_pump], SETTINGS, expected_surplus_wh=20000)
    assert result.consumer_power_w == {"pump": 0}
    assert result.battery_power_w == 4000


def test_full_battery_gives_everything_to_consumers() -> None:
    result = allocate(1200, battery(100), [ROD], SETTINGS, expected_surplus_wh=0)
    assert result.charge_secured
    assert result.consumer_power_w["rod"] == 1200
    assert result.battery_power_w == 0


def test_minimum_runtime_keeps_consumer_running() -> None:
    running = ConsumerRequest(
        subentry_id="pump", priority=1, control_mode=ControlMode.SWITCH,
        nominal_power_w=500, must_stay_on=True,
    )
    result = allocate(200, battery(50), [running], SETTINGS, expected_surplus_wh=None)
    assert result.consumer_power_w["pump"] == 500
    # The missing 300 W come from the battery.
    assert result.battery_power_w == -300


def test_minimum_pause_keeps_consumer_off() -> None:
    paused = ConsumerRequest(
        subentry_id="rod", priority=1, control_mode=ControlMode.POWER,
        min_power_w=300, max_power_w=3000, must_stay_off=True,
    )
    result = allocate(3000, battery(100), [paused], SETTINGS, expected_surplus_wh=0)
    assert result.consumer_power_w["rod"] == 0


def test_deficit_is_covered_by_battery() -> None:
    result = allocate(-1500, battery(50), [], SETTINGS, expected_surplus_wh=None)
    assert result.strategy is Strategy.SELF_CONSUMPTION
    assert result.battery_power_w == -1500


def test_peak_shaving_only_covers_import_above_limit() -> None:
    settings = AllocationSettings(**{**SETTINGS.__dict__, "peak_shaving": True})
    low = allocate(-4000, battery(15), [], settings, expected_surplus_wh=None)
    assert low.strategy is Strategy.PEAK_SHAVING
    assert low.battery_power_w == -1000
    normal = allocate(-4000, battery(50), [], settings, expected_surplus_wh=None)
    assert normal.battery_power_w == -4000


@pytest.fixture
def vienna() -> None:
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Vienna"))


def _forecast(day: datetime) -> dict:
    return {day.replace(hour=h): wh for h, wh in ((10, 1000), (11, 2000), (12, 2000), (13, 0))}


def test_remaining_pv_is_pro_rata(vienna) -> None:
    day = datetime(2026, 6, 1, tzinfo=dt_util.get_default_time_zone())
    now = day.replace(hour=11, minute=30)
    # half of 11:00 period + 12:00 period
    assert remaining_pv_wh(_forecast(day), now) == pytest.approx(3000)


def test_expected_surplus_subtracts_load_until_pv_ends(vienna) -> None:
    day = datetime(2026, 6, 1, tzinfo=dt_util.get_default_time_zone())
    now = day.replace(hour=11, minute=30)
    # PV ends 13:00 -> 1.5 h at 400 W = 600 Wh
    assert expected_surplus_wh(_forecast(day), now, 400) == pytest.approx(2400)
    assert expected_surplus_wh(_forecast(day), day.replace(hour=20), 400) == 0
    assert expected_surplus_wh(None, now, 400) is None
