"""Data coordinator: collects measurements and computes the allocation."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_state_report_event,
    async_track_time_change,
)
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.const import UnitOfTemperature
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import TemperatureConverter

from .allocation import (
    Allocation,
    AllocationSettings,
    BatteryGroup,
    CapControl,
    ConsumerRequest,
    Strategy,
    allocate,
    expected_surplus_wh,
    limit_discharge_export,
    max_discharge_export_w,
    remaining_pv_wh,
)
from .const import (
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
    DEFAULT_FEED_IN_CAP_MIN_BUFFER_PCT,
    DEFAULT_PV_PEAK_POWER_KWP,
    DEFAULT_NIGHT_RESERVE_PCT,
    DEFAULT_CONTROL_GAIN,
    DEFAULT_CONTROL_INTERVAL_S,
    DEFAULT_ROTATION_MIN_INTERVAL_MIN,
    DEFAULT_ROTATION_RAMP_MAX_S,
    DEFAULT_ROTATION_RAMP_RATE_W_PER_S,
    DEFAULT_ROTATION_SOC_THRESHOLD_PCT,
    DEFAULT_OPERATING_MODE,
    DEFAULT_PEAK_SHAVING_GRID_LIMIT_W,
    DEFAULT_PEAK_SHAVING_SOC_THRESHOLD_PCT,
    DEFAULT_SURPLUS_AVERAGE_WINDOW_S,
    DOMAIN,
    SCAN_INTERVAL,
    CapMode,
    ConsumerType,
    ControlMode,
    OperatingMode,
)
from .controller import ControlStatus, RealTimeController
from .consumers import ConsumerConfig, ConsumerState, RuntimeTracker, read_consumer_state
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
    power_limits,
)
from .cell_balancing import BalancingPhase, CellBalancer, CellMonitor
from .delivery_monitor import Action, DeliveryMonitor
from .feed_in_cap import CAP_MARGIN_W, CapPlan, CapSettings, auto_buffer, plan_cap
from .problems import ProblemReporter
from .efficiency import EfficiencyTracker, EnergyIntegrator, LossCurveLearner
from .forecast import (
    ConsumptionForecast,
    ConsumptionForecaster,
    ForecastSources,
    async_statistic_means,
)
from .forecast.accuracy import PvAccuracyTracker
from .grid_filter import GridPowerFilter
from .learning import (
    CapacityLearner,
    ConsumerLearner,
    GridTargetLearner,
    MorningGapLearner,
    auto_timing,
    consumption_underestimate,
    pv_overestimate,
)
from .grid_friendly import (
    energy_from_means,
    feed_in_limit,
    pv_correction,
    remaining_surplus,
)
from .night_discharge import NightDischargePlan, plan_night_discharge, pv_takeover
from .peak_shaving import auto_limit, hours_until_refill
from .pv_forecast import (
    PvForecast,
    async_get_pv_forecast,
    energy_on_day,
    hourly,
    mean_power,
    power_lookup,
)
from .soc_projection import ProjectionSettings, SocProjection, project_soc
from .util import state_as_watts

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_SAVE_DELAY_S = 600
# Start, phase changes and end of a balancing run are saved sooner.
BALANCING_SAVE_DELAY_S = 5
DEVICE_INFO_INTERVAL_S = 6 * 3600
# Store key of the controller data (next to the per battery subentry ids).
CONTROL_STORE_KEY = "control"
PV_ACCURACY_STORE_KEY = "pv_accuracy"
CONSUMERS_STORE_KEY = "consumers"
MORNING_GAP_STORE_KEY = "morning_gap"
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
    # Monotonic time of the first failed read since the last successful one.
    unreadable_since: float | None = None
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
    capacity_learner: CapacityLearner = field(default_factory=CapacityLearner)
    # Plan with the learned usable capacity instead of the configured one.
    learn_capacity: bool = False
    # Allowed power right now (updated with every poll).
    power_limits: PowerLimits | None = None
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
            (self.enabled and not self.balancing_requested) or self.leaving_until is not None
        ) and not self.not_responding and not self.communication_paused

    @property
    def plannable(self) -> bool:
        """Takes part in planning, total SoC and distribution."""
        return (
            self.enabled
            and not self.balancing_requested
            and not self.not_responding
            and not self.communication_paused
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
    peak_shaving_auto: bool = False
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
    # Minimum time between two control cycles.
    control_interval_s: float = DEFAULT_CONTROL_INTERVAL_S
    # Share of the remaining deviation corrected per control cycle: fixed value,
    # or start value of the automatic adaptation.
    control_gain: float = DEFAULT_CONTROL_GAIN
    auto_gain: bool = True
    # Learned instead of the fixed values (see learning).
    grid_friendly_buffer_auto: bool = False
    charge_secured_buffer_auto: bool = False
    grid_targets_auto: bool = False
    timing_auto: bool = False
    # Charge into the PV feed-in peak instead of as early as possible.
    grid_friendly_charging: bool = True
    # Energy grid friendly charging plans in addition to filling the
    # batteries (reserve against a too optimistic forecast).
    grid_friendly_buffer_kwh: float = DEFAULT_GRID_FRIENDLY_BUFFER_KWH
    night_discharge: bool = False
    # Night discharge reserve in % of tomorrow's forecast daily consumption.
    night_reserve_pct: float = DEFAULT_NIGHT_RESERVE_PCT
    # Learned from the morning gaps; share of the mornings it covers (above
    # 100 %: the largest gap times this).
    night_reserve_auto: bool = False
    night_reserve_coverage_pct: float = 100.0
    # Feed-in cap at the grid connection point (see feed_in_cap).
    feed_in_cap: bool = False
    pv_peak_power_kwp: float = DEFAULT_PV_PEAK_POWER_KWP
    feed_in_cap_limit_pct: float = DEFAULT_FEED_IN_CAP_LIMIT_PCT
    # Buffer on the energy to absorb (may be negative) and the minimum buffer
    # per peak in % of the peak power (as energy of one hour).
    feed_in_cap_buffer_pct: float = DEFAULT_FEED_IN_CAP_BUFFER_PCT
    feed_in_cap_min_buffer_pct: float = DEFAULT_FEED_IN_CAP_MIN_BUFFER_PCT
    # Buffer from the recorded PV forecast errors instead of the fixed one.
    feed_in_cap_auto_buffer: bool = False
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
    # Grid power after the conservative averaging (see grid_filter).
    grid_power_filtered_w: float | None = None
    pv_power_w: float | None = None
    # Per battery subentry id; missing if the battery could not be read.
    batteries: dict[str, BatteryTelemetry] = field(default_factory=dict)
    consumer_configs: dict[str, ConsumerConfig] = field(default_factory=dict)
    consumers: dict[str, ConsumerState] = field(default_factory=dict)
    # Consumers that did not draw the commanded power (own thermostat); they
    # are treated like uncontrolled loads for a while.
    saturated: frozenset[str] = frozenset()
    # Consumers with a cycling thermostat that draw nothing right now; they
    # keep their command, the batteries get their unused power.
    resting: frozenset[str] = frozenset()
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
    # Ratio of today's PV production to the forecast until now.
    pv_correction: float = 1.0
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
        """Total AC battery power (+charge / -discharge) of all readable batteries."""
        powers = [
            power
            for b in self.batteries.values()
            if (power := b.grid_side_power_w) is not None
        ]
        return sum(powers) if powers else None

    def _consumer_power_w(self, included_in_meter: bool) -> float:
        return sum(
            state.power_w or 0.0
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
        the PV and battery measurements.
        """
        if self.grid_power_w is None:
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
        # PV energy produced today (in memory): (local date, Wh, last sample).
        self._pv_day: tuple[object, float, float | None, float | None] | None = None
        # True once the integration covers a whole day since midnight.
        self._pv_complete = False
        self._store: Store[dict] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.efficiency"
        )
        self.forecaster = ConsumptionForecaster(hass, self._forecast_sources())
        self.controller = RealTimeController(self)
        self.problems = ProblemReporter(hass, self)
        self.pv_accuracy = PvAccuracyTracker()
        # Monotonic time since the export is above the feed-in cap.
        self.cap_exceeded_since: float | None = None
        self.grid_targets = GridTargetLearner()
        self.morning_gap = MorningGapLearner()
        self.consumer_learners = {c.subentry_id: ConsumerLearner() for c in consumers}
        # Consumers whose learned power and thermostat behaviour are used.
        self.consumer_learning: set[str] = set()

    @property
    def _config(self):
        return self.config_entry.options or self.config_entry.data

    @property
    def grid_entity_id(self) -> str:
        return self._config[CONF_GRID_POWER_ENTITY]

    async def _async_setup(self) -> None:
        """Restore learned data and follow the grid meter."""
        stored = await self._store.async_load() or {}
        self.pv_accuracy.restore(stored.get(PV_ACCURACY_STORE_KEY))
        self.morning_gap = MorningGapLearner.from_dict(stored.get(MORNING_GAP_STORE_KEY))
        for subentry_id, data in (stored.get(CONSUMERS_STORE_KEY) or {}).items():
            if subentry_id in self.consumer_learners:
                self.consumer_learners[subentry_id] = ConsumerLearner.from_dict(data)
        if gain := (stored.get(CONTROL_STORE_KEY) or {}).get("gain"):
            self.controller.gain_adapter.reset(gain)
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

        grid_entity = self._config[CONF_GRID_POWER_ENTITY]
        self._add_grid_sample(self.hass.states.get(grid_entity))
        self.config_entry.async_on_unload(
            async_track_state_change_event(self.hass, grid_entity, self._on_grid_change)
        )
        self.config_entry.async_on_unload(
            async_track_state_report_event(self.hass, grid_entity, self._on_grid_report)
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

    async def _on_forecast_time(self, _now: datetime) -> None:
        await self.async_refresh_forecast()

    async def async_refresh_forecast(self) -> None:
        """Refit the consumption forecast from the recorder statistics."""
        self.forecaster.sources = self._forecast_sources()
        try:
            await self.forecaster.async_refresh(self.settings.vacation)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Consumption forecast failed")

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
            pv=config.get(CONF_PV_POWER_ENTITY),
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
            controllable=tuple(
                c.power_entity_id
                for c in included
                if c.controllable and c.consumer_type is not ConsumerType.HEAT_PUMP
            ),
            temperature=config.get(CONF_OUTDOOR_TEMPERATURE_ENTITY)
            or own(f"{entry_id}_outdoor_temperature"),
            weather=config.get(CONF_WEATHER_ENTITY),
        )

    @callback
    def _on_grid_change(self, event: Event[EventStateChangedData]) -> None:
        self.controller.observe_meter_report()
        grid = self._add_grid_sample(event.data["new_state"])
        if grid is not None:
            self.controller.observe_grid(grid)
        self.controller.request()

    @callback
    def _on_grid_report(self, _event: Event) -> None:
        # Reported without a change of the value: only the cadence is new.
        self.controller.observe_meter_report()

    def _add_grid_sample(self, state) -> float | None:
        grid = state_as_watts(state)
        if grid is None:
            return None
        if self._config.get(CONF_GRID_POWER_INVERTED, False):
            grid = -grid
        self._grid_filter.add(time.monotonic(), grid)
        return grid

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
            }
            for b in self.batteries
        }
        data[CONSUMERS_STORE_KEY] = {
            subentry_id: learner.as_dict() for subentry_id, learner in self.consumer_learners.items()
        }
        data[CONTROL_STORE_KEY] = {"gain": self.controller.gain_adapter.gain}
        data[PV_ACCURACY_STORE_KEY] = self.pv_accuracy.as_dict()
        data[MORNING_GAP_STORE_KEY] = self.morning_gap.as_dict()
        return data

    async def _async_update_data(self) -> SystemSnapshot:
        config = self._config
        now = time.monotonic()
        snapshot = SystemSnapshot(
            consumer_configs={c.subentry_id: c for c in self.consumers},
            control_disabled=frozenset(self.consumer_control_disabled),
        )

        grid = state_as_watts(self.hass.states.get(config[CONF_GRID_POWER_ENTITY]))
        if grid is not None and config.get(CONF_GRID_POWER_INVERTED, False):
            grid = -grid
        snapshot.grid_power_w = grid
        self._grid_filter.window_s = self.average_window_s
        snapshot.grid_power_filtered_w = self._grid_filter.conservative(now)
        if pv_entity := config.get(CONF_PV_POWER_ENTITY):
            snapshot.pv_power_w = state_as_watts(self.hass.states.get(pv_entity))
        self._integrate_pv(snapshot.pv_power_w, now)

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
            self.consumer_learners[consumer.subentry_id].update(
                now, command is not None and command > 0, snapshot.consumers[consumer.subentry_id].power_w
            )

        if forecast_entries := config.get(CONF_PV_FORECAST_ENTRIES):
            snapshot.pv_forecast = await async_get_pv_forecast(self.hass, forecast_entries)
            if snapshot.pv_forecast is not None:
                wall_now = dt_util.now()
                self.pv_accuracy.record_forecast(
                    wall_now.date(), energy_on_day(snapshot.pv_forecast, wall_now.date()), wall_now
                )
        snapshot.consumption_forecast = self.forecaster.forecast
        if weather := config.get(CONF_WEATHER_ENTITY):
            snapshot.outdoor_temperature_c = _weather_temperature(self.hass.states.get(weather))

        # A failing battery must not take the whole system down: it is simply
        # missing from the snapshot and its entities become unavailable.
        for battery in self.batteries:
            if battery.paused_until is not None:
                if battery.communication_paused:
                    continue
                self.resume_communication(battery)
            try:
                telemetry = await battery.driver.read_telemetry()
            except BatteryDriverError as err:
                _LOGGER.debug("Battery %s unavailable: %s", battery.name, err)
                if battery.unreadable_since is None:
                    battery.unreadable_since = now
                continue
            battery.unreadable_since = None
            if telemetry.extra.get("inverter_state") == "ota_upgrade":
                # A firmware update is running: no further Modbus traffic.
                await self.async_pause_communication(battery, "firmware_update")
                continue
            await self._async_read_device_info(battery, now)
            snapshot.batteries[battery.subentry_id] = telemetry
            self._update_efficiency(battery, telemetry, now)
            if telemetry.ac_power_w is not None:
                battery.loss_curve.add(telemetry.ac_power_w, telemetry.power_w)
            battery.soc_window.update(telemetry.soc_pct, battery.limits)
            battery.capacity_learner.update(
                now, telemetry.soc_pct, telemetry.power_w, battery.driver.capabilities.capacity_wh
            )
            battery.power_limits = self._power_limits(battery, telemetry)
            self._check_delivery(battery, telemetry, now)
        # After all batteries were read: the balancing share of the surplus
        # depends on the other batteries.
        for battery in self.batteries:
            self._update_cells(battery, snapshot, now)
        self._store.async_delay_save(self._data_to_store, STORAGE_SAVE_DELAY_S)

        self._house_hold.check(snapshot, now)
        self._learn_morning_gap(snapshot, now)
        snapshot.saturated = self.controller.saturated
        snapshot.resting = self.controller.resting
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

    def _integrate_pv(self, pv_power_w: float | None, now: float) -> None:
        """Add up today's PV energy from the live values (restarts at midnight)."""
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
        pv_entity = self._config.get(CONF_PV_POWER_ENTITY)
        if not pv_entity:
            return
        wall_now = dt_util.now()
        try:
            means = await async_statistic_means(
                self.hass,
                [pv_entity],
                dt_util.start_of_local_day(dt_util.as_local(wall_now)),
                wall_now,
                "5minute",
            )
        except Exception:  # noqa: BLE001
            _LOGGER.debug("PV statistics of today not available", exc_info=True)
            return
        energy, covered_until = energy_from_means(means.get(pv_entity, {}), timedelta(minutes=5))
        if covered_until is None:
            return
        # Bridge the minutes since the last statistics period with the current value.
        pv_now = self.data.pv_power_w if self.data is not None else None
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

    def _power_limits(
        self, battery: BatteryRuntime, telemetry: BatteryTelemetry, *, use_soc_window: bool = True
    ) -> PowerLimits:
        caps = battery.driver.capabilities
        return power_limits(
            caps.max_charge_power_w,
            caps.max_discharge_power_w,
            battery.limits,
            battery.soc_window,
            telemetry.extra.get("internal_temperature"),
            self.settings.temperature(),
            # Read-only batteries follow their own logic.
            use_soc_window=use_soc_window and caps.controllable,
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
        action = battery.delivery.observe(
            now, commanded, since, telemetry.grid_side_power_w, telemetry.soc_pct
        )
        if action is Action.WAKE:
            self.controller.force_refresh(battery.subentry_id)
        elif action is Action.EXCLUDE:
            self.controller.request()

    def _balancing_surplus_w(self, battery: BatteryRuntime, snapshot: SystemSnapshot) -> float:
        """PV surplus a balancing battery may take (before the other batteries).

        What would be fed in if neither the batteries in normal operation nor
        the controlled consumers nor this battery took power.
        """
        if snapshot.grid_power_filtered_w is None:
            return 0.0
        batteries_w = sum(
            power
            for b in self.batteries
            if (b.participating or b is battery)
            and (telemetry := snapshot.batteries.get(b.subentry_id)) is not None
            and (power := telemetry.grid_side_power_w) is not None
        )
        return (
            -snapshot.grid_power_filtered_w
            + batteries_w
            + snapshot.controlled_consumer_power_w()
        )

    async def async_pause_communication(self, battery: BatteryRuntime, reason: str) -> None:
        """Stop all communication with a battery for the configured time.

        A manual pause first hands the battery back to its own logic; during a
        detected firmware update nothing is sent any more.
        """
        if reason == "manual" and battery.driver.capabilities.controllable:
            await self.async_release_battery(battery)
        await battery.driver.close()
        battery.paused_until = (
            dt_util.utcnow().timestamp() + self.settings.communication_pause_min * 60
        )
        battery.pause_reason = reason
        battery.unreadable_since = None
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
        self._store.async_delay_save(self._data_to_store, BALANCING_SAVE_DELAY_S)

    async def _async_read_device_info(self, battery: BatteryRuntime, now: float) -> None:
        """Read the device information once after connecting and then every few hours."""
        if battery.device_info_read is not None and now - battery.device_info_read < DEVICE_INFO_INTERVAL_S:
            return
        battery.device_info_read = now
        try:
            battery.device_info = await battery.driver.read_device_info() or battery.device_info
        except BatteryDriverError as err:
            _LOGGER.debug("Battery %s: device information not read: %s", battery.name, err)

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

    def _battery_group(self, snapshot: SystemSnapshot) -> BatteryGroup | None:
        energy = capacity = max_charge = max_discharge = weighted_eff = 0.0
        min_soc = full_soc = 0.0
        for battery in self.batteries:
            telemetry = snapshot.batteries.get(battery.subentry_id)
            if not battery.plannable or telemetry is None or telemetry.soc_pct is None:
                continue
            caps = battery.driver.capabilities
            limits = battery.power_limits or self._power_limits(battery, telemetry)
            energy += telemetry.soc_pct * battery.capacity_wh
            capacity += battery.capacity_wh
            max_charge += limits.charge_w
            max_discharge += limits.discharge_w
            weighted_eff += battery.efficiency.one_way * battery.capacity_wh
            if caps.controllable:
                min_soc += battery.limits.min_soc_pct * battery.capacity_wh
                full_soc += battery.limits.max_soc_pct * battery.capacity_wh
            else:
                full_soc += 100.0 * battery.capacity_wh
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
        if snapshot.grid_power_filtered_w is None:
            return
        controlled_w = measured_controlled_w = snapshot.controlled_consumer_power_w()
        if previous_total_w is not None:
            # Controller cycle: the consumers as the grid meter shows them, like
            # the batteries (commands still on their way count as not yet done).
            controlled_w = sum(
                self.controller.consumer_power_seen(subentry_id, state.power_w, now)
                for subentry_id, state in snapshot.consumers.items()
                if snapshot.is_controllable_now(subentry_id)
            )
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
            -snapshot.grid_power_filtered_w
            + enabled_battery_w
            + controlled_w
            + balancing_discharge_w
        )
        house = snapshot.house_power_w
        load = None if house is None else house - measured_controlled_w
        consumption = snapshot.consumption_forecast
        pv_forecast = snapshot.pv_forecast
        if pv_forecast is not None:
            snapshot.pv_correction = pv_correction(pv_forecast, wall_now, self._pv_today_wh)
            pv_forecast = {k: v * snapshot.pv_correction for k, v in pv_forecast.items()}
        # Balancing batteries take the PV surplus first.
        balancing_wh = self._balancing_charge_wh(snapshot)
        snapshot.expected_surplus_wh = expected_surplus_wh(
            pv_forecast, wall_now, load, consumption.total if consumption else None
        )
        if snapshot.expected_surplus_wh is not None:
            snapshot.expected_surplus_wh = max(0.0, snapshot.expected_surplus_wh - balancing_wh)
        battery = self._battery_group(snapshot)
        settings = self.settings
        forecast_plan = self.forecast_plan(snapshot, battery, wall_now, load, settings, balancing_wh)
        cap = forecast_plan.cap
        snapshot.feed_in_cap = cap
        snapshot.feed_in_limit_w = forecast_plan.feed_in_limit_w
        snapshot.feed_in_limit_reason = forecast_plan.feed_in_limit_reason
        peak_threshold_pct = forecast_plan.peak_threshold_pct
        peak_limit_w = forecast_plan.peak_limit_w
        snapshot.peak_shaving_limit_w = peak_limit_w if settings.peak_shaving else None
        snapshot.day_plan = forecast_plan.day_plan
        snapshot.day_plan_tomorrow = forecast_plan.day_plan_tomorrow

        requests = [
            ConsumerRequest(
                subentry_id=consumer.subentry_id,
                priority=consumer.priority,
                control_mode=consumer.control_mode,
                nominal_power_w=consumer.nominal_power_w or 0,
                min_power_w=consumer.min_power_w or 0,
                max_power_w=consumer.max_power_w or 0,
                must_stay_on=self._runtime.must_stay_on(consumer, now),
                must_stay_off=self._runtime.must_stay_off(consumer, now),
                cap_mode=self.cap_mode(consumer.subentry_id),
            )
            for consumer in map(self.effective_consumer, self.consumers)
            if snapshot.is_controllable_now(consumer.subentry_id)
        ]
        # Always set: in active mode the snapshot is a copy of the previous one.
        snapshot.night_discharge = None
        if (
            settings.night_discharge
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
                snapshot.consumption_forecast.total,
                self.night_reserve_pct(settings),
                self.secured_buffer_wh(settings, *self._next_day_energy(snapshot, wall_now)),
                battery.min_soc_pct / 100 * battery.capacity_wh,
                (
                    lambda moment: battery.full_soc_pct / 100 * battery.capacity_wh
                    - cap.space_needed_at(moment)
                )
                if cap is not None
                else None,
            )
        cap_control = (
            CapControl(
                limit_w=cap.limit_w,
                hold_charging=cap.hold_charging,
                export_w=cap.export_power_w,
                margin_w=CAP_MARGIN_W,
            )
            if cap is not None
            else None
        )
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
            snapshot.night_discharge.power_w if snapshot.night_discharge else None,
            snapshot.feed_in_limit_w,
            cap_control,
        )
        unused_w = sum(
            max(0.0, power - (snapshot.consumers[subentry_id].power_w or 0.0))
            for subentry_id, power in allocation.consumer_power_w.items()
            if subentry_id in snapshot.resting
        )
        if unused_w and battery is not None:
            max_charge = 0.0 if battery.is_full else battery.max_charge_w
            allocation.battery_power_w = max(
                allocation.battery_power_w,
                min(max_charge, allocation.battery_power_w + unused_w),
            )
        if previous_total_w is not None:
            allocation.battery_power_w = previous_total_w + gain * (
                allocation.battery_power_w - previous_total_w
            )
        if snapshot.grid_power_w is not None:
            allocation.battery_power_w = limit_discharge_export(
                allocation.battery_power_w,
                enabled_battery_w,
                snapshot.grid_power_w,
                max_discharge_export_w(settings.allocation_settings(), cap_control),
            )
        if previous_total_w is not None:
            self._learn_grid_target(snapshot, allocation, battery, settings)
        for subentry_id, power in allocation.consumer_power_w.items():
            self._runtime.update(subentry_id, power > 0, now)
        snapshot.allocation = allocation
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
        )

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
        """Buffer of charge secured and night discharge: PV too high plus consumption too low."""
        pv_share = self.pv_overestimate[0]
        consumption_share = self.consumption_underestimate[0]
        if settings.charge_secured_buffer_auto and pv_share is not None and consumption_share is not None:
            return pv_share * pv_wh + consumption_share * consumption_wh
        return settings.charge_secured_buffer_kwh * 1000

    def grid_target_w(self, settings: ControlSettings, *, charging: bool) -> float:
        learned = self.grid_targets.target_w(charging)
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
        """The consumer with its learned power and thermostat behaviour, if switched on."""
        if consumer.subentry_id not in self.consumer_learning:
            return consumer
        learner = self.consumer_learners[consumer.subentry_id]
        nominal = learner.nominal_w
        return replace(
            consumer,
            nominal_power_w=(
                nominal
                if nominal is not None and consumer.control_mode is ControlMode.SWITCH
                else consumer.nominal_power_w
            ),
            thermostat_cycles=consumer.thermostat_cycles or learner.thermostat_cycles,
        )

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
            self._energy(snapshot.pv_forecast, start, end) * snapshot.pv_correction
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
    ) -> None:
        """Sample the grid deviation while the batteries control the grid (active mode)."""
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
        if allocation.strategy in (Strategy.BATTERY_PRIORITY, Strategy.SHARED, Strategy.GRID_FRIENDLY):
            if 50 < power < 0.9 * battery.max_charge_w:
                self.grid_targets.add(True, grid, self.grid_target_w(settings, charging=True))
        elif allocation.strategy is Strategy.SELF_CONSUMPTION:
            if -0.9 * battery.max_discharge_w < power < -50:
                self.grid_targets.add(False, grid, self.grid_target_w(settings, charging=False))

    def forecast_plan(
        self,
        snapshot: SystemSnapshot,
        battery: BatteryGroup | None,
        wall_now: datetime,
        load: float | None,
        settings: ControlSettings,
        balancing_wh: float = 0.0,
        power_w: tuple[float, float] | None = None,
    ) -> ForecastPlan:
        """Plans from the forecasts with ``settings``: feed-in limit, feed-in
        cap, peak shaving and the SoC projection with the day plans.

        Used for the real planning and, with other settings, battery and
        forecasts, for the simulation. ``power_w`` replaces the charge and
        discharge power of the batteries for the feed-in cap.
        """
        result = ForecastPlan()
        consumption = snapshot.consumption_forecast
        pv_forecast = (
            {k: v * snapshot.pv_correction for k, v in snapshot.pv_forecast.items()}
            if snapshot.pv_forecast is not None
            else None
        )
        cap = self._feed_in_cap(snapshot, battery, wall_now, settings, power_w)
        result.cap = cap
        if not settings.grid_friendly_charging:
            result.feed_in_limit_reason = "disabled"
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
        # The correction factor describes today; tomorrow uses the raw forecast.
        today = dt_util.as_local(wall_now).date()
        pv_hourly = (
            {
                start: wh * (snapshot.pv_correction if start.date() == today else 1.0)
                for start, wh in hourly(snapshot.pv_forecast).items()
            }
            if snapshot.pv_forecast is not None
            else None
        )
        consumption_hourly = hourly(consumption.total) if consumption else None
        result.peak_threshold_pct, result.peak_limit_w = self._peak_shaving(
            battery, wall_now, pv_hourly, consumption_hourly, settings
        )
        if pv_forecast is None:
            return result
        if battery is not None:
            result.projection = project_soc(
                wall_now,
                battery,
                pv_hourly,
                consumption_hourly,
                load,
                ProjectionSettings(
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
                ),
                result.feed_in_limit_w,
                balancing_wh,
                cap,
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
        )
        return result

    @staticmethod
    def _pv_native(snapshot: SystemSnapshot, wall_now: datetime) -> PvForecast:
        """PV forecast in its own periods; today corrected, tomorrow raw."""
        today = dt_util.as_local(wall_now).date()
        return {
            start: wh * (snapshot.pv_correction if dt_util.as_local(start).date() == today else 1.0)
            for start, wh in (snapshot.pv_forecast or {}).items()
        }

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
                limits = self._power_limits(runtime, telemetry, use_soc_window=False)
                charge_w += limits.charge_w
                discharge_w += limits.discharge_w
        if power_w is not None:
            charge_w, discharge_w = power_w
        counted_w = emergency_w = 0.0
        for consumer in map(self.effective_consumer, self.consumers):
            if not snapshot.is_controllable_now(consumer.subentry_id):
                continue
            power = (
                consumer.nominal_power_w
                if consumer.control_mode is ControlMode.SWITCH
                else consumer.max_power_w
            ) or 0
            mode = self.cap_mode(consumer.subentry_id)
            if mode is CapMode.COUNT:
                counted_w += power
            elif mode is CapMode.EMERGENCY:
                emergency_w += power
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
                    settings.pv_peak_power_kwp * 1000 * settings.feed_in_cap_min_buffer_pct / 100
                ),
                pv_factor=pv_factor,
            ),
            counted_w,
            emergency_w,
        )

    def cap_mode(self, subentry_id: str) -> CapMode:
        return self.consumer_cap_modes.get(subentry_id, CapMode.EMERGENCY)

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
                hours_until_refill(wall_now, pv_hourly, consumption_hourly, refill_wh),
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
        soc = projection.soc_pct if projection else {}
        grid = projection.grid_w if projection else {}
        rows = []
        for hour in range(24):
            start = day_start + timedelta(hours=hour)
            rows.append(
                {
                    "start": start.isoformat(),
                    "pv_wh": round(pv_hourly.get(start, 0.0)),
                    "consumption_wh": (
                        round(consumption_hourly[start]) if start in consumption_hourly else None
                    ),
                    "planned_charge_w": round(planned[start]) if start in planned else None,
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
        grid = state_as_watts(self.hass.states.get(config[CONF_GRID_POWER_ENTITY]))
        if grid is not None and config.get(CONF_GRID_POWER_INVERTED, False):
            grid = -grid
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
            grid_power_filtered_w=self._grid_filter.conservative(now),
            batteries=batteries,
            consumers={
                consumer.subentry_id: read_consumer_state(self.hass, consumer)
                for consumer in self.consumers
            },
            saturated=saturated,
            resting=self.controller.resting,
            control_disabled=frozenset(self.consumer_control_disabled),
            allocation=None,
            distribution=None,
        )
        if pv_entity := config.get(CONF_PV_POWER_ENTITY):
            snapshot.pv_power_w = state_as_watts(self.hass.states.get(pv_entity))
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
            units.append(
                BatteryUnit(
                    battery_id=battery.subentry_id,
                    soc_pct=telemetry.soc_pct,
                    max_charge_w=limits.charge_w,
                    max_discharge_w=limits.discharge_w,
                    loss_model=battery.loss_curve.model(LossModel()),
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

    async def async_release_battery(self, battery: BatteryRuntime) -> None:
        """Hand one battery back to its internal logic."""
        if battery.driver.capabilities.controllable:
            try:
                await battery.driver.release_control()
            except BatteryDriverError as err:
                _LOGGER.debug("Battery %s not released: %s", battery.name, err)

    async def async_shutdown(self) -> None:
        """Save learned data and close all battery connections."""
        self.controller.shutdown()
        await super().async_shutdown()
        await self._store.async_save(self._data_to_store())
        if self.settings.operating_mode is OperatingMode.ACTIVE:
            await self.async_release_batteries()
        for battery in self.batteries:
            await battery.driver.close()


def _weather_temperature(state) -> float | None:
    """Current temperature of a weather entity in °C."""
    if state is None:
        return None
    temperature = state.attributes.get("temperature")
    if temperature is None:
        return None
    unit = state.attributes.get("temperature_unit", UnitOfTemperature.CELSIUS)
    return TemperatureConverter.convert(
        float(temperature), unit, UnitOfTemperature.CELSIUS
    )
