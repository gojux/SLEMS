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


def test_marstek_local_api_takes_the_ac_side_power() -> None:
    def m(entity_id: str, name: str, **values) -> EntityInfo:
        return EntityInfo.create(entity_id, name, platform="marstek_local_api", **values)

    entities = [
        m("sensor.venus_battery_soc", "Battery SOC", device_class="battery", unit="%", state="55"),
        m("sensor.venus_battery_power", "Power", device_class="power", unit="W", state="unknown"),
        m("sensor.venus_battery_power_in", "Power in", device_class="power", unit="W", state="0"),
        m("sensor.venus_grid_power", "Grid power", device_class="power", unit="W", state="-310"),
        m("sensor.venus_offgrid_power", "Off-grid power", device_class="power", unit="W", state="0"),
        m("sensor.venus_ct_phase_a_power", "CT phase A power", device_class="power", unit="W", state="420"),
    ]
    assert match_battery_entities(entities)["power_entity"] == "sensor.venus_grid_power"


def test_power_sensor_with_values_preferred() -> None:
    entities = [
        e("sensor.pack_battery_power", "Battery power", device_class="power", unit="W", state="unavailable"),
        e("sensor.pack_battery_power_2", "Battery power", device_class="power", unit="W", state="120"),
    ]
    assert match_battery_entities(entities)["power_entity"] == "sensor.pack_battery_power_2"


# Consumers.

from custom_components.slems.const import (  # noqa: E402
    CONF_BLOCK_ENTITY,
    CONF_CONTROL_ENTITY,
    CONF_ENERGY_ENTITY,
    CONF_MAX_CURRENT_A,
    CONF_MAX_POWER_W,
    CONF_MIN_CURRENT_A,
    CONF_MIN_POWER_W,
    CONF_MODE_ENTITY,
    CONF_PHASES_ENTITY,
    CONF_POWER_ENTITY,
    CONF_START_ENTITY,
    CONF_START_OFF,
    CONF_START_ON,
    CONF_TEMPERATURE_2_ENTITY,
    CONF_TEMPERATURE_ENTITY,
    ConsumerType,
    ControlMode,
)
from custom_components.slems.entity_match import (  # noqa: E402
    consumer_device_fit,
    match_consumer_entities,
    meter_device_fit,
    suggest_consumer_control,
    suggest_control_mode,
    suggest_hot_water,
    suggest_start_options,
)


def hoval(entity_id: str, name: str = "", **values) -> EntityInfo:
    return EntityInfo.create(entity_id, name, platform="hoval_unofficial", **values)


# A Hoval Belaria behind hoval_unofficial: heat output and electrical input side by side.
HOVAL = [
    hoval("sensor.hoval_status", "hp_status Status", device_class="enum",
          options=("switched_off", "heating_mode", "hot_water", "defrost", "lock")),
    hoval("sensor.hoval_outdoor_temperature", "outdoor_temp", device_class="temperature", unit="°C"),
    hoval("sensor.hoval_buffer_temperature", "buffer_temp", device_class="temperature", unit="°C"),
    hoval("sensor.hoval_current_flow_temperature_hc1", "flow_temp_hc1", device_class="temperature", unit="°C"),
    hoval("sensor.hoval_current_power_output", "current_power_output", device_class="power", unit="kW",
          state="4.2"),
    hoval("sensor.hoval_current_electrical_power_input_heat_generator", "electrical_power_input_heat_generator",
          device_class="power", unit="kW", state="1.1"),
    hoval("sensor.hoval_total_electrical_energy_heat_generator", "total_electrical_energy_heat_generator",
          device_class="energy", unit="MWh", state_class="total_increasing"),
    hoval("sensor.hoval_heat_quantity_hot_water", "heat_quantity_hot_water", device_class="energy", unit="MWh",
          state_class="total_increasing"),
    hoval("sensor.hoval_cooling_energy", "cooling_energy", device_class="energy", unit="MWh",
          state_class="total_increasing"),
    hoval("water_heater.hoval_water_heater", "dhw Water heater"),
    hoval("select.hoval_smart_grid_control", "smart_grid_mode",
          options=("normal_operation", "preferred_operation", "locked", "forced_consumption")),
    hoval("climate.hoval_heating_circuit_1", "heating_circuit_1"),
]


def test_hoval_heat_pump_with_hot_water_release() -> None:
    assert suggest_hot_water(HOVAL)
    assert suggest_control_mode(HOVAL, ConsumerType.HEAT_PUMP, hot_water=True) is ControlMode.RELEASE
    matches = match_consumer_entities(HOVAL, ConsumerType.HEAT_PUMP, ControlMode.RELEASE, hot_water=True)
    assert matches == {
        CONF_MODE_ENTITY: "sensor.hoval_status",
        CONF_TEMPERATURE_ENTITY: "water_heater.hoval_water_heater",
        CONF_POWER_ENTITY: "sensor.hoval_current_electrical_power_input_heat_generator",
        CONF_ENERGY_ENTITY: "sensor.hoval_total_electrical_energy_heat_generator",
    }


def test_heat_pump_without_hot_water_has_no_storage() -> None:
    assert suggest_control_mode(HOVAL, ConsumerType.HEAT_PUMP, hot_water=False) is ControlMode.NONE
    matches = match_consumer_entities(HOVAL, ConsumerType.HEAT_PUMP, ControlMode.RELEASE, hot_water=False)
    assert CONF_MODE_ENTITY not in matches and CONF_TEMPERATURE_ENTITY not in matches


def elwa(entity_id: str, name: str = "", **values) -> EntityInfo:
    return EntityInfo.create(entity_id, name, platform="mypv_elwa2_modbus", **values)


# A my-PV ELWA 2 behind mypv_elwa2_modbus.
ELWA = [
    elwa("sensor.elwa_2_power", "power Power", device_class="power", unit="W", state="1800"),
    elwa("sensor.elwa_2_temperature", "temperature Temperature", device_class="temperature", unit="°C"),
    elwa("sensor.elwa_2_temperature_2", "temperature_2 Temperature 2", device_class="temperature", unit="°C"),
    elwa("sensor.elwa_2_voltage", "voltage Voltage", device_class="voltage", unit="V"),
    elwa("sensor.elwa_2_max_configured_power", "max_controlled_power", device_class="power", unit="W"),
    elwa("sensor.elwa_2_max_power_currently_possible", "max_available_power", device_class="power", unit="W"),
    elwa("sensor.elwa_2_energy", "energy Energy", device_class="energy", unit="kWh", state_class="total"),
    elwa("number.elwa_2_power", "power Power", unit="W", minimum=0, maximum=3500),
    elwa("number.elwa_2_target_temperature", "target_temperature", unit="°C", minimum=20, maximum=85),
    elwa("number.elwa_2_auto_control_reserve", "auto_reserve", unit="W", minimum=0, maximum=1000),
    elwa("number.elwa_2_auto_control_max_power", "auto_max_power", unit="W", minimum=0, maximum=3500),
    elwa("switch.elwa_2_device_enabled", "device_enabled Device enabled"),
    elwa("switch.elwa_2_auto_control", "auto_control Auto control"),
    elwa("water_heater.elwa_2", ""),
]


def test_mypv_elwa_heating_rod_with_power_setpoint() -> None:
    assert suggest_control_mode(ELWA, ConsumerType.HEATING_ROD) is ControlMode.POWER
    data = suggest_consumer_control(ELWA, ConsumerType.HEATING_ROD, ControlMode.POWER)
    assert data == {
        CONF_CONTROL_ENTITY: "number.elwa_2_power",
        CONF_TEMPERATURE_ENTITY: "sensor.elwa_2_temperature",
        CONF_TEMPERATURE_2_ENTITY: "sensor.elwa_2_temperature_2",
        CONF_BLOCK_ENTITY: "water_heater.elwa_2",
        CONF_MAX_POWER_W: 3500,
        CONF_MIN_POWER_W: 0,
    }
    matches = match_consumer_entities(ELWA, ConsumerType.HEATING_ROD, ControlMode.POWER)
    assert matches[CONF_POWER_ENTITY] == "sensor.elwa_2_power"
    assert matches[CONF_ENERGY_ENTITY] == "sensor.elwa_2_energy"
    # As a switch: its own surplus control is no on/off switch.
    switch = match_consumer_entities(ELWA, ConsumerType.HEATING_ROD, ControlMode.SWITCH)
    assert switch[CONF_CONTROL_ENTITY] == "switch.elwa_2_device_enabled"


def evcc(entity_id: str, name: str = "", **values) -> EntityInfo:
    return EntityInfo.create(entity_id, name, platform="evcc_intg", **values)


AMPERES = tuple(str(a) for a in range(6, 17))
# A loadpoint of evcc behind ha-evcc.
EVCC = [
    evcc("select.evcc_garage_max_current", "Max. current", options=AMPERES),
    evcc("select.evcc_garage_min_current", "Min. current", options=AMPERES),
    evcc("select.evcc_garage_mode", "Mode", options=("off", "pv", "minpv", "now")),
    evcc("sensor.evcc_garage_phases_active", "Phases active", state="3"),
    evcc("sensor.evcc_garage_charge_power", "Charging power", device_class="power", unit="W", state="0"),
    evcc("sensor.evcc_garage_charged_energy", "Charged energy", device_class="energy", unit="kWh",
         state_class="total_increasing"),
    evcc("sensor.evcc_garage_charge_total_import", "Charge total import", device_class="energy", unit="kWh",
         state_class="total_increasing"),
    evcc("number.evcc_garage_limit_soc", "Limit SoC", unit="%"),
]


def test_evcc_loadpoint_with_current_control() -> None:
    assert suggest_control_mode(EVCC, ConsumerType.WALLBOX) is ControlMode.CURRENT
    data = suggest_consumer_control(EVCC, ConsumerType.WALLBOX, ControlMode.CURRENT)
    assert data == {
        CONF_CONTROL_ENTITY: "select.evcc_garage_max_current",
        CONF_START_ENTITY: "select.evcc_garage_mode",
        CONF_PHASES_ENTITY: "sensor.evcc_garage_phases_active",
        CONF_MIN_CURRENT_A: 6,
        CONF_MAX_CURRENT_A: 16,
    }
    matches = match_consumer_entities(EVCC, ConsumerType.WALLBOX, ControlMode.CURRENT)
    assert matches[CONF_POWER_ENTITY] == "sensor.evcc_garage_charge_power"
    assert matches[CONF_ENERGY_ENTITY] == "sensor.evcc_garage_charge_total_import"


def test_start_options_run_without_own_logic() -> None:
    # evcc: "now" (fast), not "pv" where evcc follows the surplus itself.
    assert suggest_start_options(("off", "pv", "minpv", "now")) == {CONF_START_ON: "now", CONF_START_OFF: "off"}
    assert suggest_start_options(("Aus", "Ein")) == {CONF_START_ON: "Ein", CONF_START_OFF: "Aus"}
    assert suggest_start_options(("pv", "minpv")) == {}


SHELLY_PLUG = [
    e("switch.plug_dryer", "Dryer", platform="shelly"),
    e("sensor.plug_dryer_power", "Power", device_class="power", unit="W", state="0.0", platform="shelly"),
    e("sensor.plug_dryer_energy", "Energy", device_class="energy", unit="kWh", state_class="total_increasing",
      platform="shelly"),
    e("sensor.plug_dryer_device_temperature", "Device temperature", device_class="temperature", unit="°C",
      platform="shelly"),
]
SMART_METER = [
    e("sensor.meter_power", "Power", device_class="power", unit="W", state="350", platform="shelly"),
    e("sensor.meter_energy", "Energy", device_class="energy", unit="kWh", state_class="total_increasing",
      platform="shelly"),
    e("sensor.meter_energy_returned", "Energy returned", device_class="energy", unit="kWh",
      state_class="total_increasing", platform="shelly"),
]


def test_smart_plug_switches_and_measures() -> None:
    assert suggest_control_mode(SHELLY_PLUG, ConsumerType.OTHER) is ControlMode.SWITCH
    # Its own temperature is no storage of a heating rod.
    matches = match_consumer_entities(SHELLY_PLUG, ConsumerType.HEATING_ROD, ControlMode.SWITCH)
    assert matches == {
        CONF_CONTROL_ENTITY: "switch.plug_dryer",
        CONF_POWER_ENTITY: "sensor.plug_dryer_power",
        CONF_ENERGY_ENTITY: "sensor.plug_dryer_energy",
    }
    assert meter_device_fit(SMART_METER) > 0
    assert match_consumer_entities(SMART_METER, ConsumerType.OTHER, ControlMode.NONE)[CONF_ENERGY_ENTITY] == (
        "sensor.meter_energy"
    )


def test_device_fit_ranks_the_consumers_integration_first() -> None:
    assert consumer_device_fit(HOVAL, ConsumerType.HEAT_PUMP) > consumer_device_fit(SHELLY_PLUG, ConsumerType.HEAT_PUMP)
    assert consumer_device_fit(ELWA, ConsumerType.HEATING_ROD) > consumer_device_fit(
        SHELLY_PLUG, ConsumerType.HEATING_ROD
    )
    # A device of another kind of consumer, and one that only measures, are no candidates.
    assert consumer_device_fit(HOVAL, ConsumerType.WALLBOX) <= 0
    assert consumer_device_fit(SMART_METER, ConsumerType.OTHER) <= 0
    assert consumer_device_fit(SHELLY_PLUG, ConsumerType.OTHER) > 0


def mypv(entity_id: str, name: str = "", **values) -> EntityInfo:
    return EntityInfo.create(entity_id, name, platform="my_pv", **values)


# An AC•THOR behind the my_pv integration of Home Assistant core: its heating power has no
# name of its own, no energy counter and no power set point (it controls its surplus itself).
AC_THOR = [
    mypv("sensor.ac_thor_power", "", device_class="power", unit="W", state="1200"),
    mypv("sensor.ac_thor_temperature_1", "temp1 Temperature 1", device_class="temperature", unit="°C"),
    mypv("sensor.ac_thor_temperature_2", "temp2 Temperature 2", device_class="temperature", unit="°C"),
    mypv("sensor.ac_thor_device_temperature", "temp_ps Device temperature", device_class="temperature", unit="°C"),
    mypv("sensor.ac_thor_device_status", "screen_mode_flag Device status", device_class="enum",
         options=("0", "1", "2", "3", "4", "5", "6")),
    mypv("sensor.ac_thor_current_phase_l1", "curr_l1 Current phase L1", device_class="current", unit="A"),
    mypv("number.ac_thor_boost_target_temperature", "ww1boost Boost target temperature", unit="°C"),
    mypv("switch.ac_thor_boost_mode", "bstmode Boost mode"),
    mypv("select.ac_thor_boost_mode", "bstmode Boost mode", options=("0", "1", "3", "4", "5")),
    mypv("water_heater.ac_thor", ""),
]


def test_mypv_core_heating_rod_is_measured_only() -> None:
    assert suggest_control_mode(AC_THOR, ConsumerType.HEATING_ROD) is ControlMode.NONE
    matches = match_consumer_entities(AC_THOR, ConsumerType.HEATING_ROD, ControlMode.NONE)
    assert matches == {
        CONF_TEMPERATURE_ENTITY: "sensor.ac_thor_temperature_1",
        CONF_TEMPERATURE_2_ENTITY: "sensor.ac_thor_temperature_2",
        CONF_POWER_ENTITY: "sensor.ac_thor_power",
    }
    assert consumer_device_fit(AC_THOR, ConsumerType.HEATING_ROD) > consumer_device_fit(
        AC_THOR, ConsumerType.OTHER
    )


# Smart meters and PV systems.

from custom_components.slems.const import (  # noqa: E402
    CONF_GRID_EXPORT_ENERGY_ENTITY,
    CONF_GRID_IMPORT_ENERGY_ENTITY,
    CONF_GRID_POWER_ENTITY,
)
from custom_components.slems.entity_match import (  # noqa: E402
    grid_device_fit,
    match_grid_entities,
    match_outdoor_temperature,
    match_pv_power,
    pv_device_fit,
)


def kwh(entity_id: str, name: str, platform: str) -> EntityInfo:
    return EntityInfo.create(
        entity_id, name, device_class="energy", unit="kWh", state_class="total_increasing", platform=platform
    )


def watt(entity_id: str, name: str, platform: str, **values) -> EntityInfo:
    return EntityInfo.create(entity_id, name, device_class="power", unit="W", platform=platform, **values)


# SolarEdge in Home Assistant core (solaredge_modbus): inverter and meter are devices of their own,
# their power has no name of its own.
SE_CORE = "solaredge_modbus"
SE_CORE_INVERTER = [
    watt("sensor.se3000h_power", "", SE_CORE, state="2400"),
    kwh("sensor.se3000h_energy", "", SE_CORE),
    watt("sensor.se3000h_dc_power", "dc_power DC power", SE_CORE),
]
SE_CORE_METER = [
    watt("sensor.meter_1_power", "", SE_CORE, state="-800"),
    watt("sensor.meter_1_power_phase_a", "power_phase_a Power phase A", SE_CORE),
    kwh("sensor.meter_1_energy_imported", "energy_imported Energy imported", SE_CORE),
    kwh("sensor.meter_1_energy_imported_phase_a", "energy_imported_phase_a Energy imported phase A", SE_CORE),
    kwh("sensor.meter_1_energy_exported", "energy_exported Energy exported", SE_CORE),
    kwh("sensor.meter_1_energy_exported_phase_a", "energy_exported_phase_a Energy exported phase A", SE_CORE),
]
# The same with solaredge_modbus_multi (HACS).
SE_MULTI = "solaredge_modbus_multi"
SE_MULTI_INVERTER = [
    watt("sensor.solaredge_i1_ac_power", "AC Power", SE_MULTI, state="2400"),
    kwh("sensor.solaredge_i1_ac_energy_kwh", "AC Energy", SE_MULTI),
    watt("sensor.solaredge_i1_dc_power", "DC Power", SE_MULTI),
]
SE_MULTI_METER = [
    watt("sensor.solaredge_m1_ac_power", "AC Power", SE_MULTI, state="-800"),
    watt("sensor.solaredge_m1_ac_power_a", "AC Power A", SE_MULTI),
    kwh("sensor.solaredge_m1_ac_energy_exported", "AC Energy Exported", SE_MULTI),
    kwh("sensor.solaredge_m1_ac_energy_exported_a", "AC Energy Exported A", SE_MULTI),
    kwh("sensor.solaredge_m1_ac_energy_imported", "AC Energy Imported", SE_MULTI),
    kwh("sensor.solaredge_m1_ac_energy_imported_a", "AC Energy Imported A", SE_MULTI),
]
SHELLY_PRO_3EM = [
    watt("sensor.pro3em_phase_a_active_power", "Phase A active power", "shelly"),
    watt("sensor.pro3em_total_active_power", "Total active power", "shelly", state="350"),
    kwh("sensor.pro3em_total_active_energy", "Total active energy", "shelly"),
    kwh("sensor.pro3em_total_active_returned_energy", "Total active returned energy", "shelly"),
    kwh("sensor.pro3em_phase_a_total_active_energy", "Phase A total active energy", "shelly"),
]
OPENDTU = [
    watt("sensor.hm_800_power", "Power", "opendtu", state="560"),
    watt("sensor.hm_800_dc_power", "DC power", "opendtu"),
    kwh("sensor.hm_800_yield_total", "Yield total", "opendtu"),
]


def test_solaredge_meter_and_inverter() -> None:
    for meter, prefix in ((SE_CORE_METER, "sensor.meter_1_"), (SE_MULTI_METER, "sensor.solaredge_m1_ac_")):
        matches = match_grid_entities(meter)
        assert matches[CONF_GRID_POWER_ENTITY] == prefix + "power"
        assert matches[CONF_GRID_IMPORT_ENERGY_ENTITY] == prefix + "energy_imported"
        assert matches[CONF_GRID_EXPORT_ENERGY_ENTITY] == prefix + "energy_exported"
    assert match_pv_power(SE_CORE_INVERTER) == "sensor.se3000h_power"
    assert match_pv_power(SE_MULTI_INVERTER) == "sensor.solaredge_i1_ac_power"
    # The meter ranks as the smart meter, the inverter as the PV system.
    for inverter, meter in ((SE_CORE_INVERTER, SE_CORE_METER), (SE_MULTI_INVERTER, SE_MULTI_METER)):
        assert grid_device_fit(meter) > grid_device_fit(inverter)
        assert pv_device_fit(inverter) > pv_device_fit(meter)


def test_shelly_pro_3em_total_of_all_phases() -> None:
    assert match_grid_entities(SHELLY_PRO_3EM) == {
        CONF_GRID_POWER_ENTITY: "sensor.pro3em_total_active_power",
        CONF_GRID_IMPORT_ENERGY_ENTITY: "sensor.pro3em_total_active_energy",
        CONF_GRID_EXPORT_ENERGY_ENTITY: "sensor.pro3em_total_active_returned_energy",
    }
    assert grid_device_fit(SHELLY_PRO_3EM) > grid_device_fit(SHELLY_PLUG)


def test_micro_inverter_and_plugs_as_pv() -> None:
    assert match_pv_power(OPENDTU) == "sensor.hm_800_power"
    assert pv_device_fit(OPENDTU) > pv_device_fit(SHELLY_PLUG) > 0
    # A plug may measure a balcony system: offered, but after the inverters.
    assert pv_device_fit(SHELLY_PRO_3EM) < pv_device_fit(SHELLY_PLUG)


def test_outdoor_temperature_of_a_heat_pump() -> None:
    assert match_outdoor_temperature(HOVAL) == "sensor.hoval_outdoor_temperature"
    assert match_outdoor_temperature(ELWA) is None


def test_battery_device_fit() -> None:
    from custom_components.slems.entity_match import battery_device_fit

    assert battery_device_fit(VENUS_ESPHOME) > 0
    phone = [e("sensor.phone_battery_level", "Battery level", device_class="battery", unit="%")]
    assert battery_device_fit(phone) == 0
    assert battery_device_fit(HOVAL) == 0
