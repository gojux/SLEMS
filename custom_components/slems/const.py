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
# Energy counters of the smart meter (kWh), for tariffs and bills.
CONF_GRID_IMPORT_ENERGY_ENTITY: Final = "grid_import_energy_entity"
CONF_GRID_EXPORT_ENERGY_ENTITY: Final = "grid_export_energy_entity"
# Grid power additionally read from a SunSpec meter over Modbus (grid_meter).
CONF_GRID_MODBUS: Final = "grid_modbus"
CONF_GRID_MODBUS_HOST: Final = "grid_modbus_host"
CONF_GRID_MODBUS_PORT: Final = "grid_modbus_port"
CONF_GRID_MODBUS_UNIT_ID: Final = "grid_modbus_unit_id"
CONF_GRID_MODBUS_INTERVAL_S: Final = "grid_modbus_interval_s"
CONF_GRID_MODBUS_SIGN: Final = "grid_modbus_sign"
CONF_GRID_MODBUS_REGISTER: Final = "grid_modbus_register"
CONF_GRID_MODBUS_INVERTED: Final = "grid_modbus_inverted"
DEFAULT_GRID_MODBUS_PORT: Final = 502
DEFAULT_GRID_MODBUS_INTERVAL_S: Final = 0.5

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
# Optional, for the wear costs (see battery_wear).
CONF_PURCHASE_PRICE_EUR: Final = "purchase_price_eur"
CONF_RATED_CYCLES: Final = "rated_cycles"
CONF_RELEASE_STATE: Final = "release_state"
# Battery backed by Home Assistant entities: optional sensors and control.
CONF_DEVICE: Final = "device"
CONF_BATTERY_CONTROL: Final = "battery_control"
CONF_SETPOINT_ENTITY: Final = "setpoint_entity"
CONF_SETPOINT_INVERTED: Final = "setpoint_inverted"
CONF_CHARGE_ENTITY: Final = "charge_entity"
CONF_DISCHARGE_ENTITY: Final = "discharge_entity"
CONF_MODE_ENTITY: Final = "mode_entity"
CONF_MODE_CHARGE: Final = "mode_charge"
CONF_MODE_DISCHARGE: Final = "mode_discharge"
CONF_MODE_STANDBY: Final = "mode_standby"
CONF_MODE_AUTO: Final = "mode_auto"
CONF_REMOTE_ENTITY: Final = "remote_entity"
CONF_REMOTE_ON: Final = "remote_on"
CONF_REMOTE_OFF: Final = "remote_off"
CONF_POWER_SCRIPT: Final = "power_script"
CONF_RELEASE_SCRIPT: Final = "release_script"
CONF_BATTERY_TEMPERATURE_ENTITY: Final = "battery_temperature_entity"
CONF_MAX_CELL_VOLTAGE_ENTITY: Final = "max_cell_voltage_entity"
CONF_MIN_CELL_VOLTAGE_ENTITY: Final = "min_cell_voltage_entity"
CONF_CHARGED_ENERGY_ENTITY: Final = "charged_energy_entity"
CONF_DISCHARGED_ENERGY_ENTITY: Final = "discharged_energy_entity"
CONF_MIN_COMMAND_INTERVAL_S: Final = "min_command_interval_s"
CONF_KEEPALIVE_S: Final = "keepalive_s"

# Used until a learned value is reliable, and as manual default.
DEFAULT_ROUND_TRIP_EFFICIENCY_PCT: Final = 88

DEFAULT_MODBUS_PORT: Final = 502
DEFAULT_UNIT_ID: Final = 1

# --- consumer subentry --------------------------------------------------------
SUBENTRY_TYPE_CONSUMER: Final = "consumer"
SUBENTRY_TYPE_TARIFF: Final = "tariff"

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
# Shown as a box in the energy flow of the dashboard.
CONF_SHOW_IN_FLOW: Final = "show_in_flow"
# Current control (A), e.g. a wallbox or the maximum current of an evcc loadpoint.
CONF_MIN_CURRENT_A: Final = "min_current_a"
CONF_MAX_CURRENT_A: Final = "max_current_a"
CONF_PHASES: Final = "phases"
CONF_PHASES_ENTITY: Final = "phases_entity"
CONF_VOLTAGE_V: Final = "voltage_v"
# Optional start/stop: a switch, or a select with the options for on and off
# (e.g. the charge mode of an evcc loadpoint).
CONF_START_ENTITY: Final = "start_entity"
CONF_START_ON: Final = "start_on"
CONF_START_OFF: Final = "start_off"
DEFAULT_MIN_CURRENT_A: Final = 6
DEFAULT_MAX_CURRENT_A: Final = 16
DEFAULT_PHASES: Final = 3
DEFAULT_VOLTAGE_V: Final = 230
# Defaults of a wallbox: minimum runtime and pause (minutes).
WALLBOX_MIN_ON_MINUTES: Final = 5
WALLBOX_MIN_OFF_MINUTES: Final = 5
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
DEFAULT_ROTATION_SOC_THRESHOLD_PCT: Final = 5
DEFAULT_ROTATION_MIN_INTERVAL_MIN: Final = 15
DEFAULT_ROTATION_RAMP_RATE_W_PER_S: Final = 100
DEFAULT_ROTATION_RAMP_MAX_S: Final = 30
DEFAULT_FULL_CHARGE_INTERVAL_DAYS: Final = 7
DEFAULT_CONTROL_INTERVAL_S: Final = 1.0
DEFAULT_CONTROL_GAIN: Final = 0.5


# Minimum buffer of the feed-in cap per peak, in % of the PV peak power as the
# energy of one hour: covers errors in the timing and height of a peak that
# the daily PV forecast errors behind the percentage buffer do not show.
FEED_IN_CAP_MIN_BUFFER_PCT: Final = 5

# Settings entities of earlier versions, removed from the entity registry.
REMOVED_SETTINGS: Final = ("feed_in_cap_min_buffer",)


class BatteryModel(StrEnum):
    """Supported battery models (driver keys)."""

    MARSTEK_VENUS_E3 = "marstek_venus_e3"
    # Battery backed by existing Home Assistant entities: read-only (e.g. while
    # another integration still controls the device) or controlled through them.
    HA_ENTITIES = "ha_entities"


class BatteryControl(StrEnum):
    """How SLEMS commands a battery backed by Home Assistant entities."""

    # Read-only.
    NONE = "none"
    # One number entity, +charge / -discharge (or inverted).
    SETPOINT = "setpoint"
    # One number for charging and one for discharging, optionally a mode select.
    SPLIT = "split"
    # A script receives the signed power as variable ``power_w``.
    SCRIPT = "script"


class ReleaseState(StrEnum):
    """State a battery is left in when SLEMS stops controlling it."""

    # The battery's own logic (e.g. its zero export) takes over.
    AUTO = "auto"
    # The battery stays idle at 0 W.
    STANDBY = "standby"


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
    # Charging an electric vehicle.
    WALLBOX = "wallbox"
    OTHER = "other"


class ControlMode(StrEnum):
    """How SLEMS can control a consumer."""

    # Only measured.
    NONE = "none"
    # On/off via a switch entity.
    SWITCH = "switch"
    # Continuous power set point via a number entity (W).
    POWER = "power"
    # Current set point via a number entity (A), with the phases and voltage.
    CURRENT = "current"


class TargetType(StrEnum):
    """Daily target of a consumer (see consumer_targets)."""

    NONE = "none"
    # Time it draws power.
    RUNTIME = "runtime"
    # Time SLEMS has it switched on (devices with their own control).
    ENABLED = "enabled"
    ENERGY = "energy"
    # Minimum and target temperature of its storage (temperature sensors).
    TEMPERATURE = "temperature"


class TargetSensor(StrEnum):
    """Which temperature the temperature target applies to (two sensors)."""

    MEAN = "mean"
    FIRST = "first"
    SECOND = "second"


class TargetSource(StrEnum):
    """What may cover a daily target the surplus does not reach in time."""

    SURPLUS = "surplus"
    # Also the batteries, as long as they can deliver.
    BATTERY = "battery"
    # Also the batteries and then the grid.
    GRID = "grid"


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
