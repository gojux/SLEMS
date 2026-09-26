"""Sidebar dashboard panel served by the integration."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN
from .coordinator import SlemsConfigEntry

_LOGGER = logging.getLogger(__name__)

PANEL_URL_PATH = "slems"
STATIC_URL = "/slems_static"
PANEL_FILE = "slems-panel.js"
WEB_COMPONENT = "slems-panel"
_STATIC_REGISTERED = f"{DOMAIN}_static_registered"


async def async_register_panel(hass: HomeAssistant, entry: SlemsConfigEntry) -> None:
    """Register (or refresh) the sidebar panel for the config entry."""
    directory = Path(__file__).parent / "frontend"
    if not hass.data.get(_STATIC_REGISTERED):
        # Static paths cannot be removed again; register them once per HA run.
        await hass.http.async_register_static_paths(
            [StaticPathConfig(STATIC_URL, str(directory), cache_headers=False)]
        )
        hass.data[_STATIC_REGISTERED] = True

    # The file's modification time busts the browser cache after updates.
    version = int((directory / PANEL_FILE).stat().st_mtime)
    frontend.async_remove_panel(hass, PANEL_URL_PATH, warn_if_unknown=False)
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=WEB_COMPONENT,
        sidebar_title="SLEMS",
        sidebar_icon="mdi:battery-charging-high",
        module_url=f"{STATIC_URL}/{PANEL_FILE}?v={version}",
        config=_panel_config(hass, entry),
        require_admin=False,
    )


def async_unregister_panel(hass: HomeAssistant) -> None:
    """Remove the sidebar panel."""
    frontend.async_remove_panel(hass, PANEL_URL_PATH, warn_if_unknown=False)


def _panel_config(hass: HomeAssistant, entry: SlemsConfigEntry) -> dict:
    """Devices and entities the panel cannot find on its own."""
    devices = dr.async_get(hass)
    coordinator = entry.runtime_data

    def device_id(subentry_id: str) -> str | None:
        device = devices.async_get_device(identifiers={(DOMAIN, subentry_id)})
        return device.id if device else None

    return {
        "entry_id": entry.entry_id,
        "system_device_id": coordinator.system_device_id,
        "batteries": [
            {
                "id": battery.subentry_id,
                "name": battery.name,
                "device_id": device_id(battery.subentry_id),
            }
            for battery in coordinator.batteries
        ],
        "consumers": [
            {
                "id": consumer.subentry_id,
                "name": consumer.name,
                "device_id": device_id(consumer.subentry_id),
                "power_entity": consumer.power_entity_id,
                "type": consumer.consumer_type.value,
                "controllable": consumer.controllable,
            }
            for consumer in coordinator.consumers
        ],
    }
