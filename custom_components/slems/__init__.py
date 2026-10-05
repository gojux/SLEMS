"""SLEMS - energy management for batteries and consumers in Home Assistant."""

from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.event import async_track_time_change

from .battery_wear import wear_cost
from .const import (
    CONF_EFFICIENCY_MODE,
    CONF_ROUND_TRIP_EFFICIENCY_PCT,
    CONF_CAPACITY_WH,
    CONF_PURCHASE_PRICE_EUR,
    CONF_RATED_CYCLES,
    CONF_SELF_TEST_ON_SETUP,
    DEFAULT_ROUND_TRIP_EFFICIENCY_PCT,
    DOMAIN,
    MANUFACTURER,
    REMOVED_SETTINGS,
    SUBENTRY_TYPE_BATTERY,
    SUBENTRY_TYPE_CONSUMER,
    SUBENTRY_TYPE_TARIFF,
    EfficiencyMode,
)
from .consumers import ConsumerConfig
from .coordinator import BatteryRuntime, SlemsConfigEntry, SlemsCoordinator
from .drivers import BatteryDriver, create_driver
from .efficiency import EfficiencyTracker
from .panel import async_register_panel, async_unregister_panel
from .problems import async_remove_issues
from .simulation import async_register_websocket
from .price_chart import async_register_websocket as async_register_price_websocket
from .tariff_comparison import async_register_websocket as async_register_tariff_websocket
from .tariff_updates import async_check_tariff_updates

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TIME,
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
                wear=wear_cost(
                    subentry.data.get(CONF_PURCHASE_PRICE_EUR),
                    subentry.data.get(CONF_RATED_CYCLES),
                    subentry.data.get(CONF_CAPACITY_WH),
                ),
                self_test_pending=bool(subentry.data.get(CONF_SELF_TEST_ON_SETUP))
                and driver.capabilities.controllable,
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

    _remove_old_settings(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await async_register_panel(hass, entry)
    async_register_websocket(hass)
    async_register_tariff_websocket(hass)
    async_register_price_websocket(hass)
    # The switch and select restored consent and source when their platforms were set up.
    coordinator.market_prices.start()
    # Newer prices of the templates of a tariff: after the start and every morning.
    entry.async_create_background_task(hass, async_check_tariff_updates(hass, entry), "slems tariff updates")

    async def _check_tariffs(_now) -> None:
        await async_check_tariff_updates(hass, entry)

    entry.async_on_unload(async_track_time_change(hass, _check_tariffs, hour=6, minute=0, second=0))
    coordinator.setup_key = _setup_key(entry)
    return True


def _remove_old_settings(hass: HomeAssistant, entry: SlemsConfigEntry) -> None:
    """Remove the entities of settings that no longer exist."""
    registry = er.async_get(hass)
    for key in REMOVED_SETTINGS:
        if entity_id := registry.async_get_entity_id(Platform.NUMBER, DOMAIN, f"{entry.entry_id}_{key}"):
            registry.async_remove(entity_id)


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
    """Reload the entry after its configuration changed; a change of tariffs only
    needs no reload (they are read with every computation), just a refresh."""
    coordinator = getattr(entry, "runtime_data", None)
    if coordinator is not None and coordinator.setup_key == _setup_key(entry):
        coordinator.tariffs_changed()
        hass.async_create_task(async_check_tariff_updates(hass, entry))
        return
    hass.config_entries.async_schedule_reload(entry.entry_id)


def _setup_key(entry: SlemsConfigEntry) -> str:
    """Everything the setup depends on: the configuration without the tariffs."""
    subentries = sorted(
        (subentry.subentry_id, subentry.subentry_type, subentry.title, dict(subentry.data))
        for subentry in entry.subentries.values()
        if subentry.subentry_type != SUBENTRY_TYPE_TARIFF
    )
    return json.dumps([dict(entry.data), dict(entry.options), subentries], sort_keys=True, default=str)


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
