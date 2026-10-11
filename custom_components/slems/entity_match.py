"""Suggest the entities of a battery, consumer, smart meter or PV device for each role SLEMS needs.

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
    CONF_BLOCK_ENTITY,
    CONF_CHARGE_ENTITY,
    CONF_CHARGED_ENERGY_ENTITY,
    CONF_CONTROL_ENTITY,
    CONF_DISCHARGE_ENTITY,
    CONF_DISCHARGED_ENERGY_ENTITY,
    CONF_ENERGY_ENTITY,
    CONF_GRID_EXPORT_ENERGY_ENTITY,
    CONF_GRID_IMPORT_ENERGY_ENTITY,
    CONF_GRID_POWER_ENTITY,
    CONF_MAX_CELL_VOLTAGE_ENTITY,
    CONF_MAX_CURRENT_A,
    CONF_MAX_POWER_W,
    CONF_MIN_CELL_VOLTAGE_ENTITY,
    CONF_MIN_CURRENT_A,
    CONF_MIN_POWER_W,
    CONF_MODE_AUTO,
    CONF_MODE_CHARGE,
    CONF_MODE_DISCHARGE,
    CONF_MODE_ENTITY,
    CONF_MODE_STANDBY,
    CONF_PHASES_ENTITY,
    CONF_POWER_ENTITY,
    CONF_PV_POWER_ENTITY,
    CONF_REMOTE_ENTITY,
    CONF_REMOTE_OFF,
    CONF_REMOTE_ON,
    CONF_SETPOINT_ENTITY,
    CONF_SOC_ENTITY,
    CONF_START_ENTITY,
    CONF_START_OFF,
    CONF_START_ON,
    CONF_TEMPERATURE_2_ENTITY,
    CONF_TEMPERATURE_ENTITY,
    BatteryControl,
    ConsumerType,
    ControlMode,
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


def _best_per_role(
    candidates: list[EntityInfo], roles: Iterable[tuple[str, Callable[[EntityInfo], float]]]
) -> dict[str, str]:
    """Best entity per role in the order of ``roles``; an entity is used for one role only."""
    used: set[str] = set()
    result: dict[str, str] = {}
    for role, score in roles:
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
    return result


def match_battery_entities(entities: Iterable[EntityInfo]) -> dict[str, str]:
    """Best entity per role (subentry key -> entity id); roles without a match are left out."""
    candidates = list(entities)
    result = _best_per_role(candidates, _ROLES)
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


# Integrations (entity registry platform) whose devices are this kind of consumer.
CONSUMER_PLATFORMS: Mapping[ConsumerType, frozenset[str]] = {
    ConsumerType.HEAT_PUMP: frozenset(
        {
            "aquarea",
            "daikin_onecta",
            "ems_esp",
            "hoval_unofficial",
            "idm_heatpump",
            "lambda_heat_pumps",
            "luxtronik",
            "luxtronik2",
            "melcloud",
            "mypyllant",
            "nibe_heatpump",
            "stiebel_eltron",
            "stiebel_eltron_isg",
            "vicare",
            "wolflink",
        }
    ),
    ConsumerType.HEATING_ROD: frozenset({"askoheat", "my_pv", "mypv", "mypv_elwa2_modbus"}),
    ConsumerType.WALLBOX: frozenset(
        {
            "alfen_wallbox",
            "easee",
            "evcc_intg",
            "fronius_wattpilot",
            "goecharger",
            "goecharger_api2",
            "heidelberg_energy_control",
            "keba",
            "ocpp",
            "openwb",
            "openwbmqtt",
            "tesla_wall_connector",
            "wallbox",
            "wattpilot",
            "zaptec",
        }
    ),
}

# Parts of an operating mode's state or name that mean hot water.
HOT_WATER_WORDS = (
    "hot_water", "hotwater", "dhw", "domestic", "warmwasser", "brauchwasser", "sanitary", "tap_water",
    "water_heating", "eau_chaude",
)
# Heat output, heat quantities and cooling of a heat pump next to its electrical values.
_THERMAL = (
    "output", "thermal", "heat_quantity", "heat quantity", "wärmemenge", "heizleistung", "cooling", "kühl",
    "produced", "erzeugt",
)
_ELECTRIC = ("electric", "elektr", "input", "aufnahme", "consumption", "verbrauch")
_STORAGE = (
    "dhw", "warmwasser", "hot_water", "hot water", "brauchwasser", "boiler", "speicher", "tank", "storage",
    "cylinder",
)
_NOT_STORAGE = (
    "outdoor", "outside", "außen", "aussen", "flow", "vorlauf", "return", "rücklauf", "target", "soll",
    "setpoint", "room", "raum", "pcb", "stage", "internal", "intern", "inverter", "compressor", "verdichter",
    "buffer", "puffer", "heat_generator", "evaporator", "condenser", "suction", "exhaust", "ambient",
    "device", "gerät", "chip", "board",
)


def means_hot_water(text: str) -> bool:
    """Whether a state, option or name means hot water."""
    text = text.lower().replace("-", "_").replace(" ", "_")
    return any(word in text for word in HOT_WATER_WORDS)


def _consumer_power(info: EntityInfo) -> float:
    if info.domain != "sensor" or info.unit not in _POWER_UNITS:
        return 0
    score = 2 if info.device_class == "power" else 1
    if info.has(*_ELECTRIC):
        score += 2
    if info.has(*_THERMAL):
        score -= 2
    if info.has("max", "limit", "available", "configured", "verfügbar", "target", "reserve") or info.word("pv"):
        score -= 2
    if info.state is not None and not info.has_value:
        score -= 1
    return score


def _consumer_energy(info: EntityInfo) -> float:
    if info.domain != "sensor" or info.unit not in _ENERGY_UNITS or info.device_class == "energy_storage":
        return 0
    if info.state_class not in ("total", "total_increasing") and info.device_class != "energy":
        return 0
    score = 2
    if info.has(*_ELECTRIC):
        score += 2
    if info.has(*_THERMAL) or info.has("export", "returned", "einspeis"):
        score -= 2
    if info.has("total", "gesamt", "lifetime"):
        score += 1
    if info.has("today", "heute", "daily", "week", "woche", "month", "monat", "year", "jahr", "session"):
        score -= 1
    return score


def _power_setpoint(info: EntityInfo) -> float:
    if not _number_w(info):
        return 0
    score = 2
    if info.has("power", "leistung", "setpoint", "soll"):
        score += 1
    if info.has("max", "limit", "auto", "reserve", "rate") or info.word("min"):
        score -= 2
    return score


def _numbers(options: Iterable[str]) -> list[float]:
    values = []
    for option in options:
        try:
            values.append(float(option))
        except ValueError:
            continue
    return values


def _current_setpoint(info: EntityInfo) -> float:
    if info.domain in ("number", "input_number") and info.unit == "A":
        score = 2
    elif info.domain in ("select", "input_select") and len(_numbers(info.options)) >= 2:
        score = 2
    else:
        return 0
    if info.has("current", "strom", "ampere", "charg", "lade"):
        score += 1
    if info.word("min") or info.has("min_current", "mincurrent"):
        score -= 2
    return score


def _switch(info: EntityInfo) -> float:
    if info.domain not in ("switch", "input_boolean"):
        return 0
    score = 1
    if info.has("power", "relay", "relais", "heat", "heiz", "enable", "output", "switch"):
        score += 1
    if info.has("auto", "light", "licht", "boost", "legionell", "update", "notif") or info.word(
        "child", "lock", "led", "eco"
    ):
        score -= 3
    return score


def _start(info: EntityInfo) -> float:
    if info.domain in ("select", "input_select"):
        return 3 if suggest_start_options(info.options) else 0
    if info.domain in ("switch", "input_boolean"):
        return 1 if info.has("enable", "charg", "lade", "start", "allow") else 0
    return 0


def _phases(info: EntityInfo) -> float:
    if info.domain not in ("sensor", "number", "input_number") or info.unit not in (None, ""):
        return 0
    if info.has("phases_active", "active_phases", "phasesactive", "phases active", "aktive phasen"):
        return 2
    return 1 if info.has("phase") else 0


def _hot_water_mode(info: EntityInfo) -> float:
    if info.domain not in ("sensor", "select", "input_select"):
        return 0
    if not any(means_hot_water(option) for option in info.options):
        return 0
    # A sensor shows what the heat pump does, a select is rather a setting.
    score = 3 if info.domain == "sensor" else 2
    if info.has("status", "state", "mode", "modus", "betrieb"):
        score += 1
    return score


def _storage_temperature(heat_pump: bool, second: bool) -> Callable[[EntityInfo], float]:
    """A heat pump's hot water storage needs a hot water name; at a heating rod any storage sensor fits."""

    def score(info: EntityInfo) -> float:
        if info.domain == "water_heater":
            return 0 if second else (4 if heat_pump else 1)
        if info.domain != "sensor" or info.device_class != "temperature" or info.has(*_NOT_STORAGE):
            return 0
        if info.has(*_STORAGE):
            return 3
        if heat_pump:
            return 0
        # The heating rod's first sensor before one named as second or upper.
        upper = info.entity_id.endswith(("_2", "_top", "_oben", "_upper")) or info.has(
            "temperature_2", "temperature 2", "temperatur 2", "top", "oben", "upper"
        )
        return 2 + (0.5 if upper == second else 0)

    return score


def _block(heating_rod: bool) -> Callable[[EntityInfo], float]:
    def score(info: EntityInfo) -> float:
        if info.domain == "water_heater":
            # Off when the heating rod is disabled at the device.
            return 2 if heating_rod else 0
        if info.domain not in ("binary_sensor", "input_boolean", "switch"):
            return 0
        return 2 if info.has("evu", "sperr", "utility_lock", "utility lock", "blocked", "blockiert", "lockout") else 0

    return score


_CONTROL_SCORE: Mapping[ControlMode, Callable[[EntityInfo], float]] = {
    ControlMode.POWER: _power_setpoint,
    ControlMode.CURRENT: _current_setpoint,
    ControlMode.SWITCH: _switch,
}


def match_consumer_entities(
    entities: Iterable[EntityInfo],
    consumer_type: ConsumerType,
    control_mode: ControlMode,
    *,
    hot_water: bool = False,
) -> dict[str, str]:
    """Best entity per role of a consumer (subentry key -> entity id).

    ``hot_water``: a heat pump that also heats a hot water storage.
    """
    heat_pump = consumer_type is ConsumerType.HEAT_PUMP
    heating_rod = consumer_type is ConsumerType.HEATING_ROD
    roles: list[tuple[str, Callable[[EntityInfo], float]]] = []
    if heat_pump and hot_water and control_mode is ControlMode.RELEASE:
        roles.append((CONF_MODE_ENTITY, _hot_water_mode))
    if control := _CONTROL_SCORE.get(control_mode):
        roles.append((CONF_CONTROL_ENTITY, control))
    if control_mode is ControlMode.CURRENT:
        roles += [(CONF_START_ENTITY, _start), (CONF_PHASES_ENTITY, _phases)]
    if heating_rod or (heat_pump and hot_water):
        roles += [
            (CONF_TEMPERATURE_ENTITY, _storage_temperature(heat_pump, second=False)),
            (CONF_TEMPERATURE_2_ENTITY, _storage_temperature(heat_pump, second=True)),
        ]
    if control_mode is not ControlMode.NONE:
        roles.append((CONF_BLOCK_ENTITY, _block(heating_rod)))
    roles += [(CONF_POWER_ENTITY, _consumer_power), (CONF_ENERGY_ENTITY, _consumer_energy)]
    return _best_per_role(list(entities), roles)


def suggest_hot_water(entities: Iterable[EntityInfo]) -> bool:
    """Whether a heat pump device looks like it heats hot water (an operating mode or storage of it)."""
    return any(
        _hot_water_mode(info) > 0 or _storage_temperature(True, second=False)(info) > 0 for info in entities
    )


def suggest_control_mode(
    entities: Iterable[EntityInfo], consumer_type: ConsumerType, *, hot_water: bool = False
) -> ControlMode:
    """Control mode that fits the entities of a consumer's device."""
    entities = list(entities)

    def found(mode: ControlMode, key: str = CONF_CONTROL_ENTITY) -> bool:
        return key in match_consumer_entities(entities, consumer_type, mode, hot_water=hot_water)

    if consumer_type is ConsumerType.HEAT_PUMP:
        # Released for its hot water charge; the heating stays its own.
        if hot_water and found(ControlMode.RELEASE, CONF_MODE_ENTITY):
            return ControlMode.RELEASE
        return ControlMode.NONE
    modes = (
        (ControlMode.CURRENT,) if consumer_type is ConsumerType.WALLBOX else (ControlMode.POWER, ControlMode.SWITCH)
    )
    return next((mode for mode in modes if found(mode)), ControlMode.NONE)


def suggest_consumer_control(
    entities: Iterable[EntityInfo],
    consumer_type: ConsumerType,
    control_mode: ControlMode,
    *,
    hot_water: bool = False,
) -> dict[str, object]:
    """Fields of the control step suggested from the device: entities and the range of the control entity."""
    entities = list(entities)
    matches = match_consumer_entities(entities, consumer_type, control_mode, hot_water=hot_water)
    data: dict[str, object] = {
        key: value for key, value in matches.items() if key not in (CONF_POWER_ENTITY, CONF_ENERGY_ENTITY)
    }
    control = next((info for info in entities if info.entity_id == matches.get(CONF_CONTROL_ENTITY)), None)
    if control is None:
        return data
    if control_mode is ControlMode.POWER and control.maximum:
        factor = 1000 if control.unit == "kW" else 1
        data[CONF_MAX_POWER_W] = round(control.maximum * factor)
        data[CONF_MIN_POWER_W] = round(max(control.minimum or 0, 0) * factor)
    elif control_mode is ControlMode.CURRENT:
        amperes = _numbers(control.options) or [
            value for value in (control.minimum, control.maximum) if value is not None
        ]
        if positive := [value for value in amperes if value > 0]:
            data[CONF_MIN_CURRENT_A] = min(positive)
            data[CONF_MAX_CURRENT_A] = max(positive)
    return data


def suggest_start_options(options: Iterable[str]) -> dict[str, str]:
    """Options of a start select for on (running without its own surplus logic) and off."""
    options = list(options)
    off = _pick(options, lambda _text, words: bool(words & {"off", "aus", "stop", "disabled"}))
    on = _pick(
        (o for o in options if o != off),
        lambda _text, words: bool(words & {"now", "fast", "schnell", "on", "an", "ein", "start", "enabled"}),
    )
    return {CONF_START_ON: on, CONF_START_OFF: off} if on is not None and off is not None else {}


def consumer_device_fit(entities: Iterable[EntityInfo], consumer_type: ConsumerType) -> float:
    """How well a device fits a consumer of this type; 0 or less: not a candidate."""
    entities = list(entities)
    platforms = {info.platform for info in entities if info.platform}
    score = 0.0
    if platforms & CONSUMER_PLATFORMS.get(consumer_type, frozenset()):
        score += 5
    elif any(platforms & known for known in CONSUMER_PLATFORMS.values()):
        # A device of another kind of consumer.
        score -= 5
    hot_water = consumer_type is ConsumerType.HEAT_PUMP and suggest_hot_water(entities)
    mode = suggest_control_mode(entities, consumer_type, hot_water=hot_water)
    matches = match_consumer_entities(entities, consumer_type, mode, hot_water=hot_water)
    weights = ((CONF_POWER_ENTITY, 2), (CONF_ENERGY_ENTITY, 1), (CONF_CONTROL_ENTITY, 2), (CONF_MODE_ENTITY, 2))
    score += sum(weight for key, weight in weights if key in matches)
    # Measured values alone are no consumer yet (e.g. a smart meter or an inverter);
    # a device that only measures one is offered as its meter device.
    return score if score > 3 else 0


def meter_device_fit(entities: Iterable[EntityInfo]) -> float:
    """How well a device measures a consumer (power and energy); 0: not a candidate."""
    matches = match_consumer_entities(entities, ConsumerType.OTHER, ControlMode.NONE)
    if CONF_POWER_ENTITY not in matches:
        return 0
    return 2 + (1 if CONF_ENERGY_ENTITY in matches else 0)


# Integrations (entity registry platform) of smart meters and of PV inverters. Both
# kinds of devices often come from the same integration (an inverter with its meter);
# the roles tell them apart.
GRID_PLATFORMS = frozenset(
    {
        "discovergy",
        "dsmr",
        "dsmr_reader",
        "edl21",
        "fronius",
        "homewizard",
        "huawei_solar",
        "kostal_plenticore",
        "powerfox",
        "sma",
        "solaredge_modbus",
        "solaredge_modbus_multi",
        "tibber",
    }
)
PV_PLATFORMS = frozenset(
    {
        "anker_solix",
        "apsystems",
        "ecoflow_cloud",
        "enphase_envoy",
        "fronius",
        "goodwe",
        "growatt_server",
        "hoymiles_wifi",
        "huawei_solar",
        "kostal_plenticore",
        "opendtu",
        "sma",
        "solaredge",
        "solaredge_modbus",
        "solaredge_modbus_multi",
        "solax",
        "solax_modbus",
        "sungrow",
    }
)
_GRID_WORDS = ("grid", "netz", "meter", "zähler", "bezug", "import", "export", "einspeis", "total active", "total_active")
_NOT_GRID = (
    "photovolt", "batter", "akku", "phase", "apparent", "reactive", "limit", "max", "rated", "peak",
    "string", "mppt", "load", "verbrauch", "consumption", "house", "haus", "inverter", "wechselrichter",
)
_PV_WORDS = (
    "photovolt", "yield", "ertrag", "production", "erzeugung", "generation", "input", "balkon",
    "balcony",
)
_NOT_PV = (
    "grid", "netz", "meter", "import", "export", "bezug", "einspeis", "batter", "akku", "load", "house", "haus",
    "consumption", "verbrauch", "limit", "max", "rated", "peak", "apparent", "reactive", "phase", "forecast",
    "prognose", "estimate",
)
_EXPORT_WORDS = ("export", "returned", "einspeis", "feed", "production", "produced", "to_grid", "to grid", "lieferung")
_IMPORT_WORDS = ("import", "bezug", "consum", "verbrauch", "purchased", "from_grid", "from grid", "delivered")


def _power_sensor(info: EntityInfo) -> bool:
    return info.domain == "sensor" and info.unit in _POWER_UNITS and info.device_class in (None, "power")


def _grid_power(info: EntityInfo) -> float:
    if not _power_sensor(info):
        return 0
    score = 2 if info.device_class == "power" else 1
    if info.has(*_GRID_WORDS):
        score += 1
    # "solar" as a word: it is also part of names like "solaredge".
    if info.has(*_NOT_GRID) or info.word("pv", "solar", "dc", "l1", "l2", "l3"):
        score -= 2
    if info.state is not None and not info.has_value:
        score -= 1
    return score


def _grid_counter(export: bool) -> Callable[[EntityInfo], float]:
    def score(info: EntityInfo) -> float:
        if info.domain != "sensor" or info.unit not in _ENERGY_UNITS:
            return 0
        if info.state_class not in ("total", "total_increasing") and info.device_class != "energy":
            return 0
        exporting = info.has(*_EXPORT_WORDS)
        if export != exporting:
            return 0
        value = 3 if export or info.has(*_IMPORT_WORDS) else 1
        if info.has("total", "gesamt", "lifetime"):
            value += 1
        if info.has("today", "heute", "daily", "week", "woche", "month", "monat", "year", "jahr", "tarif"):
            value -= 1
        if info.word("pv", "solar", "dc", "a", "b", "c", "l1", "l2", "l3") or info.has("phase", "batter", "akku"):
            # Not the total of all phases.
            value -= 2
        return value

    return score


def _pv_power(info: EntityInfo) -> float:
    if not _power_sensor(info):
        return 0
    score = 2 if info.device_class == "power" else 1
    if info.word("pv", "solar") or info.has(*_PV_WORDS):
        score += 2
    if info.has(*_NOT_PV):
        score -= 2
    if info.word("dc") or info.has("string", "mppt", "channel", "panel"):
        # The total of the inverter before a single string.
        score -= 1
    if info.state is not None and not info.has_value:
        score -= 1
    return score


def match_grid_entities(entities: Iterable[EntityInfo]) -> dict[str, str]:
    """Grid power and the import and export counters of a smart meter's device."""
    return _best_per_role(
        list(entities),
        (
            (CONF_GRID_EXPORT_ENERGY_ENTITY, _grid_counter(export=True)),
            (CONF_GRID_IMPORT_ENERGY_ENTITY, _grid_counter(export=False)),
            (CONF_GRID_POWER_ENTITY, _grid_power),
        ),
    )


def match_pv_power(entities: Iterable[EntityInfo]) -> str | None:
    """The PV power of an inverter's device (its AC total, or the PV input of a hybrid inverter)."""
    return _best_per_role(list(entities), ((CONF_PV_POWER_ENTITY, _pv_power),)).get(CONF_PV_POWER_ENTITY)


def match_outdoor_temperature(entities: Iterable[EntityInfo]) -> str | None:
    """The outdoor temperature sensor of a device, e.g. of a heat pump."""

    def score(info: EntityInfo) -> float:
        if info.domain != "sensor" or info.device_class != "temperature":
            return 0
        return 2 if info.has("outdoor", "outside", "außen", "aussen", "ambient") else 0

    return _best_per_role(list(entities), (("outdoor", score),)).get("outdoor")


def _platforms(entities: list[EntityInfo]) -> set[str]:
    return {info.platform for info in entities if info.platform}


def grid_device_fit(entities: Iterable[EntityInfo]) -> float:
    """How well a device measures the grid connection; 0: no power sensor."""
    entities = list(entities)
    matches = match_grid_entities(entities)
    if CONF_GRID_POWER_ENTITY not in matches:
        return 0
    score = 1.0
    if _platforms(entities) & GRID_PLATFORMS:
        score += 2
    if CONF_GRID_EXPORT_ENERGY_ENTITY in matches:
        # Export is what a smart meter has and a consumer has not.
        score += 3
    if CONF_GRID_IMPORT_ENERGY_ENTITY in matches:
        score += 1
    power = next(info for info in entities if info.entity_id == matches[CONF_GRID_POWER_ENTITY])
    if power.has(*_GRID_WORDS):
        score += 1
    return score


def pv_device_fit(entities: Iterable[EntityInfo]) -> float:
    """How well a device measures a PV system; 0: no power sensor."""
    entities = list(entities)
    power = match_pv_power(entities)
    if power is None:
        return 0
    score = 1.0
    if _platforms(entities) & PV_PLATFORMS:
        score += 2
    info = next(info for info in entities if info.entity_id == power)
    if info.word("pv", "solar") or info.has(*_PV_WORDS):
        score += 2
    if CONF_GRID_EXPORT_ENERGY_ENTITY in match_grid_entities(entities):
        # Rather a smart meter.
        score -= 2
    return max(score, 0.1)
