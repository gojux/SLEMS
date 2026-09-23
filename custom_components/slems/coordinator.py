"""Data coordinator: collects measurements and computes the allocation."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
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
from .efficiency import EfficiencyTracker, EnergyIntegrator, LossCurveLearner
from .forecast import ConsumptionForecast, ConsumptionForecaster, ForecastSources
from .grid_filter import GridPowerFilter
from .night_discharge import NightDischargePlan, plan_night_discharge
from .pv_forecast import PvForecast, async_get_pv_forecast
from .util import state_as_watts

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_SAVE_DELAY_S = 600
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

    @property
    def participating(self) -> bool:
        """Enabled, or still ramping out after being disabled."""
        return self.enabled or self.leaving_until is not None


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
    night_discharge: bool = False
    # Night discharge reserve in % of tomorrow's forecast daily consumption.
    night_reserve_pct: float = DEFAULT_NIGHT_RESERVE_PCT

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
        self._store: Store[dict] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.efficiency"
        )
        self.forecaster = ConsumptionForecaster(hass, self._forecast_sources())
        self.controller = RealTimeController(self)

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
        self.config_entry.async_create_background_task(
            self.hass, self.async_refresh_forecast(), "slems consumption forecast"
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
                continue
            snapshot.batteries[battery.subentry_id] = telemetry
            self._update_efficiency(battery, telemetry, now)
            if telemetry.ac_power_w is not None:
                battery.loss_curve.add(telemetry.ac_power_w, telemetry.power_w)
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
                "allocation",
                "distribution",
                "night_discharge",
            ):
                setattr(snapshot, name, getattr(self.data, name))
        elif mode is not OperatingMode.OFF:
            self.plan(snapshot, dt_util.now(), now)
        # Runs after the new data has been stored.
        self.hass.loop.call_soon(self.controller.request)
        return snapshot

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
        for battery in self.batteries:
            telemetry = snapshot.batteries.get(battery.subentry_id)
            if not battery.enabled or telemetry is None or telemetry.soc_pct is None:
                continue
            caps = battery.driver.capabilities
            energy += telemetry.soc_pct * caps.capacity_wh
            capacity += caps.capacity_wh
            max_charge += caps.max_charge_power_w
            max_discharge += caps.max_discharge_power_w
            weighted_eff += battery.efficiency.one_way * caps.capacity_wh
        if not capacity:
            return None
        return BatteryGroup(
            soc_pct=energy / capacity,
            capacity_wh=capacity,
            max_charge_w=max_charge,
            max_discharge_w=max_discharge,
            charge_efficiency=weighted_eff / capacity,
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
        snapshot.available_power_w = (
            -snapshot.grid_power_filtered_w + enabled_battery_w + controlled_w
        )
        house = snapshot.house_power_w
        load = None if house is None else house - controlled_w
        consumption = snapshot.consumption_forecast
        snapshot.expected_surplus_wh = expected_surplus_wh(
            snapshot.pv_forecast, wall_now, load, consumption.total if consumption else None
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
        battery = self._battery_group(snapshot)
        settings = self.settings
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
            )
        allocation = allocate(
            snapshot.available_power_w,
            battery,
            requests,
            settings.allocation_settings(),
            snapshot.expected_surplus_wh,
            snapshot.night_discharge.power_w if snapshot.night_discharge else None,
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
            caps = battery.driver.capabilities
            units.append(
                BatteryUnit(
                    battery_id=battery.subentry_id,
                    soc_pct=telemetry.soc_pct,
                    max_charge_w=caps.max_charge_power_w,
                    max_discharge_w=caps.max_discharge_power_w,
                    loss_model=battery.loss_curve.model(LossModel()),
                    leaving_fraction=(
                        None
                        if battery.enabled
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
            await battery.driver.release_control()

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
