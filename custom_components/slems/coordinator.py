"""Data coordinator: collects measurements and computes the allocation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .allocation import (
    Allocation,
    AllocationSettings,
    BatteryGroup,
    ConsumerRequest,
    allocate,
    expected_surplus_wh,
)
from .const import (
    CONF_GRID_POWER_ENTITY,
    CONF_GRID_POWER_INVERTED,
    CONF_PV_FORECAST_ENTRIES,
    CONF_PV_POWER_ENTITY,
    DEFAULT_BATTERY_PRIORITY_SOC_PCT,
    DEFAULT_BATTERY_SHARE_WHEN_SECURED_PCT,
    DEFAULT_CHARGE_GRID_TARGET_W,
    DEFAULT_CHARGE_SECURED_BUFFER_KWH,
    DEFAULT_DISCHARGE_GRID_TARGET_W,
    DEFAULT_DISCHARGE_MAX_GRID_EXPORT_W,
    DEFAULT_NIGHT_RESERVE_PCT,
    DEFAULT_OPERATING_MODE,
    DEFAULT_PEAK_SHAVING_GRID_LIMIT_W,
    DEFAULT_PEAK_SHAVING_SOC_THRESHOLD_PCT,
    DEFAULT_SURPLUS_AVERAGE_WINDOW_S,
    DOMAIN,
    SCAN_INTERVAL,
    OperatingMode,
)
from .consumers import ConsumerConfig, ConsumerState, RuntimeTracker, read_consumer_state
from .drivers import BatteryDriver, BatteryDriverError, BatteryTelemetry
from .efficiency import EfficiencyTracker, EnergyIntegrator
from .grid_filter import GridPowerFilter
from .night_discharge import NightDischargePlan, plan_night_discharge
from .pv_forecast import PvForecast, async_get_pv_forecast
from .util import state_as_watts

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_SAVE_DELAY_S = 600


@dataclass
class BatteryRuntime:
    """A configured battery: its subentry id, name, driver and efficiency."""

    subentry_id: str
    name: str
    driver: BatteryDriver
    efficiency: EfficiencyTracker


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
    pv_forecast: PvForecast | None = None
    # Hourly consumption forecast (period start -> Wh) of the whole house.
    consumption_forecast: dict[datetime, float] | None = None
    night_discharge: NightDischargePlan | None = None
    # Power SLEMS can distribute, +surplus / -deficit.
    available_power_w: float | None = None
    expected_surplus_wh: float | None = None
    allocation: Allocation | None = None

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

    def controlled_consumer_power_w(self) -> float:
        """Power of the consumers SLEMS may control right now (behind the meter)."""
        return sum(
            state.power_w or 0.0
            for subentry_id, state in self.consumers.items()
            if (config := self.consumer_configs[subentry_id]).controllable
            and config.included_in_meter
            and not state.blocked
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
        self._store: Store[dict] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.efficiency"
        )

    @property
    def _config(self):
        return self.config_entry.options or self.config_entry.data

    async def _async_setup(self) -> None:
        """Restore learned data and follow the grid meter."""
        stored = await self._store.async_load() or {}
        for battery in self.batteries:
            if data := stored.get(battery.subentry_id):
                battery.efficiency.integrator = EnergyIntegrator.from_dict(data)

        grid_entity = self._config[CONF_GRID_POWER_ENTITY]
        self._add_grid_sample(self.hass.states.get(grid_entity))
        self.config_entry.async_on_unload(
            async_track_state_change_event(self.hass, grid_entity, self._on_grid_change)
        )

    @callback
    def _on_grid_change(self, event: Event[EventStateChangedData]) -> None:
        self._add_grid_sample(event.data["new_state"])

    def _add_grid_sample(self, state) -> None:
        grid = state_as_watts(state)
        if grid is None:
            return
        if self._config.get(CONF_GRID_POWER_INVERTED, False):
            grid = -grid
        self._grid_filter.add(time.monotonic(), grid)

    def _data_to_store(self) -> dict:
        return {b.subentry_id: b.efficiency.integrator.as_dict() for b in self.batteries}

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
        self._store.async_delay_save(self._data_to_store, STORAGE_SAVE_DELAY_S)

        if self.settings.operating_mode is not OperatingMode.OFF:
            self._allocate(snapshot, dt_util.now(), now)
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
            if telemetry is None or telemetry.soc_pct is None:
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

    def _allocate(self, snapshot: SystemSnapshot, wall_now: datetime, now: float) -> None:
        if snapshot.grid_power_filtered_w is None:
            return
        controlled_w = snapshot.controlled_consumer_power_w()
        snapshot.available_power_w = (
            -snapshot.grid_power_filtered_w
            + (snapshot.battery_power_w or 0.0)
            + controlled_w
        )
        house = snapshot.house_power_w
        load = None if house is None else house - controlled_w
        snapshot.expected_surplus_wh = expected_surplus_wh(
            snapshot.pv_forecast, wall_now, load
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
            if consumer.controllable
            and consumer.included_in_meter
            and not snapshot.consumers[consumer.subentry_id].blocked
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
                snapshot.consumption_forecast,
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
        for subentry_id, power in allocation.consumer_power_w.items():
            self._runtime.update(subentry_id, power > 0, now)
        snapshot.allocation = allocation

    async def async_release_batteries(self) -> None:
        """Hand all controllable batteries back to their internal logic."""
        for battery in self.batteries:
            if battery.driver.capabilities.controllable:
                await battery.driver.release_control()

    async def async_shutdown(self) -> None:
        """Save learned data and close all battery connections."""
        await super().async_shutdown()
        await self._store.async_save(self._data_to_store())
        if self.settings.operating_mode is OperatingMode.ACTIVE:
            await self.async_release_batteries()
        for battery in self.batteries:
            await battery.driver.close()
