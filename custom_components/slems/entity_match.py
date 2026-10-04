"""Suggest the entities of a battery device for each role SLEMS needs.

Pure functions over a simplified view of the device's entities; the config
flow shows the result for confirmation. Names are matched in English and
German on the entity id, the translation key and the original name.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
import re

from .const import (
    CONF_BATTERY_TEMPERATURE_ENTITY,
    CONF_CHARGE_ENTITY,
    CONF_CHARGED_ENERGY_ENTITY,
    CONF_DISCHARGE_ENTITY,
    CONF_DISCHARGED_ENERGY_ENTITY,
    CONF_MAX_CELL_VOLTAGE_ENTITY,
    CONF_MIN_CELL_VOLTAGE_ENTITY,
    CONF_MODE_AUTO,
    CONF_MODE_CHARGE,
    CONF_MODE_DISCHARGE,
    CONF_MODE_ENTITY,
    CONF_MODE_STANDBY,
    CONF_POWER_ENTITY,
    CONF_REMOTE_ENTITY,
    CONF_REMOTE_OFF,
    CONF_REMOTE_ON,
    CONF_SETPOINT_ENTITY,
    CONF_SOC_ENTITY,
    BatteryControl,
)

# Role of the capacity sensor; only used to fill in the capacity.
CAPACITY = "capacity"

_POWER_UNITS = ("W", "kW")
_ENERGY_UNITS = ("Wh", "kWh", "MWh")
# Integrations whose AC side battery power has a name the general rules would
# take for the house grid: platform -> fragments of that entity.
_AC_POWER_OF_PLATFORM = {
    # Marstek local API: "Grid power" (ongrid_power) is the AC power of the
    # battery, "Power" (bat_power) its DC side.
    "marstek_local_api": ("grid_power", "grid power"),
}


@dataclass(frozen=True)
class EntityInfo:
    """What the matcher knows about one entity of the device."""

    entity_id: str
    device_class: str | None = None
    unit: str | None = None
    state_class: str | None = None
    # Entity id, translation key and original name, lower case.
    text: str = ""
    options: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    state: str | None = None
    words: frozenset[str] = field(default=frozenset(), compare=False)
    # Integration of the entity (entity registry platform).
    platform: str | None = None

    @classmethod
    def create(cls, entity_id: str, *names: str | None, **values) -> EntityInfo:
        text = " ".join([entity_id, *(name for name in names if name)]).lower()
        return cls(
            entity_id=entity_id,
            text=text,
            words=frozenset(re.split(r"[^a-z0-9äöü]+", text)),
            **values,
        )

    @property
    def has_value(self) -> bool:
        """Whether the entity reports a number right now."""
        try:
            float(self.state)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return False
        return True

    @property
    def domain(self) -> str:
        return self.entity_id.split(".", 1)[0]

    def has(self, *fragments: str) -> bool:
        return any(fragment in self.text for fragment in fragments)

    def word(self, *words: str) -> bool:
        return any(word in self.words for word in words)


def _charging(info: EntityInfo) -> bool:
    text = info.text.replace("discharg", "").replace("entlad", "")
    return "charg" in text or "lade" in text or "laden" in text


def _discharging(info: EntityInfo) -> bool:
    return info.has("discharg", "entlad")


def _soc(info: EntityInfo) -> float:
    if info.domain != "sensor" or info.unit != "%":
        return 0
    score = 2 if info.device_class == "battery" else 0
    if info.has("soc", "state_of_charge", "ladezustand", "battery_level", "battery level"):
        score += 2
    if info.has("target", "limit", "cutoff", "reserve", "backup") or info.word("min", "max"):
        score -= 3
    return score


def _power(info: EntityInfo) -> float:
    if info.domain != "sensor" or info.unit not in _POWER_UNITS:
        return 0
    score = 1 if info.device_class == "power" else 0
    ac_fragments = _AC_POWER_OF_PLATFORM.get(info.platform or "")
    if ac_fragments and info.has(*ac_fragments) and not info.has("off-grid", "offgrid", "off_grid"):
        # The AC side: what the smart meter sees of the battery.
        score += 4
    elif info.has("pv", "solar", "grid", "netz", "house", "home", "haus", "load", "last", "limit", "max"):
        score -= 2
    if info.has("batter", "akku", "pack"):
        score += 2
    if info.word("ac"):
        score += 1
    if info.state is not None and not info.has_value:
        # Rather one that reports values.
        score -= 1
    return score


def _number_w(info: EntityInfo) -> bool:
    return info.domain in ("number", "input_number") and info.unit in _POWER_UNITS


def _setpoint(info: EntityInfo) -> float:
    if not _number_w(info):
        return 0
    score = 3 if info.minimum is not None and info.minimum < 0 else 0
    if info.has("setpoint", "set_point", "target", "soll", "power", "leistung"):
        score += 1
    if _charging(info) or _discharging(info) or info.has("limit"):
        score -= 1
    return score


def _charge(info: EntityInfo) -> float:
    if not _number_w(info) or not _charging(info):
        return 0
    return 2 - (1 if info.has("limit", "max") else 0)


def _discharge(info: EntityInfo) -> float:
    if not _number_w(info) or not _discharging(info):
        return 0
    return 2 - (1 if info.has("limit", "max") else 0)


def _mode(info: EntityInfo) -> float:
    if info.domain not in ("select", "input_select"):
        return 0
    mapped = suggest_mode_options(info.options)
    score = len(mapped)
    if info.has("mode", "modus", "force"):
        score += 1
    return score if CONF_MODE_CHARGE in mapped or CONF_MODE_DISCHARGE in mapped else 0


def _remote(info: EntityInfo) -> float:
    if info.domain not in ("switch", "input_boolean", "select", "input_select"):
        return 0
    score = 0
    if info.has("remote", "rs485", "fernsteuer", "modbus_control", "manual"):
        score += 2
    if info.domain in ("select", "input_select") and not suggest_remote_options(info.options):
        return 0
    return score


def _capacity(info: EntityInfo) -> float:
    if info.domain != "sensor" or info.unit not in _ENERGY_UNITS:
        return 0
    score = 2 if info.device_class == "energy_storage" else 0
    if info.has("capacity", "kapazit"):
        score += 2
    if info.has("remaining", "rest", "total", "today", "heute"):
        score -= 2
    return score


def _temperature(info: EntityInfo) -> float:
    if info.domain != "sensor" or info.device_class != "temperature":
        return 0
    score = 1
    if info.has("batter", "akku", "cell", "zelle", "internal", "intern", "bms"):
        score += 1
    if info.has("ambient", "outdoor", "außen", "mos", "inverter", "wechselrichter"):
        score -= 1
    return score


def _cell_voltage(high: bool) -> Callable[[EntityInfo], float]:
    def score(info: EntityInfo) -> float:
        if info.domain != "sensor" or info.device_class != "voltage":
            return 0
        if not info.has("cell", "zell"):
            return 0
        if high:
            return 2 if info.word("max", "highest", "höchste") or info.has("max_cell") else 0
        return 2 if info.word("min", "lowest", "niedrigste") or info.has("min_cell") else 0

    return score


def _counter(charged: bool) -> Callable[[EntityInfo], float]:
    def score(info: EntityInfo) -> float:
        if info.domain != "sensor" or info.unit not in _ENERGY_UNITS:
            return 0
        if info.state_class != "total_increasing" and info.device_class != "energy":
            return 0
        direction = _charging(info) if charged else _discharging(info)
        if not direction:
            return 0
        return 2 + (1 if info.has("total", "gesamt", "lifetime") else 0)

    return score


# Most specific roles first: an entity is used for one role only.
_ROLES: tuple[tuple[str, Callable[[EntityInfo], float]], ...] = (
    (CONF_SOC_ENTITY, _soc),
    (CONF_MAX_CELL_VOLTAGE_ENTITY, _cell_voltage(True)),
    (CONF_MIN_CELL_VOLTAGE_ENTITY, _cell_voltage(False)),
    (CONF_CHARGED_ENERGY_ENTITY, _counter(True)),
    (CONF_DISCHARGED_ENERGY_ENTITY, _counter(False)),
    (CAPACITY, _capacity),
    (CONF_SETPOINT_ENTITY, _setpoint),
    (CONF_CHARGE_ENTITY, _charge),
    (CONF_DISCHARGE_ENTITY, _discharge),
    (CONF_MODE_ENTITY, _mode),
    (CONF_REMOTE_ENTITY, _remote),
    (CONF_POWER_ENTITY, _power),
    (CONF_BATTERY_TEMPERATURE_ENTITY, _temperature),
)


def match_battery_entities(entities: Iterable[EntityInfo]) -> dict[str, str]:
    """Best entity per role (subentry key -> entity id); roles without a match are left out."""
    candidates = list(entities)
    used: set[str] = set()
    result: dict[str, str] = {}
    for role, score in _ROLES:
        best: tuple[float, str] | None = None
        for info in candidates:
            if info.entity_id in used:
                continue
            value = score(info)
            if value > 0 and (best is None or value > best[0]):
                best = (value, info.entity_id)
        if best is not None:
            result[role] = best[1]
            used.add(best[1])
    # A signed set point makes separate charge/discharge numbers unlikely and vice versa.
    if CONF_CHARGE_ENTITY in result and CONF_DISCHARGE_ENTITY in result:
        by_id = {info.entity_id: info for info in candidates}
        setpoint = result.get(CONF_SETPOINT_ENTITY)
        if setpoint and not ((by_id[setpoint].minimum or 0) < 0):
            del result[CONF_SETPOINT_ENTITY]
    return result


def suggest_control(matches: Mapping[str, str]) -> BatteryControl:
    """Control type that fits the matched entities."""
    if CONF_CHARGE_ENTITY in matches and CONF_DISCHARGE_ENTITY in matches:
        return BatteryControl.SPLIT
    if CONF_SETPOINT_ENTITY in matches:
        return BatteryControl.SETPOINT
    return BatteryControl.NONE


def _pick(options: Iterable[str], test: Callable[[str, frozenset[str]], bool]) -> str | None:
    for option in options:
        text = option.lower()
        if test(text, frozenset(re.split(r"[^a-z0-9äöü]+", text))):
            return option
    return None


def suggest_mode_options(options: Iterable[str]) -> dict[str, str]:
    """Options of a mode select for charge / discharge / standby / automatic."""
    options = list(options)

    def charge(text: str, _words: frozenset[str]) -> bool:
        rest = text.replace("discharg", "").replace("entlad", "")
        return "charg" in rest or "lad" in rest

    def discharge(text: str, _words: frozenset[str]) -> bool:
        return "discharg" in text or "entlad" in text

    def standby(text: str, words: frozenset[str]) -> bool:
        return bool(words & {"standby", "stop", "idle", "none", "off", "pause", "ruhe", "halt", "aus"})

    def auto(text: str, words: frozenset[str]) -> bool:
        return "auto" in text or "self" in text or "eigen" in text or bool(
            words & {"normal", "default", "anti", "zero"}
        )

    result: dict[str, str] = {}
    for key, test in (
        (CONF_MODE_DISCHARGE, discharge),
        (CONF_MODE_CHARGE, charge),
        (CONF_MODE_STANDBY, standby),
        (CONF_MODE_AUTO, auto),
    ):
        option = _pick((o for o in options if o not in result.values()), test)
        if option is not None:
            result[key] = option
    return result


def suggest_remote_options(options: Iterable[str]) -> dict[str, str]:
    """Options of a remote control select for on and off."""
    options = list(options)
    off = _pick(
        options,
        lambda text, words: "disabl" in text or "deaktiv" in text or bool(words & {"off", "aus", "auto"}),
    )
    on = _pick(
        (o for o in options if o != off),
        lambda text, words: "enabl" in text or "aktiv" in text or "remote" in text
        or bool(words & {"on", "an", "ein", "manual", "rs485"}),
    )
    result: dict[str, str] = {}
    if on is not None:
        result[CONF_REMOTE_ON] = on
    if off is not None:
        result[CONF_REMOTE_OFF] = off
    return result if len(result) == 2 else {}
