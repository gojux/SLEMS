"""Suggestions of the settings assistant (settings tab of the panel).

Rules in layers, each suggestion naming the sources that led to it:

* basic rules: the automatic values and the regular full charge, whatever
  the plant,
* tariff and plant: what the tariff makes possible or useless,
* goal: the direction chosen by the user (one at a time, not stored),
* observations: estimates from the recorded months (backtests) and the
  battery history.

Suggestions are made whatever the current value; the panel compares them with
the entities and shows a check mark where they match. Nothing is changed here.
The values are functions of the context, so fixed values can later be derived
from the plant.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import date, timedelta
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.const import UnitOfPower, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .const import CONF_GRID_POWER_ENTITY, CONF_GRID_POWER_INVERTED, CONF_PV_POWER_ENTITY, DOMAIN
from .forecast import async_statistic_means
from .full_charge import DAY_S, is_due
from .market_prices import SOURCE_COUNTRY, PriceSource, default_source
from .number import SETTING_NUMBERS
from .simulation import SETTINGS as SIMULATED_SETTINGS
from .switch import SETTING_SWITCHES
from .tariff import Role, Side, Tariff
from .util import entity_ids

_LOGGER = logging.getLogger(__name__)

# Automatic values that suit every plant (see learning). The learned grid
# targets are left out: their charging target is often far higher than
# expected with quickly changing loads (under review).
AUTO_SWITCHES = (
    "auto_gain",
    "timing_auto",
    "grid_friendly_buffer_auto",
    "charge_secured_buffer_auto",
    "night_reserve_auto",
    "feed_in_cap_auto_buffer",
)
# Observations are only suggested from this saving per month on (currency of
# the tariffs), from at least one complete month and of the last ones at most.
MIN_SAVING_PER_MONTH = 1.0
OBSERVED_MONTHS = 3
# Countries sharing a bidding zone with the country of a price source.
ZONE_COUNTRY = {"LU": "DE"}
# Long-term statistics looked at for the observations of the plant.
OBSERVED_DAYS = 30
# A feed-in cap: the daily highest feed-in within this share of the overall
# highest on at least CAP_MIN_DAYS days (of at least CAP_MIN_DATA_DAYS with
# data), and clearly below the PV peak (a full PV output is no cap).
CAP_TOLERANCE = 0.03
CAP_MIN_DAYS = 5
CAP_MIN_DATA_DAYS = 14
CAP_BELOW_PV = 0.9
# The configured PV peak power is too low once the PV exceeds it by this share.
PV_PEAK_MARGIN = 1.05
# Hours at or above the high temperature of the charge limit that suggest it.
HOT_MIN_HOURS = 3
# Grid friendly charging: a day counts as not full this far below the maximum
# SoC; suggested from this many such days on, and the manual buffer in steps of this (kWh).
FULL_MARGIN_PCT = 3.0
SHORT_MIN_DAYS = 3
BUFFER_STEP_KWH = 0.5
# Plant limits: empty this close to the minimum SoC; nearly full power is this
# share of the batteries' power; feed-in or import above this counts (hourly
# mean, W); a day counts from this much energy (Wh); noted from this many days
# or hours on; the power to try in the simulation in steps of this (W).
EMPTY_MARGIN_PCT = 2.0
POWER_SHARE = 0.9
LIMIT_GRID_W = 200.0
LIMIT_ENERGY_WH = 1000.0
LIMIT_MIN_DAYS = 5
LIMIT_MIN_HOURS = 5
POWER_STEP_W = 100.0
# Settings without effect without PV: nothing is suggested for them then.
PV_SETTINGS = frozenset(
    {
        "grid_friendly_charging",
        "grid_friendly_buffer_auto",
        "grid_friendly_buffer",
        "charge_secured_buffer_auto",
        "charge_secured_buffer",
        "battery_priority_auto",
        "battery_priority_soc",
        "battery_share_when_secured",
        "feed_in_cap",
        "feed_in_cap_auto_buffer",
        "feed_in_cap_buffer",
        "feed_in_cap_limit",
        "pv_peak_power",
        "night_discharge",
    }
)
# Goals the user can choose.
GOALS = ("self_consumption", "costs", "grid", "battery_first", "battery_care")
# Field of ControlSettings per number key.
NUMBER_ATTRIBUTES = {description.key: description.attribute for description in SETTING_NUMBERS}


@dataclass(frozen=True)
class Source:
    """Why a value is suggested: ``kind`` rule, tariff or observation; the
    panel shows the text of ``code`` with ``params``."""

    kind: str
    code: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class Suggestion:
    key: str
    value: bool | str | float
    sources: list[Source]
    # Battery settings: the device of the battery.
    device_id: str | None = None
    # Field of ControlSettings the simulation knows (the panel offers it there).
    field: str | None = None


@dataclass(frozen=True)
class TariffFacts:
    has_tariff: bool = False
    # Needs the market prices (dynamic items or 0 at negative prices).
    needs_market: bool = False
    import_market: bool = False
    export_market: bool = False
    # The feed-in credit is 0 in hours with a negative market price.
    export_zero_negative: bool = False
    power_priced: bool = False


@dataclass(frozen=True)
class BatteryFacts:
    device_id: str
    name: str
    learn_capacity: bool
    capacity_learned: bool
    # UNIX timestamp of the last full charge, None: never since recorded.
    last_full: float | None
    # Lowest state of charge of the device itself (driver), None: unknown.
    device_min_soc_pct: float | None = None
    min_soc_pct: float = 0.0


@dataclass(frozen=True)
class Observations:
    # Mean per complete month of the price aware control (backtest), and the months.
    price_saving: float | None = None
    price_months: int = 0
    # Peak shaving with the power price (backtest): saving, the limit it held.
    peak_saving: float | None = None
    peak_limit_kw: float | None = None
    peak_months: int = 0
    # From the long-term statistics of the last OBSERVED_DAYS days:
    # highest hourly mean of the PV power (W),
    pv_peak_w: float | None = None
    # the highest feed-in reached on many days (a cap of the inverter, W), on
    # how many days and of how many days with data,
    export_cap_w: float | None = None
    export_cap_days: int = 0
    export_days: int = 0
    # Days left out: their highest feed-in lay at SLEMS's own feed-in limit.
    export_own_days: int = 0
    # per battery (name) the hours at or above the temperature of the charge limit.
    hot_hours: tuple[tuple[str, int], ...] = ()
    # Days SLEMS charged grid friendly (an own feed-in limit), of them those the
    # batteries did not get full although that day's feed-in would have filled
    # them, and the mean energy missing on those days (Wh).
    friendly_days: int = 0
    friendly_short_days: int = 0
    friendly_missing_wh: float = 0.0


@dataclass(frozen=True)
class PlantLimits:
    """What the plant itself could not do in the last OBSERVED_DAYS days (from
    hourly statistics); days and hours, and the mean or needed amount."""

    days: int = 0
    # Batteries full on a day that still fed in, and the mean feed-in then (Wh).
    full_export_days: int = 0
    full_export_wh: float = 0.0
    # Batteries empty while importing, and the mean import then per such day (Wh).
    empty_import_days: int = 0
    empty_import_wh: float = 0.0
    # Hours charging (discharging) at nearly full power while still feeding in
    # (importing), and the highest power that would have taken it (W).
    charge_limited_hours: int = 0
    charge_needed_w: float = 0.0
    discharge_limited_hours: int = 0
    discharge_needed_w: float = 0.0


@dataclass(frozen=True)
class Context:
    settings: Any  # ControlSettings
    market_prices: bool
    price_source: str
    country: str | None
    tariff: TariffFacts
    batteries: Sequence[BatteryFacts]
    observations: Observations
    now: float
    goal: str | None = None
    # PV power entities or batteries with own PV modules.
    has_pv: bool = True
    limits: PlantLimits = field(default_factory=PlantLimits)
    # Total usable capacity (Wh) and power (W) of the plannable batteries.
    capacity_wh: float = 0.0
    max_charge_w: float = 0.0
    max_discharge_w: float = 0.0


@dataclass(frozen=True)
class Note:
    """A limit of the plant: nothing to set. ``key``: shown at that setting,
    None: with the plant limits; ``hardware``: changes of the batteries
    (added to the current values) to try in the simulation."""

    code: str
    params: dict[str, Any] = field(default_factory=dict)
    key: str | None = None
    hardware: dict[str, float] | None = None


@dataclass(frozen=True)
class LimitHour:
    """One hour of the statistics: SoC of all batteries (highest, lowest), their
    maximum and minimum SoC, grid power (+import) and battery power (+charge), means in W."""

    day: date
    soc_max: float
    soc_min: float
    max_soc: float
    min_soc: float
    grid_w: float
    battery_w: float


def plant_limits(hours: Sequence[LimitHour], max_charge_w: float, max_discharge_w: float) -> PlantLimits:
    """Days the capacity and hours the power of the batteries fell short."""
    exported: dict[date, float] = {}
    full: set[date] = set()
    empty_import: dict[date, float] = {}
    charge_needed: list[float] = []
    discharge_needed: list[float] = []
    for hour in hours:
        # Feed-in while the batteries discharge (night discharge, battery export)
        # is no PV they could not take; import while they charge (from the grid)
        # is no consumption they could not cover.
        if hour.battery_w > -LIMIT_GRID_W:
            exported[hour.day] = exported.get(hour.day, 0.0) + max(0.0, -hour.grid_w)
        else:
            exported.setdefault(hour.day, 0.0)
        if hour.soc_max >= hour.max_soc - FULL_MARGIN_PCT:
            full.add(hour.day)
        if hour.soc_max <= hour.min_soc + EMPTY_MARGIN_PCT and hour.grid_w > 0 and hour.battery_w < LIMIT_GRID_W:
            empty_import[hour.day] = empty_import.get(hour.day, 0.0) + hour.grid_w
        if max_charge_w and hour.battery_w >= max_charge_w * POWER_SHARE and -hour.grid_w > LIMIT_GRID_W:
            charge_needed.append(hour.battery_w - hour.grid_w)
        if max_discharge_w and -hour.battery_w >= max_discharge_w * POWER_SHARE and hour.grid_w > LIMIT_GRID_W:
            discharge_needed.append(-hour.battery_w + hour.grid_w)
    full_export = [exported[day] for day in full if exported[day] >= LIMIT_ENERGY_WH]
    empty = [wh for wh in empty_import.values() if wh >= LIMIT_ENERGY_WH]
    return PlantLimits(
        days=len(exported),
        full_export_days=len(full_export),
        full_export_wh=sum(full_export) / len(full_export) if full_export else 0.0,
        empty_import_days=len(empty),
        empty_import_wh=sum(empty) / len(empty) if empty else 0.0,
        charge_limited_hours=len(charge_needed),
        charge_needed_w=max(charge_needed, default=0.0),
        discharge_limited_hours=len(discharge_needed),
        discharge_needed_w=max(discharge_needed, default=0.0),
    )


def notes(ctx: Context) -> list[Note]:
    """Limits of the plant and of the tariff that no setting changes."""
    limits = ctx.limits
    result: list[Note] = []
    if limits.full_export_days >= LIMIT_MIN_DAYS:
        kwh = limits.full_export_wh / 1000
        result.append(
            Note(
                "capacity_pv",
                {"days": limits.full_export_days, "of": limits.days, "kwh": round(kwh, 1)},
                hardware={"capacity_kwh": float(math.ceil(kwh))},
            )
        )
    if limits.empty_import_days >= LIMIT_MIN_DAYS:
        kwh = limits.empty_import_wh / 1000
        result.append(
            Note(
                "capacity_night",
                {"days": limits.empty_import_days, "of": limits.days, "kwh": round(kwh, 1)},
                hardware={"capacity_kwh": float(math.ceil(kwh))},
            )
        )
    if limits.charge_limited_hours >= LIMIT_MIN_HOURS and limits.charge_needed_w > ctx.max_charge_w:
        result.append(
            Note(
                "charge_power",
                {"hours": limits.charge_limited_hours, "kw": round(limits.charge_needed_w / 1000, 1)},
                hardware={"max_charge_w": _step_up(limits.charge_needed_w - ctx.max_charge_w)},
            )
        )
    if limits.discharge_limited_hours >= LIMIT_MIN_HOURS and limits.discharge_needed_w > ctx.max_discharge_w:
        result.append(
            Note(
                "discharge_power",
                {"hours": limits.discharge_limited_hours, "kw": round(limits.discharge_needed_w / 1000, 1)},
                hardware={"max_discharge_w": _step_up(limits.discharge_needed_w - ctx.max_discharge_w)},
            )
        )
    seen = ctx.observations
    if (
        ctx.tariff.needs_market
        and seen.price_saving is not None
        and seen.price_months
        and seen.price_saving < MIN_SAVING_PER_MONTH
    ):
        result.append(
            Note("price_spread", {"saving": seen.price_saving, "months": seen.price_months}, key="price_control")
        )
    result.extend(Note("goal_no_export", key=key) for key in _goal_export_blocked(ctx))
    return result


def _step_up(watts: float) -> float:
    return float(math.ceil(watts / POWER_STEP_W) * POWER_STEP_W)


def tariff_facts(tariff: Tariff | None) -> TariffFacts:
    """What the rules need of the current tariff."""
    if tariff is None:
        return TariffFacts()
    items = tariff.items
    return TariffFacts(
        has_tariff=True,
        needs_market=tariff.dynamic,
        import_market=any(item.side is Side.IMPORT and item.unit.dynamic for item in items),
        export_market=any(item.side is Side.EXPORT and item.unit.dynamic for item in items),
        export_zero_negative=any(item.side is Side.EXPORT and item.zero_when_negative for item in items),
        power_priced=tariff.power_priced,
    )


def observations_from_comparison(
    result: Mapping[str, Any] | None, tariff_id: str | None, today: date
) -> Observations:
    """The backtests of the current tariff in the last complete months of a
    tariff comparison (see tariff_comparison)."""
    if not result or tariff_id is None:
        return Observations()
    current = f"{today:%Y-%m}"
    rows = [row for row in result.get("months", []) if row.get("month", current) < current]
    rows = sorted(rows, key=lambda row: row["month"])[-OBSERVED_MONTHS:]
    price = [
        row["savings"][tariff_id]
        for row in rows
        if isinstance(row.get("savings"), Mapping) and tariff_id in row["savings"]
    ]
    costs = [row["costs"][tariff_id] for row in rows if tariff_id in row.get("costs", {})]
    # A month with a power price but without a saving is a saving of 0.
    peak = [cost.get("peak_saving", 0.0) for cost in costs]
    limits = [cost["peak_limit_kw"] for cost in costs if "peak_limit_kw" in cost]
    return Observations(
        price_saving=round(sum(price) / len(price), 2) if price else None,
        price_months=len(price),
        peak_saving=round(sum(peak) / len(peak), 2) if peak and limits else None,
        # The limit the batteries held in every month.
        peak_limit_kw=max(limits) if limits else None,
        peak_months=len(peak) if limits else 0,
    )


def pv_peak_w(means: Mapping[str, Mapping[Any, float]]) -> float | None:
    """Highest hourly mean of the PV power, summed over the PV entities."""
    total: dict[Any, float] = {}
    for series in means.values():
        for hour, value in series.items():
            total[hour] = total.get(hour, 0.0) + value
    return max(total.values()) if total else None


def export_cap(daily_max_w: Sequence[float], pv_peak: float | None) -> tuple[float, int] | None:
    """(Level, days) of a feed-in cap from the highest feed-in per day, None: no cap seen."""
    days = [value for value in daily_max_w if value > 0]
    if len(days) < CAP_MIN_DATA_DAYS or pv_peak is None:
        return None
    level = max(days)
    at_level = sum(1 for value in days if value >= level * (1 - CAP_TOLERANCE))
    if at_level < CAP_MIN_DAYS or level > pv_peak * CAP_BELOW_PV:
        return None
    return level, at_level


def at_own_limit(
    day_peaks: Mapping[date, tuple[float, Any]], limits: Mapping[Any, tuple[float, float]]
) -> set[date]:
    """Days whose highest feed-in (W, hour) lay at SLEMS's own feed-in limit of
    that hour (grid friendly charging or feed-in cap; lowest and highest limit
    of the hour): a plateau SLEMS made, not a cap of the inverter."""
    days = set()
    for day, (export, hour) in day_peaks.items():
        limit = limits.get(hour)
        if limit is not None and limit[0] * (1 - CAP_TOLERANCE) <= export <= limit[1] * (1 + CAP_TOLERANCE):
            days.add(day)
    return days


@dataclass(frozen=True)
class FriendlyDay:
    """One day with an own feed-in limit: highest SoC and maximum SoC (%) of all
    batteries, and the energy fed in (Wh); ``charge_limited``: they charged at
    nearly full power while feeding in (no buffer would have filled them)."""

    soc_pct: float
    max_soc_pct: float
    exported_wh: float
    charge_limited: bool = False


def short_days(days: Sequence[FriendlyDay], capacity_wh: float) -> tuple[int, float]:
    """Days the batteries did not get full although the energy fed in that day
    would have filled them, and the mean energy missing then (Wh)."""
    missing = [
        gap
        for day in days
        if not day.charge_limited
        and day.soc_pct < day.max_soc_pct - FULL_MARGIN_PCT
        and (gap := (day.max_soc_pct - day.soc_pct) / 100 * capacity_wh) <= day.exported_wh
    ]
    return len(missing), (sum(missing) / len(missing) if missing else 0.0)


def hours_at_or_above(maxima: Mapping[Any, float], threshold: float) -> int:
    return sum(1 for value in maxima.values() if value >= threshold)


def _field(key: str) -> str | None:
    attribute = SETTING_SWITCHES.get(key) or NUMBER_ATTRIBUTES.get(key) or key
    return attribute if attribute in SIMULATED_SETTINGS else None


def _rules(ctx: Context) -> list[Suggestion]:
    """Basic rules (always)."""
    result = [Suggestion(key, True, [Source("rule", "auto")]) for key in AUTO_SWITCHES]
    if ctx.goal != "battery_first":
        # Battery first needs a fixed, high threshold.
        result.append(Suggestion("battery_priority_auto", True, [Source("rule", "auto")]))
    full_sources = [Source("rule", "full_charge")]
    interval = ctx.settings.full_charge_interval_days
    for battery in ctx.batteries:
        if is_due(battery.last_full, ctx.now, interval):
            days = None if battery.last_full is None else int((ctx.now - battery.last_full) // DAY_S)
            full_sources.append(Source("observation", "not_full", {"name": battery.name, "days": days}))
    result.append(Suggestion("regular_full_charge", True, full_sources))
    # Like the other automatic values: until learned the configured capacity applies.
    result.extend(
        Suggestion(
            "learn_capacity",
            True,
            [Source("rule", "learned_capacity" if battery.capacity_learned else "auto")],
            device_id=battery.device_id,
        )
        for battery in ctx.batteries
    )
    # Below the device's own minimum the plans count on energy it does not deliver.
    result.extend(
        Suggestion(
            "min_soc",
            battery.device_min_soc_pct,
            [Source("plant", "device_min_soc", {"pct": battery.device_min_soc_pct})],
            device_id=battery.device_id,
        )
        for battery in ctx.batteries
        if battery.device_min_soc_pct is not None
        and battery.min_soc_pct < battery.device_min_soc_pct
        # The goal battery care suggests more than that (see _goal_rules).
        and ctx.goal != "battery_care"
    )
    return result


def _tariff_rules(ctx: Context) -> list[Suggestion]:
    """What the tariff makes possible or useless."""
    tariff = ctx.tariff
    if not tariff.has_tariff:
        return []
    result: list[Suggestion] = []
    if tariff.needs_market:
        result.append(Suggestion("market_prices", True, [Source("tariff", "dynamic")]))
        country = ZONE_COUNTRY.get(ctx.country or "", ctx.country)
        try:
            source_country = SOURCE_COUNTRY[PriceSource(ctx.price_source)]
        except ValueError:
            source_country = None
        if country in set(SOURCE_COUNTRY.values()) and source_country != country:
            result.append(
                Suggestion("price_source", default_source(country).value, [Source("tariff", "country", {"country": country})])
            )
    if not tariff.import_market:
        result.append(Suggestion("grid_charge", False, [Source("tariff", "import_fixed")]))
    if not tariff.export_market:
        result.append(Suggestion("battery_export", False, [Source("tariff", "export_fixed")]))
    if tariff.power_priced:
        result.append(Suggestion("peak_shaving_mode", "economic", [Source("tariff", "power_price")]))
    # A preference, not a limit: the goal battery first wants the batteries full early.
    if tariff.export_zero_negative and ctx.goal != "battery_first":
        result.append(Suggestion("grid_friendly_charging", True, [Source("tariff", "negative_zero")]))
    return result


def _goal_values(ctx: Context) -> list[tuple[str, bool | float]]:
    """Settings and values of the chosen goal (fixed values for a start).

    Nothing that feeds in from the batteries while the maximum export while
    discharging is 0; price settings only where the tariff makes them pay."""
    tariff = ctx.tariff
    may_export = ctx.settings.discharge_max_grid_export_w > 0
    goal = ctx.goal
    if goal == "self_consumption":
        # The night discharge feeds in what would be left in the morning:
        # nothing gained for the own use, only conversion losses.
        return [
            ("night_discharge", False),
            ("price_control", False),
            ("grid_charge", False),
            ("battery_export", False),
        ]
    if goal == "costs":
        values: list[tuple[str, bool | float]] = [("night_discharge", False)]
        if tariff.needs_market:
            values.append(("price_control", True))
        if tariff.import_market:
            values += [("grid_charge", True), ("grid_charge_max_soc", 90.0)]
        if tariff.export_market and may_export:
            values.append(("battery_export", True))
        if tariff.export_zero_negative:
            values.append(("grid_friendly_charging", True))
        if tariff.power_priced:
            values.append(("peak_shaving", True))
        return values
    if goal == "grid":
        # Feeding in at night empties the batteries for the noon peak.
        values = [("grid_friendly_charging", True), ("peak_shaving", True)]
        if may_export:
            values += [("night_discharge", True), ("night_reserve_coverage", 90.0)]
        return values
    if goal == "battery_first":
        return [
            ("night_discharge", False),
            ("night_reserve_coverage", 110.0),
            # Batteries full as early as possible.
            ("grid_friendly_charging", False),
            ("battery_priority_auto", False),
            ("battery_priority_soc", 80.0),
            ("battery_share_when_secured", 100.0),
            ("price_control", False),
            ("battery_export", False),
            ("grid_charge_max_soc", 100.0),
            # Peak shaving discharges the batteries for the peaks.
            ("peak_shaving", False),
        ]
    if goal == "battery_care":
        # Fewer cycles and less time at a high state of charge.
        return [
            ("night_discharge", False),
            ("grid_friendly_charging", True),
            ("price_min_gain", 5.0),
            ("grid_charge", False),
            ("battery_export", False),
            ("grid_charge_max_soc", 80.0),
            ("temperature_limit", True),
        ]
    return []


def _goal_export_blocked(ctx: Context) -> list[str]:
    """Settings the chosen goal would suggest but leaves out because the
    maximum export while discharging is 0 (noted at them, so that a value set
    by mistake shows)."""
    if ctx.settings.discharge_max_grid_export_w > 0:
        return []
    if ctx.goal == "grid" and ctx.has_pv:
        return ["night_discharge"]
    if ctx.goal == "costs" and ctx.tariff.export_market:
        return ["battery_export"]
    return []


def _goal_battery_values(ctx: Context, battery: BatteryFacts) -> list[tuple[str, float]]:
    """Battery settings of the chosen goal."""
    if ctx.goal == "battery_first":
        return [("max_soc", 100.0)]
    if ctx.goal == "battery_care":
        # Neither end of the state of charge; together with the regular full
        # charge (LFP calibrates when full). Never below the device's minimum.
        return [("max_soc", 90.0), ("min_soc", max(20.0, battery.device_min_soc_pct or 0.0))]
    return []


def _goal_rules(ctx: Context) -> list[Suggestion]:
    if ctx.goal not in GOALS:
        return []
    source = Source("goal", "goal", {"goal": ctx.goal})
    result = [Suggestion(key, value, [source]) for key, value in _goal_values(ctx)]
    result.extend(
        Suggestion(key, value, [source], device_id=battery.device_id)
        for battery in ctx.batteries
        for key, value in _goal_battery_values(ctx, battery)
    )
    return result


def _observation_rules(ctx: Context) -> list[Suggestion]:
    """Estimates from the recorded months."""
    seen = ctx.observations
    settings = ctx.settings
    result: list[Suggestion] = []
    cap_seen = seen.export_cap_w is not None
    if cap_seen:
        source = Source(
            "observation",
            "export_cap",
            {"kw": round(seen.export_cap_w / 1000, 1), "days": seen.export_cap_days, "of": seen.export_days},
        )
        result.append(Suggestion("feed_in_cap", True, [source]))
        pv_peak = settings.pv_peak_power_kwp * 1000
        if seen.pv_peak_w is None or seen.pv_peak_w <= pv_peak * PV_PEAK_MARGIN:
            # The limit in % of the configured PV peak power.
            pct = round(min(100.0, max(1.0, seen.export_cap_w / pv_peak * 100)))
            result.append(Suggestion("feed_in_cap_limit", float(pct), [source]))
    if (
        (settings.feed_in_cap or cap_seen)
        and seen.pv_peak_w is not None
        and seen.pv_peak_w > settings.pv_peak_power_kwp * 1000 * PV_PEAK_MARGIN
    ):
        # At least the measured PV power (the hourly mean stays below the peak).
        kwp = math.ceil(seen.pv_peak_w / 100) / 10
        result.append(
            Suggestion("pv_peak_power", kwp, [Source("observation", "pv_peak", {"kw": round(seen.pv_peak_w / 1000, 1)})])
        )
    if settings.grid_friendly_charging and seen.friendly_short_days >= SHORT_MIN_DAYS:
        params = {
            "days": seen.friendly_short_days,
            "of": seen.friendly_days,
            "kwh": round(seen.friendly_missing_wh / 1000, 1),
        }
        if settings.grid_friendly_buffer_auto:
            # The learned buffer was too small: nothing to set, a warning.
            source = Source("observation", "friendly_short", {**params, "warn": True})
            result.append(Suggestion("grid_friendly_buffer_auto", True, [source]))
        else:
            missing_kwh = seen.friendly_missing_wh / 1000
            buffer = math.ceil((settings.grid_friendly_buffer_kwh + missing_kwh) / BUFFER_STEP_KWH) * BUFFER_STEP_KWH
            result.append(
                Suggestion("grid_friendly_buffer", buffer, [Source("observation", "friendly_short", params)])
            )
    hot = [(name, hours) for name, hours in seen.hot_hours if hours >= HOT_MIN_HOURS]
    if hot:
        result.append(
            Suggestion(
                "temperature_limit",
                True,
                [
                    Source("observation", "hot", {"name": name, "hours": hours, "temp": settings.temperature_high_c})
                    for name, hours in hot
                ],
            )
        )
    if seen.price_saving is not None and seen.price_saving >= MIN_SAVING_PER_MONTH:
        result.append(
            Suggestion(
                "price_control",
                True,
                [Source("observation", "price_backtest", {"saving": seen.price_saving, "months": seen.price_months})],
            )
        )
    if ctx.tariff.power_priced and seen.peak_saving is not None and seen.peak_saving >= MIN_SAVING_PER_MONTH:
        result.append(
            Suggestion(
                "peak_shaving",
                True,
                [
                    Source(
                        "observation",
                        "peak_backtest",
                        {"saving": seen.peak_saving, "months": seen.peak_months, "limit_kw": seen.peak_limit_kw},
                    )
                ],
            )
        )
    return result


def suggestions(ctx: Context) -> list[Suggestion]:
    """All suggestions; one per setting (and battery), with all its sources.

    A later layer contradicting an earlier one is left out (the tariff limits
    what an observation may suggest)."""
    merged: dict[tuple[str, str | None], Suggestion] = {}
    for suggestion in [*_rules(ctx), *_tariff_rules(ctx), *_goal_rules(ctx), *_observation_rules(ctx)]:
        if not ctx.has_pv and suggestion.key in PV_SETTINGS:
            continue
        slot = (suggestion.key, suggestion.device_id)
        known = merged.get(slot)
        if known is None:
            suggestion.field = _field(suggestion.key) if suggestion.device_id is None else None
            merged[slot] = suggestion
        elif known.value == suggestion.value:
            known.sources.extend(suggestion.sources)
    return list(merged.values())


async def _async_statistics(
    hass: HomeAssistant, ids: set[str], start: Any, end: Any, types: set[str]
) -> dict[str, list[dict]]:
    """Hourly long-term statistics (power in W, temperature in °C)."""
    if not ids:
        return {}
    return await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        start,
        end,
        ids,
        "hour",
        {"power": UnitOfPower.WATT, "temperature": UnitOfTemperature.CELSIUS},
        types,
    )


async def _async_friendly_days(
    hass: HomeAssistant,
    entry: Any,
    registry: er.EntityRegistry,
    start: Any,
    end: Any,
    limit_days: set[date],
    exported: Mapping[date, float],
    charge_limited: set[date],
) -> dict[str, Any]:
    """Fields of Observations on the grid friendly charging: on the days with an
    own feed-in limit (except today), whether the batteries got full."""
    coordinator = entry.runtime_data
    soc = registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_battery_soc_total")
    max_soc = registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_battery_max_soc_total")
    capacity = sum(battery.capacity_wh for battery in coordinator.batteries if battery.plannable)
    if not soc or not max_soc or not capacity:
        return {}
    rows = await _async_statistics(hass, {soc, max_soc}, start, end, {"max"})

    def daily_max(statistic_id: str) -> dict[date, float]:
        result: dict[date, float] = {}
        for row in rows.get(statistic_id, []):
            if row.get("max") is not None:
                day = dt_util.as_local(dt_util.utc_from_timestamp(row["start"])).date()
                result[day] = max(result.get(day, row["max"]), row["max"])
        return result

    socs, targets = daily_max(soc), daily_max(max_soc)
    today = dt_util.now().date()
    days = [
        FriendlyDay(socs[day], targets[day], exported.get(day, 0.0), day in charge_limited)
        for day in sorted(limit_days)
        if day != today and day in socs and day in targets
    ]
    short, missing = short_days(days, capacity)
    return {"friendly_days": len(days), "friendly_short_days": short, "friendly_missing_wh": missing}


async def async_plant_limits(hass: HomeAssistant, entry: Any) -> PlantLimits:
    """Plant limits from the hourly statistics of the last OBSERVED_DAYS days."""
    coordinator = entry.runtime_data
    config = entry.options or entry.data
    grid = config.get(CONF_GRID_POWER_ENTITY)
    registry = er.async_get(hass)

    def own(key: str) -> str | None:
        return registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{key}")

    ids = {
        "soc": own("battery_soc_total"),
        "max_soc": own("battery_max_soc_total"),
        "min_soc": own("battery_min_soc_total"),
        "battery": own("battery_power_total"),
    }
    if not grid or not all(ids.values()):
        return PlantLimits()
    end = dt_util.utcnow()
    start = end - timedelta(days=OBSERVED_DAYS)
    rows = await _async_statistics(hass, {grid, *ids.values()}, start, end, {"min", "max", "mean"})
    sign = -1.0 if config.get(CONF_GRID_POWER_INVERTED, False) else 1.0

    def by_hour(statistic_id: str, kind: str) -> dict[Any, float]:
        return {row["start"]: row[kind] for row in rows.get(statistic_id, []) if row.get(kind) is not None}

    soc_max, soc_min = by_hour(ids["soc"], "max"), by_hour(ids["soc"], "min")
    max_soc, min_soc = by_hour(ids["max_soc"], "max"), by_hour(ids["min_soc"], "min")
    battery, grid_w = by_hour(ids["battery"], "mean"), by_hour(grid, "mean")
    hours = [
        LimitHour(
            day=dt_util.as_local(dt_util.utc_from_timestamp(hour)).date(),
            soc_max=soc_max[hour],
            soc_min=soc_min[hour],
            max_soc=max_soc[hour],
            min_soc=min_soc[hour],
            grid_w=sign * grid_w[hour],
            battery_w=battery[hour],
        )
        for hour in sorted(soc_max)
        if hour in soc_min and hour in max_soc and hour in min_soc and hour in battery and hour in grid_w
    ]
    plannable = [b for b in coordinator.batteries if b.plannable]
    return plant_limits(
        hours,
        sum(b.driver.capabilities.max_charge_power_w for b in plannable),
        sum(b.driver.capabilities.max_discharge_power_w for b in plannable),
    )


async def async_plant_observations(hass: HomeAssistant, entry: Any) -> dict[str, Any]:
    """Fields of Observations from the long-term statistics of the last OBSERVED_DAYS days."""
    coordinator = entry.runtime_data
    config = entry.options or entry.data
    end = dt_util.utcnow()
    start = end - timedelta(days=OBSERVED_DAYS)
    fields: dict[str, Any] = {}
    pv = await async_statistic_means(hass, entity_ids(config.get(CONF_PV_POWER_ENTITY)), start, end)
    fields["pv_peak_w"] = peak = pv_peak_w(pv)
    grid = config.get(CONF_GRID_POWER_ENTITY)
    registry = er.async_get(hass)
    if grid:
        inverted = config.get(CONF_GRID_POWER_INVERTED, False)
        own = registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_feed_in_limit")
        battery = registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_battery_power_total")
        ids = {grid, *(entity_id for entity_id in (own, battery) if entity_id)}
        rows = await _async_statistics(hass, ids, start, end, {"min", "max", "mean"})
        battery_w = {
            row["start"]: row["mean"]
            for row in (rows.get(battery, []) if battery else [])
            if row.get("mean") is not None
        }
        max_charge_w = sum(b.driver.capabilities.max_charge_power_w for b in coordinator.batteries if b.plannable)
        day_peaks: dict[date, tuple[float, Any]] = {}
        exported: dict[date, float] = {}
        charge_limited: set[date] = set()
        for row in rows.get(grid, []):
            day = dt_util.as_local(dt_util.utc_from_timestamp(row["start"])).date()
            charging = battery_w.get(row["start"], 0.0)
            # The hourly mean in W is the Wh of the hour (import and export netted);
            # feed-in while the batteries discharge is not PV they could take.
            if row.get("mean") is not None and charging > -LIMIT_GRID_W:
                export_w = max(0.0, row["mean"] if inverted else -row["mean"])
                exported[day] = exported.get(day, 0.0) + export_w
                if max_charge_w and charging >= max_charge_w * POWER_SHARE and export_w > LIMIT_GRID_W:
                    charge_limited.add(day)
            # Feed-in is negative, or positive with an inverted sign.
            value = row.get("max") if inverted else row.get("min")
            if value is None:
                continue
            export = value if inverted else -value
            if export > day_peaks.get(day, (0.0, None))[0]:
                day_peaks[day] = (export, row["start"])
        limits = {
            row["start"]: (row["min"], row["max"])
            for row in (rows.get(own, []) if own else [])
            if row.get("min") is not None and row.get("max") is not None
        }
        own_days = at_own_limit(day_peaks, limits)
        daily = [export for day, (export, _) in day_peaks.items() if day not in own_days]
        fields["export_days"] = len(daily)
        fields["export_own_days"] = len(own_days)
        if (cap := export_cap(daily, peak)) is not None:
            fields["export_cap_w"], fields["export_cap_days"] = cap
        limit_days = {dt_util.as_local(dt_util.utc_from_timestamp(hour)).date() for hour in limits}
        fields.update(
            await _async_friendly_days(hass, entry, registry, start, end, limit_days, exported, charge_limited)
        )
    temperatures = {
        battery.name: entity_id
        for battery in coordinator.batteries
        if (
            entity_id := registry.async_get_entity_id(
                "sensor", DOMAIN, f"{battery.subentry_id}_internal_temperature"
            )
        )
    }
    if temperatures:
        rows = await _async_statistics(hass, set(temperatures.values()), start, end, {"max"})
        high = coordinator.settings.temperature_high_c
        fields["hot_hours"] = tuple(
            (
                name,
                hours_at_or_above(
                    {row["start"]: row["max"] for row in rows.get(entity_id, []) if row.get("max") is not None},
                    high,
                ),
            )
            for name, entity_id in temperatures.items()
        )
    return fields


async def async_context(hass: HomeAssistant, entry: Any, goal: str | None = None) -> Context:
    """The context of the loaded entry (the tariff comparison may take seconds)."""
    from .tariff_comparison import async_tariff_comparison, configured_tariffs

    devices = dr.async_get(hass)

    def device_id(subentry_id: str) -> str | None:
        device = devices.async_get_device_by_identifier((DOMAIN, subentry_id), entry.entry_id)
        return device.id if device else None

    coordinator = entry.runtime_data
    tariffs = configured_tariffs(entry)
    current_id, current = next(
        ((key, tariff) for key, tariff in tariffs.items() if tariff.role is Role.CURRENT), (None, None)
    )
    observations = Observations()
    if current is not None:
        try:
            comparison = await async_tariff_comparison(hass, entry)
        except Exception:  # noqa: BLE001 - no observations rather than no suggestions
            comparison = None
        observations = observations_from_comparison(comparison, current_id, dt_util.now().date())
    try:
        observations = replace(observations, **await async_plant_observations(hass, entry))
    except Exception:  # noqa: BLE001 - suggestions without these observations
        _LOGGER.debug("Assistant: no observations from the statistics", exc_info=True)
    try:
        limits = await async_plant_limits(hass, entry)
    except Exception:  # noqa: BLE001 - suggestions without the plant limits
        _LOGGER.debug("Assistant: no plant limits from the statistics", exc_info=True)
        limits = PlantLimits()
    _LOGGER.debug("Assistant observations: %s; limits: %s", observations, limits)
    config = entry.options or entry.data
    plannable = [battery for battery in coordinator.batteries if battery.plannable]
    return Context(
        has_pv=bool(entity_ids(config.get(CONF_PV_POWER_ENTITY)))
        or any(battery.driver.capabilities.own_pv for battery in coordinator.batteries),
        limits=limits,
        capacity_wh=sum(battery.capacity_wh for battery in plannable),
        max_charge_w=sum(battery.driver.capabilities.max_charge_power_w for battery in plannable),
        max_discharge_w=sum(battery.driver.capabilities.max_discharge_power_w for battery in plannable),
        settings=coordinator.settings,
        market_prices=coordinator.market_prices.enabled,
        price_source=str(coordinator.market_prices.source),
        country=hass.config.country,
        tariff=tariff_facts(current),
        batteries=[
            BatteryFacts(
                device_id=device_id(battery.subentry_id),
                name=battery.name,
                learn_capacity=battery.learn_capacity,
                capacity_learned=battery.capacity_learner.capacity_wh is not None,
                last_full=battery.cell_monitor.last_full,
                device_min_soc_pct=battery.driver.capabilities.min_soc_pct,
                min_soc_pct=battery.limits.min_soc_pct,
            )
            for battery in coordinator.batteries
        ],
        observations=observations,
        now=dt_util.utcnow().timestamp(),
        goal=goal,
    )


@callback
def async_register_websocket(hass: HomeAssistant) -> None:
    """Register ``slems/assistant`` once per Home Assistant run."""
    key = f"{DOMAIN}_assistant_registered"
    if hass.data.get(key):
        return
    hass.data[key] = True
    websocket_api.async_register_command(hass, _ws_assistant)


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/assistant", vol.Optional("goal"): vol.In(GOALS)}
)
@websocket_api.async_response
async def _ws_assistant(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if getattr(entry, "runtime_data", None) is not None
    ]
    if not entries:
        connection.send_error(msg["id"], "not_loaded", "SLEMS is not loaded")
        return
    ctx = await async_context(hass, entries[0], msg.get("goal"))
    connection.send_result(
        msg["id"],
        {"suggestions": [asdict(s) for s in suggestions(ctx)], "notes": [asdict(n) for n in notes(ctx)]},
    )
