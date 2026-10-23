"""Tests for the Modbus profiles of batteries (format, loader and checks)."""

import importlib.util
from pathlib import Path

import pytest

from custom_components.slems.battery_profiles import (
    PLACEHOLDER_CHARGE,
    PersistentGuard,
    ProfileError,
    apply_values,
    bundled_profiles,
    decode,
    encode,
    parse_profile,
    release_values,
    rest_violations,
    split_power,
)
from custom_components.slems.const import ReleaseState
from custom_components.slems.drivers import marstek_venus_e3 as venus

SIMULATOR = Path(__file__).parents[1] / "dev" / "venus_sim" / "simulator.py"

MINIMAL = """\
slems_battery_profile: 1
id: test
name: Test battery
limits: {max_charge_w: 2000, max_discharge_w: 1500}
read:
  soc_pct: {address: 100, type: uint16}
  power_ac_w: {address: 101, type: int16, positive: charge}
control:
  type: split
  apply:
    - {address: 200, value: "{charge_w}", at_rest: [0, 2000]}
    - {address: 201, value: "{discharge_w}", at_rest: [0, 1500]}
    - {address: 202, value: "{mode}", modes: {idle: 0, charge: 1, discharge: 2}, at_rest: [0, 2], confirm: true}
  release:
    auto:
      - {address: 202, value: 0, confirm: true}
probe: {address: 100, type: uint16, range: [0, 100]}
"""


def error(text: str, **kwargs) -> ProfileError:
    with pytest.raises(ProfileError) as info:
        parse_profile(text, **kwargs)
    return info.value


def load_simulator():
    spec = importlib.util.spec_from_file_location("venus_sim", SIMULATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- the Venus profile -------------------------------------------------------


def test_venus_profile_matches_the_driver() -> None:
    profile = bundled_profiles()["marstek_venus_e3"]
    assert profile.bundled
    assert profile.connection.message_spacing_s == venus.MESSAGE_WAIT_S
    assert profile.connection.timeout_s == venus.TIMEOUT_S
    assert profile.limits.max_charge_w == profile.limits.max_discharge_w == venus.HARDWARE_MAX_POWER_W
    assert profile.timing.start_delay_s == venus.START_DELAY_S
    assert profile.timing.start_extra_s == venus.START_EXTRA_S
    assert profile.timing.reversal_extra_s == venus.REVERSAL_EXTRA_S
    assert profile.control.confirm.delay_s == venus.CONFIRM_DELAY_S
    assert profile.control.confirm.tolerance_w == venus.CONFIRM_TOLERANCE_W
    assert profile.control.confirm.tolerance_pct / 100 == venus.CONFIRM_TOLERANCE_RATIO

    # Every telemetry register of the driver (except the control registers) with type and scale.
    by_extra = {
        "battery_soc": "soc_pct", "battery_power": "power_dc_w", "ac_power": "power_ac_w",
        "battery_voltage": "voltage_v", "internal_temperature": "temperature_c",
        "max_cell_voltage": "max_cell_v", "min_cell_voltage": "min_cell_v", "cycle_count": "cycles",
        "total_charging_energy": "charged_kwh", "total_discharging_energy": "discharged_kwh",
    }
    for spec in venus.TELEMETRY_REGISTERS:
        if spec.key in ("rs485_control", "force_mode"):
            continue
        if spec.key == "inverter_state":
            assert profile.state.register.address == spec.address
            continue
        register = profile.read[by_extra[spec.key]].registers[0]
        assert (register.address, register.type, register.scale) == (spec.address, spec.data_type, spec.scale)
    assert profile.read["soc_pct"].fallback.address == venus.SOC_FALLBACK.address
    assert {raw: name for raw, name in profile.state.values.items()} == {
        raw: "firmware_update" if name == "ota_upgrade" else name for raw, name in venus.INVERTER_STATES.items()
    }
    assert {
        key: (register.address, register.type, register.size) for key, register in profile.device_info.items()
    } == venus.DEVICE_INFO_REGISTERS


def test_venus_profile_writes_like_the_driver() -> None:
    profile = bundled_profiles()["marstek_venus_e3"]
    assert profile.control.keepalive_s == 60
    writes = [(step.address, raw) for step, raw in apply_values(profile, 800, 0)]
    assert writes == [
        (venus.REG_RS485_CONTROL, venus.RS485_ENABLE),
        (venus.REG_SET_DISCHARGE_POWER, 0),
        (venus.REG_SET_CHARGE_POWER, 800),
        (venus.REG_FORCE_MODE, venus.FORCE_CHARGE),
    ]
    assert [raw for _, raw in apply_values(profile, 0, 600)] == [venus.RS485_ENABLE, 600, 0, venus.FORCE_DISCHARGE]
    assert [raw for _, raw in apply_values(profile, 0, 0)] == [venus.RS485_ENABLE, 0, 0, venus.FORCE_NONE]

    auto = [(step.address, raw) for step, raw in release_values(profile, ReleaseState.AUTO)]
    assert auto == [
        (venus.REG_SET_DISCHARGE_POWER, 0),
        (venus.REG_SET_CHARGE_POWER, 0),
        (venus.REG_FORCE_MODE, venus.FORCE_NONE),
        (venus.REG_RS485_CONTROL, venus.RS485_DISABLE),
    ]
    standby = [(step.address, raw) for step, raw in release_values(profile, ReleaseState.STANDBY)]
    assert standby == auto[:3]
    # As the driver: only the force mode and the RS485 switch are read back after a release.
    assert [s.address for s in profile.control.release[ReleaseState.AUTO] if s.confirm] == [
        venus.REG_FORCE_MODE, venus.REG_RS485_CONTROL,
    ]


def test_venus_profile_against_the_simulator() -> None:
    sim = load_simulator()
    model = sim.VenusModel(5120, 63.0)
    profile = bundled_profiles()["marstek_venus_e3"]

    def read(register):
        return decode(model.read(register.address, register.size), register.type, register.word_order)

    probe = read(profile.probe.register)
    assert profile.probe.range[0] <= probe <= profile.probe.range[1]
    assert read(profile.read["soc_pct"].registers[0]) * profile.read["soc_pct"].registers[0].scale == 63.0
    assert profile.state.values[read(profile.state.register)] == "standby"
    assert read(profile.device_info["device_name"]) == "VNSE3-SIM"

    # A fresh device rests; under control, after a release and with an unknown register value.
    def values():
        return {step.address: read(step) for step in profile.control.apply}

    assert rest_violations(profile, values()) == []
    for step, raw in apply_values(profile, 0, 1200):
        model.write(step.address, encode(raw, step.type, step.word_order))
    model.tick()
    assert read(profile.read["power_ac_w"].registers[0]) == 1200
    assert rest_violations(profile, values()) == []
    for step, raw in release_values(profile, ReleaseState.AUTO):
        model.write(step.address, encode(raw, step.type, step.word_order))
    assert rest_violations(profile, values()) == []
    model.write(42010, [7])
    assert [v.address for v in rest_violations(profile, values())] == [42010]


# --- format -------------------------------------------------------------------


def test_minimal_profile() -> None:
    profile = parse_profile(MINIMAL)
    assert profile.connection.port == 502
    assert profile.own_pv is None
    assert profile.control.setpoint == "battery"
    assert profile.control.confirmed
    assert profile.timing.start_delay_s is None
    assert profile.control.release.keys() == {ReleaseState.AUTO}


@pytest.mark.parametrize(
    ("old", "new", "code", "path", "line"),
    [
        # Unknown keys, e.g. a condition: the format has none.
        ("name: Test battery\n", "name: Test battery\nif: soc > 50\n", "unknown_key", "", 4),
        ("type: split\n", "type: split\n  loop: 3\n", "unknown_key", "control", 10),
        ("slems_battery_profile: 1", "slems_battery_profile: 2", "version_newer", "slems_battery_profile", 1),
        ("id: test", "id: Test Battery", "invalid", "id", 2),
        ("  soc_pct: {address: 100, type: uint16}\n", "", "missing_key", "read.soc_pct", None),
        ("limits: {max_charge_w: 2000,", "limits: {max_charge_w: 0,", "invalid", "limits.max_charge_w", 4),
        ("address: 200, value", "address: 70000, value", "invalid", "control.apply[0].address", 11),
        # A register in the release that is not declared under apply.
        ("{address: 202, value: 0, confirm: true}", "{address: 300, value: 0}", "undeclared_register",
         "control.release.auto[0].address", 16),
        ("{address: 202, value: 0, confirm: true}", "{address: 202, value: 5}", "outside_at_rest",
         "control.release.auto[0].value", 16),
        # Same register twice.
        ("{address: 201, value", "{address: 200, value", "duplicate_address", "control.apply[1].address", None),
        ('{address: 201, value: "{discharge_w}"', '{address: 201, value: "{charge_w}"', "control", "control.apply",
         None),
        ("type: split", "type: signed\n  positive: charge", "control", "control.apply", None),
        ('value: "{mode}"', 'value: "{soc}"', "invalid", "control.apply[2].value", 13),
        ("at_rest: [0, 2], confirm", "at_rest: [1, 2], confirm", "outside_at_rest", "control.apply[2].modes", 13),
        # A wrong scale shows up against the range at rest: 2000 W as 0.1 kW are 20, not up to 2000.
        ('"{charge_w}", at_rest: [0, 2000]', '"{charge_w}", scale: 1, at_rest: [0, 20]', "outside_at_rest",
         "control.apply[0].at_rest", 11),
        ('"{charge_w}", at_rest: [0, 2000]', '"{charge_w}", at_rest: [0, 2000], persistent: true',
         "persistent_power", "control.apply[0].persistent", 11),
        ("{address: 100, type: uint16, range", "{address: 100, type: char, count: 2, range", "invalid", "probe.type",
         17),
    ],
)
def test_format_errors(old: str, new: str, code: str, path: str, line: int | None) -> None:
    assert MINIMAL.count(old) == 1
    err = error(MINIMAL.replace(old, new))
    assert err.code == code
    assert err.path == path
    if line is not None:
        assert err.line == line


def test_syntax_and_duplicate_keys() -> None:
    err = error(MINIMAL.replace("read:\n", "read:\n  soc_pct: [\n"))
    assert err.code == "syntax"
    assert err.line is not None
    err = error(MINIMAL.replace("name: Test battery\n", "name: Test battery\nname: Other\n"))
    assert (err.code, err.path, err.line) == ("duplicate_key", "name", 4)
    assert error("just: text").code == "not_profile"
    assert error("- a\n- b").code == "not_profile"


def test_at_rest_is_required_for_own_profiles_only() -> None:
    text = MINIMAL.replace(', at_rest: [0, 2000]', '')
    assert error(text).code == "missing_key"
    assert parse_profile(text, bundled=True).control.apply[0].at_rest is None


def test_values_and_scale() -> None:
    text = MINIMAL.replace(
        '{address: 200, value: "{charge_w}", at_rest: [0, 2000]}',
        '{address: 200, value: "{charge_w}", scale: 100, at_rest: [0, 20]}',
    )
    profile = parse_profile(text)
    assert [raw for _, raw in apply_values(profile, 1340, 0)] == [13, 0, 1]
    with pytest.raises(ValueError):
        apply_values(profile, 100, 100)


def test_signed_control_with_32_bit_set_point() -> None:
    text = MINIMAL.replace("type: split", "type: signed\n  positive: discharge").replace(
        '    - {address: 200, value: "{charge_w}", at_rest: [0, 2000]}\n'
        '    - {address: 201, value: "{discharge_w}", at_rest: [0, 1500]}\n',
        '    - {address: 200, value: "{power_w}", type: int32, at_rest: [-2000, 1500]}\n',
    )
    profile = parse_profile(text)
    step, raw = apply_values(profile, 800, 0)[0]
    assert raw == -800
    assert encode(raw, step.type) == [0xFFFF, 0xFCE0]
    assert apply_values(profile, 0, 600)[0][1] == 600
    # The range at rest must hold the highest powers in both directions.
    assert error(text.replace("[-2000, 1500]", "[-1500, 1500]")).code == "outside_at_rest"
    # A 32 bit register overlapping another one.
    overlap = text.replace("{address: 202, value", "{address: 201, value")
    assert error(overlap).code == "duplicate_address"


def test_persistent_registers() -> None:
    text = MINIMAL.replace(
        "  release:",
        "    - {address: 203, value: 3, at_rest: [0, 3], persistent: true, max_changes_per_day: 5}\n  release:",
    )
    step = parse_profile(text).control.apply[3]
    assert (step.persistent, step.max_changes_per_day) == (True, 5)
    bad = MINIMAL.replace('at_rest: [0, 2000]}', 'at_rest: [0, 2000], max_changes_per_day: 3}')
    assert error(bad).path == "control.apply[0].max_changes_per_day"


# --- own PV -------------------------------------------------------------------

# Like a Marstek Venus A/D: four MPPT inputs, the battery power set like a Venus E 3.0.
OWN_PV_BATTERY = """\
slems_battery_profile: 1
id: own_pv_battery
name: Own PV battery
limits: {max_charge_w: 1200, max_discharge_w: 1200}
own_pv: {max_output_w: 1200, ac_charge: true}
read:
  soc_pct: {address: 32104, type: uint16}
  pv_w:
    - {address: 30037, type: uint16, scale: 0.1}
    - {address: 30038, type: uint16, scale: 0.1}
    - {address: 30039, type: uint16, scale: 0.1}
    - {address: 30040, type: uint16, scale: 0.1}
  connection_w: {address: 30006, type: int16, positive: output}
control:
  type: split
  apply:
    - {address: 42021, value: "{discharge_w}", at_rest: [0, 2500]}
    - {address: 42020, value: "{charge_w}", at_rest: [0, 2500]}
    - {address: 42010, value: "{mode}", modes: {idle: 0, charge: 1, discharge: 2}, at_rest: [0, 2]}
  release:
    standby:
      - {address: 42010, value: 0}
probe: {address: 32104, type: uint16, range: [0, 100]}
"""

# Like an Anker Solix E5000: a signed int32 set point at the AC connection, 1-99 W refused.
CONNECTION_SET_POINT = """\
slems_battery_profile: 1
id: connection_set_point
name: Connection set point
limits: {max_charge_w: 2500, max_discharge_w: 2500, min_power_w: 100}
timing: {min_command_interval_s: 1}
own_pv: {max_output_w: 1200}
read:
  soc_pct: {address: 10014, type: uint16, input: true}
  pv_w:
    - {address: 10002, type: int32, input: true}
    - {address: 10004, type: int32, input: true}
  power_dc_w: {address: 10008, type: int32, input: true, positive: discharge}
  connection_w: {address: 10100, type: int32, input: true, positive: output}
control:
  type: signed
  setpoint: connection
  positive: discharge
  keepalive_s: 30
  apply:
    - {address: 10064, value: 3, at_rest: [0, 3]}
    - {address: 10071, value: "{power_w}", type: int32, at_rest: [-2500, 1200]}
  release:
    auto:
      - {address: 10071, value: 0}
      - {address: 10064, value: 0, confirm: true}
probe: {address: 10014, type: uint16, input: true, range: [0, 100]}
"""

# Like a plug-in battery with an output register only (no input from the grid).
OUTPUT_ONLY = """\
slems_battery_profile: 1
id: output_only
name: Output only
limits: {max_charge_w: 1800, max_discharge_w: 800}
own_pv: {max_output_w: 800, ac_charge: false, passes_pv_through: true}
read:
  soc_pct: {address: 1, type: uint16}
  pv_w: {address: 2, type: uint16}
  output_w: {address: 3, type: uint16}
control:
  type: split
  setpoint: connection
  apply:
    - {address: 10, value: "{discharge_w}", at_rest: [0, 800]}
  release:
    auto:
      - {address: 10, value: 800}
probe: {address: 1, type: uint16, range: [0, 100]}
"""


def test_own_pv_profiles() -> None:
    battery = parse_profile(OWN_PV_BATTERY)
    assert battery.own_pv.ac_charge and battery.own_pv.max_output_w == 1200
    assert len(battery.read["pv_w"].registers) == 4
    assert battery.read["connection_w"].positive == "output"
    assert not battery.control.confirmed

    connection = parse_profile(CONNECTION_SET_POINT)
    assert connection.control.setpoint == "connection"
    assert connection.timing.min_command_interval_s == 1
    assert connection.read["soc_pct"].registers[0].input
    assert split_power(connection, 60) == (0, 0)
    assert split_power(connection, -3000) == (0, 2500)
    assert [raw for _, raw in apply_values(connection, 0, 700)] == [3, 700]

    output = parse_profile(OUTPUT_ONLY)
    assert not output.own_pv.ac_charge and output.own_pv.passes_pv_through
    assert [raw for _, raw in apply_values(output, 0, 500)] == [500]


@pytest.mark.parametrize(
    ("text", "old", "new", "code", "path"),
    [
        # PV without the section own_pv, and the other way round.
        (MINIMAL, "  power_ac_w:", "  pv_w: {address: 9, type: uint16}\n  power_ac_w:", "own_pv", "read.pv_w"),
        (OWN_PV_BATTERY, "  pv_w:\n", "  pv_x:\n", "unknown_key", "read"),
        (OUTPUT_ONLY, "  pv_w: {address: 2, type: uint16}\n", "", "own_pv", "read.pv_w"),
        (OWN_PV_BATTERY, "  connection_w: {address: 30006, type: int16, positive: output}\n", "", "own_pv",
         "read.output_w"),
        # Charging from the grid needs the input.
        (OUTPUT_ONLY, "ac_charge: false", "ac_charge: true", "own_pv", "own_pv.ac_charge"),
        (OUTPUT_ONLY, "  output_w: {address: 3, type: uint16}\n", "  input_w: {address: 4, type: uint16}\n",
         "own_pv", "read.output_w"),
        (MINIMAL, "type: split", "type: split\n  setpoint: connection", "own_pv", "control.setpoint"),
        # Without an input there is no charge set point at the connection.
        (OUTPUT_ONLY, '    - {address: 10, value: "{discharge_w}", at_rest: [0, 800]}\n',
         '    - {address: 10, value: "{discharge_w}", at_rest: [0, 800]}\n'
         '    - {address: 11, value: "{charge_w}", at_rest: [0, 1800]}\n', "control", "control.apply"),
        # The output limit bounds the set point at the connection.
        (OUTPUT_ONLY, "max_output_w: 800,", "max_output_w: 900,", "outside_at_rest", "control.apply[0].at_rest"),
        (OWN_PV_BATTERY, "positive: output", "positive: charge", "invalid", "read.connection_w.positive"),
        (OWN_PV_BATTERY, "{address: 30038, type: uint16, scale: 0.1}",
         "{address: 30038, type: uint16, scale: 0.1, fallback: {address: 1, type: uint16}}", "invalid",
         "read.pv_w[1].fallback"),
        (MINIMAL, "  soc_pct: {address: 100, type: uint16}", "  soc_pct: [{address: 100, type: uint16}]", "invalid",
         "read.soc_pct"),
    ],
)
def test_own_pv_errors(text: str, old: str, new: str, code: str, path: str) -> None:
    assert text.count(old) == 1
    err = error(text.replace(old, new))
    assert (err.code, err.path) == (code, path)


# --- values and checks -------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "data_type", "order"),
    [(0, "uint16", "big"), (-2, "int16", "big"), (70000, "uint32", "big"), (-70000, "int32", "little"),
     (1234.5, "float32", "big"), (-0.25, "float32", "little")],
)
def test_encode_and_decode(value: float, data_type: str, order: str) -> None:
    assert decode(encode(value, data_type, order), data_type, order) == value


def test_encode_refuses_values_outside_the_type() -> None:
    with pytest.raises(ValueError):
        encode(70000, "uint16")
    with pytest.raises(ValueError):
        encode(-1, "uint32")
    assert decode([0x4142, 0x4300, 0], "char") == "ABC"
    assert encode(70000, "uint32", "little") == [0x1170, 0x0001]


def test_rest_violations() -> None:
    profile = parse_profile(MINIMAL)
    assert rest_violations(profile, {200: 0, 201: 1500, 202: 2}) == []
    # A shifted address reads e.g. a charge voltage (56.00 V), an unread register is no rest either.
    found = rest_violations(profile, {200: 5600, 202: 1})
    assert [(v.address, v.value) for v in found] == [(200, 5600), (201, None)]
    assert found[0].step.value == PLACEHOLDER_CHARGE


def test_persistent_guard() -> None:
    text = MINIMAL.replace(
        "  release:",
        "    - {address: 203, value: 3, at_rest: [0, 3], persistent: true, max_changes_per_day: 3}\n  release:",
    )
    guard = PersistentGuard(parse_profile(text).control.apply)
    assert guard.decide(200, 500, 0) == PersistentGuard.WRITE
    assert guard.decide(203, 3, 0) == PersistentGuard.WRITE
    guard.written(203, 3, 0)
    # Unchanged: never written again, not even as a keep-alive.
    assert guard.decide(203, 3, 10) == PersistentGuard.SKIP
    # A second change within a minute is written with a warning.
    assert guard.decide(203, 1, 30) == PersistentGuard.WARN
    guard.written(203, 1, 30)
    assert guard.decide(203, 2, 200) == PersistentGuard.WRITE
    guard.written(203, 2, 200)
    # The limit per 24 h is reached until the first change leaves the window.
    assert guard.decide(203, 3, 1000) == PersistentGuard.BLOCKED
    assert guard.decide(203, 3, 24 * 3600) == PersistentGuard.WRITE
    # After a reconnect the value in the device is unknown again.
    guard.forget()
    assert guard.decide(203, 2, 24 * 3600) == PersistentGuard.WRITE
