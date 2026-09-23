"""SLEMS - energy management for batteries and consumers in Home Assistant."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, MANUFACTURER, SUBENTRY_TYPE_BATTERY
from .coordinator import BatteryRuntime, SlemsConfigEntry, SlemsCoordinator
from .drivers import create_driver

PLATFORMS: list[Platform] = [Platform.SELECT, Platform.SENSOR]


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
    batteries = [
        BatteryRuntime(
            subentry_id=subentry.subentry_id,
            name=subentry.title,
            driver=create_driver(hass, subentry.data),
        )
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_TYPE_BATTERY
    ]
    coordinator = SlemsCoordinator(hass, entry, batteries, system_device.id)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SlemsConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded


async def _async_reload_entry(hass: HomeAssistant, entry: SlemsConfigEntry) -> None:
    """Reload the entry after its configuration changed."""
    hass.config_entries.async_schedule_reload(entry.entry_id)
