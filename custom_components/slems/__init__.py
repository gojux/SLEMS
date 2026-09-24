"""SLEMS - energy management for batteries and consumers in Home Assistant."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import (
    CONF_EFFICIENCY_MODE,
    CONF_ROUND_TRIP_EFFICIENCY_PCT,
    DEFAULT_ROUND_TRIP_EFFICIENCY_PCT,
    DOMAIN,
    MANUFACTURER,
    SUBENTRY_TYPE_BATTERY,
    SUBENTRY_TYPE_CONSUMER,
    EfficiencyMode,
)
from .consumers import ConsumerConfig
from .coordinator import BatteryRuntime, SlemsConfigEntry, SlemsCoordinator
from .drivers import BatteryDriver, create_driver
from .efficiency import EfficiencyTracker
from .panel import async_register_panel, async_unregister_panel
from .problems import async_remove_issues

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: SlemsConfigEntry) -> bool:
    """Set up SLEMS from a config entry."""
    # Options changes and added/removed/edited subentries all reload the entry.
    # Registered first so changes made while this setup is running are not lost.
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    system_device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="SLEMS",
        manufacturer=MANUFACTURER,
        model="Energy manager",
    )
    batteries = []
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_BATTERY:
            continue
        driver = create_driver(hass, subentry.data)
        batteries.append(
            BatteryRuntime(
                subentry_id=subentry.subentry_id,
                name=subentry.title,
                driver=driver,
                efficiency=_efficiency_tracker(driver, subentry.data),
            )
        )
    consumers = [
        ConsumerConfig.from_subentry(subentry.subentry_id, subentry.title, subentry.data)
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_TYPE_CONSUMER
    ]
    coordinator = SlemsCoordinator(hass, entry, batteries, consumers, system_device.id)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await async_register_panel(hass, entry)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SlemsConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        async_unregister_panel(hass)
        await entry.runtime_data.async_shutdown()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: SlemsConfigEntry) -> None:
    """Remove the repair issues of a removed SLEMS entry."""
    async_remove_issues(hass)


async def _async_reload_entry(hass: HomeAssistant, entry: SlemsConfigEntry) -> None:
    """Reload the entry after its configuration changed."""
    hass.config_entries.async_schedule_reload(entry.entry_id)


def _efficiency_tracker(driver: BatteryDriver, data: Mapping[str, Any]) -> EfficiencyTracker:
    """Build the efficiency tracker; counters are preferred if the battery has them."""
    mode = data.get(CONF_EFFICIENCY_MODE)
    if mode is None:
        mode = (
            EfficiencyMode.BATTERY_COUNTERS
            if supports_energy_counters(driver)
            else EfficiencyMode.LEARNED
        )
    return EfficiencyTracker(
        EfficiencyMode(mode),
        data.get(CONF_ROUND_TRIP_EFFICIENCY_PCT, DEFAULT_ROUND_TRIP_EFFICIENCY_PCT),
        driver.capabilities.capacity_wh / 1000,
    )


def supports_energy_counters(driver: BatteryDriver) -> bool:
    """True if the driver reports lifetime charge/discharge counters."""
    return {"total_charging_energy", "total_discharging_energy"} <= driver.extra_telemetry_keys
