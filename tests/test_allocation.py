"""Tests for the distribution of power between batteries and consumers."""

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from homeassistant.util import dt as dt_util

from custom_components.slems.allocation import (
    AllocationSettings,
    BatteryGroup,
    CapControl,
    ConsumerRequest,
    Strategy,
    allocate,
    battery_full,
    expected_surplus_wh,
    limit_discharge_export,
    max_discharge_export_w,
    remaining_pv_wh,
)
from custom_components.slems.const import CapMode, ControlMode

SETTINGS = AllocationSettings(
    battery_priority_soc_pct=30,
    battery_share_when_secured_pct=75,
    charge_secured_buffer_wh=1000,
    charge_grid_target_w=0,
    discharge_grid_target_w=0,
    discharge_max_grid_export_w=200,
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


def test_group_is_full_only_when_every_battery_is() -> None:
    # Mean SoC 99.5 %, but one battery at 98.5 % still charges.
    group = replace(battery(99.5), max_charge_w=2500, all_full=False)
    assert not group.is_full
    result = allocate(4000, group, [ROD], SETTINGS, expected_surplus_wh=50000)
    assert result.battery_power_w > 0
    assert replace(battery(99.0), all_full=True).is_full
    assert battery_full(99.5, 100.0)
    assert not battery_full(98.5, 100.0)
    assert battery_full(89.5, 90.0)


def test_battery_has_priority_below_soc_threshold() -> None:
    result = allocate(4000, battery(20), [ROD], SETTINGS, expected_surplus_wh=50000)
    assert result.strategy is Strategy.BATTERY_PRIORITY
    assert result.battery_power_w == 4000
    assert result.consumer_power_w["rod"] == 0


def test_battery_priority_when_forecast_is_not_enough() -> None:
    # 60 % missing of 10 kWh / 0.95 = 6.3 kWh, with buffer 7.3 kWh needed.
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


def _with(**changes) -> AllocationSettings:
    return AllocationSettings(**{**SETTINGS.__dict__, **changes})


def test_charge_keeps_grid_surplus_target() -> None:
    result = allocate(1000, battery(50), [], _with(charge_grid_target_w=100), None)
    assert result.battery_power_w == 900


def test_idle_band_between_targets() -> None:
    settings = _with(charge_grid_target_w=100, discharge_grid_target_w=-50)
    assert allocate(60, battery(50), [], settings, None).strategy is Strategy.IDLE
    assert allocate(-20, battery(50), [], settings, None).strategy is Strategy.IDLE
    assert allocate(-80, battery(50), [], settings, None).battery_power_w == -30


def test_discharge_target_with_export() -> None:
    # Keep 50 W export while discharging: cover the 400 W deficit plus 50 W.
    result = allocate(-400, battery(50), [], _with(discharge_grid_target_w=50), None)
    assert result.battery_power_w == -450


def test_discharge_target_is_capped_by_max_export() -> None:
    settings = _with(discharge_grid_target_w=500, discharge_max_grid_export_w=100)
    result = allocate(-400, battery(50), [], settings, None)
    assert result.battery_power_w == -500


def test_night_discharge_ignores_discharge_target() -> None:
    # House needs 300 W, night plan wants 600 W, export limit 200 W allows 500 W.
    result = allocate(-300, battery(80), [], SETTINGS, None, night_discharge_w=600)
    assert result.strategy is Strategy.NIGHT_DISCHARGE
    assert result.battery_power_w == -500


def test_night_discharge_covers_larger_house_load() -> None:
    result = allocate(-1500, battery(80), [], SETTINGS, None, night_discharge_w=600)
    assert result.battery_power_w == -1500


def test_night_discharge_not_during_surplus() -> None:
    result = allocate(2000, battery(80), [], SETTINGS, None, night_discharge_w=600)
    assert result.strategy is Strategy.BATTERY_PRIORITY


def test_export_limit_reduces_discharge() -> None:
    # Battery discharges 1000 W, grid exports 300 W, limit 100 W.
    assert limit_discharge_export(-1000, -1000, -300, 100) == -800
    # Within the limit nothing changes.
    assert limit_discharge_export(-1000, -1000, 200, 100) == -1000
    # Never turns a discharge into charging, charging is untouched.
    assert limit_discharge_export(-200, 0, -900, 100) == 0
    assert limit_discharge_export(500, 0, -900, 100) == 500


CAP = CapControl(limit_w=3000, margin_w=100)
SUPPORT_ROD = ConsumerRequest(
    subentry_id="rod", priority=2, control_mode=ControlMode.POWER,
    min_power_w=300, max_power_w=3000, cap_mode=CapMode.SUPPORT,
)
SUPPORT_PUMP = ConsumerRequest(
    subentry_id="pump", priority=1, control_mode=ControlMode.SWITCH, nominal_power_w=500,
    cap_mode=CapMode.SUPPORT,
)
SMALL = BatteryGroup(
    soc_pct=20, capacity_wh=10000, max_charge_w=1000, max_discharge_w=1000,
    charge_efficiency=0.95,
)


def test_feed_in_cap_batteries_first() -> None:
    # 3100 W above 2900 W: the batteries take it, the supporting rod nothing.
    result = allocate(6000, battery(20), [SUPPORT_ROD], SETTINGS, None, cap=CAP)
    assert result.strategy is Strategy.FEED_IN_CAP
    assert result.consumer_power_w["rod"] == 0
    assert result.battery_power_w == 5000


def test_feed_in_cap_planned_support_before_the_batteries() -> None:
    early = CapControl(limit_w=3000, margin_w=100, support_w=2000)
    result = allocate(6000, battery(20), [SUPPORT_ROD], SETTINGS, None, cap=early)
    assert result.consumer_power_w["rod"] == 2000
    # 1100 W above the limit plus the surplus below it (battery priority).
    assert result.battery_power_w == 4000


def test_feed_in_cap_order_after_the_batteries() -> None:
    # Everything is above the limit: batteries, supporting pump, normal rod.
    rod = replace(SUPPORT_ROD, cap_mode=CapMode.NORMAL)
    no_room = CapControl(limit_w=100, margin_w=100)
    result = allocate(2100, SMALL, [SUPPORT_PUMP, rod], SETTINGS, None, cap=no_room)
    assert result.battery_power_w == 1000
    assert result.consumer_power_w["pump"] == 500
    assert result.consumer_power_w["rod"] == 600
    never = replace(rod, cap_mode=CapMode.NEVER)
    result = allocate(2100, SMALL, [SUPPORT_PUMP, never], SETTINGS, None, cap=no_room)
    assert result.consumer_power_w["rod"] == 0


def test_feed_in_cap_holds_charging_below_the_limit() -> None:
    hold = CapControl(limit_w=3000, margin_w=100, hold_charging=True)
    pump = replace(SUPPORT_PUMP, cap_mode=CapMode.NEVER)
    result = allocate(4000, battery(20), [pump], SETTINGS, None, cap=hold)
    # Only the 1100 W above the limit are charged; the pump gets surplus below it.
    assert result.battery_power_w == 1100
    assert result.consumer_power_w["pump"] == 500


def test_feed_in_cap_supporting_consumers_get_no_normal_surplus() -> None:
    # Surplus below the limit, charge secured: the share left for consumers is exported.
    result = allocate(
        2000, battery(80), [SUPPORT_PUMP], SETTINGS, expected_surplus_wh=50000, cap=CAP
    )
    assert result.consumer_power_w["pump"] == 0
    # Without the feed-in cap the pump takes surplus as usual.
    result = allocate(2000, battery(80), [SUPPORT_PUMP], SETTINGS, expected_surplus_wh=50000)
    assert result.consumer_power_w["pump"] == 500


def test_feed_in_cap_overrides_grid_friendly_and_share() -> None:
    result = allocate(
        5000, battery(80), [], SETTINGS, expected_surplus_wh=50000, feed_in_limit_w=4000,
        cap=CAP,
    )
    # Grid friendly would not charge at all; the part above the limit is charged anyway.
    assert result.battery_power_w == pytest.approx(2100)


def test_feed_in_cap_export_before_the_peak() -> None:
    export = CapControl(limit_w=3000, margin_w=100, export_w=1500)
    result = allocate(1000, battery(80), [], SETTINGS, None, cap=export)
    assert result.strategy is Strategy.FEED_IN_CAP
    assert result.battery_power_w == -1500
    # Never above the limit.
    result = allocate(2000, battery(80), [], SETTINGS, None, cap=export)
    assert result.battery_power_w == -900


def test_feed_in_cap_export_limit() -> None:
    assert max_discharge_export_w(SETTINGS, None) == 200
    assert max_discharge_export_w(SETTINGS, CAP) == 200
    assert max_discharge_export_w(SETTINGS, CapControl(3000, export_w=500)) == 2900


def test_remaining_pv_with_half_hour_periods() -> None:
    now = datetime(2026, 6, 1, 12, 40, tzinfo=dt_util.get_default_time_zone())
    start = now.replace(hour=11, minute=0)
    # 500 Wh per half hour from 11:00 to 14:00.
    forecast = {start + timedelta(minutes=30 * i): 500.0 for i in range(6)}
    # 12:40–13:00 is a third of the hour 12:00 (1000 Wh), then 13:00–14:00.
    assert remaining_pv_wh(forecast, now) == pytest.approx(1000 / 3 + 1000)


def test_boost_before_the_batteries() -> None:
    # Below the battery priority threshold the batteries would take everything.
    boosted = replace(ROD, boost=True)
    result = allocate(2000, battery(20), [boosted], SETTINGS, expected_surplus_wh=None)
    assert result.consumer_power_w["rod"] == 2000
    assert result.battery_power_w == 0
    result = allocate(4000, battery(20), [boosted], SETTINGS, expected_surplus_wh=None)
    assert result.consumer_power_w["rod"] == 3000
    assert result.battery_power_w == 1000


def test_forced_without_surplus() -> None:
    forced = replace(PUMP, forced=True)
    # No surplus: the pump runs, the batteries cover it (grid target 0).
    result = allocate(0, battery(60), [forced], SETTINGS, expected_surplus_wh=None)
    assert result.consumer_power_w["pump"] == 500
    assert result.battery_power_w == -500


def test_done_target_switches_off() -> None:
    done = replace(PUMP, must_stay_off=True)
    result = allocate(3000, battery(80), [done], SETTINGS, expected_surplus_wh=50000)
    assert result.consumer_power_w["pump"] == 0


def test_forced_from_the_batteries_only_limited() -> None:
    forced = replace(ROD, forced=True, forced_max_w=2500)
    result = allocate(0, battery(60), [forced], SETTINGS, expected_surplus_wh=None)
    assert result.consumer_power_w["rod"] == 2500


def test_unsupported_consumer_runs_from_the_grid() -> None:
    forced = replace(PUMP, forced=True)
    # House 300 W, forced pump 500 W: the batteries only cover the house.
    result = allocate(-300, battery(60), [forced], SETTINGS, None, unsupported=frozenset({"pump"}))
    assert result.consumer_power_w["pump"] == 500
    assert result.battery_power_w == -300
    # A measured consumer SLEMS does not control: its power is in the house load.
    measured = allocate(-1300, battery(60), [], SETTINGS, None, unsupported_measured_w=1000)
    assert measured.battery_power_w == -300
    # Never below zero: the batteries do not charge from the grid for it.
    small = allocate(-200, battery(60), [], SETTINGS, None, unsupported_measured_w=1000)
    assert small.battery_power_w == 0


def test_unsupported_keeps_peak_shaving_and_night_discharge() -> None:
    settings = AllocationSettings(**{**SETTINGS.__dict__, "peak_shaving": True})
    low = allocate(-4000, battery(15), [], settings, None, unsupported_measured_w=2000)
    assert low.battery_power_w == -1000
    # Night discharge keeps its planned power; the house part shrinks.
    night = allocate(-1300, battery(80), [], SETTINGS, None, night_discharge_w=600, unsupported_measured_w=1000)
    assert night.strategy is Strategy.NIGHT_DISCHARGE
    assert night.battery_power_w == -600


def test_price_hold_leaves_the_deficit_to_the_grid() -> None:
    held = allocate(-800, battery(50), [], SETTINGS, None, discharge_limit_w=0.0)
    assert held.strategy is Strategy.PRICE_HOLD
    assert held.battery_power_w == 0
    partly = allocate(-800, battery(50), [], SETTINGS, None, discharge_limit_w=300.0)
    assert partly.battery_power_w == -300
    # Peak shaving at low state of charge still covers the import above its limit.
    peak = _with(peak_shaving=True)
    shaved = allocate(-4000, battery(10), [], peak, None, discharge_limit_w=0.0)
    assert shaved.strategy is Strategy.PEAK_SHAVING and shaved.battery_power_w == -1000


def test_grid_charge_outside_a_surplus_below_the_import_limit() -> None:
    charging = allocate(-800, battery(30), [], SETTINGS, None, grid_charge_w=1500.0)
    assert charging.strategy is Strategy.GRID_CHARGE and charging.battery_power_w == 1500
    # Peak shaving: house 800 W + charging at most up to the 2000 W limit.
    limited = allocate(-800, battery(30), [], _with(peak_shaving=True, peak_shaving_grid_limit_w=2000), None, grid_charge_w=1500.0)
    assert limited.battery_power_w == pytest.approx(1200)
    # With a surplus the normal allocation applies.
    assert allocate(1000, battery(30), [], SETTINGS, None, grid_charge_w=1500.0).strategy is not Strategy.GRID_CHARGE
