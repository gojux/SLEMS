"""Tests for the Modbus profiles of batteries (format, loader and checks)."""

import datetime
import importlib.util
from pathlib import Path

import pytest

from custom_components.slems.battery_profiles import (
    BUNDLED_DIR,
    PLACEHOLDER_CHARGE,
    PersistentGuard,
    ProfileError,
    Verification,
    apply_values,
    bundled_profiles,
    decode,
    encode,
    parse_profile,
    profile_text,
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
  ac_power_w: {address: 101, type: int16, positive: input}
control:
  type: split
  apply:
    - {address: 200, type: uint16, value: "{charge_w}", at_rest: [0, 2000]}
    - {address: 201, type: uint16, value: "{discharge_w}", at_rest: [0, 1500]}
    - {address: 202, type: uint16, value: "{mode}", modes: {idle: 0, charge: 1, discharge: 2}, at_rest: [0, 2], confirm: true}
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

    # Every telemetry register of the driver (except the control registers) with type and scale.
    by_extra = {
        "battery_soc": "soc_pct", "battery_power": "battery_power_w", "ac_power": "ac_power_w",
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


def test_venus_v2_profile_writes_the_steps_of_its_sources() -> None:
    # Duravolt 485 protocol v1.1 and Omnibattery registers_v2.py / marstek.py.
    profile = bundled_profiles()["marstek_venus_e_v2"]
    assert profile.unverified
    writes = [(step.address, raw) for step, raw in apply_values(profile, 800, 0)]
    assert writes == [(42000, 0x55AA), (42021, 0), (42020, 800), (42010, 1)]
    assert [raw for _, raw in apply_values(profile, 0, 600)] == [0x55AA, 600, 0, 2]
    assert [raw for _, raw in apply_values(profile, 0, 0)] == [0x55AA, 0, 0, 0]
    auto = [(step.address, raw) for step, raw in release_values(profile, ReleaseState.AUTO)]
    assert auto == [(42021, 0), (42020, 0), (42010, 0), (42000, 0x55BB)]
    standby = [(step.address, raw) for step, raw in release_values(profile, ReleaseState.STANDBY)]
    assert standby == auto[:3]
    assert {key: (value.registers[0].address, value.registers[0].type) for key, value in profile.read.items()} == {
        "soc_pct": (32104, "uint16"), "battery_power_w": (32102, "int32"), "ac_power_w": (32202, "int32"),
        "voltage_v": (32100, "uint16"), "charged_kwh": (33000, "uint32"), "discharged_kwh": (33002, "uint32"),
        "temperature_c": (35000, "int16"), "max_cell_v": (37007, "int16"), "min_cell_v": (37008, "int16"),
    }


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
    assert read(profile.read["ac_power_w"].registers[0]) == 1200
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
    # The release names only the mode: the powers are set to 0 W before it.
    assert [(step.address, step.value, step.confirm) for step in profile.control.release[ReleaseState.AUTO]] == [
        (200, 0, False), (201, 0, False), (202, 0, True),
    ]


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
        ("address: 200, type: uint16, value", "address: 70000, type: uint16, value", "invalid",
         "control.apply[0].address", 11),
        # A register in the release that is not declared under apply.
        ("{address: 202, value: 0, confirm: true}", "{address: 300, value: 0}", "undeclared_register",
         "control.release.auto[0].address", 16),
        ("{address: 202, value: 0, confirm: true}", "{address: 202, value: 5}", "outside_at_rest",
         "control.release.auto[0].value", 16),
        # Same register twice.
        ("{address: 201, type: uint16, value", "{address: 200, type: uint16, value", "duplicate_address",
         "control.apply[1].address", None),
        ('{address: 201, type: uint16, value: "{discharge_w}"', '{address: 201, type: uint16, value: "{charge_w}"', "control",
         "control.apply", None),
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
        # The probe range is raw: a scale would be ignored.
        ("{address: 100, type: uint16, range", "{address: 100, type: uint16, scale: 0.1, range", "invalid",
         "probe.scale", 17),
        # Key values (on 0x55AA, off 0x55BB) as a range: a shifted address holding 5178 (51.78 V) would pass.
        ("  release:\n", "    - {address: 203, type: uint16, value: 0x55AA, at_rest: [0, 0x55BB]}\n  release:\n",
         "key_value_range", "control.apply[3].at_rest", 14),
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
    # An answer without a profile is no YAML either: not a profile, not a syntax error.
    refusal = "No profile: the set point is stored in flash.\n\n* *Some integration* (GitHub): [https://x](https://x)\n"
    assert error(refusal).code == "not_profile"


def test_at_rest_is_required_for_every_profile() -> None:
    text = MINIMAL.replace(', at_rest: [0, 2000]', '')
    assert error(text).code == "missing_key"
    assert error(text, bundled=True).code == "missing_key"


def test_type_of_write_steps_is_required_for_every_profile() -> None:
    # Without it a 32 bit power would be written as its first word only.
    text = MINIMAL.replace('{address: 200, type: uint16, value: "{charge_w}"', '{address: 200, value: "{charge_w}"')
    for bundled in (False, True):
        err = error(text, bundled=bundled)
        assert (err.code, err.path, err.line) == ("missing_key", "control.apply[0].type", 11)


VERIFIED = """\
verified:
  - {firmware: "EMS 148", date: 2026-10-30}
  - {firmware: "EMS 151", date: 2026-11-02}
"""


def test_only_shipped_profiles_are_verified() -> None:
    profile = parse_profile(MINIMAL + VERIFIED, bundled=True)
    assert profile.verified == (
        Verification("EMS 148", datetime.date(2026, 10, 30)), Verification("EMS 151", datetime.date(2026, 11, 2)),
    )
    assert not profile.unverified
    assert parse_profile(MINIMAL, bundled=True).unverified
    # An own profile is neither verified nor unverified: it has its own confirmation.
    assert not parse_profile(MINIMAL).unverified
    err = error(MINIMAL + VERIFIED)
    assert (err.code, err.path, err.detail) == ("unknown_key", "", "verified")


@pytest.mark.parametrize(
    ("verified", "code", "path"),
    [
        ("verified: []", "invalid", "verified"),
        ("verified: {firmware: EMS 148, date: 2026-10-30}", "invalid", "verified"),
        ("verified:\n  - EMS 148", "invalid", "verified[0]"),
        ("verified:\n  - {date: 2026-10-30}", "missing_key", "verified[0].firmware"),
        ("verified:\n  - {firmware: 148, date: 2026-10-30}", "invalid", "verified[0].firmware"),
        ("verified:\n  - {firmware: EMS 148}", "missing_key", "verified[0].date"),
        ('verified:\n  - {firmware: EMS 148, date: "2026-10-30"}', "invalid", "verified[0].date"),
        ("verified:\n  - {firmware: EMS 148, date: 2026-10-30 12:00:00}", "invalid", "verified[0].date"),
        ("verified:\n  - {firmware: EMS 148, date: 2026-10-30, user: me}", "unknown_key", "verified[0]"),
    ],
)
def test_invalid_verified_entries(verified: str, code: str, path: str) -> None:
    err = error(f"{MINIMAL}{verified}\n", bundled=True)
    assert (err.code, err.path) == (code, path)


CANDIDATES = Path(__file__).parents[1] / "docs" / "battery-profiles"


def test_every_candidate_loads_as_an_own_profile() -> None:
    # Candidates are pasted as own profiles; their ids must not be taken by a shipped one.
    paths = sorted(CANDIDATES.glob("*.yaml"))
    assert paths
    for path in paths:
        profile = parse_profile(path.read_text(encoding="utf-8"))
        assert profile.id == path.stem and profile.id not in bundled_profiles(), path.name


def test_every_shipped_profile_loads_with_the_rules() -> None:
    for path in sorted(BUNDLED_DIR.glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        profile = parse_profile(text, bundled=True)
        if profile.unverified:
            # The same rules as for an own profile (verified aside).
            assert parse_profile(text).content_hash == profile.content_hash, path.name


def test_durations_may_have_a_range_at_rest() -> None:
    # A time-out after which the battery returns to its own logic (e.g. 0-1440 min) is no key value.
    for step in (
        "{address: 203, type: uint16, value: 1440, at_rest: [0, 1440]}",
        "{address: 203, type: uint32, word_order: little, value: 4095, at_rest: [0, 86400]}",
    ):
        parse_profile(MINIMAL.replace("  release:", f"    - {step}\n  release:"))
    key_value = "    - {address: 203, type: uint16, value: 4096, at_rest: [0, 4351]}\n  release:"
    assert error(MINIMAL.replace("  release:", key_value)).code == "key_value_range"
    narrow = "    - {address: 203, type: uint16, value: 0x55AA, at_rest: [0x5500, 0x55FF]}\n  release:"
    parse_profile(MINIMAL.replace("  release:", narrow))


def test_release_may_repeat_type_and_word_order() -> None:
    step = "{address: 202, value: 0, confirm: true}"
    same = MINIMAL.replace(step, "{address: 202, type: uint16, word_order: big, value: 0, confirm: true}")
    assert parse_profile(same).control.release[ReleaseState.AUTO] == parse_profile(MINIMAL).control.release[
        ReleaseState.AUTO
    ]
    for key, value in (("type", "int16"), ("word_order", "little")):
        err = error(MINIMAL.replace(step, f"{{address: 202, {key}: {value}, value: 0}}"))
        assert (err.code, err.path, err.line) == ("release_type", f"control.release.auto[0].{key}", 16)


@pytest.mark.parametrize(("data_type", "order"), [("uint16", "big"), ("uint32", "big"), ("int32", "little")])
def test_state_registers(data_type: str, order: str) -> None:
    state = f"  state: {{address: 110, type: {data_type}, word_order: {order}, values: {{3: charge, 4: discharge}}}}\n"
    text = MINIMAL.replace("control:\n", f"{state}control:\n", 1)
    register = parse_profile(text).state.register
    assert (register.type, register.word_order) == (data_type, order)
    assert decode(encode(4, data_type, order), data_type, order) == 4


@pytest.mark.parametrize("data_type", ["float32", "uint64"])
def test_state_registers_refused(data_type: str) -> None:
    state = f"  state: {{address: 110, type: {data_type}, values: {{3: charge}}}}\n"
    err = error(MINIMAL.replace("control:\n", f"{state}control:\n", 1))
    assert (err.code, err.path) == ("invalid", "read.state.type")


def test_profile_text_takes_the_only_code_block() -> None:
    answer = f"I checked the technical note first.\n\n```yaml\n{MINIMAL}```\n\nCompare the write registers.\n"
    assert profile_text(answer) == MINIMAL
    assert parse_profile(profile_text(answer)).id == "test"
    assert profile_text(f"```\n{MINIMAL}```") == MINIMAL
    assert profile_text(MINIMAL) == MINIMAL
    # Two blocks or another language: left as it is (the loader then names the error).
    two = f"{answer}```yaml\nother: 1\n```\n"
    assert profile_text(two) == two
    shell = f"```sh\n{MINIMAL}```"
    assert profile_text(shell) == shell


def test_profile_text_takes_a_block_opened_at_the_end_of_a_line() -> None:
    # Mistral (prompt test rounds 7 and 8) opens the block right after its text.
    answer = f"I will check the data sheet online.```yaml\n{MINIMAL}```\n"
    assert profile_text(answer) == MINIMAL
    shell = f"Run this:```sh\n{MINIMAL}```\n"
    assert profile_text(shell) == shell


def test_values_and_scale() -> None:
    text = MINIMAL.replace(
        '{address: 200, type: uint16, value: "{charge_w}", at_rest: [0, 2000]}',
        '{address: 200, type: uint16, value: "{charge_w}", scale: 100, at_rest: [0, 20]}',
    )
    profile = parse_profile(text)
    assert [raw for _, raw in apply_values(profile, 1340, 0)] == [13, 0, 1]
    with pytest.raises(ValueError):
        apply_values(profile, 100, 100)


def test_signed_control_with_32_bit_set_point() -> None:
    text = MINIMAL.replace("type: split", "type: signed\n  positive: discharge").replace(
        '    - {address: 200, type: uint16, value: "{charge_w}", at_rest: [0, 2000]}\n'
        '    - {address: 201, type: uint16, value: "{discharge_w}", at_rest: [0, 1500]}\n',
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
    overlap = text.replace("{address: 202, type: uint16, value", "{address: 201, type: uint16, value")
    assert error(overlap).code == "duplicate_address"


# Like a Sungrow hybrid: one power register without a sign, the direction from a command.
def test_magnitude_control_with_direction_command() -> None:
    text = MINIMAL.replace("type: split", "type: magnitude").replace(
        '    - {address: 200, type: uint16, value: "{charge_w}", at_rest: [0, 2000]}\n'
        '    - {address: 201, type: uint16, value: "{discharge_w}", at_rest: [0, 1500]}\n'
        '    - {address: 202, type: uint16, value: "{mode}", modes: {idle: 0, charge: 1, discharge: 2}, at_rest: [0, 2], confirm: true}\n',
        '    - {address: 200, type: uint16, value: "{power_w}", at_rest: [0, 2000]}\n'
        '    - {address: 202, type: uint16, value: "{mode}", modes: {idle: 0xCC, charge: 0xAA, discharge: 0xBB},'
        ' at_rest_values: [0xAA, 0xBB, 0xCC], confirm: true}\n',
    ).replace("{address: 202, value: 0, confirm: true}", "{address: 202, value: 0xCC, confirm: true}")
    profile = parse_profile(text)
    assert profile.control.positive is None
    assert [raw for _, raw in apply_values(profile, 800, 0)] == [800, 0xAA]
    assert [raw for _, raw in apply_values(profile, 0, 600)] == [600, 0xBB]
    assert [raw for _, raw in apply_values(profile, 0, 0)] == [0, 0xCC]
    # The power register is set to 0 W at the start of a release that leaves it out.
    assert [(step.address, raw) for step, raw in release_values(profile, ReleaseState.AUTO)] == [(200, 0), (202, 0xCC)]
    # The range at rest must hold the highest power of both directions.
    assert error(text.replace("[0, 2000]", "[0, 1500]")).path == "control.apply[0].at_rest"
    # Without the direction, or with split placeholders.
    no_mode = text.replace('value: "{mode}", modes: {idle: 0xCC, charge: 0xAA, discharge: 0xBB},', "value: 0xCC,")
    assert (error(no_mode).code, error(no_mode).detail) == ("control", "magnitude needs {mode}")
    split = text.replace('value: "{power_w}"', 'value: "{charge_w}"')
    assert error(split).code == "control"
    assert error(text.replace("type: magnitude", "type: magnitude\n  positive: charge")).path == "control.positive"


def test_persistent_registers() -> None:
    text = MINIMAL.replace(
        "  release:",
        "    - {address: 203, type: uint16, value: 3, at_rest: [0, 3], persistent: true, max_changes_per_day: 5}\n"
        "  release:",
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
  pv_power_w:
    - {address: 30037, type: uint16, scale: 0.1}
    - {address: 30038, type: uint16, scale: 0.1}
    - {address: 30039, type: uint16, scale: 0.1}
    - {address: 30040, type: uint16, scale: 0.1}
  ac_power_w: {address: 30006, type: int16, positive: output}
control:
  type: split
  apply:
    - {address: 42021, type: uint16, value: "{discharge_w}", at_rest: [0, 2500]}
    - {address: 42020, type: uint16, value: "{charge_w}", at_rest: [0, 2500]}
    - {address: 42010, type: uint16, value: "{mode}", modes: {idle: 0, charge: 1, discharge: 2}, at_rest: [0, 2]}
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
  pv_power_w:
    - {address: 10002, type: int32, input: true}
    - {address: 10004, type: int32, input: true}
  battery_power_w: {address: 10008, type: int32, input: true, positive: discharge}
  ac_power_w: {address: 10100, type: int32, input: true, positive: output}
control:
  type: signed
  setpoint: connection
  positive: discharge
  keepalive_s: 30
  apply:
    - {address: 10064, type: uint16, value: 3, at_rest: [0, 3]}
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
  pv_power_w: {address: 2, type: uint16}
  ac_power_w: {output: {address: 3, type: uint16}}
control:
  type: split
  setpoint: connection
  apply:
    - {address: 10, type: uint16, value: "{discharge_w}", at_rest: [0, 800]}
  release:
    auto:
      - {address: 10, value: 800}
probe: {address: 1, type: uint16, range: [0, 100]}
"""


def test_own_pv_profiles() -> None:
    battery = parse_profile(OWN_PV_BATTERY)
    assert battery.own_pv.ac_charge and battery.own_pv.max_output_w == 1200
    assert len(battery.read["pv_power_w"].registers) == 4
    assert battery.read["ac_power_w"].positive == "output"
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
        (MINIMAL, "  ac_power_w:", "  pv_power_w: {address: 9, type: uint16}\n  ac_power_w:", "own_pv",
         "read.pv_power_w"),
        (OWN_PV_BATTERY, "  pv_power_w:\n", "  pv_x:\n", "unknown_key", "read"),
        (OUTPUT_ONLY, "  pv_power_w: {address: 2, type: uint16}\n", "", "own_pv", "read.pv_power_w"),
        (OWN_PV_BATTERY, "  ac_power_w: {address: 30006, type: int16, positive: output}\n", "", "own_pv",
         "read.ac_power_w"),
        # Charging from the grid needs the input.
        (OUTPUT_ONLY, "ac_charge: false", "ac_charge: true", "own_pv", "own_pv.ac_charge"),
        (OUTPUT_ONLY, "{output: {address: 3, type: uint16}}", "{input: {address: 4, type: uint16}}", "missing_key",
         "read.ac_power_w.output"),
        (MINIMAL, "type: split", "type: split\n  setpoint: connection", "own_pv", "control.setpoint"),
        # Without an input there is no charge set point at the connection.
        (OUTPUT_ONLY, '    - {address: 10, type: uint16, value: "{discharge_w}", at_rest: [0, 800]}\n',
         '    - {address: 10, type: uint16, value: "{discharge_w}", at_rest: [0, 800]}\n'
         '    - {address: 11, type: uint16, value: "{charge_w}", at_rest: [0, 1800]}\n', "control", "control.apply"),
        # The output limit bounds the set point at the connection.
        (OUTPUT_ONLY, "max_output_w: 800,", "max_output_w: 900,", "outside_at_rest", "control.apply[0].at_rest"),
        (OWN_PV_BATTERY, "positive: output", "positive: charge", "invalid", "read.ac_power_w.positive"),
        (OWN_PV_BATTERY, "{address: 30038, type: uint16, scale: 0.1}",
         "{address: 30038, type: uint16, scale: 0.1, fallback: {address: 1, type: uint16}}", "invalid",
         "read.pv_power_w[1].fallback"),
        (MINIMAL, "  soc_pct: {address: 100, type: uint16}", "  soc_pct: [{address: 100, type: uint16}]", "invalid",
         "read.soc_pct"),
    ],
)
def test_own_pv_errors(text: str, old: str, new: str, code: str, path: str) -> None:
    assert text.count(old) == 1
    err = error(text.replace(old, new))
    assert (err.code, err.path) == (code, path)


@pytest.mark.parametrize(
    ("old", "new", "code", "path"),
    [
        # A battery always charges from the grid without own PV.
        ("  ac_power_w: {address: 101, type: int16, positive: input}",
         "  ac_power_w: {output: {address: 101, type: uint16}}", "missing_key", "read.ac_power_w.input"),
        ("  ac_power_w: {address: 101, type: int16, positive: input}",
         "  battery_power_w: {charge: {address: 101, type: uint16}}", "missing_key", "read.battery_power_w.discharge"),
        # The sides are those of the key, the side gives the direction.
        ("  ac_power_w: {address: 101, type: int16, positive: input}",
         "  ac_power_w: {charge: {address: 101, type: uint16}, output: {address: 102, type: uint16}}", "unknown_key",
         "read.ac_power_w"),
        ("  ac_power_w: {address: 101, type: int16, positive: input}",
         "  ac_power_w: {input: {address: 101, type: uint16, positive: input}, output: {address: 102, type: uint16}}",
         "invalid", "read.ac_power_w.input.positive"),
        ("  ac_power_w: {address: 101, type: int16, positive: input}",
         "  ac_power_w: {input: {address: 101, type: uint16, fallback: {address: 1, type: uint16}},"
         " output: {address: 102, type: uint16}}", "invalid", "read.ac_power_w.input.fallback"),
        # A signed AC power is input or output, a battery power charge or discharge.
        ("positive: input", "positive: charge", "invalid", "read.ac_power_w.positive"),
        ("  ac_power_w: {address: 101, type: int16, positive: input}",
         "  battery_power_w: {address: 101, type: int16, positive: output}", "invalid", "read.battery_power_w.positive"),
        ("  ac_power_w: {address: 101, type: int16, positive: input}", "", "missing_key", "read.ac_power_w"),
    ],
)
def test_power_errors(old: str, new: str, code: str, path: str) -> None:
    assert MINIMAL.count(old) == 1
    err = error(MINIMAL.replace(old, new))
    assert (err.code, err.path) == (code, path)


def test_powers_as_pairs_and_sums() -> None:
    profile = parse_profile(MINIMAL.replace(
        "  ac_power_w: {address: 101, type: int16, positive: input}",
        "  battery_power_w:\n"
        "    charge: [{address: 101, type: uint16}, {address: 102, type: uint16}]\n"
        "    discharge: {address: 103, type: uint16, scale: 10}\n"
        "  ac_power_w: {input: {address: 104, type: uint16}, output: {address: 105, type: uint16}}",
    ))
    battery = profile.read["battery_power_w"]
    assert [r.address for r in battery.sides["charge"]] == [101, 102]
    assert [r.address for r in battery.registers] == [101, 102, 103]
    assert battery.positive is None
    assert set(profile.read["ac_power_w"].sides) == {"input", "output"}


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
        "    - {address: 203, type: uint16, value: 3, at_rest: [0, 3], persistent: true, max_changes_per_day: 3}\n"
        "  release:",
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
