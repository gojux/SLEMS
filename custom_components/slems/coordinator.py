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
    ConsumerRequest,
    allocate,
    expected_surplus_wh,
    limit_discharge_export,
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
    ConsumerType,
    OperatingMode,
)
from .controller import RealTimeController
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
from .problems import ProblemReporter
from .efficiency import EfficiencyTracker, EnergyIntegrator, LossCurveLearner
from .forecast import (
    ConsumptionForecast,
    ConsumptionForecaster,
    ForecastSources,
    async_statistic_means,
)
from .grid_filter import GridPowerFilter
from .grid_friendly import (
    energy_from_means,
    feed_in_limit,
    pv_correction,
    remaining_surplus,
)
from .night_discharge import NightDischargePlan, plan_night_discharge
from .pv_forecast import PvForecast, async_get_pv_forecast, hourly
from .soc_projection import ProjectionSettings, SocProjection, project_soc
from .util import state_as_watts

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_SAVE_DELAY_S = 600
# Start, phase changes and end of a balancing run are saved sooner.
BALANCING_SAVE_DELAY_S = 5
# Store key of the controller data (next to the per battery subentry ids).
CONTROL_STORE_KEY = "control"


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
    limits: BatteryLimitSettings = field(default_factory=BatteryLimitSettings)
    soc_window: SocWindow = field(default_factory=SocWindow)
    # Allowed power right now (updated with every poll).
    power_limits: PowerLimits | None = None
    delivery: DeliveryMonitor = field(init=False)

    def __post_init__(self) -> None:
        self.delivery = DeliveryMonitor(self.name)

    @property
    def balancing_requested(self) -> bool:
        return self.balancer is not None

    @property
    def not_responding(self) -> bool:
        """Excluded for a while because it did not deliver the commanded power."""
        return self.delivery.excluded(time.monotonic())

    @property
    def participating(self) -> bool:
        """Planned and controlled (or still ramping out) by the normal operation."""
        return (
            (self.enabled and not self.balancing_requested) or self.leaving_until is not None
        ) and not self.not_responding

    @property
    def plannable(self) -> bool:
        """Takes part in planning, total SoC and distribution."""
        return self.enabled and not self.balancing_requested and not self.not_responding

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
    # Charge into the PV feed-in peak instead of as early as possible.
    grid_friendly_charging: bool = True
    # Energy grid friendly charging plans in addition to filling the
    # batteries (reserve against a too optimistic forecast).
    grid_friendly_buffer_kwh: float = DEFAULT_GRID_FRIENDLY_BUFFER_KWH
    night_discharge: bool = False
    # Night discharge reserve in % of tomorrow's forecast daily consumption.
    night_reserve_pct: float = DEFAULT_NIGHT_RESERVE_PCT
    # Charge power limited by the battery temperature (see battery_limits).
    temperature_limit: bool = False
    temperature_high_c: float = 40.0
    temperature_band_c: float = 10.0
    temperature_floor_pct: float = 40.0
    temperature_low_c: float = 0.0

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
    pv_forecast: PvForecast | None = None
    consumption_forecast: ConsumptionForecast | None = None
    # Current outdoor temperature of the weather entity (°C).
    outdoor_temperature_c: float | None = None
    night_discharge: NightDischargePlan | None = None
    # Power SLEMS can distribute, +surplus / -deficit.
    available_power_w: float | None = None
    expected_surplus_wh: float | None = None
    # Grid friendly charging: batteries charge only the surplus above this.
    feed_in_limit_w: float | None = None
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
        )

    def controlled_consumer_power_w(self) -> float:
        """Power of the consumers SLEMS may control right now (behind the meter)."""
        return sum(
            state.power_w or 0.0
            for subentry_id, state in self.consumers.items()
            if self.is_controllable_now(subentry_id)
        )

    @property
    def house_power_w(self) -> float | None:
        """Consumption behind the smart meter, derived from the energy balance."""
        if self.grid_power_w is None:
            return None
        return (
            self.grid_power_w
            + (self.pv_power_w or 0.0)
            - (self.battery_power_w or 0.0)
        )

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
        # Device registry id of the central SLEMS device (parent of the batteries).
        self.system_device_id = system_device_id
        self.settings = ControlSettings()
        self._grid_filter = GridPowerFilter(self.settings.surplus_average_window_s)
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

    @property
    def _config(self):
        return self.config_entry.options or self.config_entry.data

    @property
    def grid_entity_id(self) -> str:
        return self._config[CONF_GRID_POWER_ENTITY]

    async def _async_setup(self) -> None:
        """Restore learned data and follow the grid meter."""
        stored = await self._store.async_load() or {}
        if gain := (stored.get(CONTROL_STORE_KEY) or {}).get("gain"):
            self.controller.gain_adapter.reset(gain)
        for battery in self.batteries:
            data = stored.get(battery.subentry_id) or {}
            if integrator := data.get("integrator"):
                battery.efficiency.integrator = EnergyIntegrator.from_dict(integrator)
            if loss_curve := data.get("loss_curve"):
                battery.loss_curve = LossCurveLearner.from_dict(loss_curve)
            battery.cell_monitor.restore(data.get("cell_monitor"))
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
            }
            for b in self.batteries
        }
        data[CONTROL_STORE_KEY] = {"gain": self.controller.gain_adapter.gain}
        return data

    async def _async_update_data(self) -> SystemSnapshot:
        config = self._config
        now = time.monotonic()
        snapshot = SystemSnapshot(
            consumer_configs={c.subentry_id: c for c in self.consumers}
        )

        grid = state_as_watts(self.hass.states.get(config[CONF_GRID_POWER_ENTITY]))
        if grid is not None and config.get(CONF_GRID_POWER_INVERTED, False):
            grid = -grid
        snapshot.grid_power_w = grid
        self._grid_filter.window_s = self.settings.surplus_average_window_s
        snapshot.grid_power_filtered_w = self._grid_filter.conservative(now)
        if pv_entity := config.get(CONF_PV_POWER_ENTITY):
            snapshot.pv_power_w = state_as_watts(self.hass.states.get(pv_entity))
        self._integrate_pv(snapshot.pv_power_w, now)

        for consumer in self.consumers:
            snapshot.consumers[consumer.subentry_id] = read_consumer_state(
                self.hass, consumer
            )

        if forecast_entries := config.get(CONF_PV_FORECAST_ENTRIES):
            snapshot.pv_forecast = await async_get_pv_forecast(self.hass, forecast_entries)
        snapshot.consumption_forecast = self.forecaster.forecast
        if weather := config.get(CONF_WEATHER_ENTITY):
            snapshot.outdoor_temperature_c = _weather_temperature(self.hass.states.get(weather))

        # A failing battery must not take the whole system down: it is simply
        # missing from the snapshot and its entities become unavailable.
        for battery in self.batteries:
            try:
                telemetry = await battery.driver.read_telemetry()
            except BatteryDriverError as err:
                _LOGGER.debug("Battery %s unavailable: %s", battery.name, err)
                if battery.unreadable_since is None:
                    battery.unreadable_since = now
                continue
            battery.unreadable_since = None
            snapshot.batteries[battery.subentry_id] = telemetry
            self._update_efficiency(battery, telemetry, now)
            if telemetry.ac_power_w is not None:
                battery.loss_curve.add(telemetry.ac_power_w, telemetry.power_w)
            battery.soc_window.update(telemetry.soc_pct, battery.limits)
            battery.power_limits = self._power_limits(battery, telemetry)
            self._check_delivery(battery, telemetry, now)
        # After all batteries were read: the balancing share of the surplus
        # depends on the other batteries.
        for battery in self.batteries:
            self._update_cells(battery, snapshot, now)
        self._store.async_delay_save(self._data_to_store, STORAGE_SAVE_DELAY_S)

        snapshot.saturated = self.controller.saturated
        self.controller.observe_consumers(snapshot)
        mode = self.settings.operating_mode
        if mode is OperatingMode.ACTIVE and self.data is not None:
            # In active mode only the controller plans (it owns the state of
            # distribution and runtimes); keep showing its latest result.
            for name in (
                "available_power_w",
                "expected_surplus_wh",
                "feed_in_limit_w",
                "pv_correction",
                "day_plan",
                "day_plan_tomorrow",
                "allocation",
                "distribution",
                "night_discharge",
            ):
                setattr(snapshot, name, getattr(self.data, name))
        elif mode is not OperatingMode.OFF:
            self.plan(snapshot, dt_util.now(), now)
        # Runs after the new data has been stored.
        self.hass.loop.call_soon(self.controller.request)
        self.problems.update(now)
        return snapshot

    def _integrate_pv(self, pv_power_w: float | None, now: float) -> None:
        """Add up today's PV energy from the live values (restarts at midnight)."""
        today = dt_util.now().date()
        day, energy, last_time, last_power = self._pv_day or (today, 0.0, None, None)
        if day != today:
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
                battery.cell_monitor.update(now, max_cell, min_cell, power, wall)
            return
        # The run measures itself.
        battery.cell_monitor.pause()
        if not battery.enabled:
            self.end_balancing(battery, "cancelled")
            return
        if self.settings.operating_mode is not OperatingMode.ACTIVE or battery.leaving_until is not None:
            # Commands are only sent in active mode, and only after the ramp-out.
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
            total += missing * battery.driver.capabilities.capacity_wh / battery.efficiency.one_way
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
            energy += telemetry.soc_pct * caps.capacity_wh
            capacity += caps.capacity_wh
            max_charge += limits.charge_w
            max_discharge += limits.discharge_w
            weighted_eff += battery.efficiency.one_way * caps.capacity_wh
            if caps.controllable:
                min_soc += battery.limits.min_soc_pct * caps.capacity_wh
                full_soc += battery.limits.max_soc_pct * caps.capacity_wh
            else:
                full_soc += 100.0 * caps.capacity_wh
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
        controlled_w = snapshot.controlled_consumer_power_w()
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
        load = None if house is None else house - controlled_w
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
        snapshot.feed_in_limit_w = None
        if settings.grid_friendly_charging and battery is not None and pv_forecast is not None:
            surplus = remaining_surplus(
                pv_forecast, consumption.total if consumption else None, load, wall_now
            )
            snapshot.feed_in_limit_w = feed_in_limit(
                surplus,
                battery.energy_to_full_wh + settings.grid_friendly_buffer_kwh * 1000 + balancing_wh,
                battery.max_charge_w,
            )
        if pv_forecast is not None:
            # The correction factor describes today; tomorrow uses the raw forecast.
            today = dt_util.as_local(wall_now).date()
            pv_hourly = {
                start: wh * (snapshot.pv_correction if start.date() == today else 1.0)
                for start, wh in hourly(snapshot.pv_forecast).items()
            }
            consumption_hourly = hourly(consumption.total) if consumption else None
            projection = (
                project_soc(
                    wall_now,
                    battery,
                    pv_hourly,
                    consumption_hourly,
                    load,
                    ProjectionSettings(
                        grid_friendly_charging=settings.grid_friendly_charging,
                        charge_buffer_wh=settings.grid_friendly_buffer_kwh * 1000,
                        night_buffer_wh=settings.charge_secured_buffer_kwh * 1000,
                        peak_shaving=settings.peak_shaving,
                        peak_shaving_grid_limit_w=settings.peak_shaving_grid_limit_w,
                        peak_shaving_soc_threshold_pct=settings.peak_shaving_soc_threshold_pct,
                        night_discharge=settings.night_discharge,
                        night_reserve_pct=settings.night_reserve_pct,
                    ),
                    snapshot.feed_in_limit_w,
                    balancing_wh,
                )
                if battery is not None
                else None
            )
            snapshot.day_plan = self._day_plan(
                pv_hourly, consumption_hourly, projection, wall_now
            )
            snapshot.day_plan_tomorrow = self._day_plan(
                pv_hourly, consumption_hourly, projection, wall_now, day_offset=1
            )

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
            )
            for consumer in self.consumers
            if snapshot.is_controllable_now(consumer.subentry_id)
        ]
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
                settings.night_reserve_pct,
                settings.charge_secured_buffer_kwh * 1000,
                battery.min_soc_pct / 100 * battery.capacity_wh,
            )
        allocation = allocate(
            snapshot.available_power_w,
            battery,
            requests,
            settings.allocation_settings(),
            snapshot.expected_surplus_wh,
            snapshot.night_discharge.power_w if snapshot.night_discharge else None,
            snapshot.feed_in_limit_w,
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
                settings.discharge_max_grid_export_w,
            )
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

    @staticmethod
    def _day_plan(
        pv_hourly: dict[datetime, float],
        consumption_hourly: dict[datetime, float] | None,
        projection: SocProjection | None,
        wall_now: datetime,
        day_offset: int = 0,
    ) -> list[dict]:
        """Hours of a day for the dashboard.

        Forecasts in Wh; planned charge power (W) and projected total SoC at
        the end of the hour (%) for the hours from now on.
        """
        day_start = dt_util.start_of_local_day(dt_util.as_local(wall_now))
        day_start += timedelta(days=day_offset)
        consumption_hourly = consumption_hourly or {}
        planned = projection.planned_charge_w if projection else {}
        soc = projection.soc_pct if projection else {}
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
                }
            )
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
            allocation=None,
            distribution=None,
        )
        if pv_entity := config.get(CONF_PV_POWER_ENTITY):
            snapshot.pv_power_w = state_as_watts(self.hass.states.get(pv_entity))
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
