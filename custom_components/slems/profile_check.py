"""Checks of the setup of a battery from a Modbus profile (no HA dependency).

Before a profile is saved, the setup reads every register of it once (the
probe reading) and shows what SLEMS would write (the write preview), so a
wrong address or scale shows before anything is written (see the profile
concept, sections 5 and 6):

* Blocking: the probe register outside its range, a state of charge that
  cannot be read or lies outside 0–100 %, a write register that cannot be
  read or lies outside its range at rest. The profile cannot be saved.
* Marked: values that are possible but unlikely (temperature, cell voltages,
  powers far beyond the limits, negative counters or sides of a pair, an
  unknown state, a battery power against the state read) and values that
  cannot be read.

The texts are built here in the language of Home Assistant, as markdown lists
for the description of a step.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from .battery_profiles import (
    PLACEHOLDER_CHARGE,
    PLACEHOLDER_DISCHARGE,
    PLACEHOLDER_MODE,
    PLACEHOLDER_POWER,
    BatteryProfile,
    WriteStep,
    apply_values,
    release_values,
)
from .const import ReleaseState

BLOCK = "block"
WARN = "warn"

TEMPERATURE_RANGE_C = (-30.0, 80.0)
CELL_RANGE_V = (2.0, 4.5)
# A power beyond this many times the largest limit is marked.
POWER_FACTOR = 2.0
# A battery power at least this far against the state read (charge, discharge) is marked.
STATE_POWER_MIN_W = 50.0

# Values at rest from which a listed register shows them hexadecimal (key values).
HEX_FROM = 16

# Power read keys (shown with the sign of SLEMS: +charge, +input).
_POWERS = ("battery_power_w", "ac_power_w", "pv_power_w")
_COUNTERS = ("charged_kwh", "discharged_kwh", "cycles")


@dataclass
class ProbeReading:
    """Every register of a profile read once (values scaled, powers with the sign of SLEMS)."""

    probe: float | None
    # Read keys -> value (None: not read).
    values: dict[str, float | None] = field(default_factory=dict)
    # Read keys of a pair -> value of each side without a sign (None: not read).
    sides: dict[str, dict[str, float | None]] = field(default_factory=dict)
    # Raw value of the state register (None: not read or no state in the profile).
    state: int | None = None
    device_info: dict[str, str | None] = field(default_factory=dict)
    # Raw values of the write registers by address (None: not read).
    writes: dict[int, float | None] = field(default_factory=dict)


@dataclass(frozen=True)
class Mark:
    level: str
    reason: str


def check(profile: BatteryProfile, reading: ProbeReading) -> dict[str, Mark]:
    """Marks of the reading: read keys, ``probe``, ``state``, device info keys and
    ``write_<address>``."""
    marks: dict[str, Mark] = {}
    low, high = profile.probe.range
    if reading.probe is None or not low <= reading.probe <= high:
        marks["probe"] = Mark(BLOCK, "outside")
    limit = max(
        profile.limits.max_charge_w,
        profile.limits.max_discharge_w,
        (profile.own_pv.max_output_w or 0) if profile.own_pv else 0,
    )
    values = reading.values
    for key, value in values.items():
        if value is None:
            marks[key] = Mark(BLOCK if key == "soc_pct" else WARN, "unreadable")
        elif key == "soc_pct" and not 0 <= value <= 100:
            marks[key] = Mark(BLOCK, "unlikely")
        elif key == "temperature_c" and not TEMPERATURE_RANGE_C[0] <= value <= TEMPERATURE_RANGE_C[1]:
            marks[key] = Mark(WARN, "unlikely")
        elif key in ("max_cell_v", "min_cell_v") and not CELL_RANGE_V[0] <= value <= CELL_RANGE_V[1]:
            marks[key] = Mark(WARN, "unlikely")
        elif key in _POWERS and abs(value) > POWER_FACTOR * limit:
            marks[key] = Mark(WARN, "unlikely")
        elif (key == "pv_power_w" or key in _COUNTERS) and value < 0:
            marks[key] = Mark(WARN, "negative")
        elif any(side is not None and side < 0 for side in reading.sides.get(key, {}).values()):
            marks[key] = Mark(WARN, "negative")
    high_cell, low_cell = values.get("max_cell_v"), values.get("min_cell_v")
    if high_cell is not None and low_cell is not None and high_cell < low_cell and "max_cell_v" not in marks:
        marks["max_cell_v"] = Mark(WARN, "cells_swapped")
    if profile.state is not None:
        if reading.state is None:
            marks["state"] = Mark(WARN, "unreadable")
        elif reading.state not in profile.state.values:
            marks["state"] = Mark(WARN, "unknown_state")
        else:
            _check_state_power(profile, profile.state.values[reading.state], values, marks)
    for key, value in reading.device_info.items():
        if value is None:
            marks[key] = Mark(WARN, "unreadable")
    for step in profile.control.apply:
        raw = reading.writes.get(step.address)
        if raw is None:
            marks[f"write_{step.address}"] = Mark(BLOCK, "unreadable")
        elif not step.rests_at(raw):
            marks[f"write_{step.address}"] = Mark(BLOCK, "outside")
    return marks


def _check_state_power(
    profile: BatteryProfile, state: str, values: Mapping[str, float | None], marks: dict[str, Mark]
) -> None:
    """Marks the battery power (at the battery, else at the AC connection without own PV)
    if it points against a state of charging or discharging: mostly its sign is wrong."""
    keys = ("battery_power_w",) if profile.own_pv else ("battery_power_w", "ac_power_w")
    key = next((key for key in keys if values.get(key) is not None), None)
    if key is None or key in marks:
        return
    power = values[key]
    assert power is not None
    if (state == "charge" and power <= -STATE_POWER_MIN_W) or (state == "discharge" and power >= STATE_POWER_MIN_W):
        marks[key] = Mark(WARN, "against_state")


def blocked(marks: Mapping[str, Mark]) -> bool:
    return any(mark.level == BLOCK for mark in marks.values())


@dataclass(frozen=True)
class PreviewLine:
    step: WriteStep
    before: float | None
    after: float


def preview(
    profile: BatteryProfile, writes: Mapping[int, float | None], test_w: float, release_state: ReleaseState
) -> dict[str, list[PreviewLine]]:
    """What SLEMS would write for charging and then discharging with ``test_w`` and for
    the release after it, each with the value the register holds then (phase ->
    lines in the order of the profile)."""
    phases = {
        "charge": apply_values(profile, test_w, 0),
        "discharge": apply_values(profile, 0, test_w),
        "release": release_values(profile, release_state),
    }
    held = dict(writes)
    result: dict[str, list[PreviewLine]] = {}
    for phase, steps in phases.items():
        result[phase] = [PreviewLine(step, held.get(step.address), raw) for step, raw in steps]
        held.update((step.address, raw) for step, raw in steps)
    return result


# --- texts -------------------------------------------------------------------

_WORDS: dict[str, dict[str, str]] = {
    "en": {
        "soc_pct": "State of charge",
        "battery_power_w": "Battery power (+ charge)",
        "ac_power_w": "AC power (+ input)",
        "pv_power_w": "PV power",
        "side_charge": "charge",
        "side_discharge": "discharge",
        "side_input": "input",
        "side_output": "output",
        "charged_kwh": "Charged",
        "discharged_kwh": "Discharged",
        "voltage_v": "Voltage",
        "temperature_c": "Temperature",
        "max_cell_v": "Highest cell",
        "min_cell_v": "Lowest cell",
        "cycles": "Cycles",
        "state": "State",
        "probe": "Probe register",
        "device": "Device",
        "values": "Values",
        "writes": "Write registers (at rest)",
        "unreadable": "not readable",
        "unlikely": "unlikely",
        "negative": "negative",
        "cells_swapped": "lower than the lowest cell",
        "unknown_state": "unknown value",
        "against_state": "against the state (sign?)",
        "outside": "outside",
        "allowed": "allowed",
        "blocked": "**Cannot be saved:** fix the values marked ⛔ (profile or connection).",
        "ok": "Nothing blocks saving. Compare the values with the battery's app or display.",
        "charge": "Charging with {power} W",
        "discharge": "Discharging with {power} W",
        "release": "Release ({state})",
        "auto": "Automatic",
        "standby": "Standby",
        PLACEHOLDER_CHARGE: "charge power",
        PLACEHOLDER_DISCHARGE: "discharge power",
        PLACEHOLDER_POWER: "power",
        PLACEHOLDER_MODE: "mode",
        "fixed": "fixed value",
        "unchanged": "unchanged",
        "confirm": "read back",
        "persistent": "flash",
        "not_read": "?",
    },
    "de": {
        "soc_pct": "Ladezustand",
        "battery_power_w": "Batterieleistung (+ Laden)",
        "ac_power_w": "AC-Leistung (+ Eingang)",
        "pv_power_w": "PV-Leistung",
        "side_charge": "Laden",
        "side_discharge": "Entladen",
        "side_input": "Eingang",
        "side_output": "Ausgang",
        "charged_kwh": "Geladen",
        "discharged_kwh": "Entladen",
        "voltage_v": "Spannung",
        "temperature_c": "Temperatur",
        "max_cell_v": "Höchste Zelle",
        "min_cell_v": "Niedrigste Zelle",
        "cycles": "Zyklen",
        "state": "Zustand",
        "probe": "Probe-Register",
        "device": "Gerät",
        "values": "Werte",
        "writes": "Schreibregister (in Ruhe)",
        "unreadable": "nicht lesbar",
        "unlikely": "unplausibel",
        "negative": "negativ",
        "cells_swapped": "niedriger als die niedrigste Zelle",
        "unknown_state": "unbekannter Wert",
        "against_state": "widerspricht dem Zustand (Vorzeichen?)",
        "outside": "außerhalb",
        "allowed": "erlaubt",
        "blocked": "**Nicht speicherbar:** Korrigiere die mit ⛔ markierten Werte (Profil oder Verbindung).",
        "ok": "Nichts verhindert das Speichern. Vergleiche die Werte mit der App oder der Anzeige der Batterie.",
        "charge": "Laden mit {power} W",
        "discharge": "Entladen mit {power} W",
        "release": "Freigabe ({state})",
        "auto": "Automatik",
        "standby": "Standby",
        PLACEHOLDER_CHARGE: "Ladeleistung",
        PLACEHOLDER_DISCHARGE: "Entladeleistung",
        PLACEHOLDER_POWER: "Leistung",
        PLACEHOLDER_MODE: "Modus",
        "fixed": "fester Wert",
        "unchanged": "unverändert",
        "confirm": "rückgelesen",
        "persistent": "Flash",
        "not_read": "?",
    },
}

_UNITS = {
    "soc_pct": "%", "battery_power_w": "W", "ac_power_w": "W", "pv_power_w": "W",
    "charged_kwh": "kWh", "discharged_kwh": "kWh", "voltage_v": "V",
    "temperature_c": "°C", "max_cell_v": "V", "min_cell_v": "V", "cycles": "",
}


def _words(language: str) -> dict[str, str]:
    return _WORDS["de" if language.startswith("de") else "en"]


def _number(language: str, value: float) -> str:
    text = str(int(value)) if float(value).is_integer() else f"{value:g}"
    # A real minus sign, and the decimal comma in German.
    text = text.replace("-", "−")
    return text.replace(".", ",") if language.startswith("de") else text


def _raw(step: WriteStep, value: float | None, language: str) -> str:
    """A raw value; hexadecimal for registers with key values at rest (e.g. 0x55BB,
    0xAA), decimal for small listed values like modes 0, 1, 2."""
    if value is None:
        return "?"
    if step.at_rest_values and max(step.at_rest_values) >= HEX_FROM and float(value).is_integer() and value >= 0:
        return f"0x{int(value):04X}"
    return _number(language, value)


def _mark(mark: Mark | None, words: dict[str, str]) -> str:
    if mark is None:
        return ""
    return f" {'⛔' if mark.level == BLOCK else '⚠'} {words[mark.reason]}"


def _step_name(profile: BatteryProfile, step: WriteStep, words: dict[str, str]) -> str:
    applied = profile.control.step(step.address) or step
    value = applied.value
    return words[value] if isinstance(value, str) and value in words else words["fixed"]


def _allowed(step: WriteStep, language: str) -> str:
    if step.at_rest_values is not None:
        return ", ".join(_raw(step, value, language) for value in step.at_rest_values)
    if step.at_rest is not None:
        return f"{_number(language, step.at_rest[0])}–{_number(language, step.at_rest[1])}"
    return "–"


def describe_reading(profile: BatteryProfile, reading: ProbeReading, marks: Mapping[str, Mark], language: str) -> str:
    """The probe reading as markdown: verdict, device information, values, write registers."""
    words = _words(language)
    lines = [words["blocked"] if blocked(marks) else words["ok"], ""]
    if reading.device_info:
        lines.append(f"**{words['device']}**")
        lines += [
            f"- {key}: {value if value is not None else '?'}{_mark(marks.get(key), words)}"
            for key, value in reading.device_info.items()
        ]
        lines.append("")
    lines.append(f"**{words['values']}**")
    probe = profile.probe
    lines.append(
        f"- {words['probe']} {probe.register.address}: "
        f"{'?' if reading.probe is None else _number(language, reading.probe)} "
        f"({_number(language, probe.range[0])}–{_number(language, probe.range[1])}){_mark(marks.get('probe'), words)}"
    )
    for key, value in reading.values.items():
        shown = "?" if value is None else f"{_number(language, value)} {_UNITS[key]}".rstrip()
        if sides := reading.sides.get(key):
            # Each register of a pair, so a swapped pair shows.
            shown += " (" + ", ".join(
                f"{words['side_' + side]} {'?' if part is None else _number(language, part)} {_UNITS[key]}"
                for side, part in sides.items()
            ) + ")"
        lines.append(f"- {words[key]}: {shown}{_mark(marks.get(key), words)}")
    if profile.state is not None:
        name = profile.state.values.get(reading.state) if reading.state is not None else None  # type: ignore[call-overload]
        shown = "?" if reading.state is None else f"{reading.state}" + (f" ({name})" if name else "")
        lines.append(f"- {words['state']}: {shown}{_mark(marks.get('state'), words)}")
    lines += ["", f"**{words['writes']}**"]
    for step in profile.control.apply:
        lines.append(
            f"- {step.address} {_step_name(profile, step, words)}: {_raw(step, reading.writes.get(step.address), language)} "
            f"({words['allowed']} {_allowed(step, language)}){_mark(marks.get(f'write_{step.address}'), words)}"
        )
    return "\n".join(lines)


def describe_preview(
    profile: BatteryProfile, phases: Mapping[str, list[PreviewLine]], test_w: float, release_state: ReleaseState,
    language: str,
) -> str:
    """The write preview as markdown, one list per phase."""
    words = _words(language)
    titles = {
        "charge": words["charge"].format(power=_number(language, test_w)),
        "discharge": words["discharge"].format(power=_number(language, test_w)),
        "release": words["release"].format(state=words[release_state.value]),
    }
    lines: list[str] = []
    for phase, preview_lines in phases.items():
        lines.append(f"**{titles[phase]}**")
        for line in preview_lines:
            notes = [words[key] for key, on in (("confirm", line.step.confirm), ("persistent", line.step.persistent)) if on]
            if line.before == line.after:
                change = f"{_raw(line.step, line.after, language)} ({words['unchanged']})"
            else:
                change = f"{_raw(line.step, line.before, language)} → {_raw(line.step, line.after, language)}"
            suffix = f" · {', '.join(notes)}" if notes else ""
            lines.append(f"- {line.step.address} {_step_name(profile, line.step, words)}: {change}{suffix}")
        lines.append("")
    return "\n".join(lines).rstrip()
