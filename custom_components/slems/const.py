"""Constants for the SLEMS integration."""

from __future__ import annotations

from datetime import timedelta
from enum import StrEnum
from typing import Final

DOMAIN: Final = "slems"
MANUFACTURER: Final = "SLEMS"

# Telemetry poll interval of the coordinator (entities and dashboard).
SCAN_INTERVAL: Final = timedelta(seconds=5)

# --- main entry (system) ----------------------------------------------------
CONF_GRID_POWER_ENTITY: Final = "grid_power_entity"
CONF_GRID_POWER_INVERTED: Final = "grid_power_inverted"
CONF_PV_POWER_ENTITY: Final = "pv_power_entity"
CONF_PV_FORECAST_ENTITY: Final = "pv_forecast_entity"
CONF_WEATHER_ENTITY: Final = "weather_entity"

# --- battery subentry ---------------------------------------------------------
SUBENTRY_TYPE_BATTERY: Final = "battery"

CONF_MODEL: Final = "model"
CONF_HOST: Final = "host"
CONF_PORT: Final = "port"
CONF_UNIT_ID: Final = "unit_id"
CONF_CAPACITY_WH: Final = "capacity_wh"
CONF_MAX_CHARGE_POWER_W: Final = "max_charge_power_w"
CONF_MAX_DISCHARGE_POWER_W: Final = "max_discharge_power_w"
CONF_SKIP_CONNECTION_TEST: Final = "skip_connection_test"
CONF_SOC_ENTITY: Final = "soc_entity"
CONF_POWER_ENTITY: Final = "power_entity"
CONF_POWER_INVERTED: Final = "power_inverted"

DEFAULT_MODBUS_PORT: Final = 502
DEFAULT_UNIT_ID: Final = 1


class BatteryModel(StrEnum):
    """Supported battery models (driver keys)."""

    MARSTEK_VENUS_E3 = "marstek_venus_e3"
    # Read-only battery backed by existing Home Assistant entities. Allows running
    # SLEMS in simulation mode while another integration still controls the device.
    HA_ENTITIES = "ha_entities"


class OperatingMode(StrEnum):
    """Global operating mode of the energy manager."""

    # Nothing is planned and nothing is sent.
    OFF = "off"
    # Forecasts and plans are computed and shown, but no command is sent.
    SIMULATION = "simulation"
    # Plans are executed on batteries and consumers.
    ACTIVE = "active"


DEFAULT_OPERATING_MODE: Final = OperatingMode.SIMULATION
