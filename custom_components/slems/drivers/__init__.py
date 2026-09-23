"""Battery drivers and the factory that builds them from subentry data."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.core import HomeAssistant

from ..const import (
    CONF_CAPACITY_WH,
    CONF_HOST,
    CONF_MAX_CHARGE_POWER_W,
    CONF_MAX_DISCHARGE_POWER_W,
    CONF_MODEL,
    CONF_PORT,
    CONF_POWER_ENTITY,
    CONF_POWER_INVERTED,
    CONF_SOC_ENTITY,
    CONF_UNIT_ID,
    BatteryModel,
)
from .base import BatteryCapabilities, BatteryDriver, BatteryDriverError, BatteryTelemetry
from .ha_entities import HomeAssistantEntityDriver
from .marstek_venus_e3 import MarstekVenusE3Driver

__all__ = [
    "BatteryCapabilities",
    "BatteryDriver",
    "BatteryDriverError",
    "BatteryTelemetry",
    "create_driver",
]


def create_driver(hass: HomeAssistant, data: Mapping[str, Any]) -> BatteryDriver:
    """Build the driver described by a battery subentry."""
    model = BatteryModel(data[CONF_MODEL])
    if model is BatteryModel.MARSTEK_VENUS_E3:
        return MarstekVenusE3Driver(
            data[CONF_HOST],
            data[CONF_PORT],
            data[CONF_UNIT_ID],
            capacity_wh=data[CONF_CAPACITY_WH],
            max_charge_power_w=data[CONF_MAX_CHARGE_POWER_W],
            max_discharge_power_w=data[CONF_MAX_DISCHARGE_POWER_W],
        )
    if model is BatteryModel.HA_ENTITIES:
        return HomeAssistantEntityDriver(
            hass,
            data[CONF_SOC_ENTITY],
            data.get(CONF_POWER_ENTITY),
            power_inverted=data.get(CONF_POWER_INVERTED, False),
            capacity_wh=data[CONF_CAPACITY_WH],
            max_charge_power_w=data[CONF_MAX_CHARGE_POWER_W],
            max_discharge_power_w=data[CONF_MAX_DISCHARGE_POWER_W],
        )
    raise ValueError(f"Unsupported battery model: {model}")
