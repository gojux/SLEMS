"""Setup of a battery from a Modbus profile: content hash and trial key, the probe
reading and write preview (against the Venus simulator's register model), the
loading of unusable profiles and the steps of the config flow."""

import json
from pathlib import Path
import re
from types import SimpleNamespace
from unittest.mock import patch

from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.slems import battery_profiles, config_flow, diagnostics
from custom_components.slems.battery_profiles import (
    ERROR_CODES,
    ProfileError,
    bundled_profile_text,
    bundled_profiles,
    parse_profile,
    profile_of,
)
from custom_components.slems.const import ReleaseState
from custom_components.slems.drivers.profile import ProfileDriver, connect_new, read_profile
from custom_components.slems.profile_check import (
    BLOCK,
    WARN,
    ProbeReading,
    blocked,
    check,
    describe_preview,
    describe_reading,
    preview,
)
from custom_components.slems.trial import TrialReason, TrialState, TrialStatus

from test_profile_driver import SIM, SimLink, no_confirm_delay  # noqa: F401

STRINGS = json.loads((Path(config_flow.__file__).parent / "strings.json").read_text())
VENUS_TEXT = bundled_profile_text("marstek_venus_e3")
# The Venus profile as an own one.
OWN_TEXT = VENUS_TEXT.replace("id: marstek_venus_e3", "id: my_venus")
VERIFIED = 'verified:\n  - {firmware: "EMS 148", date: 2026-10-30}\n'


def venus():
    return bundled_profiles()["marstek_venus_e3"]


# --- content hash and trial key ----------------------------------------------


def test_shipped_profile_key_has_the_hash_of_its_content() -> None:
    assert venus().key.startswith("marstek_venus_e3@1#") and len(venus().content_hash) == 12
    # A passed trial noted in the profile does not change it.
    assert parse_profile(VENUS_TEXT + VERIFIED, bundled=True).key == venus().key
    # A register does, also without a higher revision.
    changed = VENUS_TEXT.replace("at_rest: [0, 2], confirm: true", "at_rest: [0, 3], confirm: true")
    assert parse_profile(changed, bundled=True).key != venus().key


def test_own_profile_key_has_the_hash_of_its_content() -> None:
    own = parse_profile(OWN_TEXT)
    assert own.key.startswith("my_venus@1#") and len(own.content_hash) == 12
    # Comments, layout, order and the descriptive keys do not change it.
    reworded = OWN_TEXT.replace("name: Marstek Venus E 3.0", "name: My Venus").replace(
        "# RS485 control first", "# changed comment"
    )
    reworded = reworded.replace("  port: 502\n  unit_id: 1\n", "  unit_id: 1\n  port: 502\n")
    assert parse_profile(reworded).key == own.key
    # A register does.
    changed = OWN_TEXT.replace("at_rest: [0, 2], confirm: true", "at_rest: [0, 3], confirm: true")
    assert parse_profile(changed).key != own.key


def test_profile_of_a_battery() -> None:
    bundled = bundled_profiles()
    assert profile_of({"profile_id": "marstek_venus_e3"}, bundled) is venus()
    assert profile_of({"profile_id": "my_venus", "profile_yaml": OWN_TEXT}, bundled).id == "my_venus"
    with pytest.raises(ProfileError) as err:
        profile_of({"profile_id": "gone"}, bundled)
    assert err.value.code == "unknown_profile"


def test_every_profile_error_has_a_text() -> None:
    for name in ("strings.json", "translations/en.json", "translations/de.json"):
        texts = json.loads((Path(config_flow.__file__).parent / name).read_text())
        errors = texts["config_subentries"]["battery"]["error"]
        for code in ERROR_CODES:
            assert f"profile_{code}" in errors, f"{name}: {code}"
        assert "profile_unusable" in texts["issues"]


def test_every_code_the_loader_raises_is_known() -> None:
    source = Path(battery_profiles.__file__).read_text()
    raised = set(re.findall(r'(?:ProfileError|_fail)\(\s*"(\w+)"', source))
    # unknown_profile comes from profile_of (a repair, not the dialog).
    assert raised - {"unknown_profile"} <= set(ERROR_CODES)
    assert {"persistent_power", "undeclared_register"} <= raised


def test_trial_begins_again_at_another_address() -> None:
    state = TrialState("p@1", status=TrialStatus.PASSED, device="a:502/1", device_info={"ems": "148"})
    same = TrialState.restore(state.as_dict(), "p@1", 100.0, "a:502/1")
    assert same.status is TrialStatus.PASSED
    moved = TrialState.restore(state.as_dict(), "p@1", 100.0, "b:502/1")
    assert moved.status is TrialStatus.TRIAL and moved.reason is TrialReason.NEW
    assert moved.device_info == {} and moved.device == "b:502/1"
    # A state stored before the device was kept adopts it.
    old = {**state.as_dict(), "device": ""}
    adopted = TrialState.restore(old, "p@1", 100.0, "a:502/1")
    assert adopted.status is TrialStatus.PASSED and adopted.device == "a:502/1"


def test_trial_in_the_diagnostics_is_redacted() -> None:
    # It is posted in an issue to verify a shipped profile.
    state = TrialState(
        "p@1#abc", status=TrialStatus.PASSED, device="192.168.1.20:502/1",
        device_info={"ems_version": 148, "mac_address": "aa:bb:cc:dd:ee:ff", "serial_number": "VNSE3-123"},
    )
    battery = SimpleNamespace(trial=state, in_trial=False, device_info_checked=True, self_test=None)
    trial = diagnostics._trial(battery)
    assert trial["device"] == "**REDACTED**" and trial["device_info"]["mac_address"] == "**REDACTED**"
    assert trial["device_info"]["serial_number"] == "**REDACTED**"
    assert (trial["profile_key"], trial["status"], trial["device_info"]["ems_version"]) == ("p@1#abc", "passed", 148)


# --- probe reading and write preview -----------------------------------------


async def read(text: str | None = None, model=None) -> tuple:
    profile = parse_profile(text) if text else venus()
    link = SimLink(model or SIM.VenusModel(5120, 63.0))
    reading = await read_profile(profile, link)
    return profile, reading, link


async def test_fresh_venus_reads_without_a_mark() -> None:
    profile, reading, link = await read()
    assert reading.values["soc_pct"] == 63.0
    assert reading.writes == {42000: 0x55BB, 42021: 0, 42020: 0, 42010: 0}
    assert reading.device_info["ems_version"] == "148"
    assert reading.state is not None
    marks = check(profile, reading)
    assert not blocked(marks), marks
    # Only read, the connection opened for it is closed again.
    assert not link.writes() and not link.connected


async def test_wrong_scale_of_the_state_of_charge_blocks() -> None:
    text = OWN_TEXT.replace("{address: 34002, type: uint16, scale: 0.1,", "{address: 34002, type: uint16,")
    profile, reading, _ = await read(text)
    marks = check(profile, reading)
    assert marks["soc_pct"].level == BLOCK and blocked(marks)


async def test_shifted_write_register_blocks() -> None:
    # 42020 shifted onto the battery voltage (e.g. 5300 for 53.00 V).
    text = OWN_TEXT.replace('{address: 42020, type: uint16, value: "{charge_w}"', '{address: 30100, type: uint16, value: "{charge_w}"')
    text = text.replace("- {address: 42020, value: 0}", "- {address: 30100, value: 0}")
    profile, reading, _ = await read(text)
    marks = check(profile, reading)
    assert marks["write_30100"] == type(marks["write_30100"])(BLOCK, "outside")
    assert "⛔" in describe_reading(profile, reading, marks, "de")


async def test_battery_without_an_answer_reads_nothing() -> None:
    link = SimLink(SIM.VenusModel(5120, 50.0))

    async def refuse() -> bool:
        return False

    link.connect = refuse
    assert await read_profile(venus(), link) is None


async def test_refused_connection_is_tried_again_with_a_new_transport(no_confirm_delay) -> None:  # noqa: F811
    # A Venus refuses connections for 0.5 s after the last one closed; a link
    # whose connect failed waits its backoff, so the second try needs a new one.
    links = []

    def new():
        link = SimLink(SIM.VenusModel(5120, 50.0))
        if not links:
            async def refuse() -> bool:
                return False

            link.connect = refuse
        links.append(link)
        return link

    link = await connect_new(new)
    assert link is links[1] and link.connected


async def test_connected_transport_is_used_and_kept_open() -> None:
    link = SimLink(SIM.VenusModel(5120, 50.0))
    await link.connect()
    assert await read_profile(venus(), link) is not None
    assert link.connected


def test_unlikely_values_are_marked() -> None:
    profile = venus()
    reading = ProbeReading(
        probe=500,
        values={
            "soc_pct": 50.0, "battery_power_w": 0, "ac_power_w": 6000.0, "voltage_v": 53.0, "charged_kwh": -1.0,
            "discharged_kwh": 1.0, "temperature_c": 120.0, "max_cell_v": 3.2, "min_cell_v": 3.3, "cycles": None,
        },
        state=9,
        device_info={"device_name": None},
        writes={42000: 0x55BB, 42021: 0, 42020: 0, 42010: 0},
    )
    marks = check(profile, reading)
    assert {key: mark.reason for key, mark in marks.items()} == {
        "ac_power_w": "unlikely", "charged_kwh": "negative", "temperature_c": "unlikely",
        "max_cell_v": "cells_swapped", "cycles": "unreadable", "state": "unknown_state", "device_name": "unreadable",
    }
    assert all(mark.level == WARN for mark in marks.values()) and not blocked(marks)
    assert check(profile, ProbeReading(probe=1001, values={"soc_pct": None}))["soc_pct"].level == BLOCK
    assert check(profile, ProbeReading(probe=1001, values={}))["probe"].level == BLOCK


@pytest.mark.parametrize(
    ("state", "values", "marked"),
    [
        # Venus: 2 charge, 3 discharge; the battery power is checked first, else the AC power.
        (2, {"battery_power_w": -300.0, "ac_power_w": 280.0}, "battery_power_w"),
        (3, {"battery_power_w": 300.0}, "battery_power_w"),
        (3, {"ac_power_w": 300.0}, "ac_power_w"),
        (2, {"battery_power_w": -49.0}, None),
        (2, {"battery_power_w": 300.0}, None),
        (1, {"battery_power_w": -300.0}, None),
    ],
)
def test_power_against_the_state_is_marked(state: int, values: dict, marked: str | None) -> None:
    profile = venus()
    reading = ProbeReading(
        probe=500, values={"soc_pct": 50.0, **values}, state=state, writes={42000: 0x55BB, 42021: 0, 42020: 0, 42010: 0}
    )
    marks = check(profile, reading)
    assert {key for key, mark in marks.items() if mark.reason == "against_state"} == ({marked} if marked else set())
    assert not blocked(marks)
    if marked:
        assert "widerspricht dem Zustand" in describe_reading(profile, reading, marks, "de")


MINIMAL_PAIR = """\
slems_battery_profile: 1
id: pair
name: Pair
limits: {max_charge_w: 2000, max_discharge_w: 2000}
read:
  soc_pct: {address: 100, type: uint16}
  battery_power_w: {charge: {address: 101, type: uint16}, discharge: {address: 102, type: uint16}}
control:
  type: split
  apply:
    - {address: 200, type: uint16, value: "{charge_w}", at_rest: [0, 2000]}
    - {address: 201, type: uint16, value: "{discharge_w}", at_rest: [0, 2000]}
    - {address: 202, type: uint16, value: "{mode}", modes: {idle: 0, charge: 1, discharge: 2}, at_rest: [0, 2]}
  release:
    auto:
      - {address: 202, value: 0}
probe: {address: 100, type: uint16, range: [0, 100]}
"""


def test_pair_shows_its_sides_and_a_negative_side_is_marked() -> None:
    profile = parse_profile(MINIMAL_PAIR)
    reading = ProbeReading(
        probe=50,
        values={"soc_pct": 50.0, "battery_power_w": 300.0},
        sides={"battery_power_w": {"charge": 300.0, "discharge": 0}},
        writes={200: 0, 201: 0, 202: 0},
    )
    marks = check(profile, reading)
    assert marks == {}
    assert "- Batterieleistung (+ Laden): 300 W (Laden 300 W, Entladen 0 W)" in describe_reading(
        profile, reading, marks, "de"
    )
    reading.sides["battery_power_w"]["discharge"] = -20.0
    assert check(profile, reading)["battery_power_w"].reason == "negative"


def test_preview_shows_each_register_with_the_value_it_holds_then() -> None:
    profile = venus()
    writes = {42000: 0x55BB, 42021: 0, 42020: 0, 42010: 0}
    phases = preview(profile, writes, 625, ReleaseState.AUTO)
    charge = {line.step.address: (line.before, line.after) for line in phases["charge"]}
    assert charge == {42000: (0x55BB, 0x55AA), 42021: (0, 0), 42020: (0, 625), 42010: (0, 1)}
    discharge = {line.step.address: (line.before, line.after) for line in phases["discharge"]}
    assert discharge[42020] == (625, 0) and discharge[42021] == (0, 625) and discharge[42010] == (1, 2)
    release = [(line.step.address, line.before, line.after) for line in phases["release"]]
    assert release == [(42021, 625, 0), (42020, 0, 0), (42010, 2, 0), (42000, 0x55AA, 0x55BB)]
    text = describe_preview(profile, phases, 625, ReleaseState.AUTO, "de")
    assert "**Laden mit 625 W**" in text
    assert "- 42020 Ladeleistung: 0 → 625 · rückgelesen" in text
    assert "- 42000 fester Wert: 0x55BB → 0x55AA · rückgelesen" in text
    assert "**Freigabe (Automatik)**" in text


def test_small_values_at_rest_are_shown_decimal() -> None:
    # Listed modes 0, 1, 2 read like the manual; key values stay hexadecimal.
    profile = parse_profile(OWN_TEXT.replace("at_rest: [0, 2]", "at_rest_values: [0, 1, 2]", 1))
    writes = {42000: 0x55BB, 42021: 0, 42020: 0, 42010: 0}
    text = describe_preview(profile, preview(profile, writes, 625, ReleaseState.AUTO), 625, ReleaseState.AUTO, "de")
    assert "- 42010 Modus: 0 → 1 · rückgelesen" in text
    assert "- 42000 fester Wert: 0x55BB → 0x55AA · rückgelesen" in text


# --- config flow ---------------------------------------------------------------


class FakeEntries:
    def __init__(self) -> None:
        self.updated: list[tuple] = []

    def async_update_subentry(self, entry, subentry, *, data=None, title=None, **_) -> bool:
        self.updated.append((subentry.subentry_id, title, data))
        return True


def subentry(subentry_id: str, title: str, **data) -> SimpleNamespace:
    return SimpleNamespace(subentry_id=subentry_id, subentry_type="battery", title=title, data=data)


def make_flow(subentries=(), *, reconfigure=None, batteries=()) -> config_flow.BatterySubentryFlow:
    async def executor(func, *args):
        return func(*args)

    flow = config_flow.BatterySubentryFlow()
    flow.hass = SimpleNamespace(
        config=SimpleNamespace(language="de", currency="EUR"),
        async_add_executor_job=executor,
        config_entries=FakeEntries(),
    )
    flow.handler = ("entry", "battery")
    flow.flow_id = "flow"
    entry = SimpleNamespace(
        entry_id="entry",
        subentries={item.subentry_id: item for item in subentries},
        runtime_data=SimpleNamespace(batteries=list(batteries)),
    )
    flow._get_entry = lambda: entry
    flow.context = {"source": SOURCE_RECONFIGURE if reconfigure else SOURCE_USER}
    if reconfigure:
        flow._get_reconfigure_subentry = lambda: entry.subentries[reconfigure]
    flow._model = config_flow.BatteryModel.MODBUS_PROFILE
    return flow


LIMITS = {
    "name": "Pack", "capacity_wh": 5120, "max_charge_power_w": 2500, "max_discharge_power_w": 2500,
    "efficiency_mode": "battery_counters", "round_trip_efficiency_pct": 88, "release_state": "auto",
}
ADDRESS = {"host": "venus", "port": 502, "unit_id": 1, "transport": "own"}


@pytest.fixture
def sim(no_confirm_delay):  # noqa: F811
    model = SIM.VenusModel(5120, 63.0)
    links: list[SimLink] = []

    def own_link(*_args):
        links.append(SimLink(model))
        return links[-1]

    with patch.object(config_flow, "own_link", own_link):
        yield SimpleNamespace(model=model, links=links)


@pytest.fixture
def dev_profiles(monkeypatch):
    """The dev environment: the Venus profile is offered as well."""
    monkeypatch.setenv("SLEMS_DEV_PROFILES", "1")


@pytest.fixture
def no_dev_profiles(monkeypatch):
    monkeypatch.delenv("SLEMS_DEV_PROFILES", raising=False)


@pytest.fixture
def verified_venus(monkeypatch):
    """The Venus profile as if it had passed a trial on a real battery."""
    profile = parse_profile(VENUS_TEXT + VERIFIED, bundled=True)
    monkeypatch.setattr(config_flow, "bundled_profiles", lambda: {profile.id: profile})


@pytest.fixture
def only_dev_profiles(monkeypatch):
    """Only the profiles for dev are shipped (the case without a profile to offer)."""
    profile = venus()
    monkeypatch.setattr(config_flow, "bundled_profiles", lambda: {profile.id: profile})


def options(result) -> list[str]:
    return [option["value"] for option in result["data_schema"].schema["profile"].config["options"]]


def labels(result) -> dict[str, str]:
    return {option["value"]: option["label"] for option in result["data_schema"].schema["profile"].config["options"]}


def preview_fields(result) -> list[str]:
    return [str(key) for key in result["data_schema"].schema]


def test_every_profile_step_has_its_texts() -> None:
    steps = STRINGS["config_subentries"]["battery"]["step"]
    for step_id in (
        "profile", "profile_yaml", "profile_connection", "profile_read", "profile_limits", "profile_preview",
        "profile_others", "profile_reconfigure", "profile_export",
    ):
        assert step_id in steps
    assert "modbus_profile" not in steps


async def test_new_battery_from_the_shipped_profile(sim, dev_profiles) -> None:
    flow = make_flow()
    result = await flow.async_step_profile()
    assert result["type"] is FlowResultType.FORM
    result = await flow.async_step_profile({"profile": "marstek_venus_e3"})
    assert result["step_id"] == "profile_connection"
    result = await flow.async_step_profile_connection(dict(ADDRESS))
    assert result["type"] is FlowResultType.MENU and result["step_id"] == "profile_read"
    assert result["menu_options"][0] == "profile_limits"
    # A shipped profile cannot be edited here.
    assert "profile_yaml" not in result["menu_options"]
    assert "Ladezustand: 63 %" in result["description_placeholders"]["reading"]
    result = await flow.async_step_profile_limits(dict(LIMITS))
    # The Venus profile has not passed a trial on a real battery: that must be known.
    assert result["step_id"] == "profile_preview" and preview_fields(result) == ["checked_unverified"]
    result = await flow.async_step_profile_preview({"checked_unverified": False})
    assert result["errors"] == {"base": "not_checked_unverified"}
    result = await flow.async_step_profile_preview({"checked_unverified": True})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Pack"
    assert result["data"]["profile_id"] == "marstek_venus_e3" and "profile_yaml" not in result["data"]
    assert result["data"]["host"] == "venus" and result["data"]["model"] == "modbus_profile"
    # Nothing was written during the setup.
    assert not any(link.writes() for link in sim.links)


async def test_verified_shipped_profile_needs_no_confirmation(sim, dev_profiles, verified_venus) -> None:
    flow = make_flow()
    result = await flow.async_step_profile()
    assert labels(result)["marstek_venus_e3"] == "Marstek Venus E 3.0"
    await flow.async_step_profile({"profile": "marstek_venus_e3"})
    await flow.async_step_profile_connection(dict(ADDRESS))
    result = await flow.async_step_profile_limits(dict(LIMITS))
    assert result["step_id"] == "profile_preview" and preview_fields(result) == []
    result = await flow.async_step_profile_preview({})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_without_a_profile_to_choose_straight_to_the_own_one(sim, no_dev_profiles, only_dev_profiles) -> None:
    flow = make_flow()
    result = await flow.async_step_profile()
    assert result["step_id"] == "profile_yaml"
    assert result["description_placeholders"]["prompt_url"].endswith("/docs/battery-profile-prompt.de.md")
    await flow.async_step_profile_yaml({"profile_yaml": OWN_TEXT})
    result = await flow.async_step_profile_connection(dict(ADDRESS))
    # Nothing else to choose: only editing the profile.
    assert "profile_yaml" in result["menu_options"] and "profile" not in result["menu_options"]


async def test_venus_profile_only_in_dev(sim, dev_profiles) -> None:
    flow = make_flow()
    result = await flow.async_step_profile()
    assert result["step_id"] == "profile"
    assert options(result) == ["marstek_venus_e3", "marstek_venus_e_v2", "own"]
    assert labels(result)["marstek_venus_e3"] == "Marstek Venus E 3.0 (ungeprüft)"
    assert result["description_placeholders"]["prompt_url"].endswith("/docs/battery-profile-prompt.de.md")


async def test_without_dev_the_unverified_venus_v2_profile_is_offered(sim, no_dev_profiles) -> None:
    flow = make_flow()
    result = await flow.async_step_profile()
    assert result["step_id"] == "profile" and options(result) == ["marstek_venus_e_v2", "own"]
    assert labels(result)["marstek_venus_e_v2"] == "Marstek Venus E Gen 2 (ungeprüft)"


async def test_without_dev_a_copy_is_still_offered(sim, no_dev_profiles, only_dev_profiles) -> None:
    flow = make_flow([subentry("p1", "Pack 1", model="modbus_profile", profile_id="my_venus", profile_yaml=OWN_TEXT)])
    result = await flow.async_step_profile()
    assert result["step_id"] == "profile" and options(result) == ["copy_p1", "own"]


async def test_reconfigured_battery_keeps_its_dev_profile(sim, no_dev_profiles, only_dev_profiles) -> None:
    data = {"model": "modbus_profile", "profile_id": "marstek_venus_e3", **ADDRESS, **LIMITS}
    flow = make_flow([subentry("p1", "Pack", **data)], reconfigure="p1")
    await flow.async_step_profile_reconfigure()
    result = await flow.async_step_profile()
    assert options(result) == ["marstek_venus_e3", "own"]
    flow = make_flow()
    assert (await flow.async_step_profile())["step_id"] == "profile_yaml"


async def test_own_profile_errors_show_line_and_key(sim) -> None:
    flow = make_flow()
    await flow.async_step_profile({"profile": "own"})
    result = await flow.async_step_profile_yaml({"profile_yaml": "slems_battery_profile: 1\nid: [unclosed"})
    assert result["errors"] == {"base": "profile_syntax"}
    # An AI's refusal without a code block is no profile, not a syntax error in it.
    refusal = "No profile: the set point is stored in flash.\n\n* *Integration* (GitHub): [https://x](https://x)\n"
    result = await flow.async_step_profile_yaml({"profile_yaml": refusal})
    assert result["errors"] == {"base": "profile_not_profile"}
    result = await flow.async_step_profile_yaml({"profile_yaml": OWN_TEXT.replace("  port: 502\n", "  port: 502\n  speed: 9600\n")})
    assert result["errors"] == {"base": "profile_unknown_key"}
    assert result["description_placeholders"]["where"] == "`connection` (Zeile 12)"
    assert result["description_placeholders"]["detail"] == "speed"
    result = await flow.async_step_profile_yaml({"profile_yaml": VENUS_TEXT})
    assert result["errors"] == {"base": "profile_bundled_id"}
    # The text stays in the field.
    assert flow._yaml == VENUS_TEXT


async def test_own_profile_pasted_with_the_text_of_the_answer(sim) -> None:
    flow = make_flow()
    await flow.async_step_profile({"profile": "own"})
    answer = f"I checked the manual first, here is the profile.\n\n```yaml\n{OWN_TEXT}```\n\nCompare the registers.\n"
    result = await flow.async_step_profile_yaml({"profile_yaml": answer})
    assert result["step_id"] == "profile_connection"
    # Only the YAML is kept.
    assert flow._yaml == OWN_TEXT


async def test_shipped_profile_pasted_unchanged_is_named(sim) -> None:
    flow = make_flow()
    await flow.async_step_profile({"profile": "own"})
    result = await flow.async_step_profile_yaml({"profile_yaml": f"```yaml\n{VENUS_TEXT}```"})
    assert result["errors"] == {"base": "profile_bundled_id"}
    assert result["description_placeholders"]["detail"] == "marstek_venus_e3"
    # A verified one has a key own profiles must not have, but is shipped anyway.
    result = await flow.async_step_profile_yaml({"profile_yaml": VENUS_TEXT + VERIFIED})
    assert result["errors"] == {"base": "profile_bundled_id"}
    assert result["description_placeholders"]["detail"] == "marstek_venus_e3"


async def test_own_profile_needs_the_manual_checked(sim) -> None:
    flow = make_flow()
    await flow.async_step_profile({"profile": "own"})
    result = await flow.async_step_profile_yaml({"profile_yaml": OWN_TEXT})
    assert result["step_id"] == "profile_connection"
    result = await flow.async_step_profile_connection(dict(ADDRESS))
    assert "profile_yaml" in result["menu_options"]
    await flow.async_step_profile_limits(dict(LIMITS))
    result = await flow.async_step_profile_preview({"checked": False})
    assert result["errors"] == {"base": "not_checked"}
    result = await flow.async_step_profile_preview({"checked": True})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["profile_id"] == "my_venus" and result["data"]["profile_yaml"] == OWN_TEXT


async def test_blocked_reading_cannot_go_on(sim) -> None:
    flow = make_flow()
    await flow.async_step_profile({"profile": "own"})
    await flow.async_step_profile_yaml(
        {"profile_yaml": OWN_TEXT.replace("{address: 34002, type: uint16, scale: 0.1,", "{address: 34002, type: uint16,")}
    )
    result = await flow.async_step_profile_connection(dict(ADDRESS))
    assert "profile_limits" not in result["menu_options"]


async def test_no_answer_and_address_in_use(sim, dev_profiles) -> None:
    flow = make_flow([subentry("v1", "Venus 1", model="marstek_venus_e3", host="venus", port=502, unit_id=1)])
    await flow.async_step_profile({"profile": "marstek_venus_e3"})
    result = await flow.async_step_profile_connection(dict(ADDRESS))
    assert result["errors"] == {"base": "address_in_use"}
    assert result["description_placeholders"]["battery"] == "Venus 1"
    sim.model.read = lambda *_: None
    result = await flow.async_step_profile_connection({**ADDRESS, "host": "other"})
    assert result["errors"] == {"base": "cannot_connect"}


async def test_copy_of_another_battery_s_profile(sim) -> None:
    flow = make_flow([subentry("p1", "Pack 1", model="modbus_profile", profile_id="my_venus", profile_yaml=OWN_TEXT)])
    result = await flow.async_step_profile()
    options = result["data_schema"].schema["profile"].config["options"]
    assert {"value": "copy_p1", "label": "Kopie von Pack 1: Marstek Venus E 3.0"} in options
    result = await flow.async_step_profile({"profile": "copy_p1"})
    assert result["step_id"] == "profile_yaml" and flow._yaml == OWN_TEXT


async def test_reconfigured_at_the_same_address_reads_over_the_running_battery(sim) -> None:
    data = {"model": "modbus_profile", "profile_id": "my_venus", "profile_yaml": OWN_TEXT, **ADDRESS, **LIMITS}
    running = SimLink(sim.model)
    await running.connect()
    driver = ProfileDriver(parse_profile(OWN_TEXT), running, capacity_wh=5120, max_charge_power_w=2500, max_discharge_power_w=2500)
    flow = make_flow([subentry("p1", "Pack", **data)], reconfigure="p1", batteries=[SimpleNamespace(subentry_id="p1", driver=driver)])
    result = await flow.async_step_profile_reconfigure()
    assert result["type"] is FlowResultType.MENU
    await flow.async_step_profile({"profile": "own"})
    result = await flow.async_step_profile_yaml({"profile_yaml": OWN_TEXT})
    result = await flow.async_step_profile_connection(dict(ADDRESS))
    assert result["step_id"] == "profile_read"
    # No connection of its own (the battery would refuse it), the running one stays open.
    assert not sim.links and running.reads() and running.connected


async def test_only_name_and_limits_read_nothing(sim) -> None:
    data = {"model": "modbus_profile", "profile_id": "marstek_venus_e3", **ADDRESS, **LIMITS}
    flow = make_flow([subentry("p1", "Pack", **data)], reconfigure="p1")
    flow.async_update_and_abort = lambda entry, sub, *, title, data: {"type": "abort", "title": title, "data": data}
    await flow.async_step_profile_reconfigure()
    result = await flow.async_step_profile_settings()
    assert result["step_id"] == "profile_limits"
    result = await flow.async_step_profile_limits({**LIMITS, "name": "Renamed", "capacity_wh": 4000})
    assert result["title"] == "Renamed" and result["data"]["capacity_wh"] == 4000
    assert result["data"]["host"] == "venus" and result["data"]["profile_id"] == "marstek_venus_e3"
    assert not sim.links


async def test_changed_profile_is_offered_to_the_batteries_with_the_same(sim) -> None:
    changed = OWN_TEXT.replace("revision: 1", "revision: 2")
    edited = OWN_TEXT.replace("notes: v3", "notes: edited v3").replace("keepalive_s: 60", "keepalive_s: 50")
    other = SimpleNamespace(**{**ADDRESS, "host": "venus-2"})
    batteries = [
        subentry("p1", "Pack 1", model="modbus_profile", profile_id="my_venus", profile_yaml=OWN_TEXT, **ADDRESS, **LIMITS),
        subentry("p2", "Pack 2", model="modbus_profile", profile_id="my_venus", profile_yaml=OWN_TEXT, **vars(other), **LIMITS),
        subentry("p3", "Pack 3", model="modbus_profile", profile_id="my_venus", profile_yaml=edited, **{**ADDRESS, "host": "venus-3"}, **LIMITS),
        subentry("v1", "Venus", model="modbus_profile", profile_id="marstek_venus_e3", **{**ADDRESS, "host": "venus-4"}, **LIMITS),
    ]
    flow = make_flow(batteries, reconfigure="p1")
    flow.async_update_and_abort = lambda entry, sub, *, title, data: {"type": "abort", "title": title, "data": data}
    await flow.async_step_profile_reconfigure()
    await flow.async_step_profile({"profile": "own"})
    await flow.async_step_profile_yaml({"profile_yaml": changed})
    await flow.async_step_profile_connection(dict(ADDRESS))
    await flow.async_step_profile_limits(dict(LIMITS))
    result = await flow.async_step_profile_preview({"checked": True})
    assert result["step_id"] == "profile_others"
    field = next(key for key in result["data_schema"].schema if str(key) == "batteries")
    # The unchanged copy is selected, the one changed since only offered.
    assert field.default() == ["p2"]
    labels = [option["label"] for option in result["data_schema"].schema[field].config["options"]]
    assert labels == ["Pack 2 (gleiches Profil)", "Pack 3 (inzwischen geändert)"]
    result = await flow.async_step_profile_others({"batteries": ["p2"]})
    assert result["data"]["profile_yaml"] == changed
    assert [(sub_id, data["profile_yaml"]) for sub_id, _, data in flow.hass.config_entries.updated] == [("p2", changed)]


async def test_other_battery_not_at_rest_is_left_out(sim) -> None:
    changed = OWN_TEXT.replace("revision: 1", "revision: 2")
    batteries = [
        subentry("p1", "Pack 1", model="modbus_profile", profile_id="my_venus", profile_yaml=OWN_TEXT, **ADDRESS, **LIMITS),
        subentry("p2", "Pack 2", model="modbus_profile", profile_id="my_venus", profile_yaml=OWN_TEXT, **{**ADDRESS, "host": "venus-2"}, **LIMITS),
    ]
    flow = make_flow(batteries, reconfigure="p1")
    await flow.async_step_profile_reconfigure()
    await flow.async_step_profile({"profile": "own"})
    await flow.async_step_profile_yaml({"profile_yaml": changed})
    await flow.async_step_profile_connection(dict(ADDRESS))
    await flow.async_step_profile_limits(dict(LIMITS))
    await flow.async_step_profile_preview({"checked": True})
    # Another system set the mode of the second battery.
    sim.model.write(42010, [5])
    result = await flow.async_step_profile_others({"batteries": ["p2"]})
    assert result["errors"] == {"base": "others_not_at_rest"}
    assert result["description_placeholders"]["batteries"] == "Pack 2"
    assert not flow.hass.config_entries.updated
