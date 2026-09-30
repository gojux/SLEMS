"""Tests for suggesting the entities of a battery device."""

from custom_components.slems.const import BatteryControl
from custom_components.slems.entity_match import (
    CAPACITY,
    EntityInfo,
    match_battery_entities,
    suggest_control,
    suggest_mode_options,
    suggest_remote_options,
)


def e(entity_id: str, name: str = "", **values) -> EntityInfo:
    return EntityInfo.create(entity_id, name, **values)


# A Venus behind an ESPHome RS485 bridge.
VENUS_ESPHOME = [
    e("sensor.venus_battery_soc", "Battery SOC", device_class="battery", unit="%"),
    e("sensor.venus_min_soc", "Discharge cutoff min SOC", unit="%"),
    e("sensor.venus_ac_power", "AC Power", device_class="power", unit="W"),
    e("sensor.venus_pv_power", "PV Power", device_class="power", unit="W"),
    e("sensor.venus_internal_temperature", "Internal Temperature", device_class="temperature", unit="°C"),
    e("sensor.venus_max_cell_voltage", "Max Cell Voltage", device_class="voltage", unit="V"),
    e("sensor.venus_min_cell_voltage", "Min Cell Voltage", device_class="voltage", unit="V"),
    e("sensor.venus_total_charging_energy", "Total Charging Energy", device_class="energy",
      unit="kWh", state_class="total_increasing"),
    e("sensor.venus_total_discharging_energy", "Total Discharging Energy", device_class="energy",
      unit="kWh", state_class="total_increasing"),
    e("number.venus_set_charge_power", "Set Charge Power", unit="W", minimum=0, maximum=2500),
    e("number.venus_set_discharge_power", "Set Discharge Power", unit="W", minimum=0, maximum=2500),
    e("select.venus_force_mode", "Force Mode", options=("None", "Charge", "Discharge")),
    e("select.venus_rs485_control_mode", "RS485 Control Mode", options=("Enable", "Disable")),
    e("sensor.venus_capacity", "Battery Capacity", device_class="energy_storage", unit="kWh"),
]

# A battery with one signed set point number.
SIGNED = [
    e("sensor.pack_state_of_charge", "State of charge", device_class="battery", unit="%"),
    e("sensor.pack_battery_power", "Battery power", device_class="power", unit="W"),
    e("sensor.pack_grid_power", "Grid power", device_class="power", unit="W"),
    e("number.pack_power_setpoint", "Power setpoint", unit="W", minimum=-1200, maximum=1200),
    e("number.pack_output_limit", "Output limit", unit="W", minimum=0, maximum=800),
    e("switch.pack_remote_control", "Remote control"),
]


def test_venus_behind_esphome() -> None:
    matches = match_battery_entities(VENUS_ESPHOME)
    assert matches == {
        "soc_entity": "sensor.venus_battery_soc",
        "power_entity": "sensor.venus_ac_power",
        "battery_temperature_entity": "sensor.venus_internal_temperature",
        "max_cell_voltage_entity": "sensor.venus_max_cell_voltage",
        "min_cell_voltage_entity": "sensor.venus_min_cell_voltage",
        "charged_energy_entity": "sensor.venus_total_charging_energy",
        "discharged_energy_entity": "sensor.venus_total_discharging_energy",
        "charge_entity": "number.venus_set_charge_power",
        "discharge_entity": "number.venus_set_discharge_power",
        "mode_entity": "select.venus_force_mode",
        "remote_entity": "select.venus_rs485_control_mode",
        CAPACITY: "sensor.venus_capacity",
    }
    assert suggest_control(matches) is BatteryControl.SPLIT


def test_signed_setpoint() -> None:
    matches = match_battery_entities(SIGNED)
    assert matches["soc_entity"] == "sensor.pack_state_of_charge"
    assert matches["power_entity"] == "sensor.pack_battery_power"
    assert matches["setpoint_entity"] == "number.pack_power_setpoint"
    assert matches["remote_entity"] == "switch.pack_remote_control"
    assert suggest_control(matches) is BatteryControl.SETPOINT


def test_nothing_to_control() -> None:
    matches = match_battery_entities(SIGNED[:2])
    assert suggest_control(matches) is BatteryControl.NONE


def test_mode_options_in_english_and_german() -> None:
    assert suggest_mode_options(["None", "Charge", "Discharge"]) == {
        "mode_charge": "Charge",
        "mode_discharge": "Discharge",
        "mode_standby": "None",
    }
    assert suggest_mode_options(["Laden", "Entladen", "Standby", "Automatik"]) == {
        "mode_charge": "Laden",
        "mode_discharge": "Entladen",
        "mode_standby": "Standby",
        "mode_auto": "Automatik",
    }


def test_remote_options() -> None:
    assert suggest_remote_options(["Enable", "Disable"]) == {
        "remote_on": "Enable",
        "remote_off": "Disable",
    }
    assert suggest_remote_options(["Aktiviert", "Deaktiviert"]) == {
        "remote_on": "Aktiviert",
        "remote_off": "Deaktiviert",
    }
    assert suggest_remote_options(["Red", "Green"]) == {}
