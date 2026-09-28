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
# Config entry ids of solar forecast providers (HA energy platform), summed up.
CONF_PV_FORECAST_ENTRIES: Final = "pv_forecast_entries"
CONF_WEATHER_ENTITY: Final = "weather_entity"
# Optional history sources for the consumption forecast.
CONF_HOUSE_HISTORY_ENTITY: Final = "house_history_entity"
CONF_OUTDOOR_TEMPERATURE_ENTITY: Final = "outdoor_temperature_entity"

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
CONF_EFFICIENCY_MODE: Final = "efficiency_mode"
CONF_ROUND_TRIP_EFFICIENCY_PCT: Final = "round_trip_efficiency_pct"

# Used until a learned value is reliable, and as manual default.
DEFAULT_ROUND_TRIP_EFFICIENCY_PCT: Final = 88

DEFAULT_MODBUS_PORT: Final = 502
DEFAULT_UNIT_ID: Final = 1

# --- consumer subentry --------------------------------------------------------
SUBENTRY_TYPE_CONSUMER: Final = "consumer"

CONF_CONSUMER_TYPE: Final = "consumer_type"
CONF_ENERGY_ENTITY: Final = "energy_entity"
CONF_INCLUDED_IN_METER: Final = "included_in_meter"
CONF_CONTROL_MODE: Final = "control_mode"
CONF_CONTROL_ENTITY: Final = "control_entity"
CONF_NOMINAL_POWER_W: Final = "nominal_power_w"
CONF_MIN_POWER_W: Final = "min_power_w"
CONF_MAX_POWER_W: Final = "max_power_w"
CONF_BLOCK_ENTITY: Final = "block_entity"
CONF_THERMOSTAT_CYCLES: Final = "thermostat_cycles"
# Optional temperature sensors of the consumer's storage (e.g. a boiler).
CONF_TEMPERATURE_ENTITY: Final = "temperature_entity"
CONF_TEMPERATURE_2_ENTITY: Final = "temperature_2_entity"
CONF_PRIORITY: Final = "priority"
CONF_MIN_ON_MINUTES: Final = "min_on_minutes"
CONF_MIN_OFF_MINUTES: Final = "min_off_minutes"

DEFAULT_PRIORITY: Final = 5

# --- runtime settings (entities, restored after restart) ----------------------
DEFAULT_PEAK_SHAVING_GRID_LIMIT_W: Final = 3000
DEFAULT_PEAK_SHAVING_SOC_THRESHOLD_PCT: Final = 20
DEFAULT_SURPLUS_AVERAGE_WINDOW_S: Final = 5
DEFAULT_BATTERY_PRIORITY_SOC_PCT: Final = 30
DEFAULT_BATTERY_SHARE_WHEN_SECURED_PCT: Final = 75
DEFAULT_CHARGE_SECURED_BUFFER_KWH: Final = 1.0
DEFAULT_GRID_FRIENDLY_BUFFER_KWH: Final = 1.0
# Target grid surplus (+export / -import) while charging and while discharging.
DEFAULT_CHARGE_GRID_TARGET_W: Final = 100
DEFAULT_DISCHARGE_GRID_TARGET_W: Final = 50
DEFAULT_DISCHARGE_MAX_GRID_EXPORT_W: Final = 5000
DEFAULT_NIGHT_RESERVE_PCT: Final = 25
DEFAULT_PV_PEAK_POWER_KWP: Final = 10.0
DEFAULT_FEED_IN_CAP_LIMIT_PCT: Final = 60
DEFAULT_FEED_IN_CAP_BUFFER_PCT: Final = 20
DEFAULT_FEED_IN_CAP_MIN_BUFFER_PCT: Final = 5
DEFAULT_ROTATION_SOC_THRESHOLD_PCT: Final = 5
DEFAULT_ROTATION_MIN_INTERVAL_MIN: Final = 15
DEFAULT_ROTATION_RAMP_RATE_W_PER_S: Final = 100
DEFAULT_ROTATION_RAMP_MAX_S: Final = 30
DEFAULT_CONTROL_INTERVAL_S: Final = 1.0
DEFAULT_CONTROL_GAIN: Final = 0.5


class BatteryModel(StrEnum):
    """Supported battery models (driver keys)."""

    MARSTEK_VENUS_E3 = "marstek_venus_e3"
    # Read-only battery backed by existing Home Assistant entities. Allows running
    # SLEMS in simulation mode while another integration still controls the device.
    HA_ENTITIES = "ha_entities"


class EfficiencyMode(StrEnum):
    """Source of a battery's round trip efficiency."""

    # Lifetime charge/discharge counters reported by the battery.
    BATTERY_COUNTERS = "battery_counters"
    # Learned by integrating the measured battery power.
    LEARNED = "learned"
    MANUAL = "manual"


class ConsumerType(StrEnum):
    """What a consumer is; used by the forecast to pick its model."""

    # Space heating and domestic hot water; strongly weather dependent.
    HEAT_PUMP = "heat_pump"
    # Electric heating rod, e.g. in the hot water tank.
    HEATING_ROD = "heating_rod"
    OTHER = "other"


class ControlMode(StrEnum):
    """How SLEMS can control a consumer."""

    # Only measured.
    NONE = "none"
    # On/off via a switch entity.
    SWITCH = "switch"
    # Continuous power set point via a number entity (W).
    POWER = "power"


class CapMode(StrEnum):
    """How a consumer takes part in the feed-in cap (see feed_in_cap)."""

    # Takes the surplus above the limit the batteries cannot absorb (from the
    # start of a peak that does not fit into them), no other surplus while
    # the feed-in cap is on.
    SUPPORT = "support"
    # Surplus as without the feed-in cap; above the limit it takes what the
    # batteries and the supporting consumers cannot, before it is curtailed.
    NORMAL = "normal"
    # Surplus as without the feed-in cap, never the surplus above the limit.
    NEVER = "never"


# Stored values of the modes of earlier versions.
LEGACY_CAP_MODES = {"count": CapMode.SUPPORT, "emergency": CapMode.SUPPORT}


class OperatingMode(StrEnum):
    """Global operating mode of the energy manager."""

    # Nothing is planned and nothing is sent.
    OFF = "off"
    # Forecasts and plans are computed and shown, but no command is sent.
    SIMULATION = "simulation"
    # Plans are executed on batteries and consumers.
    ACTIVE = "active"


DEFAULT_OPERATING_MODE: Final = OperatingMode.SIMULATION
