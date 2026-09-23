"""Data coordinator: collects battery telemetry and grid/PV measurements."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_GRID_POWER_ENTITY,
    CONF_GRID_POWER_INVERTED,
    CONF_PV_POWER_ENTITY,
    DEFAULT_OPERATING_MODE,
    DOMAIN,
    SCAN_INTERVAL,
    OperatingMode,
)
from .drivers import BatteryDriver, BatteryDriverError, BatteryTelemetry
from .util import state_as_watts

_LOGGER = logging.getLogger(__name__)


@dataclass
class BatteryRuntime:
    """A configured battery: its subentry id, name and driver."""

    subentry_id: str
    name: str
    driver: BatteryDriver


@dataclass
class SystemSnapshot:
    """One consistent view of the energy system."""

    # Grid power, +import / -export.
    grid_power_w: float | None = None
    pv_power_w: float | None = None
    # Per battery subentry id; missing if the battery could not be read.
    batteries: dict[str, BatteryTelemetry] = field(default_factory=dict)

    @property
    def battery_power_w(self) -> float | None:
        """Total battery power (+charge / -discharge) of all readable batteries."""
        powers = [b.power_w for b in self.batteries.values() if b.power_w is not None]
        return sum(powers) if powers else None

    @property
    def house_power_w(self) -> float | None:
        """House consumption derived from the energy balance."""
        if self.grid_power_w is None:
            return None
        return (
            self.grid_power_w
            + (self.pv_power_w or 0.0)
            - (self.battery_power_w or 0.0)
        )


type SlemsConfigEntry = ConfigEntry[SlemsCoordinator]


class SlemsCoordinator(DataUpdateCoordinator[SystemSnapshot]):
    """Polls all batteries and reads the configured measurement entities."""

    config_entry: SlemsConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: SlemsConfigEntry,
        batteries: list[BatteryRuntime],
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
        # Device registry id of the central SLEMS device (parent of the batteries).
        self.system_device_id = system_device_id
        self.operating_mode: OperatingMode = DEFAULT_OPERATING_MODE

    async def _async_update_data(self) -> SystemSnapshot:
        config = self.config_entry.options or self.config_entry.data
        snapshot = SystemSnapshot()

        grid = state_as_watts(self.hass.states.get(config[CONF_GRID_POWER_ENTITY]))
        if grid is not None and config.get(CONF_GRID_POWER_INVERTED, False):
            grid = -grid
        snapshot.grid_power_w = grid
        if pv_entity := config.get(CONF_PV_POWER_ENTITY):
            snapshot.pv_power_w = state_as_watts(self.hass.states.get(pv_entity))

        # A failing battery must not take the whole system down: it is simply
        # missing from the snapshot and its entities become unavailable.
        for battery in self.batteries:
            try:
                snapshot.batteries[battery.subentry_id] = (
                    await battery.driver.read_telemetry()
                )
            except BatteryDriverError as err:
                _LOGGER.debug("Battery %s unavailable: %s", battery.name, err)
        return snapshot

    async def async_shutdown(self) -> None:
        """Close all battery connections."""
        await super().async_shutdown()
        for battery in self.batteries:
            if self.operating_mode is OperatingMode.ACTIVE:
                await battery.driver.release_control()
            await battery.driver.close()
