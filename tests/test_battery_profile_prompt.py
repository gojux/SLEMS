"""The AI prompts for battery profiles (docs/battery-profile-prompt*.md): their
example loads as an own profile, is the same in both languages, and the prompt
names every key of the format."""

from pathlib import Path
import re

import pytest

from custom_components.slems.battery_profiles import (
    FORMAT_KEY,
    PLACEHOLDERS,
    READ_KEYS,
    REGISTER_TYPES,
    STATES,
    bundled_profiles,
    parse_profile,
)

DOCS = Path(__file__).parent.parent / "docs"
PROMPTS = ("battery-profile-prompt.md", "battery-profile-prompt.de.md")
# Keys of the format besides the read keys (see battery_profiles._Parser).
KEYS = (
    FORMAT_KEY, "id", "name", "manufacturer", "revision", "notes", "connection", "limits", "timing", "own_pv",
    "read", "device_info", "control", "probe",
    "port", "unit_id", "message_spacing_s", "timeout_s",
    "max_charge_w", "max_discharge_w", "min_power_w",
    "start_delay_s", "start_extra_s", "reversal_extra_s", "min_command_interval_s",
    "max_output_w", "ac_charge", "passes_pv_through",
    "address", "type", "scale", "word_order", "input", "count", "positive", "fallback", "values", "range",
    "setpoint", "keepalive_s", "confirm", "apply", "release", "delay_s", "tolerance_w", "tolerance_pct",
    "value", "at_rest", "at_rest_values", "modes", "persistent", "max_changes_per_day", "auto", "standby",
)


def prompt(name: str) -> str:
    text = (DOCS / name).read_text(encoding="utf-8")
    return text.split("````text", 1)[1].split("````", 1)[0]


def example(name: str) -> str:
    (block,) = re.findall(r"```yaml\n(.*?)```", prompt(name), re.S)
    return block


@pytest.mark.parametrize("name", PROMPTS)
def test_example_loads_as_an_own_profile(name: str) -> None:
    profile = parse_profile(example(name))
    assert profile.id not in bundled_profiles()
    assert all(step.at_rest or step.at_rest_values for step in profile.control.apply)
    assert profile.device_info


def test_example_is_the_same_in_both_languages() -> None:
    hashes = {parse_profile(example(name)).content_hash for name in PROMPTS}
    assert len(hashes) == 1


@pytest.mark.parametrize("name", PROMPTS)
def test_prompt_names_every_key_of_the_format(name: str) -> None:
    text = prompt(name)
    format_part = text.split("```yaml", 1)[0]
    for key in (*KEYS, *READ_KEYS):
        assert re.search(rf"\b{key}\b", format_part), key
    for word in (*STATES, *PLACEHOLDERS, *REGISTER_TYPES):
        assert word in format_part, word
