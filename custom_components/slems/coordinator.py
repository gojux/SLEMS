"""Data coordinator: collects measurements and computes the allocation."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from functools import partial
import asyncio
import logging
import math
import time
from typing import Any

from homeassistant.components.modbus import async_get_unit
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.history import get_significant_states
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_state_report_event,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.const import UnitOfTemperature
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import TemperatureConverter
from modbus_connection import ModbusTcpParams

from .allocation import (
    Allocation,
    AllocationSettings,
    BatteryGroup,
    CapControl,
    ConsumerRequest,
    Strategy,
    allocate,
    battery_full,
    drawn_power_w,
    expected_surplus_wh,
    limit_charge_to_surplus,
    limit_discharge_export,
    max_discharge_export_w,
    remaining_pv_wh,
)
from .const import (
    CONF_GRID_MODBUS,
    CONF_GRID_MODBUS_HOST,
    CONF_GRID_MODBUS_INTERVAL_S,
    CONF_GRID_MODBUS_INVERTED,
    CONF_GRID_MODBUS_PORT,
    CONF_GRID_MODBUS_REGISTER,
    CONF_GRID_MODBUS_UNIT_ID,
    CONF_GRID_EXPORT_ENERGY_ENTITY,
    CONF_GRID_IMPORT_ENERGY_ENTITY,
    CONF_GRID_PHASE_ENTITIES,
    CONF_GRID_POWER_ENTITY,
    CONF_GRID_POWER_INVERTED,
    CONF_HOUSE_HISTORY_ENTITY,
    CONF_OUTDOOR_TEMPERATURE_ENTITY,
    CONF_PV_FORECAST_ENTRIES,
    CONF_PV_POWER_ENTITY,
    CONF_WEATHER_ENTITY,
    DEFAULT_BATTERY_PRIORITY_SOC_PCT,
    DEFAULT_BATTERY_SHARE_WHEN_SECURED_PCT,
    DEFAULT_CHARGE_GRID_TARGET_W,
    DEFAULT_CHARGE_SECURED_BUFFER_KWH,
    DEFAULT_GRID_FRIENDLY_BUFFER_KWH,
    DEFAULT_DISCHARGE_GRID_TARGET_W,
    DEFAULT_DISCHARGE_MAX_GRID_EXPORT_W,
    DEFAULT_FEED_IN_CAP_BUFFER_PCT,
    DEFAULT_FEED_IN_CAP_LIMIT_PCT,
    FEED_IN_CAP_MIN_BUFFER_PCT,
    DEFAULT_PV_PEAK_POWER_KWP,
    DEFAULT_NIGHT_RESERVE_PCT,
    DEFAULT_CONTROL_GAIN,
    DEFAULT_CONTROL_INTERVAL_S,
    DEFAULT_ROTATION_MIN_INTERVAL_MIN,
    DEFAULT_ROTATION_RAMP_MAX_S,
    DEFAULT_FULL_CHARGE_INTERVAL_DAYS,
    DEFAULT_ROTATION_RAMP_RATE_W_PER_S,
    DEFAULT_ROTATION_SOC_THRESHOLD_PCT,
    DEFAULT_OPERATING_MODE,
    DEFAULT_PEAK_SHAVING_GRID_LIMIT_W,
    DEFAULT_PEAK_SHAVING_SOC_THRESHOLD_PCT,
    DEFAULT_SURPLUS_AVERAGE_WINDOW_S,
    DOMAIN,
    SCAN_INTERVAL,
    CapMode,
    SurplusWindow,
    TargetSensor,
    TargetSource,
    TargetType,
    ConsumerType,
    ControlMode,
    OperatingMode,
    ReleaseState,
)
from .controller import ControlStatus, RealTimeController
from .consumers import (
    AVOID_CYCLING_RUN_S,
    RUNNING_AT_START_RATIO,
    ConsumerConfig,
    ConsumerState,
    RuntimeTracker,
    active_phases,
    amps_for,
    cycling_holds,
    read_consumer_state,
    temperature_c_of,
)
from .drivers import BatteryDriver, BatteryDriverError, BatteryTelemetry
from .battery_distribution import (
    LEAVE_RAMP_S,
    BatteryDistributor,
    BatteryUnit,
    Distribution,
    LossModel,
    RotationSettings,
)
from .battery_limits import (
    BatteryLimitSettings,
    PowerLimits,
    SocWindow,
    TemperatureLimit,
    TopTaper,
    power_limits,
)
from .cell_balancing import BMS_END_CELL_V, BMS_END_SOC_PCT, BalancingPhase, CellBalancer, CellMonitor
from .own_pv import CurtailControl, feasible_command_w
from .self_test import Outcome, Phase as SelfTestPhase, Sample, SelfTest, self_test_power_w, step_power_w
from .trial import TrialState
from .delivery_monitor import Action, DeliveryMonitor
from .full_charge import REST_S as FULL_CHARGE_REST_S, FullChargeCandidate, due_battery
from .feed_in_cap import CAP_MARGIN_W, CapConsumer, CapPlan, CapSettings, auto_buffer, plan_cap
from .problems import ProblemReporter
from .efficiency import EfficiencyTracker, EnergyIntegrator, LossCurveLearner
from .forecast import (
    ConsumptionForecast,
    ConsumptionForecaster,
    ForecastSources,
    VacationDays,
    async_statistic_means,
    sum_series,
)
from .forecast.accuracy import PvAccuracyTracker
from .bad_weather import BadWeatherMode
from .battery_wear import ESTIMATED_CT_PER_KWH, Wear
from .battery_support import BatterySupport, SupportBudget, next_refill, support_budget_wh
from .grid_filter import MAX_HOLD_S, GridPowerFilter
from .grid_meter import ModbusGridMeter
from .grid_quarters import GridQuarters
from .netting import NettingCheck
from .phases import HISTORY_S, REPLAY_STEP_S, Phase, PhaseLearner, PhaseModel, replay
from .market_prices import MarketPrices
from .learning import (
    CapacityLearner,
    ConsumerLearner,
    CoolingLearner,
    GridTargetLearner,
    MorningGapLearner,
    OutdoorPowerLearner,
    ThermalLearner,
    auto_timing,
    consumption_underestimate,
    pv_overestimate,
)
from .grid_friendly import (
    corrected_forecast,
    energy_from_means,
    feed_in_limit,
    pv_correction,
    pv_elapsed_share,
    remaining_surplus,
    remaining_surplus_by_hour,
)
from .consumer_targets import (
    RUNNING_W,
    TargetMode,
    TargetProgress,
    TargetSettings,
    TargetState,
    ESTIMATE_DAYS,
    START_MARGIN,
    NoPowerWatch,
    SurplusDemand,
    energy_to_target,
    evaluate,
    daily_demands,
    daily_energy_estimate,
    forced_load,
    next_period_demand,
    period_end,
    no_power_threshold_s,
    plan_surplus_window,
    remaining,
    surplus_demand,
    target_temperature,
    window_opens,
    window_start,
)
from .night_discharge import NightDischargePlan, plan_night_discharge, pv_takeover
from .price_chart import hourly_import_prices, period_import_prices
from .grid_charge import MIN_STEP_WH as GRID_CHARGE_STEP_WH
from .grid_charge import (
    QUARTER,
    ChargeBattery,
    GridChargePlan,
    PlanChanges,
    PlanTiming,
    price_decision,
    plan_grid_charge,
    slot_start,
)
from .energy_history import async_grid_energy, async_hourly_changes
from .price_backtest import BacktestBattery, measured_saving
from .price_savings import PriceSavings
from .price_hold import PriceHold, cheapest_start, plan_price_hold
from .tariff import Side, Tariff, Unit
from .tariff_comparison import configured_tariffs
from .peak_shaving import auto_limit, hours_until_refill
from .pv_forecast import (
    PvForecast,
    async_get_pv_forecast,
    energy_on_day,
    hourly,
    mean_power,
    power_lookup,
)
from .soc_projection import OwnPvBattery, ProjectionSettings, SocProjection, own_charge_limit, project_soc
from .util import entity_ids, state_as_float, state_as_kwh, state_as_watts, watts_factor

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_SAVE_DELAY_S = 600
# Start, phase changes and end of a balancing run are saved sooner.
BALANCING_SAVE_DELAY_S = 5
# A self-test ends when the battery cannot be read for this long.
SELF_TEST_UNREADABLE_S = 30.0
# An unread battery counts with its last measured power for this long: the
# Venus keeps its last set point without new commands (measured for 90 s).
# Afterwards it may have reached a SoC limit, the house power is unknown.
UNREAD_BATTERY_HOLD_S = 300.0
# Charging from the grid is planned at most this far ahead without a refill.
MAX_GRID_CHARGE_HORIZON = timedelta(hours=36)
GRID_CHARGE_REPLAN_S = 60.0
# The recorded quarter hours are saved this often (a restart loses at most this).
QUARTERS_SAVE_INTERVAL = timedelta(minutes=15)
# Releases tried when SLEMS unloads, and the wait between them.
SHUTDOWN_RELEASE_ATTEMPTS = 3
SHUTDOWN_RELEASE_WAIT_S = 2.0
DEVICE_INFO_INTERVAL_S = 6 * 3600
# Store key of the controller data (next to the per battery subentry ids).
CONTROL_STORE_KEY = "control"
PV_ACCURACY_STORE_KEY = "pv_accuracy"
CONSUMERS_STORE_KEY = "consumers"
THERMAL_STORE_KEY = "thermal"
COOLING_STORE_KEY = "cooling"
TARGETS_STORE_KEY = "targets"
NO_POWER_STORE_KEY = "no_power"
PHASES_STORE_KEY = "phases"
NETTING_STORE_KEY = "netting"
# Share of the learned storage capacity the feed-in cap planning relies on.
THERMAL_SAFETY = 0.8
# A power consumer counts as commanded at full power from this share on.
FULL_COMMAND_SHARE = 0.9
# Its power is learned once a command is this old, at least twice its
# response time (some devices take several seconds to follow).
CONSUMER_SETTLE_S = 15.0
MORNING_GAP_STORE_KEY = "morning_gap"
PRICE_SAVINGS_STORE_KEY = "price_savings"
PLAN_CHANGES_STORE_KEY = "price_plan_changes"
VACATION_DAYS_STORE_KEY = "vacation_days"
OUTDOOR_POWER_STORE_KEY = "outdoor_power"
# Hourly statistics of an hour are compiled a little after it ended.
STATISTICS_DELAY = timedelta(minutes=30)
HALF_HOUR = timedelta(minutes=30)


@dataclass
class BatteryRuntime:
    """A configured battery: its subentry id, name, driver and efficiency."""

    subentry_id: str
    name: str
    driver: BatteryDriver
    efficiency: EfficiencyTracker
    loss_curve: LossCurveLearner = field(default_factory=LossCurveLearner)
    # Temporarily disabled batteries are still measured (their power is part of
    # the energy balance) but neither planned with nor controlled.
    enabled: bool = True
    # Set while a battery disabled during discharging ramps out: the moment its
    # share reaches zero. It is released after it was commanded to 0 W.
    leaving_until: float | None = None
    cell_monitor: CellMonitor = field(default_factory=CellMonitor)
    # Active cell balancing run, None without one. A balancing battery is
    # treated like a disabled one (measured, but not planned with); a
    # discharging battery ramps out first.
    balancer: CellBalancer | None = None
    # Power the balancing run wants (+charge / -discharge), sent by the
    # controller; None while the run is paused.
    balancing_power_w: float | None = None
    # Outcome of the last run: "done", "cancelled", "timeout" or "telemetry".
    balancing_result: str | None = None
    # Running self-test, None without one. Like a balancing run the battery is
    # measured but not planned with meanwhile.
    self_test: SelfTest | None = None
    # Power the self-test wants (+charge / -discharge), None while nothing is sent.
    self_test_power_w: float | None = None
    # Result of the last self-test (SelfTest.as_dict), kept over restarts.
    self_test_last: dict | None = None
    # A self-test was asked for when the battery was added and has not run yet.
    self_test_pending: bool = False
    # Trial of a battery from a Modbus profile (see trial.py), None without one.
    trial: TrialState | None = None
    # A battery with a trial is controlled only once its device information
    # was compared after the start and after a pause (a firmware update).
    device_info_checked: bool = True
    # The write registers read for the next sample of a trial.
    trial_registers: dict[int, float | None] | None = None
    # The write registers read for an observation, and whether nothing was written during the read.
    observed_registers: tuple[dict[int, float | None], bool] | None = None
    # Duration of the last telemetry read (s), None before the first one.
    telemetry_read_s: float | None = None
    # Monotonic time of the first failed read since the last successful one.
    unreadable_since: float | None = None
    # Last polled AC power (+charge / -discharge) and its monotonic time; the
    # controller trusts it over a command the battery does not follow.
    measured_power_w: float | None = None
    measured_at: float | None = None
    # Production of the own PV modules at that time (batteries with own PV).
    measured_pv_w: float | None = None
    # Batteries with own PV: power sensor of the modules and the PV forecast
    # provider entry of them (None: no forecast, not part of the PV forecast).
    own_pv_entity: str | None = None
    own_pv_forecast_entry: str | None = None
    # Monotonic time since a release (hand back to its own logic) is
    # outstanding: it failed and is tried again until it succeeds.
    release_pending_since: float | None = None
    # Communication paused (e.g. for a firmware update) until this wall clock
    # time (UNIX timestamp); nothing is read or sent meanwhile.
    paused_until: float | None = None
    # "manual" or "firmware_update" (detected by the battery state).
    pause_reason: str | None = None
    # Firmware versions and other static information from the driver.
    device_info: dict[str, str] = field(default_factory=dict)
    device_info_read: float | None = None
    limits: BatteryLimitSettings = field(default_factory=BatteryLimitSettings)
    soc_window: SocWindow = field(default_factory=SocWindow)
    top_taper: TopTaper = field(default_factory=TopTaper)
    capacity_learner: CapacityLearner = field(default_factory=CapacityLearner)
    # Plan with the learned usable capacity instead of the configured one.
    learn_capacity: bool = True
    # Allowed power right now (updated with every poll).
    power_limits: PowerLimits | None = None
    # Costs per kWh stored and delivered again (see battery_wear).
    wear: Wear = field(default_factory=lambda: Wear(ESTIMATED_CT_PER_KWH))
    delivery: DeliveryMonitor = field(init=False)

    def __post_init__(self) -> None:
        self.delivery = DeliveryMonitor(self.name)

    @property
    def capacity_wh(self) -> float:
        """Usable capacity in effect: learned (if switched on and known) or configured."""
        learned = self.capacity_learner.capacity_wh
        if self.learn_capacity and learned is not None:
            return learned
        return self.driver.capabilities.capacity_wh

    @property
    def balancing_requested(self) -> bool:
        return self.balancer is not None

    @property
    def self_test_requested(self) -> bool:
        return self.self_test is not None

    @property
    def in_trial(self) -> bool:
        """Not controlled yet: in its trial or its device information not compared yet."""
        return (self.trial is not None and self.trial.in_trial) or not self.device_info_checked

    @property
    def not_responding(self) -> bool:
        """Excluded for a while because it did not deliver the commanded power."""
        return self.delivery.excluded(time.monotonic())

    @property
    def communication_paused(self) -> bool:
        return self.paused_until is not None and dt_util.utcnow().timestamp() < self.paused_until

    @property
    def participating(self) -> bool:
        """Planned and controlled (or still ramping out) by the normal operation."""
        return (
            (self.enabled and not self.balancing_requested and not self.self_test_requested)
            or self.leaving_until is not None
        ) and not self.not_responding and not self.communication_paused and not self.in_trial

    @property
    def plannable(self) -> bool:
        """Takes part in planning, total SoC and distribution."""
        return (
            self.enabled
            and not self.balancing_requested
            and not self.self_test_requested
            and not self.not_responding
            and not self.communication_paused
            and not self.in_trial
        )

    @property
    def supports_balancing(self) -> bool:
        keys = self.driver.extra_telemetry_keys
        return (
            self.driver.capabilities.controllable
            and "max_cell_voltage" in keys
            and "min_cell_voltage" in keys
        )

    @property
    def balancing_phase(self) -> BalancingPhase:
        if self.balancer is None:
            return BalancingPhase.OFF
        if self.balancing_power_w is None and not self.balancer.finished:
            return BalancingPhase.WAITING
        return self.balancer.phase


@dataclass
class ControlSettings:
    """User settings changed at runtime via entities (restored after restart)."""

    operating_mode: OperatingMode = DEFAULT_OPERATING_MODE
    # Household is away; the forecast uses the absence profile.
    vacation: bool = False
    # When enabled and the total SoC is at or below the threshold, the batteries
    # only discharge to keep the grid import below the limit.
    peak_shaving: bool = False
    peak_shaving_grid_limit_w: float = DEFAULT_PEAK_SHAVING_GRID_LIMIT_W
    peak_shaving_soc_threshold_pct: float = DEFAULT_PEAK_SHAVING_SOC_THRESHOLD_PCT
    # Import limit calculated from the consumption peaks (see peak_shaving).
    peak_shaving_auto: bool = True
    peak_shaving_reserve_pct: float = 20.0
    # Averaging window of the grid power; 0 disables averaging.
    surplus_average_window_s: float = DEFAULT_SURPLUS_AVERAGE_WINDOW_S
    battery_priority_soc_pct: float = DEFAULT_BATTERY_PRIORITY_SOC_PCT
    battery_share_when_secured_pct: float = DEFAULT_BATTERY_SHARE_WHEN_SECURED_PCT
    charge_secured_buffer_kwh: float = DEFAULT_CHARGE_SECURED_BUFFER_KWH
    charge_grid_target_w: float = DEFAULT_CHARGE_GRID_TARGET_W
    discharge_grid_target_w: float = DEFAULT_DISCHARGE_GRID_TARGET_W
    discharge_max_grid_export_w: float = DEFAULT_DISCHARGE_MAX_GRID_EXPORT_W
    # Rotation between batteries.
    rotation_soc_threshold_pct: float = DEFAULT_ROTATION_SOC_THRESHOLD_PCT
    rotation_min_interval_min: float = DEFAULT_ROTATION_MIN_INTERVAL_MIN
    rotation_ramp_rate_w_per_s: float = DEFAULT_ROTATION_RAMP_RATE_W_PER_S
    rotation_ramp_max_s: float = DEFAULT_ROTATION_RAMP_MAX_S
    # Prefer a battery not full for longer than the interval (see full_charge).
    regular_full_charge: bool = True
    full_charge_interval_days: float = DEFAULT_FULL_CHARGE_INTERVAL_DAYS
    # Minimum time between two control cycles.
    control_interval_s: float = DEFAULT_CONTROL_INTERVAL_S
    # Share of the remaining deviation corrected per control cycle: fixed value,
    # or start value of the automatic adaptation.
    control_gain: float = DEFAULT_CONTROL_GAIN
    auto_gain: bool = True
    # Learned instead of the fixed values (see learning); the fixed values
    # apply until enough is learned.
    grid_friendly_buffer_auto: bool = True
    charge_secured_buffer_auto: bool = True
    grid_targets_auto: bool = True
    # The learned discharge grid target is at least 0 W (no import aimed at).
    grid_targets_no_import: bool = False
    timing_auto: bool = True
    # Charge into the PV feed-in peak instead of as early as possible.
    grid_friendly_charging: bool = True
    # Energy grid friendly charging plans in addition to filling the
    # batteries (reserve against a too optimistic forecast).
    grid_friendly_buffer_kwh: float = DEFAULT_GRID_FRIENDLY_BUFFER_KWH
    night_discharge: bool = False
    # Keep the stored energy for the expensive hours (see price_hold); the
    # import price must be lower by at least the minimum gain.
    price_control: bool = False
    price_min_gain_ct: float = 2.0
    # Charge the batteries from the grid when it pays (see grid_charge; only
    # with price aware control), up to this state of charge and power (0: the
    # charge power of the batteries).
    grid_charge: bool = False
    grid_charge_max_soc_pct: float = 90.0
    grid_charge_max_w: float = 0.0
    # Highest grid import any charging from the grid may cause (W, 0: no limit),
    # e.g. the main fuse; in addition to the import limit of peak shaving.
    grid_import_max_w: float = 0.0
    # Feed in from the batteries at a high credit, accepting grid import later
    # (see grid_charge; only with price aware control); not below the morning
    # reserve.
    battery_export: bool = False
    # Night discharge reserve in % of tomorrow's forecast daily consumption.
    night_reserve_pct: float = DEFAULT_NIGHT_RESERVE_PCT
    # Learned from the morning gaps; share of the mornings it covers (above
    # 100 %: the largest gap times this).
    night_reserve_auto: bool = True
    night_reserve_coverage_pct: float = 100.0
    # Feed-in cap at the grid connection point (see feed_in_cap).
    feed_in_cap: bool = False
    pv_peak_power_kwp: float = DEFAULT_PV_PEAK_POWER_KWP
    feed_in_cap_limit_pct: float = DEFAULT_FEED_IN_CAP_LIMIT_PCT
    # Buffer on the energy to absorb (may be negative) and the minimum buffer
    # per peak in % of the peak power (as energy of one hour).
    feed_in_cap_buffer_pct: float = DEFAULT_FEED_IN_CAP_BUFFER_PCT
    # Buffer from the recorded PV forecast errors instead of the fixed one.
    feed_in_cap_auto_buffer: bool = True
    # Duration of a communication pause (firmware update), minutes.
    communication_pause_min: float = 20.0
    # Charge power limited by the battery temperature (see battery_limits).
    temperature_limit: bool = False
    temperature_high_c: float = 40.0
    temperature_band_c: float = 10.0
    temperature_floor_pct: float = 40.0
    temperature_low_c: float = 0.0

    @property
    def feed_in_cap_limit_w(self) -> float:
        return self.pv_peak_power_kwp * 1000 * self.feed_in_cap_limit_pct / 100

    def temperature(self) -> TemperatureLimit:
        return TemperatureLimit(
            enabled=self.temperature_limit,
            high_c=self.temperature_high_c,
            band_c=self.temperature_band_c,
            floor_pct=self.temperature_floor_pct,
            low_c=self.temperature_low_c,
        )

    def allocation_settings(self) -> AllocationSettings:
        return AllocationSettings(
            battery_priority_soc_pct=self.battery_priority_soc_pct,
            battery_share_when_secured_pct=self.battery_share_when_secured_pct,
            charge_secured_buffer_wh=self.charge_secured_buffer_kwh * 1000,
            charge_grid_target_w=self.charge_grid_target_w,
            discharge_grid_target_w=self.discharge_grid_target_w,
            discharge_max_grid_export_w=self.discharge_max_grid_export_w,
            peak_shaving=self.peak_shaving,
            peak_shaving_grid_limit_w=self.peak_shaving_grid_limit_w,
            peak_shaving_soc_threshold_pct=self.peak_shaving_soc_threshold_pct,
            grid_friendly_charging=self.grid_friendly_charging,
        )


@dataclass
class SystemSnapshot:
    """One consistent view of the energy system.

    Power balance at the grid connection point:
    grid = house - pv + battery, where house contains every consumer behind the
    smart meter.
    """

    # Grid power, +import / -export.
    grid_power_w: float | None = None
    # Grid power without the batteries in the planning (what the house and the
    # PV do on their own) after the conservative averaging (see grid_filter).
    grid_without_batteries_filtered_w: float | None = None
    # The same plus the battery power the grid meter shows now.
    grid_power_filtered_w: float | None = None
    # PV of the installation, including the own PV modules of batteries.
    pv_power_w: float | None = None
    # Of it the own PV modules of the batteries that were read.
    own_pv_power_w: float = 0.0
    # PV the batteries with own PV curtail for the feed-in cap (see own_pv).
    own_pv_curtail_w: float = 0.0
    # Forecast of the own PV modules per battery (native periods, raw).
    own_pv_forecasts: dict[str, PvForecast] = field(default_factory=dict)
    # Per battery subentry id; missing if the battery could not be read.
    batteries: dict[str, BatteryTelemetry] = field(default_factory=dict)
    # Last measured power of the controllable batteries that could not be
    # read this time (see SlemsCoordinator._unread_battery_power_w); None: unknown.
    unread_battery_power_w: float | None = 0.0
    consumer_configs: dict[str, ConsumerConfig] = field(default_factory=dict)
    consumers: dict[str, ConsumerState] = field(default_factory=dict)
    # Consumers that did not draw the commanded power (own thermostat); they
    # are treated like uncontrolled loads for a while.
    saturated: frozenset[str] = frozenset()
    # Consumers with a cycling thermostat that draw nothing right now; they
    # keep their command, the batteries get their unused power.
    resting: frozenset[str] = frozenset()
    # Consumers switched off that run on their own (started by hand, a run a
    # closed release does not end); treated like uncontrolled loads meanwhile.
    running_on_its_own: frozenset[str] = frozenset()
    # Consumers whose control the user switched off (measured only).
    control_disabled: frozenset[str] = frozenset()
    # Import limit of peak shaving in effect (fixed or automatic), None when off.
    peak_shaving_limit_w: float | None = None
    # Replacement for a negative house power (last valid value, None: unknown).
    house_power_hold_w: float | None = None
    pv_forecast: PvForecast | None = None
    consumption_forecast: ConsumptionForecast | None = None
    # Current outdoor temperature of the weather entity (°C).
    outdoor_temperature_c: float | None = None
    night_discharge: NightDischargePlan | None = None
    # Hours in which the batteries keep their energy (see price_hold).
    price_hold: PriceHold | None = None
    # Charging from the grid (see grid_charge), None when nothing is planned.
    grid_charge: GridChargePlan | None = None
    # Feed-in cap plan, None when off or without forecasts.
    feed_in_cap: CapPlan | None = None
    # Buffer of the feed-in cap in effect: % and "manual" or "auto" (+ days).
    feed_in_cap_buffer_pct: float | None = None
    feed_in_cap_buffer_source: str | None = None
    feed_in_cap_buffer_days: int = 0
    # Power SLEMS can distribute, +surplus / -deficit.
    available_power_w: float | None = None
    expected_surplus_wh: float | None = None
    # Grid friendly charging: batteries charge only the surplus above this.
    feed_in_limit_w: float | None = None
    # Why there is no feed-in limit: "disabled", "no_forecast" (no battery or
    # PV forecast) or "not_enough_surplus" (charge at once).
    feed_in_limit_reason: str | None = None
    # Ratio of today's PV production to the forecast until now, applied with a
    # weight depending on the share of the day passed (see grid_friendly).
    pv_correction: float = 1.0
    pv_elapsed_share: float = 0.0
    # Hours of today for the dashboard: start, corrected PV forecast (Wh),
    # consumption forecast (Wh), planned battery charging (W) and projected
    # total SoC at the end of the hour (%), the last two from now on.
    day_plan: list[dict] = field(default_factory=list)
    # Same for tomorrow (raw PV forecast, projection continued from today).
    day_plan_tomorrow: list[dict] = field(default_factory=list)
    allocation: Allocation | None = None
    # Planned power per battery subentry id (+charge / -discharge, AC).
    distribution: Distribution | None = None

    @property
    def battery_power_w(self) -> float | None:
        """Total AC battery power (+charge / -discharge), with the held power of
        unread batteries; None while one of them is unknown."""
        if self.unread_battery_power_w is None:
            return None
        powers = [
            power
            for b in self.batteries.values()
            if (power := b.grid_side_power_w) is not None
        ]
        if not powers and not self.unread_battery_power_w:
            return None
        return sum(powers) + self.unread_battery_power_w

    def _consumer_power_w(self, included_in_meter: bool) -> float:
        return sum(
            state.total_power_w or 0.0
            for subentry_id, state in self.consumers.items()
            if self.consumer_configs[subentry_id].included_in_meter == included_in_meter
        )

    def is_controllable_now(self, subentry_id: str) -> bool:
        """True if SLEMS may control the consumer right now."""
        config = self.consumer_configs[subentry_id]
        state = self.consumers[subentry_id]
        return (
            config.controllable
            and config.included_in_meter
            and not state.blocked
            and subentry_id not in self.saturated
            and subentry_id not in self.running_on_its_own
            and subentry_id not in self.control_disabled
        )

    def controlled_consumer_power_w(self) -> float:
        """Power of the consumers SLEMS may control right now (behind the meter)."""
        return sum(
            state.power_w or 0.0
            for subentry_id, state in self.consumers.items()
            if self.is_controllable_now(subentry_id)
        )

    @property
    def raw_house_power_w(self) -> float | None:
        """Consumption behind the smart meter from the energy balance, unchecked.

        Negative for a moment when the smart meter reports a change later than
        the PV and battery measurements. Unknown while an enabled battery
        could not be read for longer than UNREAD_BATTERY_HOLD_S: it keeps
        its last set point, its power would end up in the house (a disabled
        one never makes it unknown, see ``_unread_battery_power_w``).
        """
        if self.grid_power_w is None or self.unread_battery_power_w is None:
            return None
        return (
            self.grid_power_w
            + (self.pv_power_w or 0.0)
            - (self.battery_power_w or 0.0)
        )

    @property
    def house_power_w(self) -> float | None:
        """Consumption behind the smart meter; negative values replaced (see HousePowerHold)."""
        raw = self.raw_house_power_w
        if raw is None or raw >= 0:
            return raw
        return self.house_power_hold_w

    @property
    def base_load_w(self) -> float | None:
        """House consumption without the separately measured consumers."""
        house = self.house_power_w
        if house is None:
            return None
        return house - self._consumer_power_w(included_in_meter=True)

    @property
    def total_consumption_w(self) -> float | None:
        """House consumption plus consumers that are not behind the smart meter."""
        house = self.house_power_w
        if house is None:
            return None
        return house + self._consumer_power_w(included_in_meter=False)


@dataclass
class ForecastPlan:
    """Result of ``SlemsCoordinator.forecast_plan``."""

    cap: CapPlan | None = None
    feed_in_limit_w: float | None = None
    feed_in_limit_reason: str | None = None
    peak_threshold_pct: float = 0.0
    peak_limit_w: float = 0.0
    projection: SocProjection | None = None
    # Energy the batteries can spare for consumers on battery support
    # "automatic" (see battery_support); None without such a consumer.
    support_budget_wh: float | None = None
    price_hold: PriceHold | None = None
    grid_charge: GridChargePlan | None = None
    day_plan: list[dict] = field(default_factory=list)
    day_plan_tomorrow: list[dict] = field(default_factory=list)


class HousePowerHold:
    """Replacement for a negative house power from the energy balance.

    The smart meter often reports a change later than the PV and battery
    measurements; for a moment the balance then gives a negative consumption.
    The consumption itself has usually not changed, so the last valid value
    is kept, for at most ``HOLD_S``; afterwards the value is unknown (a
    persistent negative balance points to a wrong sensor).
    """

    HOLD_S = 30.0

    def __init__(self) -> None:
        self._last: tuple[float, float] | None = None

    def check(self, snapshot: SystemSnapshot, now: float) -> None:
        """Remember a valid value or set the replacement in ``snapshot``."""
        raw = snapshot.raw_house_power_w
        snapshot.house_power_hold_w = None
        if raw is None:
            return
        if raw >= 0:
            self._last = (raw, now)
        elif self._last is not None and now - self._last[1] <= self.HOLD_S:
            snapshot.house_power_hold_w = self._last[0]


type SlemsConfigEntry = ConfigEntry[SlemsCoordinator]


def is_full_command(consumer: ConsumerConfig, command: float | None, full_power_w: float) -> bool:
    """Commanded at its full power, so its measured power is the one to learn.

    An on/off consumer takes its power whenever it is on: its command is the
    planned power (the learned one once in use) and may be below the
    configured power without throttling it.
    """
    if command is None or command <= 0:
        return False
    return consumer.control_mode.on_off or command >= FULL_COMMAND_SHARE * full_power_w


class SlemsCoordinator(DataUpdateCoordinator[SystemSnapshot]):
    """Polls all batteries, reads the measurement entities and allocates power."""

    config_entry: SlemsConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: SlemsConfigEntry,
        batteries: list[BatteryRuntime],
        consumers: list[ConsumerConfig],
        system_device_id: str,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.batteries = batteries
        self.consumers = consumers
        # Consumers whose control the user switched off (restored by their switch).
        self.consumer_control_disabled: set[str] = set()
        # Part of each consumer in the feed-in cap (restored by its select).
        self.consumer_cap_modes: dict[str, CapMode] = {}
        # Device registry id of the central SLEMS device (parent of the batteries).
        self.system_device_id = system_device_id
        self.settings = ControlSettings()
        self._grid_filter = GridPowerFilter(self.settings.surplus_average_window_s)
        self._house_hold = HousePowerHold()
        self._runtime = RuntimeTracker()
        self._distributor = BatteryDistributor()
        self._curtail = CurtailControl()
        # PV energy produced today (in memory): (local date, Wh, last sample).
        self._pv_day: tuple[object, float, float | None, float | None] | None = None
        # True once the integration covers a whole day since midnight.
        self._pv_complete = False
        # Grid export today (Wh) from every grid value, like the PV energy:
        # (day, energy, monotonic time and grid power of the last value).
        self._export_day: tuple[object, float, float | None, float | None] | None = None
        self._export_complete = False
        self._store: Store[dict] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.efficiency"
        )
        self.forecaster = ConsumptionForecaster(hass, self._forecast_sources())
        self.controller = RealTimeController(self)
        self.problems = ProblemReporter(hass, self)
        # Batteries left out because their profile cannot be used (subentry id -> name, error).
        self.unusable_profiles: dict[str, dict[str, str]] = {}
        # Day-ahead prices, fetched only when the user switches it on.
        self.market_prices = MarketPrices(hass, entry.entry_id)
        # Grid import / export per quarter hour (see grid_quarters), own store.
        self.grid_quarters = GridQuarters()
        self._quarters_store: Store[dict] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.grid_quarters"
        )
        # Measured saving of the price aware control (see price_savings).
        self.price_savings = PriceSavings()
        # Hourly prices per side (import, feed-in credit): (quarter hour, end, prices update), prices.
        self._import_price_cache: dict[tuple[Side, bool], tuple[tuple, dict]] = {}
        # Configuration the setup depends on (see __init__._setup_key).
        self.setup_key: str | None = None
        # Price plans of the last inputs (real and simulated) -> (monotonic expiry, plan).
        self._grid_charge_cache: dict[tuple, tuple[float, GridChargePlan | None]] = {}
        # Price plan of the real control in effect and the computation running
        # in the executor (see _grid_charge).
        self._grid_charge_last: GridChargePlan | None = None
        self._grid_charge_task: asyncio.Task | None = None
        # Duration of the price plans made since the start (also for the simulation).
        self.grid_charge_timing = PlanTiming()
        self.plan_changes = PlanChanges()
        # Last tariff comparison for the dashboard: (monotonic time, result).
        self.tariff_comparison_cache: tuple[float, tuple, dict] | None = None
        # Grid power read over Modbus (see grid_meter); None if not configured
        # or not available (then grid_meter_error says why).
        self.grid_meter: ModbusGridMeter | None = None
        self.grid_meter_error: str | None = None
        self.bad_weather = BadWeatherMode(
            lambda day: get_astral_event_date(hass, "sunrise", day),
            lambda day: get_astral_event_date(hass, "sunset", day),
        )
        # Battery support per consumer (restored by its select entity) and the
        # budget of the consumers on "automatic".
        self.battery_support: dict[str, BatterySupport] = {}
        self.support_budget = SupportBudget()
        # Consumers whose power the batteries do not cover right now.
        self.unsupported: frozenset[str] = frozenset()
        self.pv_accuracy = PvAccuracyTracker()
        # Monotonic time since the export is above the feed-in cap.
        self.cap_exceeded_since: float | None = None
        self.grid_targets = GridTargetLearner()
        self.morning_gap = MorningGapLearner()
        # Power of the on/off consumers' runs against the outdoor temperature.
        self.outdoor_power = {c.subentry_id: OutdoorPowerLearner() for c in consumers if c.control_mode.on_off}
        # Days left out of the learned consumption (see forecast).
        self.vacation_days = VacationDays()
        self.consumer_learners = {c.subentry_id: ConsumerLearner() for c in consumers}
        self.thermal_learners = {
            c.subentry_id: ThermalLearner() for c in consumers if c.temperature_entity_ids
        }
        self.cooling_learners = {
            c.subentry_id: CoolingLearner() for c in consumers if c.temperature_entity_ids
        }
        # Consumers whose learned power and thermostat behaviour are used.
        self.consumer_learning: set[str] = set()
        # Daily targets of the consumers (see consumer_targets): settings
        # (restored by their entities), progress of the period (stored) and
        # the mode of the last plan.
        self.consumer_targets = {c.subentry_id: TargetSettings() for c in consumers}
        # Avoid short runs: monotonic time since the surplus is short for a running consumer.
        self._short_since: dict[str, float] = {}
        # Consumers with a daily target that draw no power although switched on.
        self.no_power = {c.subentry_id: NoPowerWatch() for c in consumers}
        # Phase of each battery and consumer behind the meter (see phases), and
        # whether the meter's counters net the phases (see netting).
        self.phase_model = PhaseModel()
        # The recorder's history being taken into the phase statistics (live readings wait).
        self._phase_history_run = 0
        self._phase_history_loading = False
        # Last replay of the history (diagnostics): hours taken, error.
        self.phase_history_status: dict[str, Any] = {}
        self.netting = NettingCheck()
        self.target_progress = {c.subentry_id: TargetProgress() for c in consumers}
        self.target_states: dict[str, TargetState] = {}
        # Local hour start -> Wh of the forced runs the targets may still need
        # (extra load in the planning, see consumer_targets.forced_load).
        self.target_load: dict[datetime, float] = {}
        # The same per consumer with the source "grid" (battery support applies).
        self.target_load_grid: dict[str, dict[datetime, float]] = {}
        # Energy (Wh) each target still needs, and the parts expected from the
        # surplus in order of priority (see consumer_targets.surplus_demand).
        self.target_energy_wh: dict[str, float | None] = {}
        # Daily energy of the last days per controllable consumer (see
        # consumer_targets.daily_energy_estimate), the consumers whose target
        # energy is that estimate right now, and today's energy until the last
        # refresh with the counter reading (kWh) then.
        self.target_energy_estimate: dict[str, float] = {}
        self.target_energy_estimated: set[str] = set()
        self._energy_today: dict[str, tuple[float, float | None]] = {}
        self.target_demands: list[SurplusDemand] = []
        # Forecast temperature of a temperature target's storage at its deadline
        # without heating (None while the cooling is not learned).
        self.target_forecast_c: dict[str, float | None] = {}
        # Planned surplus window of a release consumer: (opens, closes, deadline).
        self.surplus_windows: dict[str, tuple[datetime, datetime, datetime]] = {}
        # Battery due for its regular full charge (see full_charge), batteries
        # resting after it (monotonic end) and the last full charge seen.
        self.full_charge_battery: str | None = None
        self._full_charge_rest: dict[str, float] = {}
        self._last_full_seen: dict[str, float | None] = {}

    @property
    def _config(self):
        return self.config_entry.options or self.config_entry.data

    @property
    def grid_entity_id(self) -> str:
        return self._config[CONF_GRID_POWER_ENTITY]

    async def _async_setup(self) -> None:
        """Restore learned data and follow the grid meter."""
        stored = await self._store.async_load() or {}
        await self.market_prices.async_load()
        self._wanted_references()
        self.grid_quarters = GridQuarters.from_dict(await self._quarters_store.async_load())
        self.pv_accuracy.restore(stored.get(PV_ACCURACY_STORE_KEY))
        self.morning_gap = MorningGapLearner.from_dict(stored.get(MORNING_GAP_STORE_KEY))
        self.price_savings = PriceSavings.from_dict(stored.get(PRICE_SAVINGS_STORE_KEY))
        self.plan_changes = PlanChanges.from_dict(stored.get(PLAN_CHANGES_STORE_KEY))
        self.vacation_days = VacationDays.from_list(stored.get(VACATION_DAYS_STORE_KEY))
        for subentry_id, data in (stored.get(OUTDOOR_POWER_STORE_KEY) or {}).items():
            if subentry_id in self.outdoor_power:
                self.outdoor_power[subentry_id] = OutdoorPowerLearner.from_dict(data)
        consumers = {c.subentry_id: c for c in self.consumers}
        for subentry_id, data in (stored.get(CONSUMERS_STORE_KEY) or {}).items():
            if (consumer := consumers.get(subentry_id)) is not None:
                self.consumer_learners[subentry_id] = ConsumerLearner.from_dict(
                    data, not consumer.control_mode.on_off
                )
        for subentry_id, data in (stored.get(THERMAL_STORE_KEY) or {}).items():
            if subentry_id not in self.thermal_learners:
                continue
            sensors = list(consumers[subentry_id].temperature_entity_ids)
            # What was learned with other temperature sensors does not apply
            # (data stored before the sensors were recorded is kept).
            if data.get("sensors", sensors) == sensors:
                self.thermal_learners[subentry_id] = ThermalLearner.from_dict(data)
        for subentry_id, data in (stored.get(COOLING_STORE_KEY) or {}).items():
            # Learned with other temperature sensors: starts anew.
            if subentry_id in self.cooling_learners and data.get("sensors") == list(
                consumers[subentry_id].temperature_entity_ids
            ):
                self.cooling_learners[subentry_id] = CoolingLearner.from_dict(data)
        for subentry_id, data in (stored.get(TARGETS_STORE_KEY) or {}).items():
            if subentry_id in self.target_progress:
                self.target_progress[subentry_id] = TargetProgress.from_dict(data)
        for subentry_id, data in (stored.get(NO_POWER_STORE_KEY) or {}).items():
            if subentry_id in self.no_power:
                self.no_power[subentry_id] = NoPowerWatch.from_dict(data)
        self.phase_model = PhaseModel.from_dict(stored.get(PHASES_STORE_KEY))
        self.phase_model.configure(self.phase_devices, (b.subentry_id for b in self.batteries))
        self.netting = NettingCheck.from_dict(stored.get(NETTING_STORE_KEY))
        control = stored.get(CONTROL_STORE_KEY) or {}
        if gain := control.get("gain"):
            self.controller.gain_adapter.reset(gain)
        self.bad_weather.restore(control.get("bad_weather"))
        # Response times learned with another grid source contain its delay.
        if control.get("grid_source", "entity") == self.grid_source_configured:
            if response := control.get("battery_response_s"):
                self.controller.battery_response.response_s = response
            self.controller.battery_responses.restore(control.get("battery_responses") or {})
            self.controller.battery_responses.restore_reversals(control.get("battery_reversal_start_responses") or {})
            self.controller.battery_responses.restore_starts(control.get("battery_start_responses") or {})
            if reversal := control.get("battery_reversal_start_response_s"):
                self.controller.battery_reversal_response.response_s = reversal
        self.controller.battery_starts.restore(control.get("battery_starts") or {})
        for battery in self.batteries:
            data = stored.get(battery.subentry_id) or {}
            if integrator := data.get("integrator"):
                battery.efficiency.integrator = EnergyIntegrator.from_dict(integrator)
            if loss_curve := data.get("loss_curve"):
                battery.loss_curve = LossCurveLearner.from_dict(loss_curve)
            battery.cell_monitor.restore(data.get("cell_monitor"))
            battery.capacity_learner = CapacityLearner.from_dict(data.get("capacity_learner"))
            battery.paused_until = data.get("paused_until")
            battery.pause_reason = data.get("pause_reason")
            if balancer := data.get("balancer"):
                battery.balancer = CellBalancer.from_dict(
                    balancer, battery.driver.capabilities.max_charge_power_w
                )
            battery.self_test_last = data.get("self_test_last")
            if data.get("self_test_on_setup_done"):
                battery.self_test_pending = False
            if battery.driver.trial_spec is not None:
                battery.trial = TrialState.restore(
                    data.get("trial"), battery.driver.trial_key, dt_util.utcnow().timestamp(), battery.driver.trial_device
                )
                battery.device_info_checked = False
                if battery.trial.wrote:
                    # Written during the trial before the restart: handed back first.
                    battery.release_pending_since = time.monotonic()

        grid_entity = self._config[CONF_GRID_POWER_ENTITY]
        self._add_grid_sample(self.hass.states.get(grid_entity))
        if self._config.get(CONF_GRID_MODBUS):
            self._start_grid_meter()
        self.config_entry.async_on_unload(
            async_track_state_change_event(self.hass, grid_entity, self._on_grid_change)
        )
        self.config_entry.async_on_unload(
            async_track_state_report_event(self.hass, grid_entity, self._on_grid_report)
        )
        self.config_entry.async_on_unload(
            async_track_time_interval(self.hass, self._async_save_quarters, QUARTERS_SAVE_INTERVAL)
        )
        self.config_entry.async_on_unload(
            async_track_time_change(self.hass, self._on_price_runs_time, minute=40, second=0)
        )
        # The forecast is refitted every hour; the first run must not delay setup.
        self.config_entry.async_on_unload(
            async_track_time_change(
                self.hass, self._on_forecast_time, minute=5, second=0
            )
        )
        # Right after midnight "tomorrow" is a new day the forecast must cover.
        self.config_entry.async_on_unload(
            async_track_time_change(
                self.hass, self._on_forecast_time, hour=0, minute=0, second=30
            )
        )
        self.config_entry.async_create_background_task(
            self.hass, self.async_refresh_forecast(), "slems consumption forecast"
        )
        self.config_entry.async_create_background_task(
            self.hass, self._async_restore_pv_today(), "slems pv energy today"
        )
        self.config_entry.async_create_background_task(
            self.hass, self._async_restore_export_today(), "slems grid export today"
        )
        if self.phases_measured and self.phase_model.empty:
            self._load_phase_history()

    async def _on_forecast_time(self, _now: datetime) -> None:
        await self.async_refresh_forecast()

    async def async_refresh_forecast(self) -> None:
        """Refit the consumption forecast from the recorder statistics."""
        self.forecaster.sources = self._forecast_sources()
        if self.settings.vacation and self.vacation_days.mark(dt_util.now().date()):
            self._store.async_delay_save(self._data_to_store, STORAGE_SAVE_DELAY_S)
        excluded = frozenset(self.vacation_days.days)
        self.morning_gap.forget(excluded)
        try:
            await self.forecaster.async_refresh(self.settings.vacation, excluded)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Consumption forecast failed")
        try:
            await self._async_refresh_energy_estimates()
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Daily energy of the consumers failed")

    async def _async_refresh_energy_estimates(self) -> None:
        """Daily energy of the last days and today's energy of the controllable consumers."""
        now = dt_util.now()
        today = now.date()
        end = dt_util.start_of_local_day(now)
        start = end - timedelta(days=ESTIMATE_DAYS)
        estimates: dict[str, float] = {}
        energy_today: dict[str, tuple[float, float | None]] = {}
        for consumer in self.consumers:
            # A hot water release: its counter includes the heating.
            if (
                not consumer.controllable
                or consumer.consumer_type is ConsumerType.WALLBOX
                or consumer.hot_water_release
                or not consumer.energy_entity_id
            ):
                continue
            counter = state_as_kwh(self.hass.states.get(consumer.energy_entity_id))
            hourly = await async_hourly_changes(self.hass, consumer.energy_entity_id, start, now)
            if (estimate := daily_energy_estimate(hourly, today, self.vacation_days.days)) is not None:
                estimates[consumer.subentry_id] = estimate
            so_far = sum(wh for hour, wh in hourly.items() if hour >= end)
            energy_today[consumer.subentry_id] = (so_far, counter)
        self.target_energy_estimate = estimates
        self._energy_today = energy_today

    def _today_wh(self, subentry_id: str, state: ConsumerState | None) -> float:
        """Energy of the consumer today: the statistics plus the counter since."""
        so_far, counter = self._energy_today.get(subentry_id, (0.0, None))
        if counter is not None and state is not None and state.energy_kwh is not None:
            so_far += max(0.0, state.energy_kwh - counter) * 1000
        return so_far

    def _wanted_references(self) -> None:
        """The official monthly market values the tariffs refer to (fetched with the market prices)."""
        self.market_prices.wanted_references = {
            item.market for tariff in configured_tariffs(self.config_entry).values() for item in tariff.items if item.market
        }

    @callback
    def tariffs_changed(self) -> None:
        """A tariff was added, changed or removed (no reload needed): forget what
        was computed with the old tariffs."""
        self._import_price_cache.clear()
        self._grid_charge_cache.clear()
        self._grid_charge_last = None
        self.tariff_comparison_cache = None
        self._wanted_references()
        self.market_prices.references_update = None
        self.market_prices.refresh()

    def _forecast_sources(self) -> ForecastSources:
        config = self._config
        registry = er.async_get(self.hass)
        entry_id = self.config_entry.entry_id

        def own(unique_id: str) -> str | None:
            return registry.async_get_entity_id("sensor", DOMAIN, unique_id)

        included = [c for c in self.consumers if c.included_in_meter]
        return ForecastSources(
            house=tuple(
                entity_id
                for entity_id in (
                    own(f"{entry_id}_house_power"),
                    config.get(CONF_HOUSE_HISTORY_ENTITY),
                )
                if entity_id
            ),
            grid=config[CONF_GRID_POWER_ENTITY],
            grid_inverted=config.get(CONF_GRID_POWER_INVERTED, False),
            pv=entity_ids(config.get(CONF_PV_POWER_ENTITY)),
            batteries=tuple(
                entity_id
                for battery in self.batteries
                if (entity_id := own(f"{battery.subentry_id}_battery_power"))
            ),
            heat_pumps=tuple(
                c.power_entity_id
                for c in included
                if c.consumer_type is ConsumerType.HEAT_PUMP
            ),
            # A wallbox charges when a car is there: never part of the house
            # forecast, even if it is only measured.
            controllable=tuple(
                c.power_entity_id
                for c in included
                if (c.controllable or c.consumer_type is ConsumerType.WALLBOX)
                and c.consumer_type is not ConsumerType.HEAT_PUMP
            ),
            temperature=config.get(CONF_OUTDOOR_TEMPERATURE_ENTITY)
            or own(f"{entry_id}_outdoor_temperature"),
            weather=config.get(CONF_WEATHER_ENTITY),
        )

    @property
    def grid_source_configured(self) -> str:
        return "modbus" if self._config.get(CONF_GRID_MODBUS) else "entity"

    @property
    def grid_source(self) -> str:
        """Where the grid power comes from right now: "modbus" or "entity"."""
        meter = self.grid_meter
        return "modbus" if meter is not None and meter.fresh(time.monotonic()) else "entity"

    def _start_grid_meter(self) -> None:
        config = self._config
        try:
            unit = async_get_unit(
                self.hass,
                self.config_entry,
                ModbusTcpParams(
                    host=config[CONF_GRID_MODBUS_HOST], port=config[CONF_GRID_MODBUS_PORT]
                ),
                config[CONF_GRID_MODBUS_UNIT_ID],
            )
        except HomeAssistantError as err:
            self.grid_meter_error = str(err)
            _LOGGER.warning("Grid meter over Modbus not available: %s", err)
            return
        self.grid_meter = ModbusGridMeter(
            unit.read_holding_registers,
            config[CONF_GRID_MODBUS_REGISTER],
            config[CONF_GRID_MODBUS_INTERVAL_S],
            config.get(CONF_GRID_MODBUS_INVERTED, False),
            self._on_modbus_grid,
        )
        self.config_entry.async_create_background_task(
            self.hass, self.grid_meter.run(), "slems grid meter"
        )

    @callback
    def _on_modbus_grid(self, grid: float) -> None:
        self.controller.observe_meter_report()
        self._grid_filter.add(time.monotonic(), grid, self._battery_seen_w())
        self._integrate_export(grid, time.monotonic())
        self.controller.observe_grid(grid)
        self.controller.request()

    def _grid_from_entity(self) -> float | None:
        grid = state_as_watts(self.hass.states.get(self._config[CONF_GRID_POWER_ENTITY]))
        if grid is not None and self._config.get(CONF_GRID_POWER_INVERTED, False):
            grid = -grid
        return grid

    def grid_power_w(self) -> float | None:
        """Current grid power: from Modbus while fresh, otherwise from the entity."""
        meter = self.grid_meter
        if meter is not None and meter.fresh(time.monotonic()):
            return meter.value
        return self._grid_from_entity()

    def _battery_seen_w(self, seen: dict[str, float] | None = None) -> float:
        """Battery power the grid meter shows now, of the batteries in the
        planning (the battery power of the energy balance in ``plan``): ``seen``
        (default: the controller's commands the meter shows already), otherwise
        the telemetry."""
        data = self.data
        if data is None:
            return 0.0
        if seen is None:
            seen = self.controller.seen_battery_power(time.monotonic())
        return sum(
            power
            for battery in self.batteries
            if battery.participating
            and (telemetry := data.batteries.get(battery.subentry_id)) is not None
            and (power := seen.get(battery.subentry_id, telemetry.grid_side_power_w)) is not None
        )

    def _filter_grid(
        self, snapshot: SystemSnapshot, now: float, seen: dict[str, float] | None = None
    ) -> None:
        """Fill the filtered grid power of ``snapshot``."""
        grid_filter = self._grid_filter
        grid_filter.window_s = self.average_window_s
        grid_filter.hold_s = self.grid_hold_s
        filtered = grid_filter.conservative(now)
        snapshot.grid_without_batteries_filtered_w = filtered
        snapshot.grid_power_filtered_w = None if filtered is None else filtered + self._battery_seen_w(seen)

    @property
    def grid_hold_s(self) -> float:
        """Time a rise of the grid power without the batteries must last to
        count: twice the time the batteries need to follow. A reaction to a
        shorter load pulse arrives after its end and only feeds its energy into
        the grid."""
        return min(MAX_HOLD_S, 2 * self.controller.battery_response.value)

    def grid_age_s(self) -> float | None:
        """Age of the current grid power (None without a value)."""
        meter = self.grid_meter
        now = time.monotonic()
        if meter is not None and meter.fresh(now):
            return now - meter.updated
        state = self.hass.states.get(self._config[CONF_GRID_POWER_ENTITY])
        if state is None or state_as_float(state) is None:
            return None
        return time.time() - state.last_reported.timestamp()

    @callback
    def _on_grid_change(self, event: Event[EventStateChangedData]) -> None:
        if self.grid_source == "modbus":
            # The entity is only the fallback; its reports would mix the cadence.
            return
        self.controller.observe_meter_report()
        grid = self._add_grid_sample(event.data["new_state"])
        if grid is not None:
            self.controller.observe_grid(grid)
        self.controller.request()

    @callback
    def _on_grid_report(self, _event: Event) -> None:
        # Reported without a change of the value: only the cadence is new.
        if self.grid_source == "entity":
            self.controller.observe_meter_report()
            if self._export_day is not None:
                self._integrate_export(self._export_day[3], time.monotonic())

    def _add_grid_sample(self, state) -> float | None:
        grid = state_as_watts(state)
        if grid is None:
            return None
        if self._config.get(CONF_GRID_POWER_INVERTED, False):
            grid = -grid
        self._grid_filter.add(time.monotonic(), grid, self._battery_seen_w())
        self._integrate_export(grid, time.monotonic())
        return grid

    def set_bad_weather(self, on: bool) -> None:
        """Switch the bad weather mode on (until the next morning, see bad_weather) or off."""
        if on:
            data = self.data
            now = dt_util.now()
            consumption = data.consumption_forecast if data is not None else None
            self.bad_weather.switch_on(
                now,
                hourly(self._pv_native(data, now)) if data and data.pv_forecast else None,
                self._with_target_load(consumption.total) if consumption is not None else None,
            )
        else:
            self.bad_weather.switch_off()
        self._store.async_delay_save(self._data_to_store, BALANCING_SAVE_DELAY_S)
        self.hass.async_create_task(self.async_request_refresh())

    def _integrate_export(self, grid_w: float | None, now: float) -> None:
        """Add up today's grid export from the grid values (restarts at midnight)."""
        today = dt_util.now().date()
        day, energy, last_time, last_grid = self._export_day or (today, 0.0, None, None)
        if day != today:
            day, energy, last_time, last_grid = today, 0.0, None, None
            self._export_complete = True
        if last_time is not None and last_grid is not None and now - last_time < 300:
            energy += max(0.0, -last_grid) * (now - last_time) / 3600
        self._export_day = (day, energy, now, grid_w)
        self.grid_quarters.add(grid_w, time.time())

    async def _on_price_runs_time(self, _now: datetime) -> None:
        try:
            await self.async_evaluate_price_runs()
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Evaluating the price aware control failed")

    async def async_evaluate_price_runs(self) -> None:
        """Add the measured saving of the finished runs whose statistics exist."""
        wall_now = dt_util.now()
        due = self.price_savings.due(wall_now, STATISTICS_DELAY)
        tariffs = configured_tariffs(self.config_entry)
        battery = self.backtest_battery()
        if not due or not tariffs or battery is None:
            return
        tariff = next(iter(tariffs.values()))
        sources = self.history_sources()
        config = self._config
        for run in due:
            start, end = run.start, run.end
            means = await async_statistic_means(self.hass, [*sources.house, *sources.pv], start, end)
            load = next((means[i] for i in sources.house if means.get(i)), None)
            if not load:
                continue
            pv = sum_series(means.get(p, {}) for p in sources.pv)
            energy = await async_grid_energy(
                self.hass, start, end,
                import_entity=config.get(CONF_GRID_IMPORT_ENERGY_ENTITY),
                export_entity=config.get(CONF_GRID_EXPORT_ENERGY_ENTITY),
                grid_power_entity=config[CONF_GRID_POWER_ENTITY],
                grid_inverted=config.get(CONF_GRID_POWER_INVERTED, False),
                quarters=self.grid_quarters,
            )
            imported: dict[datetime, float] = {}
            exported: dict[datetime, float] = {}
            for series, target in ((energy.imported, imported), (energy.exported, exported)):
                for moment, wh in series.items():
                    hour = dt_util.as_local(moment).replace(minute=0, second=0, microsecond=0)
                    target[hour] = target.get(hour, 0.0) + wh
            hours = []
            hour = start
            while hour < end:
                hours.append(hour)
                hour += timedelta(hours=1)
            saving = measured_saving(
                hours,
                {dt_util.as_local(h): max(0.0, v) for h, v in load.items()},
                {dt_util.as_local(h): max(0.0, v) for h, v in pv.items()},
                battery,
                run.start_wh,
                imported,
                exported,
                hourly_import_prices(tariff, self.market_prices, start, end),
                hourly_import_prices(tariff, self.market_prices, start, end, Side.EXPORT),
            )
            self.price_savings.add(run, saving)
            _LOGGER.debug("Price aware control %s – %s: saved %.2f €", start, end, saving)
        self._store.async_delay_save(self._data_to_store, BALANCING_SAVE_DELAY_S)
        self.async_update_listeners()

    async def _async_save_quarters(self, _now: datetime) -> None:
        await self._quarters_store.async_save(self._quarters_to_store())

    def _quarters_to_store(self) -> dict:
        self.grid_quarters.prune(time.time())
        return self.grid_quarters.as_dict()

    async def _async_restore_export_today(self) -> None:
        """Take today's grid export until now from the recorder (5 minute means)."""
        grid_entity = self._config[CONF_GRID_POWER_ENTITY]
        wall_now = dt_util.now()
        try:
            means = await async_statistic_means(
                self.hass,
                [grid_entity],
                dt_util.start_of_local_day(dt_util.as_local(wall_now)),
                wall_now,
                "5minute",
            )
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Grid statistics of today not available", exc_info=True)
            return
        sign = 1.0 if self._config.get(CONF_GRID_POWER_INVERTED, False) else -1.0
        exported = {start: sign * mean for start, mean in means.get(grid_entity, {}).items()}
        energy, covered_until = energy_from_means(exported, timedelta(minutes=5))
        if covered_until is None:
            return
        # Bridge the minutes since the last statistics period with the current value.
        grid_now = self.grid_power_w()
        gap_h = max(0.0, (dt_util.now() - covered_until).total_seconds() / 3600)
        energy += max(0.0, -(grid_now or 0.0)) * gap_h
        self._export_day = (wall_now.date(), energy, time.monotonic(), grid_now)
        self._export_complete = True
        _LOGGER.debug("Grid export today restored from statistics: %.0f Wh", energy)

    @property
    def exported_today_wh(self) -> float | None:
        """Grid export today, None if it is not known since midnight."""
        if self._export_day is None or not self._export_complete:
            return None
        if self._export_day[0] != dt_util.now().date():
            return 0.0
        return self._export_day[1]

    def _data_to_store(self) -> dict:
        data: dict = {
            b.subentry_id: {
                "integrator": b.efficiency.integrator.as_dict(),
                "loss_curve": b.loss_curve.as_dict(),
                "cell_monitor": b.cell_monitor.as_dict(),
                "balancer": b.balancer.as_dict() if b.balancer else None,
                "paused_until": b.paused_until,
                "pause_reason": b.pause_reason,
                "capacity_learner": b.capacity_learner.as_dict(),
                "self_test_last": b.self_test_last,
                "self_test_on_setup_done": not b.self_test_pending,
                "trial": b.trial.as_dict() if b.trial is not None else None,
            }
            for b in self.batteries
        }
        data[CONSUMERS_STORE_KEY] = {
            subentry_id: learner.as_dict() for subentry_id, learner in self.consumer_learners.items()
        }
        sensors = {c.subentry_id: list(c.temperature_entity_ids) for c in self.consumers}
        data[THERMAL_STORE_KEY] = {
            subentry_id: {**learner.as_dict(), "sensors": sensors[subentry_id]}
            for subentry_id, learner in self.thermal_learners.items()
        }
        data[COOLING_STORE_KEY] = {
            subentry_id: {**learner.as_dict(), "sensors": sensors[subentry_id]}
            for subentry_id, learner in self.cooling_learners.items()
        }
        data[TARGETS_STORE_KEY] = {
            subentry_id: progress.as_dict() for subentry_id, progress in self.target_progress.items()
        }
        data[NO_POWER_STORE_KEY] = {
            subentry_id: watch.as_dict() for subentry_id, watch in self.no_power.items()
        }
        data[CONTROL_STORE_KEY] = {
            "gain": self.controller.gain_adapter.gain,
            "battery_response_s": self.controller.battery_response.response_s,
            "battery_responses": self.controller.battery_responses.as_dict(),
            # Reversals until the new direction shows.
            "battery_reversal_start_response_s": self.controller.battery_reversal_response.response_s,
            "battery_reversal_start_responses": self.controller.battery_responses.reversals_as_dict(),
            "battery_start_responses": self.controller.battery_responses.starts_as_dict(),
            "battery_starts": self.controller.battery_starts.as_dict(),
            "grid_source": self.grid_source_configured,
            "bad_weather": self.bad_weather.as_dict(),
        }
        data[PV_ACCURACY_STORE_KEY] = self.pv_accuracy.as_dict()
        data[MORNING_GAP_STORE_KEY] = self.morning_gap.as_dict()
        data[PRICE_SAVINGS_STORE_KEY] = self.price_savings.as_dict()
        data[PLAN_CHANGES_STORE_KEY] = self.plan_changes.as_dict()
        data[VACATION_DAYS_STORE_KEY] = self.vacation_days.as_list()
        data[OUTDOOR_POWER_STORE_KEY] = {
            subentry_id: learner.as_dict() for subentry_id, learner in self.outdoor_power.items()
        }
        data[PHASES_STORE_KEY] = self.phase_model.as_dict()
        data[NETTING_STORE_KEY] = self.netting.as_dict()
        return data

    @property
    def phases_measured(self) -> bool:
        """Whether the smart meter's power per phase is configured."""
        return all(self._config.get(key) for key in CONF_GRID_PHASE_ENTITIES)

    @property
    def phase_devices(self) -> dict[str, str]:
        """Subentry id -> name of the batteries and the consumers behind the meter (whose phase is learned)."""
        devices = {battery.subentry_id: battery.name for battery in self.batteries}
        devices.update({c.subentry_id: c.name for c in self.consumers if c.included_in_meter})
        return devices

    @property
    def phase_learners(self) -> dict[str, PhaseLearner]:
        return self.phase_model.learners

    def phase_learner(self, subentry_id: str) -> PhaseLearner:
        return self.phase_model.learner(subentry_id)

    def _observe_phases(self, snapshot: SystemSnapshot, now: float) -> None:
        """One reading of the phases for every battery and consumer behind the meter."""
        phases = self._grid_phases_w()
        if phases is None:
            return
        if self.phase_model.configure(self.phase_devices, (b.subentry_id for b in self.batteries)):
            self._load_phase_history()
        if self._phase_history_loading:
            return
        readings: dict[str, float | None] = {}
        for battery in self.batteries:
            telemetry = snapshot.batteries.get(battery.subentry_id)
            readings[battery.subentry_id] = telemetry.connection_power_w if telemetry is not None else None
        three_phase_w = 0.0
        for consumer in self.consumers:
            if not consumer.included_in_meter:
                continue
            state = snapshot.consumers.get(consumer.subentry_id)
            power = None
            if state is not None:
                power = state.total_power_w if state.total_power_w is not None else state.power_w
            active = state_as_float(self.hass.states.get(consumer.phases_entity_id)) if consumer.phases_entity_id else 1
            if active is not None and active != 1 and power is not None:
                # Switching between one and three phases is no change of the phase: only the one phase is
                # learned, on more the power counts as spread evenly.
                three_phase_w += power
                power = 0.0
            readings[consumer.subentry_id] = power
        # The own PV modules of a battery are part of its output.
        pv = None if snapshot.pv_power_w is None else snapshot.pv_power_w - snapshot.own_pv_power_w
        if self.phase_model.add(now, readings, phases, pv, three_phase_w):
            self.phase_model.evaluate()
            self._store.async_delay_save(self._data_to_store, STORAGE_SAVE_DELAY_S)

    def _grid_phases_w(self) -> tuple[float | None, float | None, float | None] | None:
        """The smart meter's power per phase (+ import), None if not configured."""
        if not self.phases_measured:
            return None
        sign = -1.0 if self._config.get(CONF_GRID_POWER_INVERTED, False) else 1.0
        watts = [state_as_watts(self.hass.states.get(self._config[key])) for key in CONF_GRID_PHASE_ENTITIES]
        return tuple(None if w is None else sign * w for w in watts)  # type: ignore[return-value]

    def _load_phase_history(self) -> None:
        """Start the phase statistics anew from the recorder's history (in the background)."""
        self.phase_model.restart()
        self._phase_history_run += 1
        self._phase_history_loading = True
        self.config_entry.async_create_background_task(
            self.hass, self._async_load_phase_history(self._phase_history_run), "slems phase history"
        )

    def _phase_history_sources(self) -> dict[str, tuple[str, float]]:
        """Key -> entity and the factor to W with the sign of the live readings (+ import)."""
        sign = -1.0 if self._config.get(CONF_GRID_POWER_INVERTED, False) else 1.0
        sources = {f"phase_{i}": (self._config[key], sign) for i, key in enumerate(CONF_GRID_PHASE_ENTITIES)}
        registry = er.async_get(self.hass)
        for battery in self.batteries:
            # SLEMS' own sensors: the AC power (+ discharge), else the battery power (+ charge).
            for key, factor in (("ac_power", -1.0), ("battery_power", 1.0)):
                if entity_id := registry.async_get_entity_id("sensor", DOMAIN, f"{battery.subentry_id}_{key}"):
                    sources[battery.subentry_id] = (entity_id, factor)
                    break
        for consumer in self.consumers:
            if consumer.included_in_meter:
                sources[consumer.subentry_id] = (consumer.power_entity_id, 1.0)
                if consumer.phases_entity_id:
                    sources[f"active_{consumer.subentry_id}"] = (consumer.phases_entity_id, 1.0)
        for i, entity_id in enumerate(entity_ids(self._config.get(CONF_PV_POWER_ENTITY))):
            sources[f"pv_{i}"] = (entity_id, 1.0)
        return {
            key: (entity_id, factor if key.startswith("active_") else factor * watts_factor(self.hass.states.get(entity_id)))
            for key, (entity_id, factor) in sources.items()
        }

    async def _async_load_phase_history(self, run: int) -> None:
        """Replay the last ``HISTORY_S`` of the recorder into the phase statistics, an hour at a time."""
        sources = self._phase_history_sources()
        entity_list = sorted({entity_id for entity_id, _ in sources.values()})
        end = time.time() // REPLAY_STEP_S * REPLAY_STEP_S
        start = end - HISTORY_S
        status: dict[str, Any] = {"started": dt_util.utcnow().isoformat(), "hours": 0, "error": None}
        status["sources"] = {key: entity_id for key, (entity_id, _) in sources.items()}
        self.phase_history_status = status
        try:
            while start < end and run == self._phase_history_run:
                chunk_end = min(end, start + 3600)
                rows = await get_instance(self.hass).async_add_executor_job(
                    partial(
                        get_significant_states,
                        self.hass,
                        dt_util.utc_from_timestamp(start),
                        dt_util.utc_from_timestamp(chunk_end),
                        entity_list,
                        include_start_time_state=True,
                        significant_changes_only=False,
                        minimal_response=True,
                        no_attributes=True,
                        compressed_state_format=True,
                    )
                )
                if run != self._phase_history_run:
                    return
                series = {key: _history_series(rows.get(entity_id) or [], factor) for key, (entity_id, factor) in sources.items()}
                replay(
                    self.phase_model,
                    start,
                    chunk_end,
                    [series[f"phase_{i}"] for i in range(3)],
                    {device: series[device] for device in self.phase_devices if device in series},
                    [s for key, s in series.items() if key.startswith("pv_")],
                    {key.removeprefix("active_"): s for key, s in series.items() if key.startswith("active_")},
                )
                start = chunk_end
                status["hours"] += 1
                # Let the event loop run between the hours.
                await asyncio.sleep(0)
        except Exception as err:  # noqa: BLE001
            status["error"] = repr(err)
            _LOGGER.warning("Phase history not available: %s", err, exc_info=True)
        finally:
            if run == self._phase_history_run:
                self._phase_history_loading = False
        if run == self._phase_history_run:
            status["finished"] = dt_util.utcnow().isoformat()
            _LOGGER.info("Phase statistics from %s hours of history", status["hours"])
            self.phase_model.evaluate()
            self._phase_changed()

    def _observe_netting(self) -> None:
        imported = self._config.get(CONF_GRID_IMPORT_ENERGY_ENTITY)
        exported = self._config.get(CONF_GRID_EXPORT_ENERGY_ENTITY)
        if not imported or not exported:
            return
        found = len(self.netting.per_phase)
        self.netting.observe(
            time.time(),
            (imported, exported),
            state_as_kwh(self.hass.states.get(imported)),
            state_as_kwh(self.hass.states.get(exported)),
            self.grid_quarters,
        )
        if len(self.netting.per_phase) != found:
            self._store.async_delay_save(self._data_to_store, STORAGE_SAVE_DELAY_S)

    @callback
    def relearn_phase(self, subentry_id: str) -> None:
        """Learn the phase of a battery or consumer anew (its button): forgets it and starts the
        statistics anew from the recorder's history."""
        self.phase_learner(subentry_id).relearn()
        if self.phases_measured:
            self._load_phase_history()
        self._phase_changed()

    @callback
    def choose_phase(self, subentry_id: str, phase: Phase) -> None:
        """The user's answer to a changed or confirmed phase; another than measured counts as chosen by hand."""
        learner = self.phase_learner(subentry_id)
        learner.choose(phase, manual=phase not in (learner.changed_to, learner.confirm))
        self._phase_changed()

    def _phase_changed(self) -> None:
        self._store.async_delay_save(self._data_to_store, STORAGE_SAVE_DELAY_S)
        if self.data is not None:
            self.problems.update(time.monotonic(), self.data)
            self.async_update_listeners()

    async def _async_update_data(self) -> SystemSnapshot:
        config = self._config
        now = time.monotonic()
        snapshot = SystemSnapshot(
            consumer_configs={c.subentry_id: c for c in self.consumers},
            control_disabled=frozenset(self.consumer_control_disabled),
        )

        grid = self.grid_power_w()
        snapshot.grid_power_w = grid
        self._filter_grid(snapshot, now)

        for consumer in self.consumers:
            snapshot.consumers[consumer.subentry_id] = read_consumer_state(
                self.hass, consumer
            )
            # Learned all the time, used when switched on (effective_consumer).
            command = (
                self.controller.consumer_command(consumer.subentry_id)
                if self.settings.operating_mode is OperatingMode.ACTIVE
                else None
            )
            state = snapshot.consumers[consumer.subentry_id]
            learner = self.consumer_learners[consumer.subentry_id]
            full_power = self._full_power_w(consumer)
            since = self.controller.consumer_command_since(consumer.subentry_id)
            response = self.controller.consumer_response_s(consumer.subentry_id, on=True) or 0.0
            settled = since is None or now - since >= max(CONSUMER_SETTLE_S, 2 * response)
            full_command = settled and is_full_command(consumer, command, full_power)
            learner.update(now, command is not None and command > 0, state.power_w, full_command)
            if thermal := self.thermal_learners.get(consumer.subentry_id):
                thermal.update(
                    now,
                    command is not None and command > 0,
                    state.power_w,
                    state.temperature_c,
                    learner.nominal_w or full_power,
                    full_command,
                    state.temperatures_c,
                )
            if cooling := self.cooling_learners.get(consumer.subentry_id):
                settings = self.consumer_targets[consumer.subentry_id]
                cooling.use_sensor(settings.sensor.value)
                cooling.update(
                    now,
                    dt_util.now(),
                    target_temperature(settings, state.temperature_c, state.temperatures_c),
                    heating=state.power_w is not None and state.power_w >= RUNNING_W,
                )
            self._count_target(consumer, state, now, command is not None and command > 0)

        forecast_entries = list(config.get(CONF_PV_FORECAST_ENTRIES) or [])
        # The forecasts of own PV modules are part of the installation's PV.
        own_entries = {
            battery.subentry_id: entry_id
            for battery in self.batteries
            if (entry_id := battery.own_pv_forecast_entry) and battery.driver.capabilities.own_pv
        }
        forecast_entries += [e for e in dict.fromkeys(own_entries.values()) if e not in forecast_entries]
        snapshot.own_pv_forecasts = {}
        if forecast_entries:
            snapshot.pv_forecast = await async_get_pv_forecast(self.hass, forecast_entries)
            for subentry_id, entry_id in own_entries.items():
                if (own := await async_get_pv_forecast(self.hass, [entry_id])) is not None:
                    snapshot.own_pv_forecasts[subentry_id] = own
            if snapshot.pv_forecast is not None:
                wall_now = dt_util.now()
                self.pv_accuracy.record_forecast(
                    wall_now.date(), energy_on_day(snapshot.pv_forecast, wall_now.date()), wall_now
                )
        snapshot.consumption_forecast = self.forecaster.forecast
        if weather := config.get(CONF_WEATHER_ENTITY):
            snapshot.outdoor_temperature_c = _weather_temperature(self.hass.states.get(weather))
        outdoor = self._live_outdoor_c(snapshot)
        for subentry_id, outdoor_learner in self.outdoor_power.items():
            if outdoor_learner.update(now, snapshot.consumers[subentry_id].power_w, outdoor):
                self._store.async_delay_save(self._data_to_store, STORAGE_SAVE_DELAY_S)

        # The batteries are read in parallel (each has its own connection), so
        # a slow one does not delay the others. A failing battery must not take
        # the whole system down: it is simply missing from the snapshot and its
        # entities become unavailable.
        readings = await asyncio.gather(
            *(self._async_read_battery(battery, now) for battery in self.batteries)
        )
        for battery, telemetry in zip(self.batteries, readings, strict=True):
            if telemetry is None:
                continue
            if telemetry.extra.get("inverter_state") == "ota_upgrade":
                # A firmware update is running: no further Modbus traffic.
                await self.async_pause_communication(battery, "firmware_update")
                continue
            snapshot.batteries[battery.subentry_id] = telemetry
            self._update_efficiency(battery, telemetry, now)
            if telemetry.ac_power_w is not None:
                battery.loss_curve.add(telemetry.ac_power_w, telemetry.power_w)
            battery.soc_window.update(telemetry.soc_pct, battery.limits)
            battery.delivery.update_stop(telemetry.soc_pct, telemetry.grid_side_power_w)
            battery.delivery.update_full(
                telemetry.soc_pct, telemetry.grid_side_power_w, telemetry.extra.get("max_cell_voltage")
            )
            battery.measured_power_w = telemetry.grid_side_power_w
            battery.measured_pv_w = telemetry.pv_w
            battery.measured_at = now
            battery.top_taper.update(telemetry.extra.get("max_cell_voltage"))
            battery.capacity_learner.update(
                now, telemetry.soc_pct, telemetry.power_w, battery.driver.capabilities.capacity_wh
            )
            battery.power_limits = self._power_limits(battery, telemetry)
            if (
                battery.trial is not None
                and (observation := battery.trial.observation) is not None
                and observation.quiet_ok
                and (spec := battery.driver.trial_spec) is not None
            ):
                battery.power_limits = observation.cap(battery.power_limits, spec.small_w)
            self._check_delivery(battery, telemetry, now)
            self._update_observation(battery, telemetry, now)
        self._set_pv_power(snapshot)
        self._integrate_pv(self._forecast_pv_power_w(snapshot), now)
        # After all batteries were read: the balancing share of the surplus
        # depends on the other batteries.
        for battery in self.batteries:
            self._update_cells(battery, snapshot, now)
            self._update_self_test(battery, snapshot, now)
        self._update_full_charge(snapshot, now)
        self._store.async_delay_save(self._data_to_store, STORAGE_SAVE_DELAY_S)

        snapshot.unread_battery_power_w = self._unread_battery_power_w(snapshot, now)
        self._house_hold.check(snapshot, now)
        self._learn_morning_gap(snapshot, now)
        snapshot.saturated = self.controller.saturated
        snapshot.resting = self.controller.resting
        snapshot.running_on_its_own = self.controller.running_on_its_own
        self.controller.observe_consumers(snapshot)
        mode = self.settings.operating_mode
        if mode is OperatingMode.ACTIVE and self.data is not None:
            # In active mode only the controller plans (it owns the state of
            # distribution and runtimes); keep showing its latest result.
            for name in (
                "available_power_w",
                "expected_surplus_wh",
                "feed_in_limit_w",
                "feed_in_limit_reason",
                "peak_shaving_limit_w",
                "pv_correction",
                "pv_elapsed_share",
                "day_plan",
                "day_plan_tomorrow",
                "allocation",
                "distribution",
                "night_discharge",
                "feed_in_cap",
                "feed_in_cap_buffer_pct",
                "feed_in_cap_buffer_source",
                "feed_in_cap_buffer_days",
            ):
                setattr(snapshot, name, getattr(self.data, name))
        elif mode is not OperatingMode.OFF:
            self.plan(snapshot, dt_util.now(), now)
        self._check_cap_exceeded(snapshot, now)
        self._observe_phases(snapshot, now)
        self._observe_netting()
        # Runs after the new data has been stored.
        self.hass.loop.call_soon(self.controller.request)
        self.problems.update(now, snapshot)
        return snapshot

    def _check_cap_exceeded(self, snapshot: SystemSnapshot, now: float) -> None:
        settings = self.settings
        grid = snapshot.grid_power_w
        if (
            settings.feed_in_cap
            and settings.operating_mode is not OperatingMode.OFF
            and grid is not None
            and -grid > settings.feed_in_cap_limit_w
        ):
            if self.cap_exceeded_since is None:
                self.cap_exceeded_since = now
        else:
            self.cap_exceeded_since = None

    def _pv_power_w(self) -> float | None:
        """Power of all PV systems; one that reports nothing (e.g. a micro inverter at night) counts as 0,
        None only if none reports."""
        values = [
            watts
            for entity_id in entity_ids(self._config.get(CONF_PV_POWER_ENTITY))
            if (watts := state_as_watts(self.hass.states.get(entity_id))) is not None
        ]
        return sum(values) if values else None

    def _set_pv_power(self, snapshot: SystemSnapshot) -> None:
        """PV of the PV systems plus the own PV modules of the batteries read."""
        own = [pv for telemetry in snapshot.batteries.values() if (pv := telemetry.pv_w) is not None]
        pv = self._pv_power_w()
        snapshot.own_pv_power_w = sum(own)
        snapshot.pv_power_w = pv if not own else (pv or 0.0) + sum(own)

    def _forecast_pv_power_w(self, snapshot: SystemSnapshot) -> float | None:
        """PV the PV forecast covers: without own PV modules that have no forecast entry."""
        if snapshot.pv_power_w is None:
            return None
        uncovered = sum(
            telemetry.pv_w
            for battery in self.batteries
            if not battery.own_pv_forecast_entry
            and (telemetry := snapshot.batteries.get(battery.subentry_id)) is not None
            and telemetry.pv_w is not None
        )
        return snapshot.pv_power_w - uncovered

    def _integrate_pv(self, pv_power_w: float | None, now: float) -> None:
        """Add up today's PV energy the forecast covers from the live values
        (restarts at midnight); the actual value of the PV correction and accuracy."""
        today = dt_util.now().date()
        day, energy, last_time, last_power = self._pv_day or (today, 0.0, None, None)
        if day != today:
            if self._pv_complete:
                self.pv_accuracy.record_actual(day, energy)
            day, energy, last_time, last_power = today, 0.0, None, None
            self._pv_complete = True
        if last_time is not None and last_power is not None and now - last_time < 300:
            energy += last_power * (now - last_time) / 3600
        self._pv_day = (day, energy, now, pv_power_w)

    async def _async_restore_pv_today(self) -> None:
        """Take today's PV energy from the recorder so a restart loses nothing.

        Without statistics for the PV sensor the integration only becomes
        complete at the next midnight.
        """
        pv_entities = [
            *entity_ids(self._config.get(CONF_PV_POWER_ENTITY)),
            *(b.own_pv_entity for b in self.batteries if b.own_pv_entity and b.own_pv_forecast_entry),
        ]
        if not pv_entities:
            return
        wall_now = dt_util.now()
        try:
            means = await async_statistic_means(
                self.hass,
                list(pv_entities),
                dt_util.start_of_local_day(dt_util.as_local(wall_now)),
                wall_now,
                "5minute",
            )
        except Exception:  # noqa: BLE001
            _LOGGER.debug("PV statistics of today not available", exc_info=True)
            return
        energy = 0.0
        covered_until = None
        for pv_entity in pv_entities:
            part, until = energy_from_means(means.get(pv_entity, {}), timedelta(minutes=5))
            if until is None:
                # One PV system without statistics: today is only known from the next midnight.
                return
            energy += part
            covered_until = until if covered_until is None else min(covered_until, until)
        assert covered_until is not None
        # Bridge the minutes since the last statistics period with the current value.
        pv_now = self._forecast_pv_power_w(self.data) if self.data is not None else None
        gap_h = max(0.0, (dt_util.now() - covered_until).total_seconds() / 3600)
        energy += (pv_now or 0.0) * gap_h
        self._pv_day = (wall_now.date(), energy, time.monotonic(), pv_now)
        self._pv_complete = True
        _LOGGER.debug("PV energy today restored from statistics: %.0f Wh", energy)

    @property
    def _pv_today_wh(self) -> float | None:
        """PV energy today, None if it is not known since midnight."""
        if self._pv_day is None or not self._pv_complete:
            return None
        return self._pv_day[1]

    def _update_cells(self, battery: BatteryRuntime, snapshot: SystemSnapshot, now: float) -> None:
        """Cell monitor and one step of an active balancing run."""
        telemetry = snapshot.batteries.get(battery.subentry_id)
        extra = telemetry.extra if telemetry else {}
        max_cell = extra.get("max_cell_voltage")
        min_cell = extra.get("min_cell_voltage")
        power = telemetry.power_w if telemetry else None
        wall = dt_util.utcnow().timestamp()
        if telemetry is not None:
            battery.cell_monitor.observe_full(
                max_cell, telemetry.soc_pct, wall, bms_full=battery.delivery.bms_full
            )
        balancer = battery.balancer
        if balancer is None:
            if telemetry is not None:
                battery.cell_monitor.update(
                    now, max_cell, min_cell, power, wall, telemetry.soc_pct
                )
            return
        # The run measures itself.
        battery.cell_monitor.pause()
        if not battery.enabled:
            self.end_balancing(battery, "cancelled")
            return
        if (
            self.settings.operating_mode is not OperatingMode.ACTIVE
            or battery.leaving_until is not None
            or battery.communication_paused
        ):
            # Commands are only sent in active mode, after the ramp-out and
            # while the communication is not paused.
            balancer.pause()
            battery.balancing_power_w = None
            return
        if telemetry is None:
            # As in Omnibattery: invalid telemetry ends the run at once.
            self.end_balancing(battery, "telemetry")
            return
        previous_phase = balancer.phase
        previous_delta = balancer.last_delta_mv
        step = balancer.step(
            now, wall, max_cell, min_cell, power, self._balancing_surplus_w(battery, snapshot)
        )
        if balancer.last_delta_mv is not None and balancer.last_delta_mv != previous_delta:
            battery.cell_monitor.record(balancer.last_delta_mv, wall, "balancing")
        # Power and temperature limits apply, the SoC window does not (the run
        # needs the top of the charge).
        limits = self._power_limits(battery, telemetry, use_soc_window=False)
        battery.balancing_power_w = max(-limits.discharge_w, min(limits.charge_w, step.power_w))
        if step.phase is BalancingPhase.DONE:
            self.end_balancing(battery, "done")
        elif step.phase is BalancingPhase.ERROR:
            self.end_balancing(battery, balancer.error or "error")
        elif step.phase is not previous_phase:
            self._store.async_delay_save(self._data_to_store, BALANCING_SAVE_DELAY_S)

    def _update_full_charge(self, snapshot: SystemSnapshot, now: float) -> None:
        """Battery due for its regular full charge, and the rest once it got full."""
        for battery in self.batteries:
            last_full = battery.cell_monitor.last_full
            previous = self._last_full_seen.get(battery.subentry_id, last_full)
            if last_full != previous and battery.subentry_id == self.full_charge_battery:
                # Full now: no power for a moment, so the top cell delta is measured.
                self._full_charge_rest[battery.subentry_id] = now + FULL_CHARGE_REST_S
            self._last_full_seen[battery.subentry_id] = last_full
        self._full_charge_rest = {k: v for k, v in self._full_charge_rest.items() if v > now}
        if not self.settings.regular_full_charge:
            self.full_charge_battery = None
            return
        candidates = [
            FullChargeCandidate(battery.subentry_id, battery.name, battery.cell_monitor.last_full)
            for battery in self.batteries
            if battery.plannable
            and battery.driver.capabilities.controllable
            and battery.subentry_id in snapshot.batteries
        ]
        self.full_charge_battery = due_battery(
            candidates, dt_util.utcnow().timestamp(), self.settings.full_charge_interval_days
        )

    def _power_limits(
        self,
        battery: BatteryRuntime,
        telemetry: BatteryTelemetry,
        *,
        use_soc_window: bool = True,
        plan: bool = False,
    ) -> PowerLimits:
        """Limits right now; ``plan``: for the plans ahead, where the own PV of
        the hour decides (charging not limited by the PV now, discharging by
        the whole output)."""
        caps = battery.driver.capabilities
        own_pv_w = (0.0 if plan else telemetry.pv_w) if caps.own_pv else None
        return power_limits(
            caps.max_charge_power_w,
            caps.max_discharge_power_w,
            battery.limits,
            battery.soc_window,
            telemetry.extra.get("internal_temperature"),
            self.settings.temperature(),
            # Read-only batteries follow their own logic.
            use_soc_window=use_soc_window and caps.controllable,
            full_charge=battery.subentry_id == self.full_charge_battery,
            # Read-only batteries charge by their own logic.
            top_taper=battery.top_taper.active and caps.controllable,
            bms_limit_w=battery.delivery.bms_limit_w,
            bms_full=battery.delivery.bms_full,
            own_pv_w=own_pv_w,
            ac_charge=caps.ac_charge or plan,
            max_output_w=caps.max_output_w,
            full=telemetry.soc_pct is not None and battery_full(telemetry.soc_pct, 100.0),
        )

    def _check_delivery(
        self, battery: BatteryRuntime, telemetry: BatteryTelemetry, now: float
    ) -> None:
        """Judge whether the battery delivers the commanded power (see delivery_monitor)."""
        battery.delivery.tick(now)
        if (
            self.settings.operating_mode is not OperatingMode.ACTIVE
            or not battery.driver.capabilities.controllable
            or not battery.participating
            or battery.leaving_until is not None
        ):
            return
        command = self.controller.command_state(battery.subentry_id)
        if command is None:
            return
        commanded, since = command
        caps = battery.driver.capabilities
        if caps.own_pv:
            commanded = feasible_command_w(
                commanded, telemetry.pv_w, ac_charge=caps.ac_charge, max_output_w=caps.max_output_w
            )
        action = battery.delivery.observe(
            now,
            commanded,
            since,
            telemetry.grid_side_power_w,
            telemetry.soc_pct,
            telemetry.extra.get("max_cell_voltage"),
        )
        if action is Action.WAKE:
            self.controller.force_refresh(battery.subentry_id)
        elif action is Action.EXCLUDE:
            self.controller.request()
        elif action in (Action.BMS_STOP, Action.BMS_FULL):
            battery.power_limits = self._power_limits(battery, telemetry)
            self.controller.request()

    def _balancing_surplus_w(self, battery: BatteryRuntime, snapshot: SystemSnapshot) -> float:
        """PV surplus a balancing battery may take (before the other batteries).

        What would be fed in if neither the batteries in normal operation nor
        the controlled consumers nor this battery took power.
        """
        if snapshot.grid_without_batteries_filtered_w is None:
            return 0.0
        # The batteries in normal operation are not part of the filtered power.
        own_w = (
            0.0
            if battery.participating
            or (telemetry := snapshot.batteries.get(battery.subentry_id)) is None
            else telemetry.grid_side_power_w or 0.0
        )
        return (
            -snapshot.grid_without_batteries_filtered_w
            + own_w
            + snapshot.controlled_consumer_power_w()
        )

    async def async_pause_communication(self, battery: BatteryRuntime, reason: str) -> None:
        """Stop all communication with a battery for the configured time.

        A manual pause first hands the battery back to its own logic; during a
        detected firmware update nothing is sent any more. A running pause is
        extended by the configured time (counted from its end).
        """
        now = dt_util.utcnow().timestamp()
        if battery.paused_until is not None and battery.paused_until > now:
            battery.paused_until += self.settings.communication_pause_min * 60
            if reason == "firmware_update":
                battery.pause_reason = reason
            _LOGGER.info("Battery %s: communication pause extended", battery.name)
            self._store.async_delay_save(self._data_to_store, BALANCING_SAVE_DELAY_S)
            self.async_update_listeners()
            return
        if reason == "manual" and battery.driver.capabilities.controllable:
            await self.controller.async_release([battery])
        await battery.driver.close()
        battery.paused_until = (
            dt_util.utcnow().timestamp() + self.settings.communication_pause_min * 60
        )
        battery.pause_reason = reason
        battery.unreadable_since = None
        if battery.trial is not None:
            # A firmware update shows in the device information: compared
            # before it is controlled again (the pause ends by the clock,
            # before the next poll resumes it).
            battery.device_info_checked = False
        # Nothing is sent while paused: an outstanding release is given up (the
        # user paused on purpose, e.g. another integration holds the connection).
        battery.release_pending_since = None
        if reason == "firmware_update":
            _LOGGER.warning(
                "Battery %s reports a firmware update, communication paused for %d minutes",
                battery.name,
                self.settings.communication_pause_min,
            )
        self.controller.request()
        self._store.async_delay_save(self._data_to_store, BALANCING_SAVE_DELAY_S)
        self.async_update_listeners()

    @callback
    def resume_communication(self, battery: BatteryRuntime) -> None:
        """Communicate again (reconnects with the next poll)."""
        if battery.paused_until is not None:
            _LOGGER.info("Battery %s: communication resumed", battery.name)
        battery.paused_until = None
        battery.pause_reason = None
        if battery.trial is not None:
            battery.device_info_checked = False
        self._store.async_delay_save(self._data_to_store, BALANCING_SAVE_DELAY_S)

    def _unread_battery_power_w(self, snapshot: SystemSnapshot, now: float) -> float | None:
        """Last measured power of the controllable batteries missing from
        ``snapshot``; None if an enabled one was not read for UNREAD_BATTERY_HOLD_S.

        A disabled battery never makes it unknown: until it is handed back to
        its own logic it may hold its last set point and keeps its last power;
        once released it counts 0 W, in release state AUTO only after
        UNREAD_BATTERY_HOLD_S (it regulates on its own and likely goes on
        like before a short gap, see ``unread_power_unknown``).
        """
        total = 0.0
        for battery in self.batteries:
            if battery.subentry_id in snapshot.batteries or not battery.driver.capabilities.controllable:
                continue
            held = battery.measured_at is not None and now - battery.measured_at <= UNREAD_BATTERY_HOLD_S
            if battery.enabled:
                if not held:
                    return None
            elif self.disabled_and_released(battery) and not (
                held and battery.driver.release_state is ReleaseState.AUTO
            ):
                continue
            # Without its own PV (unknown now) only its output counts.
            total += (battery.measured_power_w or 0.0) - (battery.measured_pv_w or 0.0)
        return total

    def disabled_and_released(self, battery: BatteryRuntime) -> bool:
        """Disabled and handed back to its own logic (the release confirmed)."""
        return (
            not battery.enabled
            and battery.leaving_until is None
            and battery.release_pending_since is None
            and not self.controls(battery)
        )

    def unread_power_unknown(self, battery: BatteryRuntime) -> bool:
        """Counts 0 W in the balance while unread although it may charge or
        discharge: disabled and released to its own logic (release state AUTO)."""
        return (
            battery.driver.capabilities.controllable
            and battery.driver.release_state is ReleaseState.AUTO
            and self.disabled_and_released(battery)
        )

    async def _async_read_battery(self, battery: BatteryRuntime, now: float) -> BatteryTelemetry | None:
        """Read one battery (an outstanding release first); None if not read."""
        if battery.paused_until is not None:
            if battery.communication_paused:
                return None
            self.resume_communication(battery)
        if battery.release_pending_since is not None and not self.controls(battery):
            await self.async_release_battery(battery)
        read_start = time.monotonic()
        try:
            telemetry = await battery.driver.read_telemetry(complete=battery.self_test is not None)
        except BatteryDriverError as err:
            _LOGGER.debug("Battery %s unavailable: %s", battery.name, err)
            if battery.unreadable_since is None:
                battery.unreadable_since = now
            return None
        finally:
            battery.telemetry_read_s = round(time.monotonic() - read_start, 3)
        battery.unreadable_since = None
        if telemetry.extra.get("inverter_state") != "ota_upgrade":
            await self._async_read_device_info(battery, now)
        battery.trial_registers = None
        if battery.self_test is not None and battery.self_test.registers_due(now):
            try:
                battery.trial_registers = await battery.driver.read_control_registers()
            except BatteryDriverError as err:
                _LOGGER.debug("Battery %s: registers of the trial not read: %s", battery.name, err)
                battery.trial_registers = {r.address: None for r in battery.self_test.trial.registers}  # type: ignore[union-attr]
        battery.observed_registers = None
        observation = battery.trial.observation if battery.trial is not None else None
        if (
            observation is not None
            and battery.self_test is None
            and battery.device_info_checked
            and observation.registers_due(now, self._command_direction(battery), not self.controls(battery))
        ):
            count = battery.driver.write_count
            try:
                registers = await battery.driver.read_control_registers()
            except BatteryDriverError as err:
                _LOGGER.debug("Battery %s: registers not read for its observation: %s", battery.name, err)
            else:
                battery.observed_registers = (registers, battery.driver.write_count == count)
        return telemetry

    def _command_direction(self, battery: BatteryRuntime) -> str | None:
        """Direction of the latest command while SLEMS controls the battery."""
        state = self.controller.command_state(battery.subentry_id) if self.controls(battery) else None
        if state is None or abs(state[0]) < 1:
            return None
        return "charge" if state[0] > 0 else "discharge"

    def _update_observation(self, battery: BatteryRuntime, telemetry: BatteryTelemetry, now: float) -> None:
        """One step of the observation after a firmware update (see trial.py)."""
        trial = battery.trial
        observation = trial.observation if trial is not None else None
        read, battery.observed_registers = battery.observed_registers, None
        if trial is None or observation is None or battery.self_test is not None:
            return
        spec = battery.driver.trial_spec
        assert spec is not None
        wall = dt_util.utcnow().timestamp()
        registers, quiet_write = read if read is not None else (None, False)
        if not observation.quiet_ok:
            if registers is None or (check := observation.quiet(spec, now, registers)) is None:
                return
            if check.outcome is Outcome.OK:
                _LOGGER.info("Battery %s: registers unchanged at rest after the firmware update, controlled under observation", battery.name)
                self.controller.request()
            elif check.outcome is Outcome.FAILED:
                trial.observation_failed(wall, quiet=True)
                self._observation_found(battery, {"reason": "quiet", "check": check.as_dict()})
            self._store.async_delay_save(self._data_to_store, BALANCING_SAVE_DELAY_S)
            return
        problem = None
        if self.controls(battery):
            state = self.controller.command_state(battery.subentry_id)
            problem = observation.observe_power(
                now, state[0] if state else None, telemetry.grid_side_power_w, spec.small_w
            )
            if problem is None and registers is not None and quiet_write:
                problem = observation.observe_registers(spec, now, registers, battery.driver.written_values())
            if observation.release_pending:
                observation.taken_over()
        elif registers is not None and observation.release_pending:
            problem = observation.observe_release(spec, now, registers)
        if problem is None and battery.not_responding:
            problem = {"reason": "not_delivering"}
        if problem is not None:
            trial.observation_failed(wall)
            if self.controls(battery) or battery.leaving_until is not None:
                # Handed back to its own logic until its trial.
                trial.wrote = True
                battery.release_pending_since = time.monotonic()
            self._observation_found(battery, problem)
            self.controller.request()
        elif observation.complete(wall):
            trial.observation_passed()
            _LOGGER.info("Battery %s: observation after the firmware update passed", battery.name)
        self._store.async_delay_save(self._data_to_store, BALANCING_SAVE_DELAY_S)

    def _observation_found(self, battery: BatteryRuntime, problem: dict) -> None:
        _LOGGER.warning("Battery %s behaves differently after the firmware update: %s", battery.name, problem)
        self.config_entry.async_create_background_task(
            self.hass,
            self.problems.async_notify_observation(battery, problem),
            "slems observation notification",
        )

    async def _async_read_device_info(self, battery: BatteryRuntime, now: float) -> None:
        """Read the device information once after connecting and then every few hours;
        for a battery with a trial compared with the one before (a firmware update)."""
        if (
            battery.device_info_checked
            and battery.device_info_read is not None
            and now - battery.device_info_read < DEVICE_INFO_INTERVAL_S
        ):
            return
        battery.device_info_read = now
        try:
            info = await battery.driver.read_device_info()
        except BatteryDriverError as err:
            _LOGGER.debug("Battery %s: device information not read: %s", battery.name, err)
            return
        battery.device_info = info or battery.device_info
        if battery.trial is None:
            return
        controlled = self.controls(battery)
        if battery.trial.device_info_read(info, dt_util.utcnow().timestamp()):
            _LOGGER.warning(
                "Battery %s: device information changed (%s), observed",
                battery.name,
                ", ".join(f"{key} {old} -> {new}" for key, (old, new) in battery.trial.firmware_change.items()),
            )
            if controlled:
                # It still holds a set point of SLEMS: handed back to its own logic.
                battery.trial.wrote = True
                battery.release_pending_since = time.monotonic()
            self.controller.request()
        battery.device_info_checked = True
        self._store.async_delay_save(self._data_to_store, BALANCING_SAVE_DELAY_S)

    @property
    def self_test_holds(self) -> bool:
        """A self-test measures: the other batteries and the consumers keep their set points."""
        return any(b.self_test is not None and b.self_test.holds_others for b in self.batteries)

    def self_test_blocked(self, battery: BatteryRuntime) -> str | None:
        """Why a self-test of the battery cannot start now (translation key), None if it can."""
        if not battery.driver.capabilities.controllable:
            return "self_test_not_controllable"
        if self.settings.operating_mode is not OperatingMode.ACTIVE:
            return "self_test_requires_active"
        if not battery.enabled:
            return "self_test_disabled"
        if battery.communication_paused:
            return "self_test_communication_paused"
        if battery.balancing_requested:
            return "self_test_balancing"
        if any(b.self_test is not None for b in self.batteries if b is not battery):
            return "self_test_other_running"
        if battery.power_limits is None or battery.unreadable_since is not None or not battery.device_info_checked:
            return "self_test_unreadable"
        return None

    @callback
    def start_self_test(self, battery: BatteryRuntime) -> None:
        """Start a self-test (check ``self_test_blocked`` first)."""
        capabilities = battery.driver.capabilities
        power = self_test_power_w(capabilities.max_charge_power_w, capabilities.max_discharge_power_w)
        limits = battery.power_limits
        battery.self_test = SelfTest(
            step_power_w(power, limits.charge_w if limits else 0.0),
            step_power_w(power, limits.discharge_w if limits else 0.0),
            dt_util.utcnow().timestamp(),
            battery.driver.release_state.value,
            battery.driver.trial_spec if battery.trial is not None and battery.trial.in_trial else None,
        )
        battery.self_test_power_w = None
        battery.self_test_pending = False
        # A discharging battery hands over smoothly, like when it is disabled.
        self.controller.disable_battery(battery)
        self.controller.request()
        self._store.async_delay_save(self._data_to_store, BALANCING_SAVE_DELAY_S)

    @callback
    def end_self_test(self, battery: BatteryRuntime, reason: str | None = None) -> None:
        """End a self-test: early with ``reason`` ("cancelled", ...), or after the release."""
        test = battery.self_test
        if test is None:
            return
        if reason is not None:
            test.stop(reason, dt_util.utcnow().timestamp())
        battery.self_test = None
        battery.self_test_power_w = None
        battery.leaving_until = None
        battery.self_test_last = test.as_dict()
        if test.trial is not None and battery.trial is not None:
            battery.trial.finished(battery.self_test_last, dt_util.utcnow().timestamp())
            _LOGGER.info("Trial of %s: %s", battery.name, battery.trial.status)
        if (phase := test.grid_phase()) is not None and battery.subentry_id in self.phase_model.devices:
            self.phase_learner(battery.subentry_id).observe(phase)
            self._phase_changed()
        if test.result != "cancelled":
            self.config_entry.async_create_background_task(
                self.hass,
                self.problems.async_notify_self_test(battery, test),
                "slems self-test notification",
            )
        _LOGGER.info("Self-test of %s: %s", battery.name, test.result)
        self.controller.request()
        self._store.async_delay_save(self._data_to_store, BALANCING_SAVE_DELAY_S)

    @callback
    def cancel_self_test(self, battery: BatteryRuntime) -> None:
        """Cancel a running self-test, or no longer wait for the one asked for at the setup."""
        battery.self_test_pending = False
        self.end_self_test(battery, "cancelled")
        self._store.async_delay_save(self._data_to_store, BALANCING_SAVE_DELAY_S)

    def _update_self_test(self, battery: BatteryRuntime, snapshot: SystemSnapshot, now: float) -> None:
        """One step of a running self-test, or the start of one asked for at the setup."""
        test = battery.self_test
        wall = dt_util.utcnow().timestamp()
        if test is None:
            due = battery.self_test_pending or (battery.trial is not None and battery.trial.due(wall))
            if due and self.self_test_blocked(battery) is None:
                self.start_self_test(battery)
            return
        if not battery.enabled:
            self.end_self_test(battery, "cancelled")
            return
        if test.phase is SelfTestPhase.DONE:
            self.end_self_test(battery)
            return
        if test.awaiting_release:
            if self.controller.has_override(battery.subentry_id):
                return
            # Released by the controller (None: nothing was sent before); the meter is watched then.
            test.released(self.controller.pop_release_result(battery.subentry_id), wall, now)
            if test.phase is SelfTestPhase.DONE:
                self.end_self_test(battery)
                return
        if self.settings.operating_mode is not OperatingMode.ACTIVE:
            self.end_self_test(battery, "not_active")
            return
        if battery.communication_paused:
            self.end_self_test(battery, "paused")
            return
        if self.controller.status is ControlStatus.GRID_STALE:
            # Nothing is sent, and the meter is the reference.
            self.end_self_test(battery, "grid_stale")
            return
        telemetry = snapshot.batteries.get(battery.subentry_id)
        if telemetry is None:
            if battery.unreadable_since is not None and now - battery.unreadable_since >= SELF_TEST_UNREADABLE_S:
                self.end_self_test(battery, "unreadable")
            return
        others: list[float | None] = []
        for other in self.batteries:
            if other is not battery:
                other_telemetry = snapshot.batteries.get(other.subentry_id)
                others.append(other_telemetry.grid_side_power_w if other_telemetry is not None else None)
        grid = snapshot.grid_power_w
        sample = Sample(
            now,
            None if grid is None else grid - sum(p for p in others if p is not None),
            telemetry.grid_side_power_w,
            telemetry.soc_pct,
            telemetry.extra.get("total_charging_energy"),
            telemetry.extra.get("total_discharging_energy"),
            self._grid_phases_w(),
            tuple(others),
            battery.trial_registers,
        )
        battery.trial_registers = None
        battery.self_test_power_w = test.step(now, sample, ready=battery.leaving_until is None, wall=wall)
        if test.phase is SelfTestPhase.DONE:
            self.end_self_test(battery)

    @callback
    def start_balancing(self, battery: BatteryRuntime) -> None:
        """Start an active balancing run (operating mode active only)."""
        battery.balancer = CellBalancer(
            battery.driver.capabilities.max_charge_power_w, dt_util.utcnow().timestamp()
        )
        if battery.cell_monitor.last is not None:
            battery.balancer.initial_delta_mv = battery.cell_monitor.last.delta_mv
        battery.balancing_power_w = None
        battery.balancing_result = None
        # A discharging battery hands over smoothly, like when it is disabled.
        self.controller.disable_battery(battery)
        self.controller.request()
        self._store.async_delay_save(self._data_to_store, BALANCING_SAVE_DELAY_S)

    @callback
    def end_balancing(self, battery: BatteryRuntime, result: str) -> None:
        """End a balancing run; the battery returns to normal operation."""
        balancer = battery.balancer
        if balancer is None:
            return
        if result == "done":
            _LOGGER.info(
                "Cell balancing of %s finished, cell delta %s mV", battery.name, balancer.last_delta_mv
            )
        elif result != "cancelled":
            _LOGGER.warning("Cell balancing of %s stopped: %s", battery.name, result)
        if result != "cancelled":
            self.config_entry.async_create_background_task(
                self.hass,
                self.problems.async_notify_balancing(
                    battery,
                    result,
                    balancer.initial_delta_mv,
                    balancer.last_delta_mv,
                    dt_util.utcnow().timestamp() - balancer.started_at,
                    balancer.end_reason,
                ),
                "slems balancing notification",
            )
        battery.balancer = None
        battery.balancing_power_w = None
        battery.balancing_result = result
        battery.leaving_until = None
        self.controller.request()
        self._store.async_delay_save(self._data_to_store, BALANCING_SAVE_DELAY_S)

    def _balancing_charge_wh(self, snapshot: SystemSnapshot) -> float:
        """Energy balancing batteries still charge before they reach the top."""
        total = 0.0
        for battery in self.batteries:
            balancer = battery.balancer
            telemetry = snapshot.batteries.get(battery.subentry_id)
            if (
                balancer is None
                or balancer.phase not in (BalancingPhase.PRE_TOP_CHARGE, BalancingPhase.CHARGE)
                or telemetry is None
                or telemetry.soc_pct is None
            ):
                continue
            missing = max(0.0, 100.0 - telemetry.soc_pct) / 100
            total += missing * battery.capacity_wh / battery.efficiency.one_way
        return total

    @staticmethod
    def _update_efficiency(
        battery: BatteryRuntime, telemetry: BatteryTelemetry, now: float
    ) -> None:
        extra = telemetry.extra
        battery.efficiency.update(
            now,
            telemetry.grid_side_power_w,
            telemetry.soc_pct,
            extra.get("total_charging_energy"),
            extra.get("total_discharging_energy"),
        )

    def _battery_group(self, snapshot: SystemSnapshot, *, planning: bool = False) -> BatteryGroup | None:
        """All plannable batteries as one; their powers are the limits right now.

        ``planning``: the powers for the plans ahead, like the feed-in cap
        (without the SoC window, full batteries included), so a battery full
        or blocked right now still charges and discharges in later hours.
        """
        energy = capacity = max_charge = max_discharge = weighted_eff = 0.0
        min_soc = full_soc = 0.0
        all_full = True
        for battery in self.batteries:
            telemetry = snapshot.batteries.get(battery.subentry_id)
            if not battery.plannable or telemetry is None or telemetry.soc_pct is None:
                continue
            caps = battery.driver.capabilities
            if planning:
                limits = self._power_limits(battery, telemetry, use_soc_window=False, plan=True)
            else:
                limits = battery.power_limits or self._power_limits(battery, telemetry)
            energy += telemetry.soc_pct * battery.capacity_wh
            capacity += battery.capacity_wh
            max_discharge += limits.discharge_w
            weighted_eff += battery.efficiency.one_way * battery.capacity_wh
            if caps.controllable:
                min_soc += battery.limits.min_soc_pct * battery.capacity_wh
                # Due for its full charge: may charge above its maximum SoC.
                maximum = 100.0 if battery.subentry_id == self.full_charge_battery else battery.limits.max_soc_pct
            else:
                maximum = 100.0
            full_soc += maximum * battery.capacity_wh
            # A full battery takes nothing, whatever its power limit says.
            if battery_full(telemetry.soc_pct, maximum) or battery.delivery.bms_full:
                if planning:
                    max_charge += limits.charge_w
                continue
            all_full = False
            max_charge += limits.charge_w
        if not capacity:
            return None
        return BatteryGroup(
            soc_pct=energy / capacity,
            capacity_wh=capacity,
            max_charge_w=max_charge,
            max_discharge_w=max_discharge,
            charge_efficiency=weighted_eff / capacity,
            min_soc_pct=min_soc / capacity,
            full_soc_pct=full_soc / capacity,
            all_full=all_full,
        )

    def plan(
        self,
        snapshot: SystemSnapshot,
        wall_now: datetime,
        now: float,
        *,
        previous_total_w: float | None = None,
        gain: float = 1.0,
    ) -> None:
        """Compute allocation and distribution for ``snapshot`` (in place).

        With ``previous_total_w`` the total battery power only moves by
        ``gain`` of the difference towards the allocation (damped correction).
        """
        if snapshot.grid_without_batteries_filtered_w is None:
            return
        controlled_w = measured_controlled_w = snapshot.controlled_consumer_power_w()
        seen_w: dict[str, float] = {}
        beyond_w: dict[str, float] = {}
        if previous_total_w is not None:
            # Controller cycle: the consumers as the grid meter shows them, like
            # the batteries (commands still on their way count as not yet done),
            # at most with their command while switched off or turned down: the
            # rest is house load until they really draw less.
            for subentry_id, state in snapshot.consumers.items():
                if not snapshot.is_controllable_now(subentry_id):
                    continue
                seen = self.controller.consumer_power_seen(subentry_id, state.power_w, now)
                seen_w[subentry_id] = self.controller.consumer_power_counted(subentry_id, seen)
                beyond_w[subentry_id] = seen - seen_w[subentry_id]
            controlled_w = sum(seen_w.values())
        enabled_battery_w = sum(
            power
            for battery in self.batteries
            if battery.participating
            and (telemetry := snapshot.batteries.get(battery.subentry_id)) is not None
            and (power := telemetry.grid_side_power_w) is not None
        )
        # A balancing battery counts like a load while it charges (the other
        # batteries cover it), but its discharge is fed in and not stored by
        # the other batteries.
        balancing_discharge_w = sum(
            min(0.0, power)
            for battery in self.batteries
            if battery.balancing_requested
            and not battery.participating
            and (telemetry := snapshot.batteries.get(battery.subentry_id)) is not None
            and (power := telemetry.grid_side_power_w) is not None
        )
        snapshot.available_power_w = (
            -snapshot.grid_without_batteries_filtered_w + controlled_w + balancing_discharge_w
        )
        house = snapshot.house_power_w
        load = None if house is None else house - measured_controlled_w
        consumption = snapshot.consumption_forecast
        pv_forecast = snapshot.pv_forecast
        if pv_forecast is not None:
            snapshot.pv_correction = pv_correction(pv_forecast, wall_now, self._pv_today_wh)
            snapshot.pv_elapsed_share = pv_elapsed_share(pv_forecast, wall_now)
            pv_forecast = self._pv_native(snapshot, wall_now)
        # Balancing batteries take the PV surplus first.
        balancing_wh = self._balancing_charge_wh(snapshot)
        snapshot.expected_surplus_wh = expected_surplus_wh(
            pv_forecast, wall_now, load, consumption.total if consumption else None
        )
        if snapshot.expected_surplus_wh is not None:
            snapshot.expected_surplus_wh = max(0.0, snapshot.expected_surplus_wh - balancing_wh)
        battery = self._battery_group(snapshot)
        plan_battery = self._battery_group(snapshot, planning=True)
        settings = self.settings
        # The daily targets first: their forced runs are a load in the plans.
        self._target_states(snapshot, battery, pv_forecast, load, wall_now)
        self._end_needless_saturation(time.monotonic())
        if self.bad_weather.update(
            wall_now,
            hourly(pv_forecast) if pv_forecast is not None else None,
            self._with_target_load(consumption.total) if consumption else None,
        ):
            self._store.async_delay_save(self._data_to_store, BALANCING_SAVE_DELAY_S)
        forecast_plan = self.forecast_plan(
            snapshot, plan_battery, wall_now, load, settings, balancing_wh, background=True
        )
        self.support_budget.update(forecast_plan.support_budget_wh)
        cap = forecast_plan.cap
        snapshot.feed_in_cap = cap
        snapshot.feed_in_limit_w = forecast_plan.feed_in_limit_w
        snapshot.feed_in_limit_reason = forecast_plan.feed_in_limit_reason
        peak_threshold_pct = forecast_plan.peak_threshold_pct
        peak_limit_w = forecast_plan.peak_limit_w
        snapshot.peak_shaving_limit_w = peak_limit_w if settings.peak_shaving else None
        snapshot.day_plan = forecast_plan.day_plan
        snapshot.day_plan_tomorrow = forecast_plan.day_plan_tomorrow
        snapshot.price_hold = forecast_plan.price_hold
        snapshot.grid_charge = forecast_plan.grid_charge
        price_plan = forecast_plan.grid_charge or forecast_plan.price_hold
        price_limit_w = price_plan.limit_w(wall_now) if price_plan else None
        grid_charge_w = forecast_plan.grid_charge.charge_at(wall_now) if forecast_plan.grid_charge else 0.0
        battery_export_w = forecast_plan.grid_charge.export_at(wall_now) if forecast_plan.grid_charge else 0.0
        charge_cap_w = forecast_plan.grid_charge.charge_cap_at(wall_now) if forecast_plan.grid_charge else None
        if settings.price_control:
            self.plan_changes.add(wall_now, price_decision(price_limit_w, grid_charge_w, battery_export_w, charge_cap_w))

        surplus_w = self._run_surplus(pv_forecast, consumption, load, wall_now)
        for consumer in map(self.effective_consumer, self.consumers):
            state = snapshot.consumers.get(consumer.subentry_id)
            nominal = consumer.nominal_power_w or 0
            if (
                consumer.control_mode.on_off
                and nominal > 0
                and snapshot.is_controllable_now(consumer.subentry_id)
                and state is not None
                and (state.power_w or 0.0) >= RUNNING_AT_START_RATIO * nominal
            ):
                self._runtime.adopt_running(consumer.subentry_id, now)
        # The consumers' power as it is in the energy balance (available power).
        drawing_w = seen_w or {
            subentry_id: state.power_w or 0.0
            for subentry_id, state in snapshot.consumers.items()
            if snapshot.is_controllable_now(subentry_id)
        }
        requests = [
            self._request(
                consumer,
                now,
                battery,
                snapshot.available_power_w,
                surplus_w,
                drawing_w.get(consumer.subentry_id),
            )
            for consumer in map(self.effective_consumer, self.consumers)
            if snapshot.is_controllable_now(consumer.subentry_id)
        ]
        # Always set: in active mode the snapshot is a copy of the previous one.
        snapshot.night_discharge = None
        if (
            settings.night_discharge
            and not self.bad_weather.active(wall_now)
            and battery is not None
            and snapshot.pv_forecast is not None
            and snapshot.consumption_forecast is not None
        ):
            snapshot.night_discharge = plan_night_discharge(
                wall_now,
                battery.soc_pct,
                battery.capacity_wh,
                battery.charge_efficiency,
                battery.charge_efficiency,
                snapshot.pv_forecast,
                self._with_target_load(snapshot.consumption_forecast.total),
                self.night_reserve_pct(settings),
                self.secured_buffer_wh(settings, *self._next_day_energy(snapshot, wall_now)),
                battery.min_soc_pct / 100 * battery.capacity_wh,
                (
                    lambda moment: battery.full_soc_pct / 100 * battery.capacity_wh
                    - cap.space_needed_at(moment)
                )
                if cap is not None
                else None,
                full_wh=battery.full_soc_pct / 100 * battery.capacity_wh,
                max_charge_w=plan_battery.max_charge_w,
                demands=self.target_demands,
                charge_limit=own_charge_limit(self._own_pv_batteries(snapshot, wall_now), plan_battery.max_charge_w),
                keep=_price_keep(forecast_plan.grid_charge, forecast_plan.price_hold, battery),
            )
        cap_control = (
            CapControl(
                limit_w=cap.limit_w,
                hold_charging=cap.hold_charging,
                export_w=cap.export_power_w,
                margin_w=CAP_MARGIN_W,
                support_w=cap.support_w,
            )
            if cap is not None
            else None
        )
        unsupported_measured_w = self._update_unsupported(snapshot, beyond_w)
        allocation = allocate(
            snapshot.available_power_w,
            battery,
            requests,
            replace(
                settings.allocation_settings(),
                peak_shaving_grid_limit_w=peak_limit_w,
                peak_shaving_soc_threshold_pct=peak_threshold_pct,
                charge_secured_buffer_wh=self.secured_buffer_wh(
                    settings, *self._rest_of_day_energy(snapshot, wall_now)
                ),
                charge_grid_target_w=self.grid_target_w(settings, charging=True),
                discharge_grid_target_w=self.grid_target_w(settings, charging=False),
            ),
            snapshot.expected_surplus_wh,
            snapshot.night_discharge.power_w if snapshot.night_discharge and price_limit_w is None else None,
            snapshot.feed_in_limit_w,
            cap_control,
            unsupported=self.unsupported,
            unsupported_measured_w=unsupported_measured_w,
            discharge_limit_w=price_limit_w,
            grid_charge_w=grid_charge_w,
            battery_export_w=battery_export_w,
            charge_cap_w=charge_cap_w,
            grid_import_limit_w=settings.grid_import_max_w or math.inf,
        )
        # Resting consumers draw nothing: the batteries take their allocation.
        # Counted at their command (controller cycle), that command is not in
        # the grid power and comes off again.
        unused_w = 0.0
        by_id = {request.subentry_id: request for request in requests}
        for subentry_id, power in allocation.consumer_power_w.items():
            if subentry_id in snapshot.resting:
                measured = snapshot.consumers[subentry_id].power_w or 0.0
                drawn = drawn_power_w(by_id[subentry_id], power) if subentry_id in by_id else power
                unused_w += max(0.0, drawn - measured) - (seen_w.get(subentry_id, measured) - measured)
        if unused_w > 0 and battery is not None:
            max_charge = 0.0 if battery.is_full else battery.max_charge_w
            allocation.battery_power_w = max(
                allocation.battery_power_w,
                min(max_charge, allocation.battery_power_w + unused_w),
            )
        elif unused_w < 0:
            allocation.battery_power_w += unused_w
        if allocation.strategy is not Strategy.GRID_CHARGE:
            allocation.battery_power_w = limit_charge_to_surplus(
                allocation.battery_power_w,
                -snapshot.grid_without_batteries_filtered_w,
                self.grid_target_w(settings, charging=True),
            )
        if previous_total_w is not None:
            allocation.battery_power_w = previous_total_w + gain * (
                allocation.battery_power_w - previous_total_w
            )
        max_export_w = max_discharge_export_w(settings.allocation_settings(), cap_control)
        if snapshot.grid_power_w is not None:
            allocation.battery_power_w = limit_discharge_export(
                allocation.battery_power_w, enabled_battery_w, snapshot.grid_power_w, max_export_w
            )
        if previous_total_w is not None:
            self._learn_grid_target(snapshot, allocation, battery, settings, max_export_w)
        for subentry_id, power in allocation.consumer_power_w.items():
            self._runtime.update(subentry_id, power > 0, now)
        snapshot.allocation = allocation
        if self.price_savings.observe(
            wall_now,
            price_plan is not None,
            battery.soc_pct / 100 * battery.capacity_wh if battery is not None else None,
            settings.operating_mode is OperatingMode.ACTIVE
            and allocation.strategy in (Strategy.PRICE_HOLD, Strategy.GRID_CHARGE, Strategy.BATTERY_EXPORT),
        ):
            self._store.async_delay_save(self._data_to_store, BALANCING_SAVE_DELAY_S)
        snapshot.distribution = self._distributor.distribute(
            allocation.battery_power_w,
            self._battery_units(snapshot),
            RotationSettings(
                soc_threshold_pct=settings.rotation_soc_threshold_pct,
                min_interval_s=settings.rotation_min_interval_min * 60,
                ramp_rate_w_per_s=settings.rotation_ramp_rate_w_per_s,
                ramp_max_s=settings.rotation_ramp_max_s,
            ),
            now,
            full_charge=self.full_charge_battery,
        )
        self._own_pv_control(snapshot, allocation, cap_control, controller_cycle=previous_total_w is not None)

    def _own_pv_control(
        self,
        snapshot: SystemSnapshot,
        allocation: Allocation,
        cap: CapControl | None,
        *,
        controller_cycle: bool,
    ) -> None:
        """Batteries with own PV: probing for curtailed PV at the top of the
        charge and curtailing for the feed-in cap (see own_pv)."""
        own = [
            b for b in self.batteries
            if b.driver.capabilities.own_pv and b.driver.capabilities.controllable
        ]
        if not own:
            return
        curtailable: dict[str, float] = {}
        for battery in own:
            telemetry = snapshot.batteries.get(battery.subentry_id)
            if not battery.participating or telemetry is None or telemetry.pv_w is None:
                continue
            full = battery.delivery.bms_full or (
                telemetry.soc_pct is not None and battery_full(telemetry.soc_pct, 100.0)
            )
            if battery.driver.capabilities.passes_pv_through and full:
                continue
            power = snapshot.distribution.power_w.get(battery.subentry_id, 0.0)
            available = battery.driver.own_pv_available_w(telemetry.pv_w)
            curtailable[battery.subentry_id] = max(0.0, available - max(0.0, power))
        if controller_cycle:
            export = None if snapshot.grid_power_filtered_w is None else -snapshot.grid_power_filtered_w
            self._curtail.update(
                export, cap.max_export_w if cap is not None else None,
                allocation.cap_excess_w, sum(curtailable.values()),
            )
        left = snapshot.own_pv_curtail_w = self._curtail.total_w
        for battery in own:
            share = min(left, curtailable.get(battery.subentry_id, 0.0))
            left -= share
            battery.driver.set_own_pv_state(at_top=self._own_pv_at_top(battery, snapshot), curtail_w=share)

    @staticmethod
    def _own_pv_at_top(battery: BatteryRuntime, snapshot: SystemSnapshot) -> bool:
        """Whether the battery may take less than commanded (full, at the top
        of the charge, limited by temperature). The maximum SoC of SLEMS is not
        enough: below the top the battery takes what the output leaves."""
        telemetry = snapshot.batteries.get(battery.subentry_id)
        if telemetry is None or telemetry.soc_pct is None:
            return False
        limits = battery.power_limits
        if battery.delivery.bms_full or (
            limits is not None and limits.charge_reason in ("temperature", "top", "bms")
        ):
            return True
        cell = telemetry.extra.get("max_cell_voltage")
        return cell >= BMS_END_CELL_V if cell is not None else telemetry.soc_pct >= BMS_END_SOC_PCT

    # --- learned values (see learning) ------------------------------------------------

    @property
    def pv_overestimate(self) -> tuple[float | None, int]:
        return pv_overestimate(self.pv_accuracy.days)

    @property
    def consumption_underestimate(self) -> tuple[float | None, int]:
        accuracy = self.forecaster.accuracy
        days = [(d.forecast_wh, d.actual_wh) for d in accuracy.days] if accuracy else []
        return consumption_underestimate(days)

    def night_reserve_pct(self, settings: ControlSettings) -> float:
        """Reserve of the night discharge in %: learned from the morning gaps or set."""
        learned = self.morning_gap.reserve_pct(settings.night_reserve_coverage_pct)
        if settings.night_reserve_auto and learned is not None:
            return learned
        return settings.night_reserve_pct

    def _learn_morning_gap(self, snapshot: SystemSnapshot, now: float) -> None:
        """Keep the planned PV takeover of the coming morning and measure the gap."""
        local_now = dt_util.now()
        learner = self.morning_gap
        consumption = snapshot.consumption_forecast
        if snapshot.pv_forecast is not None and consumption is not None and (
            learner.planned is None or local_now < learner.planned
        ):
            takeover = pv_takeover(local_now, snapshot.pv_forecast, consumption.total)
            if takeover is not None:
                learner.plan(takeover, consumption.energy_on_day(takeover.date()))
        learner.update(now, local_now, snapshot.pv_power_w, snapshot.total_consumption_w)

    def grid_friendly_buffer_wh(self, settings: ControlSettings, pv_remaining_wh: float) -> float:
        """Buffer of grid friendly charging: learned PV overestimate of the rest of the day."""
        share = self.pv_overestimate[0]
        if settings.grid_friendly_buffer_auto and share is not None:
            return share * pv_remaining_wh
        return settings.grid_friendly_buffer_kwh * 1000

    def secured_buffer_wh(
        self, settings: ControlSettings, pv_wh: float, consumption_wh: float
    ) -> float:
        """Buffer of charge secured and night discharge: PV too high plus
        consumption too low. While only one part is learned, the larger of it
        and the set buffer."""
        fixed = settings.charge_secured_buffer_kwh * 1000
        if not settings.charge_secured_buffer_auto:
            return fixed
        pv_share = self.pv_overestimate[0]
        consumption_share = self.consumption_underestimate[0]
        pv_part = None if pv_share is None else pv_share * pv_wh
        consumption_part = None if consumption_share is None else consumption_share * consumption_wh
        if pv_part is not None and consumption_part is not None:
            return pv_part + consumption_part
        learned = pv_part if pv_part is not None else consumption_part
        return fixed if learned is None else max(fixed, learned)

    def grid_target_w(self, settings: ControlSettings, *, charging: bool) -> float:
        learned = self.grid_targets.target_w(charging, settings.grid_targets_no_import)
        if settings.grid_targets_auto and learned is not None:
            return learned
        return settings.charge_grid_target_w if charging else settings.discharge_grid_target_w

    @property
    def learned_timing(self) -> tuple[float, float] | None:
        """(control interval, averaging window) from the smart meter, None until known."""
        return auto_timing(self.controller.meter.interval_s)

    @property
    def control_interval_s(self) -> float:
        timing = self.learned_timing
        if self.settings.timing_auto and timing is not None:
            return timing[0]
        return self.settings.control_interval_s

    @property
    def average_window_s(self) -> float:
        timing = self.learned_timing
        if self.settings.timing_auto and timing is not None:
            return timing[1]
        return self.settings.surplus_average_window_s

    def effective_consumer(self, consumer: ConsumerConfig) -> ConsumerConfig:
        """The consumer with its active phases (current control), its observed
        thermostat cycling and, if switched on, its learned power.

        On/off: the learned power replaces the nominal power. Power controlled:
        the learned power at full command becomes its nominal power, used for
        planning (forecast, daily target, feed-in cap); the maximum power it is
        commanded stays as configured, as its power varies (e.g. a heating
        element with the water temperature) and the batteries balance what it
        does not take.
        """
        if consumer.control_mode is ControlMode.CURRENT:
            consumer = consumer.with_phases(active_phases(self.hass, consumer))
        learner = self.consumer_learners.get(consumer.subentry_id)
        if learner is None:
            return consumer
        # Observed pauses of its own thermostat apply also without the learned values.
        if learner.thermostat_cycles and not consumer.thermostat_cycles:
            consumer = replace(consumer, thermostat_cycles=True)
        if consumer.subentry_id not in self.consumer_learning:
            return consumer
        nominal = learner.nominal_w
        if nominal is None:
            return consumer
        if not consumer.control_mode.on_off:
            nominal = max(nominal, consumer.min_power_w or 0)
        return replace(consumer, nominal_power_w=nominal)

    @staticmethod
    def _energy(series: dict[datetime, float] | None, start: datetime, end: datetime) -> float:
        return sum(wh for moment, wh in (series or {}).items() if start <= moment < end)

    def _rest_of_day_energy(self, snapshot: SystemSnapshot, wall_now: datetime) -> tuple[float, float]:
        """(PV, consumption) forecast from the current hour until midnight (Wh)."""
        start = dt_util.as_local(wall_now).replace(minute=0, second=0, microsecond=0)
        end = dt_util.start_of_local_day(dt_util.as_local(wall_now)) + timedelta(days=1)
        return self._forecast_energy(snapshot, start, end)

    def _next_day_energy(self, snapshot: SystemSnapshot, wall_now: datetime) -> tuple[float, float]:
        """(PV, consumption) forecast of the next 24 hours (Wh)."""
        start = dt_util.as_local(wall_now).replace(minute=0, second=0, microsecond=0)
        return self._forecast_energy(snapshot, start, start + timedelta(days=1))

    def _forecast_energy(
        self, snapshot: SystemSnapshot, start: datetime, end: datetime
    ) -> tuple[float, float]:
        consumption = snapshot.consumption_forecast
        pv = (
            self._energy(self._pv_native(snapshot, dt_util.now()), start, end)
            if snapshot.pv_forecast
            else 0.0
        )
        return pv, self._energy(consumption.total if consumption else None, start, end)

    def _learn_grid_target(
        self,
        snapshot: SystemSnapshot,
        allocation: Allocation,
        battery: BatteryGroup | None,
        settings: ControlSettings,
        max_export_w: float,
    ) -> None:
        """Sample the grid deviation while the batteries control the grid (active mode).

        The deviation is taken from the target the batteries really aim at: the
        maximum grid export while discharging lowers the discharge target, and
        grid friendly charging holds the batteries back on purpose (not sampled).
        """
        grid = snapshot.grid_power_w
        if (
            grid is None
            or battery is None
            or settings.operating_mode is not OperatingMode.ACTIVE
            or self.controller.status is not ControlStatus.ACTIVE
        ):
            return
        power = allocation.battery_power_w
        # Not at a limit, otherwise the batteries cannot correct the deviation.
        if allocation.strategy in (Strategy.BATTERY_PRIORITY, Strategy.SHARED):
            if 50 < power < 0.9 * battery.max_charge_w:
                self.grid_targets.add(True, grid, self.grid_target_w(settings, charging=True))
        elif allocation.strategy is Strategy.SELF_CONSUMPTION:
            if -0.9 * battery.max_discharge_w < power < -50:
                target = min(self.grid_target_w(settings, charging=False), max_export_w)
                self.grid_targets.add(False, grid, target)

    def forecast_plan(
        self,
        snapshot: SystemSnapshot,
        battery: BatteryGroup | None,
        wall_now: datetime,
        load: float | None,
        settings: ControlSettings,
        balancing_wh: float = 0.0,
        power_w: tuple[float, float] | None = None,
        tariff: Tariff | None = None,
        background: bool = False,
    ) -> ForecastPlan:
        """Plans from the forecasts with ``settings``: feed-in limit, feed-in
        cap, peak shaving and the SoC projection with the day plans.

        Used for the real planning and, with other settings, battery,
        forecasts and ``tariff`` (instead of the current one), for the
        simulation. ``power_w`` replaces the charge and discharge power of the
        batteries for the feed-in cap.
        """
        result = ForecastPlan()
        consumption = snapshot.consumption_forecast
        pv_forecast = (
            self._pv_native(snapshot, wall_now) if snapshot.pv_forecast is not None else None
        )
        cap = self._feed_in_cap(snapshot, battery, wall_now, settings, power_w)
        result.cap = cap
        if not settings.grid_friendly_charging:
            result.feed_in_limit_reason = "disabled"
        elif self.bad_weather.active(wall_now):
            result.feed_in_limit_reason = "bad_weather"
        elif battery is None or pv_forecast is None:
            result.feed_in_limit_reason = "no_forecast"
        else:
            surplus = remaining_surplus(
                pv_forecast, consumption.total if consumption else None, load, wall_now
            )
            result.feed_in_limit_w = feed_in_limit(
                surplus,
                battery.energy_to_full_wh
                + self.grid_friendly_buffer_wh(settings, remaining_pv_wh(pv_forecast, wall_now))
                + balancing_wh,
                battery.max_charge_w,
            )
            result.feed_in_limit_reason = (
                "not_enough_surplus" if result.feed_in_limit_w is None else None
            )
            if cap is not None and result.feed_in_limit_w is not None:
                result.feed_in_limit_w = min(result.feed_in_limit_w, cap.limit_w)
        pv_hourly = hourly(pv_forecast) if pv_forecast is not None else None
        consumption_hourly = self._with_target_load(consumption.total) if consumption else None
        own = self._own_pv_batteries(snapshot, wall_now)
        result.peak_threshold_pct, result.peak_limit_w = self._peak_shaving(
            battery, wall_now, pv_hourly, consumption_hourly, settings, own
        )
        if pv_forecast is None:
            return result
        if battery is not None:
            projection_settings = ProjectionSettings(
                    grid_friendly_charging=settings.grid_friendly_charging,
                    # Like the feed-in limit: only with grid friendly charging.
                    charge_buffer_wh=(
                        self.grid_friendly_buffer_wh(settings, remaining_pv_wh(pv_forecast, wall_now))
                        if settings.grid_friendly_charging
                        else 0.0
                    ),
                    night_buffer_wh=self.secured_buffer_wh(
                        settings, *self._next_day_energy(snapshot, wall_now)
                    ),
                    peak_shaving=settings.peak_shaving,
                    peak_shaving_grid_limit_w=result.peak_limit_w,
                    peak_shaving_soc_threshold_pct=result.peak_threshold_pct,
                    night_discharge=settings.night_discharge,
                    night_reserve_pct=self.night_reserve_pct(settings),
                    discharge_max_grid_export_w=settings.discharge_max_grid_export_w,
                    bad_weather_until=self.bad_weather.until,
                    battery_priority_soc_pct=settings.battery_priority_soc_pct,
                )
            grid_load, budget_load = self._support_loads()
            until = (
                next_refill(
                    wall_now,
                    pv_hourly,
                    consumption_hourly,
                    (battery.full_soc_pct - battery.min_soc_pct) / 100 * battery.capacity_wh
                    / (battery.charge_efficiency or 1.0),
                    battery.max_charge_w,
                )
                if consumption_hourly
                else None
            )
            if settings.price_control and consumption_hourly:
                result.grid_charge = self._grid_charge(
                    battery, wall_now, pv_forecast, pv_hourly, consumption_hourly, grid_load, until, settings,
                    result.peak_limit_w, cap, tariff, background,
                    own=own,
                )
                if result.grid_charge is None:
                    result.price_hold = self._price_hold(
                        battery, wall_now, pv_forecast, consumption_hourly, grid_load, until, settings, tariff
                    )
                limits = (result.grid_charge or result.price_hold)
                if limits is not None:
                    # Beyond the allowed discharge the grid covers the deficit.
                    grid_load = {
                        **grid_load,
                        **{
                            hour: max(0.0, consumption_hourly.get(hour, 0.0) - (pv_hourly or {}).get(hour, 0.0) - limit)
                            for hour, limit in limits.hourly_limits_w.items()
                        },
                    }
            if consumption_hourly and any(
                self.support_of(c.subentry_id) is BatterySupport.AUTO for c in self.consumers
            ):
                result.support_budget_wh = self._support_budget(
                    snapshot,
                    battery,
                    wall_now,
                    load,
                    pv_hourly,
                    consumption_hourly,
                    grid_load,
                    budget_load,
                    until,
                    settings,
                    replace(projection_settings, night_discharge=False),
                    result.feed_in_limit_w,
                    balancing_wh,
                    cap,
                )
            result.projection = project_soc(
                wall_now,
                battery,
                pv_hourly,
                consumption_hourly,
                load,
                projection_settings,
                result.feed_in_limit_w,
                balancing_wh,
                cap,
                self.target_demands,
                grid_load=grid_load,
                budget_load=budget_load,
                budget_wh=result.support_budget_wh,
                budget_until=until,
                grid_charge=result.grid_charge.hourly_charge_w if result.grid_charge else None,
                battery_export=result.grid_charge.hourly_export_w if result.grid_charge else None,
                charge_caps=result.grid_charge.hourly_caps_w if result.grid_charge else None,
                night_keep=_price_keep(result.grid_charge, result.price_hold, battery),
                own=own,
            )
        pv_power = power_lookup(self._pv_native(snapshot, wall_now))
        result.day_plan = self._day_plan(
            pv_hourly,
            consumption_hourly,
            result.projection,
            wall_now,
            cap=cap,
            pv_power=pv_power,
            battery=battery,
            target_load=self.target_load,
        )
        result.day_plan_tomorrow = self._day_plan(
            pv_hourly,
            consumption_hourly,
            result.projection,
            wall_now,
            day_offset=1,
            cap=cap,
            pv_power=pv_power,
            battery=battery,
            target_load=self.target_load,
        )
        return result

    def support_of(self, subentry_id: str) -> BatterySupport:
        """Battery support of a consumer; a wallbox defaults to automatic."""
        if (support := self.battery_support.get(subentry_id)) is not None:
            return support
        consumer = next((c for c in self.consumers if c.subentry_id == subentry_id), None)
        if consumer is not None and consumer.consumer_type is ConsumerType.WALLBOX:
            return BatterySupport.AUTO
        return BatterySupport.ALWAYS

    def consumer_current(self, consumer: ConsumerConfig, power_w: float) -> tuple[int, int] | None:
        """(amperes, phases) a current controlled consumer gets for ``power_w``."""
        if consumer.control_mode is not ControlMode.CURRENT:
            return None
        phases = active_phases(self.hass, consumer)
        return amps_for(power_w, consumer.voltage_v, phases), phases

    def _update_unsupported(self, snapshot: SystemSnapshot, beyond_w: Mapping[str, float]) -> float:
        """Consumers the batteries must not cover right now (see battery_support).

        Sets ``unsupported`` and returns the measured power of those SLEMS does
        not control right now (blocked, control off, measured only or running
        on its own), and what those it controls draw beyond their command
        (``beyond_w``, see controller.consumer_power_counted).
        """
        unsupported: set[str] = set()
        measured_w = 0.0
        for consumer in self.consumers:
            subentry_id = consumer.subentry_id
            support = self.support_of(subentry_id)
            if not consumer.included_in_meter or support is BatterySupport.ALWAYS:
                continue
            if support is BatterySupport.AUTO and self.support_budget.available:
                continue
            # A forced run with the source "battery" may use the batteries.
            state = self.target_states.get(subentry_id)
            if (
                state is not None
                and state.mode in (TargetMode.FORCED, TargetMode.WINDOW, TargetMode.PLANNED)
                and self.consumer_targets[subentry_id].source is TargetSource.BATTERY
            ):
                continue
            unsupported.add(subentry_id)
            if not snapshot.is_controllable_now(subentry_id):
                measured = snapshot.consumers.get(subentry_id)
                measured_w += max(0.0, (measured.power_w or 0.0) if measured else 0.0)
            else:
                measured_w += max(0.0, beyond_w.get(subentry_id, 0.0))
        self.unsupported = frozenset(unsupported)
        return measured_w

    def _price_hold(
        self,
        battery: BatteryGroup,
        wall_now: datetime,
        pv_forecast: PvForecast | None,
        consumption_hourly: Mapping[datetime, float],
        grid_load: Mapping[datetime, float],
        until: datetime | None,
        settings: ControlSettings,
        tariff: Tariff | None = None,
    ) -> PriceHold | None:
        """Quarter hours in which the batteries keep their energy for more expensive ones."""
        if until is None:
            return None
        prices = self._import_prices(wall_now, until, tariff=tariff)
        if prices is None:
            return None
        deficits = {
            start: max(0.0, wh)
            for start, wh in _quarter_nets(wall_now, until, pv_forecast, consumption_hourly, grid_load).items()
        }
        stored = battery.soc_pct / 100 * battery.capacity_wh
        floor = battery.min_soc_pct / 100 * battery.capacity_wh
        usable = max(0.0, stored - floor) * (battery.charge_efficiency or 1.0)
        return plan_price_hold(wall_now, usable, deficits, prices, until, settings.price_min_gain_ct)

    def _grid_charge(
        self,
        battery: BatteryGroup,
        wall_now: datetime,
        pv_forecast: PvForecast | None,
        pv_hourly: Mapping[datetime, float] | None,
        consumption_hourly: Mapping[datetime, float],
        grid_load: Mapping[datetime, float],
        until: datetime | None,
        settings: ControlSettings,
        peak_limit_w: float,
        cap: CapPlan | None,
        tariff: Tariff | None = None,
        background: bool = False,
        own: Sequence[OwnPvBattery] = (),
    ) -> GridChargePlan | None:
        """Price plan of the batteries (see grid_charge).

        Until the refill (a day whose PV surplus fills the batteries, see
        next_refill) or the last known price; smaller PV surpluses before are
        part of the plan. With a negative import or feed-in price ahead the
        plan goes on until the last known price: it keeps room for the PV
        surplus of the negative hours (feeding in before at a positive price)
        and charges from the grid when the import price is negative. Not into
        the space the feed-in cap needs.
        """
        local_now = dt_util.as_local(wall_now)
        # Whole hours, so the prices are computed once per quarter hour.
        horizon = local_now.replace(minute=0, second=0, microsecond=0) + MAX_GRID_CHARGE_HORIZON
        prices = self._import_prices(wall_now, horizon, tariff=tariff)
        if prices is None:
            if background:
                self._grid_charge_last = None
            return None
        credits = self._import_prices(wall_now, horizon, Side.EXPORT, tariff=tariff) or {}
        # Only known negative prices plan for them; the estimates are means of past days.
        negative = any(
            v is not None and v < 0
            for v in [
                *(self._import_prices(wall_now, horizon, estimate=False, tariff=tariff) or {}).values(),
                *(self._import_prices(wall_now, horizon, Side.EXPORT, estimate=False, tariff=tariff) or {}).values(),
            ]
        )
        # A credit that follows the market price: the surplus hours are part
        # of the plan (room kept for the hours with a lower credit).
        surplus = self.battery_export_effective(tariff)
        if not (negative or surplus):
            if not (settings.grid_charge or settings.battery_export):
                if background:
                    self._grid_charge_last = None
                return None
            horizon = min(until, horizon) if until is not None else horizon
        if negative or surplus:
            until = None
        nets = _quarter_nets(wall_now, horizon, pv_forecast, consumption_hourly, grid_load)
        capacity = battery.capacity_wh
        enabled = [b for b in self.batteries if b.enabled]
        weights = [b.driver.capabilities.capacity_wh or 1.0 for b in enabled]
        wear = (
            sum(b.wear.ct_per_kwh * w for b, w in zip(enabled, weights, strict=True)) / sum(weights)
            if enabled
            else 0.0
        )
        # Batteries with own PV and without an input do not charge from the grid.
        grid_power = (
            max(0.0, battery.max_charge_w - sum(b.max_charge_w for b in own)) if settings.grid_charge else 0.0
        )
        if settings.grid_charge_max_w > 0:
            grid_power = min(grid_power, settings.grid_charge_max_w)
        own_room_wh = sum(max(0.0, b.full_wh - b.stored_wh) for b in own)
        import_limit = peak_limit_w if settings.peak_shaving else math.inf
        if settings.grid_import_max_w > 0:
            import_limit = min(import_limit, settings.grid_import_max_w)
        day_start = dt_util.start_of_local_day(local_now) + timedelta(days=1)
        tomorrow = sum(
            wh for start, wh in consumption_hourly.items() if day_start <= start < day_start + timedelta(days=1)
        )
        charge_battery = ChargeBattery(
            capacity_wh=capacity,
            stored_wh=battery.soc_pct / 100 * capacity,
            floor_wh=battery.min_soc_pct / 100 * capacity,
            grid_max_wh=min(
                min(settings.grid_charge_max_soc_pct, battery.full_soc_pct) / 100 * capacity,
                battery.full_soc_pct / 100 * capacity - cap.space_needed_at(horizon)
                if cap is not None
                else math.inf,
                battery.full_soc_pct / 100 * capacity - own_room_wh,
            ),
            max_charge_w=battery.max_charge_w,
            max_discharge_w=battery.max_discharge_w,
            efficiency=battery.charge_efficiency or 1.0,
            wear_ct=wear,
            full_wh=battery.full_soc_pct / 100 * capacity,
            grid_charge_w=grid_power,
        )
        # Not below the morning reserve (as the night discharge).
        export_floor = battery.min_soc_pct / 100 * capacity + self.night_reserve_pct(settings) / 100 * tomorrow
        export_max = min(settings.discharge_max_grid_export_w, cap.limit_w if cap is not None else math.inf)
        # The plan takes a noticeable time: made again for a new quarter hour,
        # a step of the stored energy, other settings or at least every minute.
        step = max(GRID_CHARGE_STEP_WH, capacity / 100)
        key = (
            slot_start(local_now),
            replace(charge_battery, stored_wh=round(charge_battery.stored_wh / step)),
            until,
            settings.price_min_gain_ct,
            import_limit,
            round(export_floor / step),
            export_max,
            settings.battery_export,
            self.market_prices.last_update,
            None if tariff is None else (tariff.items, tuple(sorted(tariff.vat_pct.items()))),
        )
        monotonic = time.monotonic()
        cached = self._grid_charge_cache.get(key)
        if cached is not None and cached[0] > monotonic:
            if background:
                self._grid_charge_last = cached[1]
            return cached[1]
        plan_args = partial(
            plan_grid_charge,
            wall_now,
            charge_battery,
            nets,
            prices,
            until,
            settings.price_min_gain_ct,
            import_limit_w=import_limit,
            export_prices=credits,
            export_floor_wh=export_floor,
            export_max_w=export_max,
            battery_export=settings.battery_export,
        )
        if not background:
            started = time.perf_counter()
            plan = plan_args()
            self._store_grid_charge(key, plan, time.perf_counter() - started, len(nets))
            return plan
        # Computed in the executor (up to about a second on a slow host); the
        # control goes on with the last plan meanwhile.
        if self._grid_charge_task is None:
            self._grid_charge_task = self.config_entry.async_create_background_task(
                self.hass, self._async_grid_charge(key, plan_args, len(nets)), "slems price plan"
            )
        return self._grid_charge_last

    async def _async_grid_charge(self, key: tuple, plan_args: partial, periods: int) -> None:
        """Make the price plan in the executor and start a control cycle with it."""
        try:
            started = time.perf_counter()
            plan = await self.hass.async_add_executor_job(plan_args)
            self._store_grid_charge(key, plan, time.perf_counter() - started, periods)
            self._grid_charge_last = plan
        finally:
            self._grid_charge_task = None
        self.controller.request()

    def _store_grid_charge(self, key: tuple, plan: GridChargePlan | None, seconds: float, periods: int) -> None:
        self.grid_charge_timing.add(seconds, periods)
        monotonic = time.monotonic()
        self._grid_charge_cache = {
            k: v for k, v in self._grid_charge_cache.items() if v[0] > monotonic
        }
        self._grid_charge_cache[key] = (monotonic + GRID_CHARGE_REPLAN_S, plan)

    def backtest_battery(self) -> BacktestBattery | None:
        """The batteries and price settings for the backtest of the price aware control."""
        snapshot = self.data
        battery = self._battery_group(snapshot, planning=True) if snapshot is not None else None
        if battery is None:
            return None
        settings = self.settings
        capacity = battery.capacity_wh
        enabled = [b for b in self.batteries if b.enabled]
        weights = [b.driver.capabilities.capacity_wh or 1.0 for b in enabled]
        # Batteries with own PV and without an input do not charge from the grid.
        own = self._own_pv_batteries(snapshot, dt_util.now())
        max_charge = max(0.0, battery.max_charge_w - sum(b.max_charge_w for b in own))
        if settings.grid_charge_max_w > 0:
            max_charge = min(max_charge, settings.grid_charge_max_w)
        return BacktestBattery(
            capacity_wh=capacity,
            min_wh=battery.min_soc_pct / 100 * capacity,
            full_wh=battery.full_soc_pct / 100 * capacity,
            max_charge_w=max_charge,
            max_discharge_w=battery.max_discharge_w,
            efficiency=battery.charge_efficiency or 1.0,
            wear_ct=sum(b.wear.ct_per_kwh * w for b, w in zip(enabled, weights, strict=True)) / sum(weights)
            if enabled
            else 0.0,
            grid_charge=settings.grid_charge,
            grid_max_wh=min(settings.grid_charge_max_soc_pct, battery.full_soc_pct) / 100 * capacity,
        )

    def battery_export_effective(self, tariff: Tariff | None = None) -> bool:
        """Whether the feed-in credit of ``tariff`` (default: the current one)
        changes over the day (market price)."""
        if tariff is None:
            tariffs = configured_tariffs(self.config_entry)
            if not tariffs:
                return False
            tariff = next(iter(tariffs.values()))
        return any(item.side is Side.EXPORT and item.unit is Unit.SPOT for item in tariff.items)

    def history_sources(self) -> ForecastSources:
        """Statistic ids of house consumption and PV (as the consumption forecast uses them)."""
        return self._forecast_sources()

    def _import_prices(
        self,
        wall_now: datetime,
        until: datetime,
        side: Side = Side.IMPORT,
        *,
        estimate: bool = True,
        tariff: Tariff | None = None,
    ) -> dict[datetime, float | None] | None:
        """Import price (or feed-in credit) per quarter hour with the current
        tariff, after the last known market price estimated (see
        market_prices.estimate_prices), kept per quarter hour; None without tariff.
        With ``tariff`` (the simulation) its prices, not kept."""
        if tariff is not None:
            return period_import_prices(tariff, self.market_prices, wall_now, until, side, estimate=estimate)
        tariffs = configured_tariffs(self.config_entry)
        if not tariffs:
            return None
        stamp = int(wall_now.timestamp())
        key = (stamp - stamp % 900, until, self.market_prices.last_update)
        cached = self._import_price_cache.get((side, estimate))
        if cached is None or cached[0] != key:
            prices = period_import_prices(
                next(iter(tariffs.values())), self.market_prices, wall_now, until, side, estimate=estimate
            )
            cached = self._import_price_cache[(side, estimate)] = (key, prices)
        return cached[1]

    def _support_loads(self) -> tuple[dict[datetime, float], dict[datetime, float]]:
        """Forced runs (source "grid") of consumers on battery support never / automatic."""
        grid: dict[datetime, float] = {}
        auto: dict[datetime, float] = {}
        for subentry_id, load in self.target_load_grid.items():
            target = {BatterySupport.NEVER: grid, BatterySupport.AUTO: auto}.get(
                self.support_of(subentry_id)
            )
            if target is None:
                continue
            for hour, wh in load.items():
                target[hour] = target.get(hour, 0.0) + wh
        return grid, auto

    def _support_budget(
        self,
        snapshot: SystemSnapshot,
        battery: BatteryGroup,
        wall_now: datetime,
        load: float | None,
        pv_hourly: dict[datetime, float],
        consumption_hourly: dict[datetime, float],
        grid_load: dict[datetime, float],
        budget_load: dict[datetime, float],
        until: datetime | None,
        settings: ControlSettings,
        projection_settings: ProjectionSettings,
        feed_in_limit_w: float | None,
        balancing_wh: float,
        cap: CapPlan | None,
    ) -> float:
        """Energy the batteries can spare for consumers on "automatic" (see battery_support)."""
        # The house without the loads the budget is for (and without the ones
        # the grid covers anyway), without night discharge.
        house = {
            hour: wh - grid_load.get(hour, 0.0) - budget_load.get(hour, 0.0)
            for hour, wh in consumption_hourly.items()
        }
        projection = project_soc(
            wall_now,
            battery,
            pv_hourly,
            house,
            load,
            projection_settings,
            feed_in_limit_w,
            balancing_wh,
            cap,
            self.target_demands,
            own=self._own_pv_batteries(snapshot, wall_now),
        )
        capacity = battery.capacity_wh
        floor = battery.min_soc_pct / 100 * capacity
        if settings.peak_shaving:
            floor = max(floor, projection_settings.peak_shaving_soc_threshold_pct / 100 * capacity)
        refill_day = dt_util.as_local(until).date() if until is not None else None
        day_consumption = sum(
            wh for hour, wh in consumption_hourly.items() if dt_util.as_local(hour).date() == refill_day
        )
        floor += self.night_reserve_pct(settings) / 100 * day_consumption
        floor += self.secured_buffer_wh(settings, *self._next_day_energy(snapshot, wall_now))
        return support_budget_wh(
            battery.soc_pct / 100 * capacity, projection.soc_pct, capacity, until, floor
        )

    @staticmethod
    def _pv_native(snapshot: SystemSnapshot, wall_now: datetime) -> PvForecast:
        """PV forecast in its own periods; today corrected (weighted), tomorrow raw."""
        return corrected_forecast(
            snapshot.pv_forecast or {}, wall_now, snapshot.pv_correction, snapshot.pv_elapsed_share
        )

    def _feed_in_cap(
        self,
        snapshot: SystemSnapshot,
        battery: BatteryGroup | None,
        wall_now: datetime,
        settings: ControlSettings,
        power_w: tuple[float, float] | None = None,
    ) -> CapPlan | None:
        """Plan of the feed-in cap; also sets the buffer in effect in ``snapshot``."""
        snapshot.feed_in_cap_buffer_pct = None
        snapshot.feed_in_cap_buffer_source = None
        snapshot.feed_in_cap_buffer_days = 0
        consumption = snapshot.consumption_forecast
        if (
            not settings.feed_in_cap
            or battery is None
            or snapshot.pv_forecast is None
            or consumption is None
        ):
            return None
        buffer_pct = settings.feed_in_cap_buffer_pct
        pv_factor = 1.0
        source = "manual"
        if settings.feed_in_cap_auto_buffer:
            learned, days = auto_buffer(self.pv_accuracy.days)
            snapshot.feed_in_cap_buffer_days = days
            if learned is not None:
                # The learned underestimation raises the PV forecast instead.
                pv_factor, buffer_pct, source = 1 + learned, learned * 100, "auto"
        snapshot.feed_in_cap_buffer_pct = buffer_pct
        snapshot.feed_in_cap_buffer_source = source
        pv = self._pv_native(snapshot, wall_now)
        charge_w = discharge_w = 0.0
        for runtime in self.batteries if power_w is None else ():
            telemetry = snapshot.batteries.get(runtime.subentry_id)
            if runtime.plannable and telemetry is not None:
                # Without the SoC window: the power when the peak comes.
                limits = self._power_limits(runtime, telemetry, use_soc_window=False, plan=True)
                charge_w += limits.charge_w
                discharge_w += limits.discharge_w
        if power_w is not None:
            charge_w, discharge_w = power_w
        support: list[CapConsumer] = []
        normal: list[CapConsumer] = []
        consumers = sorted(
            map(self.effective_consumer, self.consumers), key=lambda c: (c.priority, c.subentry_id)
        )
        for consumer in consumers:
            if not snapshot.is_controllable_now(consumer.subentry_id):
                continue
            power = self._full_power_w(consumer)
            mode = self.cap_mode(consumer.subentry_id)
            if mode is CapMode.SUPPORT:
                support.append(self.cap_consumer(consumer, power))
            elif mode is CapMode.NORMAL:
                normal.append(
                    CapConsumer(power_w=power, switch=consumer.control_mode.on_off)
                )
        return plan_cap(
            wall_now,
            battery,
            charge_w,
            discharge_w,
            pv,
            hourly(consumption.total),
            CapSettings(
                limit_w=settings.feed_in_cap_limit_w,
                buffer_pct=0.0 if source == "auto" else buffer_pct,
                min_buffer_wh=(
                    settings.pv_peak_power_kwp * 1000 * FEED_IN_CAP_MIN_BUFFER_PCT / 100
                ),
                pv_factor=pv_factor,
            ),
            support,
            normal,
            charge_limit=self._own_pv_charge_limit(snapshot, wall_now, charge_w) if power_w is None else None,
        )

    def _own_pv_batteries(self, snapshot: SystemSnapshot, wall_now: datetime) -> list[OwnPvBattery]:
        """The plannable batteries with own PV and without an input from the
        grid, with the forecast of their modules (corrected like the PV forecast)."""
        result = []
        for battery in self.batteries:
            caps = battery.driver.capabilities
            telemetry = snapshot.batteries.get(battery.subentry_id)
            if (
                not caps.own_pv
                or caps.ac_charge
                or not battery.plannable
                or telemetry is None
                or telemetry.soc_pct is None
            ):
                continue
            capacity = battery.capacity_wh
            maximum = (
                100.0
                if battery.subentry_id == self.full_charge_battery or not caps.controllable
                else battery.limits.max_soc_pct
            )
            minimum = battery.limits.min_soc_pct if caps.controllable else 0.0
            forecast = snapshot.own_pv_forecasts.get(battery.subentry_id)
            result.append(
                OwnPvBattery(
                    stored_wh=telemetry.soc_pct / 100 * capacity,
                    full_wh=maximum / 100 * capacity,
                    floor_wh=minimum / 100 * capacity,
                    max_charge_w=self._power_limits(battery, telemetry, use_soc_window=False, plan=True).charge_w,
                    pv=corrected_forecast(forecast, wall_now, snapshot.pv_correction, snapshot.pv_elapsed_share)
                    if forecast
                    else {},
                )
            )
        return result

    def _own_pv_charge_limit(
        self, snapshot: SystemSnapshot, wall_now: datetime, charge_w: float
    ) -> Callable[[datetime], float] | None:
        """Charge power of the batteries at a moment: those with own PV at
        most the forecast of their modules (None without such batteries)."""
        own = self._own_pv_batteries(snapshot, wall_now)
        if not own:
            return None
        others_w = max(0.0, charge_w - sum(b.max_charge_w for b in own))
        lookups = [(b.max_charge_w, power_lookup(b.pv) if b.pv else None) for b in own]

        def limit(moment: datetime) -> float:
            return others_w + sum(
                min(max_w, lookup(moment)) for max_w, lookup in lookups if lookup is not None
            )

        return limit

    def _run_surplus(
        self,
        pv_forecast: PvForecast | None,
        consumption: ConsumptionForecast | None,
        load: float | None,
        wall_now: datetime,
    ) -> Callable[[float], float] | None:
        """Mean forecast surplus (W) of the next ``seconds``; None without a PV forecast."""
        if pv_forecast is None:
            return None
        total = consumption.total if consumption is not None else None

        def mean(seconds: float) -> float:
            until = wall_now + timedelta(seconds=seconds)
            energy = 0.0
            for start, surplus_w, _ in remaining_surplus_by_hour(pv_forecast, total, load, wall_now, until):
                overlap = (min(start + timedelta(hours=1), until) - max(start, wall_now)).total_seconds()
                energy += surplus_w * max(0.0, overlap)
            return energy / seconds if seconds > 0 else 0.0

        return mean

    def _cycling_holds(
        self,
        consumer: ConsumerConfig,
        now: float,
        mode: TargetMode,
        available_w: float | None,
        run_surplus: Callable[[float], float] | None,
    ) -> tuple[bool, bool]:
        """(keep on, keep off) of a consumer that avoids short runs (see
        consumers.cycling_holds); a run is the minimum runtime, or what its
        daily target still needs if less."""
        subentry_id = consumer.subentry_id
        if not consumer.avoid_cycling or mode in (
            TargetMode.FORCED, TargetMode.WINDOW, TargetMode.PLANNED, TargetMode.BOOST
        ):
            self._short_since.pop(subentry_id, None)
            return False, False
        power = self._full_power_w(consumer)
        run_s = consumer.min_on_s or AVOID_CYCLING_RUN_S
        settings = self.consumer_targets[subentry_id]
        if settings.type in (TargetType.RUNTIME, TargetType.ENABLED, TargetType.ENERGY) and power > 0:
            missing = remaining(settings, self.target_progress[subentry_id])
            needed = missing if settings.type is not TargetType.ENERGY else missing / power * 3600
            if needed > 0:
                run_s = min(run_s, needed)
        elif settings.type is TargetType.TEMPERATURE and power > 0:
            # Until the target temperature (the energy per kelvin learned, or
            # the daily energy of the last days).
            energy = self.target_energy_wh.get(subentry_id)
            if energy:
                run_s = min(run_s, energy / power * 3600)
        keep_on, keep_off, since = cycling_holds(
            self._runtime.is_on(subentry_id),
            power,
            now,
            self._short_since.get(subentry_id),
            available_w,
            run_surplus,
            run_s,
        )
        if since is None:
            self._short_since.pop(subentry_id, None)
        else:
            self._short_since[subentry_id] = since
        return keep_on, keep_off

    def _request(
        self,
        consumer: ConsumerConfig,
        now: float,
        battery: BatteryGroup | None,
        available_w: float | None = None,
        run_surplus: Callable[[float], float] | None = None,
        drawing_w: float | None = None,
    ) -> ConsumerRequest:
        """Allocation request of a consumer, with its daily target; ``drawing_w``
        is its power in the energy balance."""
        mode = self.target_states.get(consumer.subentry_id, TargetState(TargetMode.NONE)).mode
        keep_on, keep_off = self._cycling_holds(consumer, now, mode, available_w, run_surplus)
        must_stay_on = self._runtime.must_stay_on(consumer, now) or keep_on or self._window_holds(consumer, now, mode)
        return ConsumerRequest(
            subentry_id=consumer.subentry_id,
            priority=consumer.priority,
            control_mode=consumer.control_mode,
            nominal_power_w=consumer.nominal_power_w or 0,
            min_power_w=consumer.min_power_w or 0,
            max_power_w=consumer.max_power_w or 0,
            must_stay_on=must_stay_on,
            running=self._runtime.is_on(consumer.subentry_id),
            drawing_w=drawing_w,
            # A met target or one before its earliest start keeps it off
            # (after its minimum runtime).
            must_stay_off=self._runtime.must_stay_off(consumer, now)
            or keep_off
            or (mode in (TargetMode.DONE, TargetMode.WAITING) and not must_stay_on),
            cap_mode=self.cap_mode(consumer.subentry_id),
            boost=mode is TargetMode.BOOST,
            forced=mode in (TargetMode.FORCED, TargetMode.WINDOW, TargetMode.PLANNED),
            forced_max_w=(
                battery.max_discharge_w
                if mode in (TargetMode.FORCED, TargetMode.WINDOW, TargetMode.PLANNED)
                and battery is not None
                and self.consumer_targets[consumer.subentry_id].source is TargetSource.BATTERY
                else None
            ),
        )

    def _surplus_window(
        self,
        consumer: ConsumerConfig,
        settings: TargetSettings,
        state: TargetState,
        wall_now: datetime,
        *,
        hourly_surplus: Mapping[datetime, float] | None,
        power_w: float,
    ) -> TargetState:
        """Planned window of a release consumer for its run from the surplus
        (see consumer_targets.plan_surplus_window); with the option "release"
        it is released for it (*planned*).

        Before the window opens it follows the forecast; once open it is kept
        until it closes, so the automation gets one window.
        """
        subentry_id = consumer.subentry_id
        if (
            consumer.control_mode is not ControlMode.RELEASE
            or settings.surplus_window is SurplusWindow.OFF
            or state.mode in (TargetMode.NONE, TargetMode.DONE)
            or state.end is None
            or power_w <= 0
        ):
            self.surplus_windows.pop(subentry_id, None)
            return state
        local_now = dt_util.as_local(wall_now)
        kept = self.surplus_windows.get(subentry_id)
        if kept is not None and kept[2] == state.end and kept[0] <= local_now < kept[1]:
            window = kept[:2]
        else:
            start = max(local_now, window_start(settings, state.end) or local_now)
            # Up to the forced run: from then on it runs anyway.
            end = state.end
            if settings.source is not TargetSource.SURPLUS and state.latest_start is not None:
                end = min(end, state.latest_start)
            energy = self.target_energy_wh.get(subentry_id)
            run = (
                timedelta(hours=energy / power_w)
                if energy
                else timedelta(minutes=settings.release_window_min or 60)
            )
            window = (
                plan_surplus_window(
                    hourly_surplus,
                    start,
                    end,
                    run=run,
                    power_w=power_w,
                    window=timedelta(minutes=settings.release_window_min),
                )
                if hourly_surplus is not None and end > start
                else None
            )
            if window is None:
                self.surplus_windows.pop(subentry_id, None)
                return state
            self.surplus_windows[subentry_id] = (*window, state.end)
        if (
            settings.surplus_window is SurplusWindow.RELEASE
            and window[0] <= local_now < window[1]
            and state.mode in (TargetMode.SURPLUS, TargetMode.BOOST)
        ):
            return replace(state, mode=TargetMode.PLANNED)
        return state

    def _window_holds(self, consumer: ConsumerConfig, now: float, mode: TargetMode) -> bool:
        """A release stays open for its window (see consumer_targets), unless
        its daily target is met or waits for its period."""
        window_s = self.consumer_targets[consumer.subentry_id].release_window_min * 60
        if consumer.control_mode is not ControlMode.RELEASE or window_s <= 0:
            return False
        if mode in (TargetMode.DONE, TargetMode.WAITING):
            return False
        on_for = self._runtime.on_for(consumer.subentry_id, now)
        return on_for is not None and on_for < window_s

    def _end_needless_saturation(self, now: float) -> None:
        """A saturated consumer keeps its command (see controller), a release its
        release; once its daily target is met or waits for its period, it is
        controlled again after its minimum runtime, so it is switched off."""
        for consumer in self.consumers:
            subentry_id = consumer.subentry_id
            state = self.target_states.get(subentry_id)
            if (
                subentry_id in self.controller.saturated
                and state is not None
                and state.mode in (TargetMode.DONE, TargetMode.WAITING)
                and not self._runtime.must_stay_on(consumer, now)
            ):
                self.controller.end_saturation(subentry_id)

    def _count_target(
        self, consumer: ConsumerConfig, state: ConsumerState, now: float, commanded_on: bool
    ) -> None:
        """Progress of the consumer's daily target; a missed one is notified once
        (not when SLEMS could not control the consumer at some time of the
        period or the consumer declined power)."""
        settings = self.consumer_targets[consumer.subentry_id]
        watch = self.no_power[consumer.subentry_id]
        if settings.type is TargetType.NONE:
            watch.reset()
            return
        watch.update(
            now,
            dt_util.now(),
            commanded_on,
            state.power_w,
            no_power_threshold_s(settings, self._full_power_w(consumer), self.target_progress[consumer.subentry_id].end),
        )
        progress = self.target_progress[consumer.subentry_id]
        goal, unit = self.target_goal(settings)
        got = self.target_got(settings, progress)
        end = progress.end
        local_now = dt_util.now()
        result = progress.update(
            now,
            local_now,
            settings,
            state.power_w,
            commanded_on,
            controlled=self.settings.operating_mode is OperatingMode.ACTIVE
            and consumer.subentry_id not in self.consumer_control_disabled,
            blocked=state.blocked,
            declined=consumer.subentry_id in self.controller.saturated
            or consumer.subentry_id in self.controller.resting,
        )
        temperature = target_temperature(settings, state.temperature_c, state.temperatures_c)
        start = window_start(settings, progress.end) if progress.end is not None else None
        # Temperatures between the deadline and midnight belong to no day.
        if settings.type is TargetType.TEMPERATURE:
            progress.use_sensor(settings.sensor)
        if (
            settings.type is TargetType.TEMPERATURE
            and temperature is not None
            and (start is None or local_now >= start)
        ):
            progress.track_temperature(settings, temperature)
        # Only if SLEMS could control the consumer all the period and it did
        # not decline power (saturated or resting: it needed no more).
        if result == "missed" and end is not None and progress.last_controlled and not progress.last_declined:
            self.config_entry.async_create_background_task(
                self.hass,
                self.problems.async_notify_target_missed(
                    consumer.subentry_id, consumer.name, got, goal, unit,
                    dt_util.as_local(end).strftime("%H:%M"),
                    progress.last_blocked_s,
                ),
                "slems target notification",
            )

    @staticmethod
    def target_goal(settings: TargetSettings) -> tuple[float, str]:
        if settings.type is TargetType.ENERGY:
            return settings.energy_kwh, "kWh"
        if settings.type is TargetType.TEMPERATURE:
            return settings.min_temp_c, "°C"
        return settings.hours, "h"

    def target_got(self, settings: TargetSettings, progress: TargetProgress) -> float:
        if settings.type is TargetType.ENERGY:
            return progress.energy_wh / 1000
        if settings.type is TargetType.RUNTIME:
            return progress.runtime_s / 3600
        if settings.type is TargetType.ENABLED:
            return progress.enabled_s / 3600
        return 0.0

    def _target_states(
        self,
        snapshot: SystemSnapshot,
        battery: BatteryGroup | None,
        pv_forecast: PvForecast | None,
        load: float | None,
        wall_now: datetime,
    ) -> None:
        """Mode of every consumer's daily target for this plan, the load of the
        forced runs they may still need and the part expected from the surplus."""
        consumption = snapshot.consumption_forecast
        local_now = dt_util.as_local(wall_now)
        self.target_states = {}
        self.target_load = {}
        self.target_load_grid = {}
        self.target_energy_wh = {}
        self.target_energy_estimated = set()
        self.target_forecast_c = {}
        self.target_demands = []
        consumers = sorted(map(self.effective_consumer, self.consumers), key=lambda c: c.priority)
        # A supporting consumer only takes the surplus above the feed-in cap's
        # limit; the cap plan counts it there.
        def supporting(consumer: ConsumerConfig) -> bool:
            return self.settings.feed_in_cap and self.cap_mode(consumer.subentry_id) is CapMode.SUPPORT

        for consumer in consumers:
            settings = self.consumer_targets[consumer.subentry_id]
            # With its control switched off SLEMS runs it never: nothing planned.
            if not consumer.controllable or consumer.subentry_id in self.consumer_control_disabled:
                continue
            estimate = self.target_energy_estimate.get(consumer.subentry_id)
            if settings.type is TargetType.NONE:
                if estimate is not None and _plans_daily_energy(consumer) and not supporting(consumer):
                    self.target_demands += daily_demands(
                        estimate,
                        self._today_wh(consumer.subentry_id, snapshot.consumers.get(consumer.subentry_id)),
                        local_now,
                        power_w=self._full_power_w(consumer),
                    )
                continue
            progress = self.target_progress[consumer.subentry_id]
            power = self._full_power_w(consumer)
            state = snapshot.consumers.get(consumer.subentry_id)
            end = progress.end
            surplus = 0.0
            if pv_forecast is not None and end is not None:
                surplus = sum(
                    power_w * hours
                    for _, power_w, hours in remaining_surplus_by_hour(
                        pv_forecast, consumption.total if consumption else None, load, wall_now, end
                    )
                )
            thermal = self.thermal_learners.get(consumer.subentry_id)
            temperature = (
                target_temperature(settings, state.temperature_c, state.temperatures_c)
                if state
                else None
            )
            wh_per_k = self._target_wh_per_k(settings, thermal)
            energy = energy_to_target(
                settings, progress, power_w=power, temperature_c=temperature, wh_per_k=wh_per_k
            )
            if settings.type is TargetType.TEMPERATURE and estimate is not None and energy != 0:
                # Water draws and heat losses: at least the daily energy of the
                # last days minus this period's (also while not learned yet).
                expected = max(0.0, estimate - progress.energy_wh)
                if energy is None or expected > energy:
                    energy = expected
                    self.target_energy_estimated.add(consumer.subentry_id)
            self.target_energy_wh[consumer.subentry_id] = energy
            # The batteries must deliver an on/off consumer's full power, a
            # power controlled one at least its minimum (it is then limited).
            needed = power if consumer.control_mode.on_off else max(consumer.min_power_w or 0, 1.0)
            battery_need = battery.energy_to_full_wh if battery else 0.0
            cooling = self.cooling_learners.get(consumer.subentry_id)
            forecast_c = (
                cooling.forecast_c(
                    temperature, local_now, progress.end or period_end(local_now, settings.deadline)
                )
                if settings.type is TargetType.TEMPERATURE and cooling is not None and temperature is not None
                else None
            )
            self.target_forecast_c[consumer.subentry_id] = forecast_c
            self.target_states[consumer.subentry_id] = self._price_window(
                consumer,
                settings,
                evaluate(
                    settings,
                    progress,
                    dt_util.as_local(wall_now),
                    power_w=power,
                    temperature_c=temperature,
                    wh_per_k=wh_per_k,
                    expected_surplus_wh=surplus,
                    battery_need_wh=battery_need,
                    battery_can_supply=battery is not None and battery.max_discharge_w >= needed,
                    temperature_at_end_c=forecast_c,
                ),
                wall_now,
                short=energy is not None and surplus < energy + battery_need,
                power_w=power,
                pv_forecast=pv_forecast,
                consumption=consumption,
            )
            self.target_states[consumer.subentry_id] = self._surplus_window(
                consumer,
                settings,
                self.target_states[consumer.subentry_id],
                wall_now,
                hourly_surplus=(
                    {
                        start: surplus_w
                        for start, surplus_w, _ in remaining_surplus_by_hour(
                            pv_forecast, consumption.total if consumption else None, load, wall_now, end
                        )
                    }
                    if pv_forecast is not None and end is not None
                    else None
                ),
                power_w=power,
            )
            # A heat pump's consumption is already in its own forecast model.
            if consumer.consumer_type is ConsumerType.HEAT_PUMP:
                continue
            forced_w = power
            if settings.source is TargetSource.BATTERY:
                forced_w = min(power, battery.max_discharge_w) if battery is not None else 0.0
            forced = forced_load(
                settings,
                self.target_states[consumer.subentry_id],
                local_now,
                power_w=forced_w,
                wh_per_k=wh_per_k,
            )
            for hour, wh in forced.items():
                self.target_load[hour] = self.target_load.get(hour, 0.0) + wh
            if forced and settings.source is TargetSource.GRID:
                self.target_load_grid[consumer.subentry_id] = forced
            demand = surplus_demand(
                settings,
                self.target_states[consumer.subentry_id],
                local_now,
                energy_wh=energy,
                forced_wh=sum(forced.values()),
                power_w=power,
            )
            if supporting(consumer):
                continue
            if demand is not None:
                self.target_demands.append(demand)
            following = next_period_demand(
                settings,
                self.target_states[consumer.subentry_id].end or period_end(local_now, settings.deadline),
                power_w=power,
                estimate_wh=estimate,
            )
            if following is not None:
                self.target_demands.append(following)

    def _live_outdoor_c(self, snapshot: SystemSnapshot) -> float | None:
        """Outdoor temperature now: the configured sensor, else the weather entity."""
        if entity := self._config.get(CONF_OUTDOOR_TEMPERATURE_ENTITY):
            value = temperature_c_of(self.hass.states.get(entity))
            if value is not None:
                return value
        return snapshot.outdoor_temperature_c

    def _run_prices(
        self,
        consumer: ConsumerConfig,
        power_w: float,
        wall_now: datetime,
        end: datetime,
        pv_forecast: PvForecast | None,
        consumption: ConsumptionForecast | None,
    ) -> dict[datetime, float | None] | None:
        """Cost (ct) of a run per kWh of the consumer's power, per quarter hour:
        its power at the forecast outdoor temperature (see OutdoorPowerLearner),
        the part the forecast surplus covers at the feed-in credit, the rest at
        the import price. Without a learned dependency and surplus the import
        price."""
        prices = self._import_prices(wall_now, end)
        if prices is None or power_w <= 0:
            return prices
        credits = self._import_prices(wall_now, end, Side.EXPORT) or {}
        nets = _quarter_nets(wall_now, end, pv_forecast, consumption.total if consumption else {}, {})
        learner = self.outdoor_power.get(consumer.subentry_id)
        temperatures = self.forecaster.forecast_temperatures
        result: dict[datetime, float | None] = {}
        for start, price in prices.items():
            if price is None:
                result[start] = None
                continue
            power = power_w
            if learner is not None:
                expected = learner.power_at(temperatures.get(start.replace(minute=0)))
                if expected is not None:
                    power = expected
            surplus_w = max(0.0, -nets.get(start, 0.0)) / (QUARTER / timedelta(hours=1))
            covered = min(power, surplus_w)
            result[start] = ((power - covered) * price + covered * (credits.get(start) or 0.0)) / power_w
        return result

    def _price_window(
        self,
        consumer: ConsumerConfig,
        settings: TargetSettings,
        state: TargetState,
        wall_now: datetime,
        *,
        short: bool,
        power_w: float,
        pv_forecast: PvForecast | None,
        consumption: ConsumptionForecast | None,
    ) -> TargetState:
        """Move the forced run of a target with the source "grid" into a cheaper
        window if the surplus is short for it anyway (see price_hold); a
        window's cost from ``_run_prices``."""
        if (
            not self.settings.price_control
            or not short
            or settings.source is not TargetSource.GRID
            or state.mode not in (TargetMode.SURPLUS, TargetMode.BOOST, TargetMode.WINDOW)
            or state.latest_start is None
            or state.end is None
        ):
            return state
        prices = self._run_prices(consumer, power_w, wall_now, state.end, pv_forecast, consumption)
        if prices is None:
            return state
        local_now = dt_util.as_local(wall_now)
        start = cheapest_start(
            prices,
            local_now,
            state.latest_start,
            state.end - START_MARGIN - state.latest_start,
            self.settings.price_min_gain_ct,
        )
        if start is None:
            return state
        opens = window_opens(settings, start, state.end)
        if local_now >= start:
            mode = TargetMode.FORCED
        elif opens is not None and local_now >= opens:
            mode = TargetMode.WINDOW
        else:
            mode = state.mode
        return replace(state, latest_start=start, mode=mode, price_window=True)

    def consumer_full_power_w(self, consumer: ConsumerConfig) -> float:
        """Power of the consumer when it runs at full power (learned or configured)."""
        return self._full_power_w(self.effective_consumer(consumer))

    @staticmethod
    def _target_wh_per_k(settings: TargetSettings, thermal: ThermalLearner | None) -> float | None:
        """Energy per kelvin of the temperature the target applies to (the mean
        until the chosen sensor's own value is learned)."""
        if thermal is None:
            return None
        index = {TargetSensor.FIRST: 0, TargetSensor.SECOND: 1}.get(settings.sensor)
        own = thermal.sensor_energy_per_k(index) if index is not None else None
        return own if own is not None else thermal.energy_per_k

    def _with_target_load(self, consumption: dict[datetime, float]) -> dict[datetime, float]:
        """Hourly consumption forecast plus the forced runs of the daily targets."""
        result = hourly(consumption)
        for hour, wh in self.target_load.items():
            result[hour] = result.get(hour, 0.0) + wh
        return result

    @staticmethod
    def _full_power_w(consumer: ConsumerConfig) -> float:
        """Power at full command: the nominal power (on/off, or the learned one of
        a power controlled consumer), else its maximum power."""
        if consumer.control_mode.on_off:
            return float(consumer.nominal_power_w or 0)
        return float(consumer.nominal_power_w or consumer.max_power_w or 0)

    def thermal_capacity(self, subentry_id: str) -> tuple[float, float] | None:
        """(energy until it cycles, until it is full) in Wh from the temperatures now."""
        learner = self.thermal_learners.get(subentry_id)
        state = self.data.consumers.get(subentry_id) if self.data is not None else None
        if learner is None or state is None:
            return None
        return learner.capacity(state.temperature_c)

    def cap_consumer(self, consumer: ConsumerConfig, power_w: float) -> CapConsumer:
        """Supporting consumer for the cap planning (capacity from the temperatures)."""
        switch = consumer.control_mode.on_off
        capacity = self.thermal_capacity(consumer.subentry_id)
        if capacity is None:
            return CapConsumer(power_w=power_w, switch=switch)
        until_pause, until_full = capacity
        return CapConsumer(
            power_w=power_w,
            switch=switch,
            capacity_wh=until_full * THERMAL_SAFETY,
            full_power_wh=until_pause * THERMAL_SAFETY,
            cycling_power_w=self.thermal_learners[consumer.subentry_id].cycling_w,
        )

    def takeover_consumers(self, snapshot: SystemSnapshot) -> list[str]:
        """Names of the consumers taking surplus above the limit, supporting ones first."""
        consumers = [c for c in self.consumers if snapshot.is_controllable_now(c.subentry_id)]
        return [
            consumer.name
            for mode in (CapMode.SUPPORT, CapMode.NORMAL)
            for consumer in consumers
            if self.cap_mode(consumer.subentry_id) is mode
        ]

    def cap_mode(self, subentry_id: str) -> CapMode:
        return self.consumer_cap_modes.get(subentry_id, CapMode.NORMAL)

    def missing_batteries(self, snapshot: SystemSnapshot) -> list[tuple[str, str]]:
        """(name, reason) of the batteries that are not planned with right now."""
        missing = []
        for battery in self.batteries:
            if battery.plannable and battery.subentry_id in snapshot.batteries:
                continue
            if battery.communication_paused:
                reason = "paused"
            elif not battery.enabled:
                reason = "disabled"
            elif battery.balancing_requested:
                reason = "balancing"
            elif battery.not_responding:
                reason = "not_responding"
            elif battery.in_trial and battery.subentry_id in snapshot.batteries:
                reason = "trial"
            else:
                reason = "unreadable"
            missing.append((battery.name, reason))
        return missing

    @property
    def min_soc_pct(self) -> float:
        """Capacity weighted minimum SoC of the controllable batteries."""
        weighted = capacity = 0.0
        for battery in self.batteries:
            caps = battery.driver.capabilities
            if caps.controllable:
                weighted += battery.limits.min_soc_pct * caps.capacity_wh
                capacity += caps.capacity_wh
        return weighted / capacity if capacity else 0.0

    def _peak_shaving(
        self,
        battery: BatteryGroup | None,
        wall_now: datetime,
        pv_hourly: dict[datetime, float] | None,
        consumption_hourly: dict[datetime, float] | None,
        settings: ControlSettings,
        own: Sequence[OwnPvBattery] = (),
    ) -> tuple[float, float]:
        """SoC threshold and import limit of peak shaving in effect.

        The threshold never lies below the minimum SoC of the batteries. With
        the automatic limit (see peak_shaving) the usable energy above the
        minimum SoC has to last until PV refills the batteries. It is
        calculated from the SoC capped at the threshold: above it peak shaving
        is not active, and the limit shows what applies once it is reached.
        """
        threshold = settings.peak_shaving_soc_threshold_pct
        limit = settings.peak_shaving_grid_limit_w
        if battery is None:
            return threshold, limit
        threshold = max(threshold, battery.min_soc_pct)
        profile = self.forecaster.peak_profile
        if (
            settings.peak_shaving_auto
            and profile is not None
            and not profile.is_empty
            and pv_hourly is not None
            and consumption_hourly is not None
        ):
            usable_wh = (
                max(0.0, min(battery.soc_pct, threshold) - battery.min_soc_pct)
                / 100
                * battery.capacity_wh
                * battery.charge_efficiency
            )
            # AC energy that charges the batteries from the minimum back to the threshold.
            refill_wh = (
                max(0.0, threshold - battery.min_soc_pct)
                / 100
                * battery.capacity_wh
                / battery.charge_efficiency
            )
            limit = auto_limit(
                profile,
                hours_until_refill(
                    wall_now, pv_hourly, consumption_hourly, refill_wh, own_charge_limit(own, battery.max_charge_w)
                ),
                usable_wh,
                settings.peak_shaving_reserve_pct / 100,
            )
        return threshold, limit

    @staticmethod
    def _day_plan(
        pv_hourly: dict[datetime, float],
        consumption_hourly: dict[datetime, float] | None,
        projection: SocProjection | None,
        wall_now: datetime,
        day_offset: int = 0,
        cap: CapPlan | None = None,
        pv_power=None,
        battery: BatteryGroup | None = None,
        target_load: dict[datetime, float] | None = None,
    ) -> list[dict]:
        """Hours of a day for the dashboard.

        Forecasts in Wh; planned charge power (W), expected grid power (W)
        and projected total SoC at the end of the hour (%) for the hours from
        now on. With the feed-in
        cap: PV level above which is capped (consumption + limit) and the
        energy above it, of which the curtailed part, from the fine periods.
        Per half hour: mean PV power from the native forecast periods
        (``pv_half_w``) and the feed-in cap energies (``cap_*_half_wh``).
        ``cap_lost_wh`` is the energy the projection expects above the limit
        (curtailed by the inverter), with the reason: batteries "full" or
        their "charge_power" too low.
        """
        day_start = dt_util.start_of_local_day(dt_util.as_local(wall_now))
        day_start += timedelta(days=day_offset)
        consumption_hourly = consumption_hourly or {}
        planned = projection.planned_charge_w if projection else {}
        planned_grid = projection.planned_grid_charge_w if projection else {}
        soc = projection.soc_pct if projection else {}
        grid = projection.grid_w if projection else {}
        from_surplus = projection.consumer_w if projection else {}
        rows = []
        for hour in range(24):
            start = day_start + timedelta(hours=hour)
            # Daily targets: forced runs (already in the consumption) and the
            # part the projection expects from the surplus.
            consumers = (target_load or {}).get(start, 0.0) + from_surplus.get(start, 0.0)
            rows.append(
                {
                    "start": start.isoformat(),
                    "pv_wh": round(pv_hourly.get(start, 0.0)),
                    "consumption_wh": (
                        round(consumption_hourly[start] + from_surplus.get(start, 0.0))
                        if start in consumption_hourly
                        else None
                    ),
                    # Part of it: planned load of the consumers' daily targets.
                    "consumer_wh": round(consumers) if consumers else None,
                    "planned_charge_w": round(planned[start]) if start in planned else None,
                    # Part of it from the grid.
                    "planned_grid_charge_w": (
                        round(planned_grid[start]) if start in planned_grid else None
                    ),
                    "soc_pct": round(soc[start], 1) if start in soc else None,
                    # Expected grid power (+ import / − export) from the projection.
                    "grid_w": round(grid[start]) if start in grid else None,
                }
            )
            if pv_power is not None:
                rows[-1]["pv_half_w"] = [
                    round(mean_power(pv_power, start + HALF_HOUR * i, HALF_HOUR))
                    for i in range(2)
                ]
            if cap is not None:
                halves = [cap.half_hourly.get(start + HALF_HOUR * i) for i in range(2)]
                rows[-1]["cap_excess_half_wh"] = [round(h.excess_wh) if h else None for h in halves]
                rows[-1]["cap_curtailed_half_wh"] = [
                    round(h.curtailed_wh) if h else None for h in halves
                ]
                hour_cap = cap.hourly.get(start)
                rows[-1].update(
                    {
                        "cap_line_wh": round(consumption_hourly.get(start, 0.0) + cap.limit_w),
                        "cap_excess_wh": round(hour_cap.excess_wh) if hour_cap else None,
                        "cap_curtailed_wh": round(hour_cap.curtailed_wh) if hour_cap else None,
                    }
                )
                if start in grid:
                    # The projection has no consumers: those taking surplus
                    # above the limit reduce what is lost.
                    lost = max(0.0, -grid[start] - cap.limit_w - cap.consumers_w)
                    rows[-1]["cap_lost_wh"] = round(lost)
                    if lost > 0 and battery is not None:
                        full = start in soc and soc[start] >= battery.full_soc_pct - 0.5
                        rows[-1]["cap_lost_reason"] = "full" if full else "charge_power"
        return rows

    def fast_snapshot(
        self,
        battery_power_w: dict[str, float],
        saturated: frozenset[str],
        *,
        previous_total_w: float | None,
        gain: float,
    ) -> SystemSnapshot | None:
        """Snapshot for the real-time controller without polling the batteries.

        Measurements come from the current HA states. ``battery_power_w`` are
        the battery powers the grid meter currently reflects (commands older
        than the response time); batteries without one use their telemetry.
        The planned total battery power moves from ``previous_total_w`` by
        ``gain`` of the difference.
        """
        if self.data is None:
            return None
        config = self._config
        now = time.monotonic()
        grid = self.grid_power_w()
        batteries = {
            battery_id: replace(
                telemetry,
                ac_power_w=battery_power_w.get(battery_id, telemetry.grid_side_power_w),
            )
            for battery_id, telemetry in self.data.batteries.items()
        }
        snapshot = replace(
            self.data,
            grid_power_w=grid,
            batteries=batteries,
            consumers={
                consumer.subentry_id: read_consumer_state(self.hass, consumer)
                for consumer in self.consumers
            },
            saturated=saturated,
            resting=self.controller.resting,
            running_on_its_own=self.controller.running_on_its_own,
            control_disabled=frozenset(self.consumer_control_disabled),
            allocation=None,
            distribution=None,
        )
        self._filter_grid(snapshot, now, battery_power_w)
        # The own PV of the batteries as last read.
        self._set_pv_power(snapshot)
        self._house_hold.check(snapshot, now)
        self.plan(snapshot, dt_util.now(), now, previous_total_w=previous_total_w, gain=gain)
        return snapshot

    @callback
    def publish(self, snapshot: SystemSnapshot) -> None:
        """Show a snapshot of the controller without rescheduling the polling."""
        self.data = snapshot
        self.async_update_listeners()

    def _battery_units(self, snapshot: SystemSnapshot) -> list[BatteryUnit]:
        units = []
        for battery in self.batteries:
            telemetry = snapshot.batteries.get(battery.subentry_id)
            if not battery.participating or telemetry is None or telemetry.soc_pct is None:
                continue
            limits = battery.power_limits or self._power_limits(battery, telemetry)
            resting = battery.subentry_id in self._full_charge_rest
            units.append(
                BatteryUnit(
                    battery_id=battery.subentry_id,
                    soc_pct=telemetry.soc_pct,
                    max_charge_w=0.0 if resting else limits.charge_w,
                    max_discharge_w=0.0 if resting else limits.discharge_w,
                    loss_model=battery.loss_curve.model(LossModel()),
                    response_s=self.controller.battery_responses.learned(battery.subentry_id),
                    min_charge_w=limits.min_charge_w,
                    leaving_fraction=(
                        None
                        if battery.plannable
                        else max(0.0, (battery.leaving_until or 0) - time.monotonic())
                        / LEAVE_RAMP_S
                    ),
                )
            )
        return units

    async def async_release_batteries(self) -> None:
        """Hand all controllable batteries back to their internal logic."""
        for battery in self.batteries:
            await self.async_release_battery(battery)

    async def async_release_battery(self, battery: BatteryRuntime) -> bool:
        """Hand one battery back to its internal logic; True if that is confirmed.

        A failed release stays outstanding and is tried again with every
        update while SLEMS does not control the battery (see
        ``release_pending_since``).
        """
        if not battery.driver.capabilities.controllable or (
            battery.trial is not None and battery.trial.in_trial and not battery.trial.wrote
        ):
            # A battery in its trial is written only by its test: the release
            # would write the registers under trial.
            battery.release_pending_since = None
            return True
        if battery.communication_paused:
            # Nothing is sent while paused; a manual pause released the battery
            # before it began. Connecting would also take the battery's only
            # connection slot until the pause ends.
            return True
        try:
            await battery.driver.release_control()
        except BatteryDriverError as err:
            if battery.release_pending_since is None:
                battery.release_pending_since = time.monotonic()
                _LOGGER.warning("Battery %s not released, trying again: %s", battery.name, err)
            else:
                _LOGGER.debug("Battery %s still not released: %s", battery.name, err)
            return False
        if battery.release_pending_since is not None:
            _LOGGER.info("Battery %s released", battery.name)
        battery.release_pending_since = None
        if battery.trial is not None and battery.trial.wrote:
            battery.trial.wrote = False
            self._store.async_delay_save(self._data_to_store, BALANCING_SAVE_DELAY_S)
        if battery.trial is not None and battery.trial.observation is not None:
            battery.trial.observation.released(time.monotonic())
        return True

    def trial_writing(self, battery: BatteryRuntime) -> None:
        """The test of a battery in its trial writes: from now on it is to be released."""
        if battery.trial is not None and battery.trial.in_trial and not battery.trial.wrote:
            battery.trial.wrote = True
            self._store.async_delay_save(self._data_to_store, BALANCING_SAVE_DELAY_S)

    @callback
    def confirm_trial(self, battery: BatteryRuntime) -> bool:
        """The user takes an unclear trial as passed; False if it cannot be confirmed."""
        if battery.trial is None or battery.self_test is not None or not battery.trial.confirm():
            return False
        _LOGGER.info("Trial of %s confirmed by the user", battery.name)
        self.controller.request()
        self._store.async_delay_save(self._data_to_store, BALANCING_SAVE_DELAY_S)
        self.async_update_listeners()
        return True

    def controls(self, battery: BatteryRuntime) -> bool:
        """Whether SLEMS sends set points to the battery right now."""
        return (
            self.settings.operating_mode is OperatingMode.ACTIVE
            and self.controller.status is ControlStatus.ACTIVE
            and battery.driver.capabilities.controllable
            and not battery.communication_paused
            and (battery.participating or battery.balancing_requested)
        )

    async def async_shutdown(self) -> None:
        """Save learned data and close all battery connections."""
        self.controller.shutdown()
        await super().async_shutdown()
        await self.market_prices.async_stop()
        await self._quarters_store.async_save(self._quarters_to_store())
        await self._store.async_save(self._data_to_store())
        if self.settings.operating_mode is OperatingMode.ACTIVE:
            await self.controller.async_release(self.batteries)
            # After the unload nothing tries again: a few attempts now.
            for _ in range(SHUTDOWN_RELEASE_ATTEMPTS - 1):
                pending = [b for b in self.batteries if b.release_pending_since is not None]
                if not pending:
                    break
                await asyncio.sleep(SHUTDOWN_RELEASE_WAIT_S)
                for battery in pending:
                    await self.async_release_battery(battery)
            for battery in self.batteries:
                if battery.release_pending_since is not None:
                    _LOGGER.warning(
                        "Battery %s could not be handed back to its own logic", battery.name
                    )
        for battery in self.batteries:
            await battery.driver.close()


def _price_keep(
    grid_charge: GridChargePlan | None, price_hold: PriceHold | None, battery: BatteryGroup
) -> Callable[[datetime], float | None] | None:
    """Stored energy the price plan keeps at a moment (None: outside the plan),
    for the night discharge: the grid charge plan's level, or for the price
    hold the minimum plus what the batteries deliver after the moment."""
    if grid_charge is not None:
        return grid_charge.kept_wh
    if price_hold is None:
        return None
    floor = battery.min_soc_pct / 100 * battery.capacity_wh
    efficiency = battery.charge_efficiency or 1.0

    def kept(moment: datetime) -> float | None:
        if moment >= price_hold.until:
            return None
        return floor + price_hold.delivered_from(moment) / efficiency

    return kept


def _quarter_nets(
    wall_now: datetime,
    end: datetime,
    pv_forecast: PvForecast | None,
    consumption_hourly: Mapping[datetime, float],
    grid_load: Mapping[datetime, float],
) -> dict[datetime, float]:
    """Consumption minus PV (Wh) per local quarter hour start until ``end``:
    the hourly consumption forecast spread evenly, PV from the forecast's own
    periods. ``grid_load`` (per hour) is covered by the grid, not the batteries."""
    power = power_lookup(pv_forecast) if pv_forecast else (lambda _moment: 0.0)
    share = QUARTER / timedelta(hours=1)
    nets: dict[datetime, float] = {}
    start = slot_start(wall_now)
    end = dt_util.as_local(end)
    while start < end:
        hour = start.replace(minute=0)
        if hour in consumption_hourly:
            load = (consumption_hourly[hour] - grid_load.get(hour, 0.0)) * share
            nets[start] = load - mean_power(power, start, QUARTER) * share
        start += QUARTER
    return nets


def _plans_daily_energy(consumer: ConsumerConfig) -> bool:
    """Its usual daily energy is planned (heat pumps have their own forecast
    model, a wallbox charges when a car is there)."""
    return consumer.controllable and consumer.consumer_type not in (
        ConsumerType.HEAT_PUMP,
        ConsumerType.WALLBOX,
    )


def _weather_temperature(state) -> float | None:
    """Current temperature of a weather entity in °C."""
    if state is None:
        return None
    try:
        temperature = float(state.attributes.get("temperature"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(temperature):
        return None
    unit = state.attributes.get("temperature_unit", UnitOfTemperature.CELSIUS)
    return TemperatureConverter.convert(temperature, unit, UnitOfTemperature.CELSIUS)


def _history_series(rows: list, factor: float) -> list[tuple[float, float | None]]:
    """(time, value × factor) of recorder rows in the compressed format; None if not a number."""
    series: list[tuple[float, float | None]] = []
    for row in rows:
        try:
            value: float | None = float(row["s"]) * factor
        except (KeyError, TypeError, ValueError):
            value = None
        if value is not None and not math.isfinite(value):
            value = None
        series.append((float(row["lu"]), value))
    return series
