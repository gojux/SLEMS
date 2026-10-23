"""Battery drivers and the factory that builds them from subentry data."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.components.modbus import async_get_unit
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from modbus_connection import ModbusTcpParams

from ..battery_profiles import BatteryProfile
from ..const import (
    CONF_CAPACITY_WH,
    CONF_HOST,
    CONF_MAX_CHARGE_POWER_W,
    CONF_MAX_DISCHARGE_POWER_W,
    CONF_MODEL,
    CONF_PORT,
    CONF_RELEASE_STATE,
    CONF_TRANSPORT,
    CONF_UNIT_ID,
    BatteryModel,
    ModbusTransport,
    ReleaseState,
)
from .base import BatteryCapabilities, BatteryDriver, BatteryDriverError, BatteryTelemetry
from .ha_entities import EntityBatteryConfig, HomeAssistantEntityDriver
from .marstek_venus_e3 import MarstekVenusE3Driver
from .profile import HaUnitTransport, ProfileDriver, ProfileTransport, own_link

__all__ = [
    "BatteryCapabilities",
    "BatteryDriver",
    "BatteryDriverError",
    "BatteryTelemetry",
    "create_driver",
]


def create_driver(
    hass: HomeAssistant,
    data: Mapping[str, Any],
    entry: ConfigEntry | None = None,
    profile: BatteryProfile | None = None,
) -> BatteryDriver:
    """Build the driver described by a battery subentry (``profile``: the one
    of a battery from a Modbus profile, loaded beforehand outside the event loop;
    ``entry`` for a unit of Home Assistant's Modbus backend)."""
    model = BatteryModel(data[CONF_MODEL])
    if model is BatteryModel.MARSTEK_VENUS_E3:
        return MarstekVenusE3Driver(
            data[CONF_HOST],
            data[CONF_PORT],
            data[CONF_UNIT_ID],
            capacity_wh=data[CONF_CAPACITY_WH],
            max_charge_power_w=data[CONF_MAX_CHARGE_POWER_W],
            max_discharge_power_w=data[CONF_MAX_DISCHARGE_POWER_W],
            release_state=ReleaseState(data.get(CONF_RELEASE_STATE, ReleaseState.AUTO)),
        )
    if model is BatteryModel.HA_ENTITIES:
        return HomeAssistantEntityDriver(hass, EntityBatteryConfig.from_data(data))
    if model is BatteryModel.MODBUS_PROFILE:
        if profile is None:
            raise ValueError("A battery from a Modbus profile needs its profile")
        return ProfileDriver(
            profile,
            _profile_transport(hass, entry, data, profile),
            capacity_wh=data[CONF_CAPACITY_WH],
            max_charge_power_w=data[CONF_MAX_CHARGE_POWER_W],
            max_discharge_power_w=data[CONF_MAX_DISCHARGE_POWER_W],
            release_state=ReleaseState(data.get(CONF_RELEASE_STATE, ReleaseState.AUTO)),
        )
    raise ValueError(f"Unsupported battery model: {model}")


def _profile_transport(
    hass: HomeAssistant, entry: ConfigEntry | None, data: Mapping[str, Any], profile: BatteryProfile
) -> ProfileTransport:
    host, port, unit_id = data[CONF_HOST], data[CONF_PORT], data[CONF_UNIT_ID]
    if ModbusTransport(data.get(CONF_TRANSPORT, ModbusTransport.OWN)) is ModbusTransport.OWN:
        return own_link(host, port, unit_id, profile)
    if entry is None:
        raise ValueError("Home Assistant's Modbus backend needs the config entry")

    return HaUnitTransport(
        lambda: async_get_unit(hass, entry, ModbusTcpParams(host=host, port=port), unit_id),
        message_spacing_s=profile.connection.message_spacing_s,
        timeout_s=profile.connection.timeout_s,
    )
