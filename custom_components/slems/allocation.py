"""Distribution of the available power between batteries and consumers.

``available_w`` is the power SLEMS can distribute: the (filtered) grid export
plus the power currently drawn by everything SLEMS controls (batteries and
unblocked controllable consumers). Positive = surplus, negative = deficit.

Consumers that must keep running (minimum runtime) keep their power first.
Running on/off consumers keep their power before power controlled consumers
of higher priority (those can adapt, switching costs a cycle).
With the remaining power three cases exist:

Surplus (remaining above the charge grid target): the power above the target
is distributed, so the grid always keeps at least the target surplus.
1. As long as the battery charge is not *secured*, the battery has priority.
   Charge is secured when the total SoC is at least the priority threshold and
   the expected PV surplus for the rest of the day covers the energy needed to
   fill the batteries (including charge losses) plus a safety buffer.
2. Once secured, the surplus is split: the battery share goes to the batteries,
   the rest to the consumers in order of priority. Whatever one side cannot
   take is offered to the other.

Deficit (remaining below the discharge grid target): the batteries discharge
so that the grid reaches the discharge target, which is capped by the maximum
grid export while discharging. With import peak shaving enabled and the SoC at
or below its threshold, they only cover the import above the grid limit.

In between the batteries stay idle.

Grid friendly charging (optional, see grid_friendly): once the charge is
secured, the batteries only charge with the surplus above the feed-in limit.

Night discharge (optional, see night_discharge): outside a surplus the
batteries discharge at least with the planned night power, ignoring the
discharge grid target but still respecting the maximum grid export.

Price hold (optional, see price_hold): in an hour whose energy is kept for
more expensive hours the batteries cover no deficit (or only up to the power
allotted to a partly covered hour), the house draws from the grid; import peak
shaving still covers the import above its limit.

Grid charging (optional, see grid_charge): outside a surplus the batteries
charge with the planned power from the grid, below the import limit of peak
shaving.

Room for cheaper hours (price aware control with negative prices ahead, see
grid_charge): in a surplus the batteries take at most the planned power, the
rest goes to the consumers or is fed in now.

Feeding in from the batteries (optional, see grid_charge): outside a surplus
the batteries cover the deficit and feed in the planned power on top.

Battery support (see battery_support): the power of consumers that must not
draw from the batteries right now (``unsupported``) is left to the grid in a
deficit; the batteries cover only the rest. Import peak shaving still covers
the import above its limit.

Daily targets of consumers (see consumer_targets): a consumer short of its
target gets the surplus before the batteries (boost); from its latest start
on it runs at full power regardless of the surplus (forced), the batteries
or the grid cover it like any other load.

Feed-in cap (optional, see feed_in_cap) takes precedence over all of the
above: the surplus above the limit goes to the supporting consumers as far as
the plan runs them from the start of a peak that does not fit into the
batteries (``support_w``), then to the batteries (regardless of battery
priority, share or grid friendly charging), then to the supporting and then
the normal consumers (``CapMode``). The surplus below the limit is distributed
as usual without the supporting consumers, and the batteries do not charge
with it while the space is needed later (``hold_charging``). Before a peak the
batteries feed in the planned export power; the export never exceeds the
limit, and the cap export may exceed the maximum grid export while
discharging.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
import math

from homeassistant.util import dt as dt_util

from .const import CapMode, ControlMode
from .pv_forecast import PvForecast, hourly

FULL_SOC_PCT = 99.5
PV_PERIOD = timedelta(hours=1)


class Strategy(StrEnum):
    """Why the allocation looks the way it does."""

    BATTERY_PRIORITY = "battery_priority"
    SHARED = "shared"
    SELF_CONSUMPTION = "self_consumption"
    PEAK_SHAVING = "peak_shaving"
    NIGHT_DISCHARGE = "night_discharge"
    PRICE_HOLD = "price_hold"
    GRID_CHARGE = "grid_charge"
    BATTERY_EXPORT = "battery_export"
    PRICE_ROOM = "price_room"
    GRID_FRIENDLY = "grid_friendly"
    FEED_IN_CAP = "feed_in_cap"
    IDLE = "idle"


@dataclass(frozen=True)
class BatteryGroup:
    """All batteries seen as one."""

    soc_pct: float
    capacity_wh: float
    max_charge_w: float
    max_discharge_w: float
    # One way efficiency (charging), 0..1.
    charge_efficiency: float
    # SoC window of the batteries (capacity weighted): "full" is the maximum SoC.
    min_soc_pct: float = 0.0
    full_soc_pct: float = 100.0
    # Every battery full on its own (see ``battery_full``); None: judged by the
    # mean SoC. The mean hides one battery below full behind full ones.
    all_full: bool | None = None

    @property
    def energy_to_full_wh(self) -> float:
        """Energy that must be charged to fill the batteries, losses included."""
        missing = max(0.0, self.full_soc_pct - self.soc_pct) / 100 * self.capacity_wh
        return missing / self.charge_efficiency

    @property
    def is_full(self) -> bool:
        if self.all_full is not None:
            return self.all_full
        return battery_full(self.soc_pct, self.full_soc_pct)


def battery_full(soc_pct: float, full_soc_pct: float) -> bool:
    """Whether a battery (or the group) with this SoC and maximum SoC is full."""
    return soc_pct >= min(FULL_SOC_PCT, full_soc_pct - (100.0 - FULL_SOC_PCT))


@dataclass(frozen=True)
class ConsumerRequest:
    """A controllable, currently unblocked consumer."""

    subentry_id: str
    priority: int
    control_mode: ControlMode
    nominal_power_w: float = 0.0
    min_power_w: float = 0.0
    max_power_w: float = 0.0
    # Minimum runtime not yet elapsed: must keep running.
    must_stay_on: bool = False
    # Switched on right now (an on/off consumer keeps its power before the
    # power controlled ones, see _distribute).
    running: bool = False
    # Minimum pause not yet elapsed: must stay off.
    must_stay_off: bool = False
    cap_mode: CapMode = CapMode.NORMAL
    # Daily target (see consumer_targets): gets the surplus before the
    # batteries (boost), or runs regardless of the surplus (forced).
    boost: bool = False
    forced: bool = False
    # Forced from the batteries only: at most what they can deliver.
    forced_max_w: float | None = None

    @property
    def minimum_running_power_w(self) -> float:
        if self.control_mode is ControlMode.SWITCH:
            return self.nominal_power_w
        return self.min_power_w

    @property
    def full_power_w(self) -> float:
        if self.control_mode is ControlMode.SWITCH:
            return self.nominal_power_w
        return self.max_power_w


@dataclass(frozen=True)
class AllocationSettings:
    """User settings influencing the allocation."""

    battery_priority_soc_pct: float
    battery_share_when_secured_pct: float
    charge_secured_buffer_wh: float
    # Target grid surplus (+export / -import) while charging / discharging.
    charge_grid_target_w: float
    discharge_grid_target_w: float
    # Upper limit of the grid export caused while discharging.
    discharge_max_grid_export_w: float
    peak_shaving: bool
    peak_shaving_grid_limit_w: float
    peak_shaving_soc_threshold_pct: float


@dataclass(frozen=True)
class CapControl:
    """Feed-in cap in effect right now (see feed_in_cap)."""

    limit_w: float
    # Do not charge with surplus below the limit.
    hold_charging: bool = False
    # Battery energy to feed in right now (AC).
    export_w: float = 0.0
    # Kept between the export and the limit.
    margin_w: float = 100.0
    # Planned power of the supporting consumers right now (before the
    # batteries, in a peak that does not fit into them).
    support_w: float = 0.0

    @property
    def max_export_w(self) -> float:
        return max(0.0, self.limit_w - self.margin_w)


def max_discharge_export_w(settings: AllocationSettings, cap: CapControl | None) -> float:
    """Upper limit of the grid export caused while discharging."""
    if cap is None:
        return settings.discharge_max_grid_export_w
    if cap.export_w > 0:
        return cap.max_export_w
    return min(settings.discharge_max_grid_export_w, cap.max_export_w)


@dataclass
class Allocation:
    """Result: battery power (+charge / -discharge) and consumer powers."""

    strategy: Strategy
    battery_power_w: float = 0.0
    consumer_power_w: dict[str, float] = field(default_factory=dict)
    charge_secured: bool = False


def allocate(
    available_w: float,
    battery: BatteryGroup | None,
    consumers: Sequence[ConsumerRequest],
    settings: AllocationSettings,
    expected_surplus_wh: float | None,
    night_discharge_w: float | None = None,
    feed_in_limit_w: float | None = None,
    cap: CapControl | None = None,
    unsupported: frozenset[str] = frozenset(),
    unsupported_measured_w: float = 0.0,
    discharge_limit_w: float | None = None,
    grid_charge_w: float = 0.0,
    battery_export_w: float = 0.0,
    charge_cap_w: float | None = None,
    grid_import_limit_w: float = math.inf,
) -> Allocation:
    """Distribute ``available_w`` between batteries and consumers.

    ``discharge_limit_w``: the batteries cover a deficit with at most this
    power (price hold; 0: they keep their energy). ``grid_charge_w``: planned
    charging from the grid (in a surplus the whole planned charge power),
    ``battery_export_w``: planned feed-in from the batteries beyond the
    deficit, ``charge_cap_w``: the batteries take at most this of a surplus
    (room kept for cheaper hours), ``grid_import_limit_w``: grid import that
    charging from the grid must not exceed.

    ``unsupported`` are controllable consumers whose planned power the
    batteries must not cover, ``unsupported_measured_w`` the measured power of
    such consumers that SLEMS does not control right now.
    """
    ordered = sorted(consumers, key=lambda c: (c.priority, c.subentry_id))
    consumer_power = {c.subentry_id: 0.0 for c in ordered}

    # Consumers within their minimum runtime keep at least their running power;
    # forced ones (daily target) run at full power, the batteries or the grid
    # cover the deficit like any other load.
    for consumer in ordered:
        if consumer.forced:
            power = consumer.full_power_w
            if consumer.forced_max_w is not None:
                power = min(power, consumer.forced_max_w)
            consumer_power[consumer.subentry_id] = power
        elif consumer.must_stay_on:
            consumer_power[consumer.subentry_id] = consumer.minimum_running_power_w
    remaining = available_w - sum(consumer_power.values())
    unsupported_w = unsupported_measured_w + sum(
        power for subentry_id, power in consumer_power.items() if subentry_id in unsupported
    )

    charge_secured = battery is not None and _charge_secured(
        battery, settings, expected_surplus_wh
    )

    if (
        battery is not None
        and grid_charge_w > 0
        and not battery.is_full
        and grid_charge_w > remaining - settings.charge_grid_target_w
    ):
        power = min(grid_charge_w, battery.max_charge_w)
        limit = grid_import_limit_w
        if settings.peak_shaving:
            limit = min(limit, settings.peak_shaving_grid_limit_w)
        # remaining is the surplus (+) or deficit (−) before charging.
        power = min(power, max(0.0, limit + remaining))
        return Allocation(
            strategy=Strategy.GRID_CHARGE,
            battery_power_w=power,
            consumer_power_w=consumer_power,
            charge_secured=charge_secured,
        )

    if battery is not None and battery_export_w > 0 and remaining <= settings.charge_grid_target_w:
        return Allocation(
            strategy=Strategy.BATTERY_EXPORT,
            battery_power_w=-min(battery.max_discharge_w, battery_export_w + max(0.0, -remaining)),
            consumer_power_w=consumer_power,
            charge_secured=charge_secured,
        )

    max_export = max_discharge_export_w(settings, cap)
    discharge_target = min(settings.discharge_grid_target_w, max_export)
    cap_export = cap.export_w if cap is not None else 0.0
    if (
        battery is not None
        and (cap_export > 0 or (night_discharge_w and remaining <= settings.charge_grid_target_w))
        and not _peak_shaving_active(battery, settings)
    ):
        normal = max(0.0, discharge_target - remaining - unsupported_w)
        discharge = min(
            max(normal, night_discharge_w or 0.0, cap_export),
            max_export - remaining,
            battery.max_discharge_w,
        )
        if discharge > 0 or not cap_export:
            return Allocation(
                strategy=(
                    Strategy.FEED_IN_CAP
                    if cap_export > (night_discharge_w or 0.0)
                    else Strategy.NIGHT_DISCHARGE
                ),
                battery_power_w=-max(0.0, discharge),
                consumer_power_w=consumer_power,
                charge_secured=charge_secured,
            )
    if remaining < discharge_target:
        allocation = _cover_deficit(
            remaining, discharge_target, battery, settings, unsupported_w, discharge_limit_w
        )
        allocation.consumer_power_w = consumer_power
        allocation.charge_secured = charge_secured
        return allocation
    if remaining <= settings.charge_grid_target_w:
        return Allocation(
            strategy=Strategy.IDLE,
            consumer_power_w=consumer_power,
            charge_secured=charge_secured,
        )
    max_charge = 0.0 if battery is None or battery.is_full else battery.max_charge_w
    room_kept = charge_cap_w is not None and charge_cap_w < max_charge
    if room_kept:
        max_charge = max(0.0, charge_cap_w)
    cap_charge = 0.0
    capped = False
    if cap is not None:
        # Surplus above the limit: the planned part of the supporting
        # consumers, the batteries, the supporting consumers, the normal ones.
        over = max(0.0, remaining - cap.max_export_w)
        capped = over > 0
        support = [c for c in ordered if c.cap_mode is CapMode.SUPPORT]
        early = min(over, cap.support_w)
        left = over - early + _distribute(early, support, consumer_power)
        cap_charge = min(max_charge, left)
        left = _distribute(left - cap_charge, support, consumer_power)
        _distribute(left, [c for c in ordered if c.cap_mode is CapMode.NORMAL], consumer_power)
        remaining -= over
        max_charge = 0.0 if cap.hold_charging else max_charge - cap_charge
    remaining = max(0.0, remaining - settings.charge_grid_target_w)
    # Daily targets that are short on time take the surplus before the batteries.
    remaining = _distribute(remaining, [c for c in ordered if c.boost], consumer_power)

    if charge_secured:
        strategy = Strategy.SHARED
        battery_budget = remaining * settings.battery_share_when_secured_pct / 100
        if feed_in_limit_w is not None:
            room = max(0.0, remaining - feed_in_limit_w)
            if room < min(max_charge, battery_budget):
                strategy = Strategy.GRID_FRIENDLY
            max_charge = min(max_charge, room)
    else:
        # Without batteries the whole surplus goes to the consumers.
        strategy = Strategy.BATTERY_PRIORITY if battery is not None else Strategy.SELF_CONSUMPTION
        battery_budget = remaining
    battery_power = min(battery_budget, max_charge)
    consumer_budget = remaining - battery_power

    # With the feed-in cap, supporting consumers only take surplus above the limit.
    normal = ordered if cap is None else [c for c in ordered if c.cap_mode is not CapMode.SUPPORT]
    unused = _distribute(consumer_budget, normal, consumer_power)
    # What the consumers cannot take goes back to the batteries.
    battery_power = min(battery_power + unused, max_charge) + cap_charge
    if capped:
        strategy = Strategy.FEED_IN_CAP
    elif room_kept and battery_power >= max_charge - 1:
        strategy = Strategy.PRICE_ROOM

    return Allocation(
        strategy=strategy,
        battery_power_w=battery_power,
        consumer_power_w=consumer_power,
        charge_secured=charge_secured,
    )


def _charge_secured(
    battery: BatteryGroup,
    settings: AllocationSettings,
    expected_surplus_wh: float | None,
) -> bool:
    if battery.soc_pct < settings.battery_priority_soc_pct:
        return False
    if battery.is_full:
        return True
    if expected_surplus_wh is None:
        return False
    required = battery.energy_to_full_wh + settings.charge_secured_buffer_wh
    return expected_surplus_wh >= required


def _peak_shaving_active(battery: BatteryGroup, settings: AllocationSettings) -> bool:
    return settings.peak_shaving and battery.soc_pct <= settings.peak_shaving_soc_threshold_pct


def _cover_deficit(
    remaining_w: float,
    grid_target_w: float,
    battery: BatteryGroup | None,
    settings: AllocationSettings,
    unsupported_w: float = 0.0,
    limit_w: float | None = None,
) -> Allocation:
    if battery is None:
        return Allocation(strategy=Strategy.SELF_CONSUMPTION)
    if limit_w is not None and not _peak_shaving_active(battery, settings):
        discharge = max(0.0, grid_target_w - remaining_w - unsupported_w)
        return Allocation(
            strategy=Strategy.PRICE_HOLD,
            battery_power_w=-min(discharge, limit_w, battery.max_discharge_w),
        )
    if _peak_shaving_active(battery, settings):
        grid_import = -remaining_w
        discharge = max(0.0, grid_import - settings.peak_shaving_grid_limit_w)
        strategy = Strategy.PEAK_SHAVING
    else:
        discharge = max(0.0, grid_target_w - remaining_w - unsupported_w)
        strategy = Strategy.SELF_CONSUMPTION
    return Allocation(
        strategy=strategy, battery_power_w=-min(discharge, battery.max_discharge_w)
    )


def _distribute(
    budget: float,
    consumers: Iterable[ConsumerRequest],
    consumer_power: dict[str, float],
) -> float:
    """Hand out ``budget`` in order of priority; return what is left.

    Running on/off consumers come first: a power controlled consumer of higher
    priority can take less instead, while switching an on/off consumer off and
    on again costs a cycle (e.g. a compressor). The priority still decides who
    is switched on first.
    """
    consumers = sorted(
        consumers, key=lambda c: not (c.control_mode is ControlMode.SWITCH and c.running)
    )
    for consumer in consumers:
        if budget <= 0:
            break
        if consumer.must_stay_off:
            continue
        current = consumer_power[consumer.subentry_id]
        if consumer.control_mode is ControlMode.SWITCH:
            if current == 0 and budget >= consumer.nominal_power_w:
                consumer_power[consumer.subentry_id] = consumer.nominal_power_w
                budget -= consumer.nominal_power_w
            continue
        extra = min(budget, consumer.max_power_w - current)
        if current + extra < consumer.min_power_w:
            continue
        consumer_power[consumer.subentry_id] = current + extra
        budget -= extra
    return budget


def limit_discharge_export(
    planned_w: float, current_w: float, grid_w: float, max_export_w: float
) -> float:
    """Reduce a planned discharge so the grid export stays below the maximum.

    Uses the current (unfiltered) grid power: changing the battery power from
    ``current_w`` to ``planned_w`` changes the grid power by the difference.
    Charging is not affected.
    """
    if planned_w >= 0:
        return planned_w
    # export' = -(grid + planned - current) <= max_export
    lowest = current_w - grid_w - max_export_w
    return min(0.0, max(planned_w, lowest))


def remaining_pv_wh(forecast: PvForecast, now: datetime) -> float:
    """Forecast PV energy from ``now`` until the end of the local day."""
    end_of_day = dt_util.start_of_local_day(dt_util.as_local(now)) + timedelta(days=1)
    total = 0.0
    for start, wh in hourly(forecast).items():
        end = start + PV_PERIOD
        if end <= now or start >= end_of_day:
            continue
        # Pro rata share of the running period.
        covered = (min(end, end_of_day) - max(start, now)) / PV_PERIOD
        total += wh * covered
    return total


def pv_end_today(forecast: PvForecast, now: datetime) -> datetime | None:
    """End of the last hour with PV production today, None if already over."""
    end_of_day = dt_util.start_of_local_day(dt_util.as_local(now)) + timedelta(days=1)
    ends = [
        start + PV_PERIOD
        for start, wh in hourly(forecast).items()
        if wh > 0 and start < end_of_day and start + PV_PERIOD > now
    ]
    return max(ends) if ends else None


def expected_surplus_wh(
    forecast: PvForecast | None,
    now: datetime,
    load_w: float | None,
    consumption_forecast: Mapping[datetime, float] | None = None,
) -> float | None:
    """Expected PV surplus for the rest of today.

    With a consumption forecast: sum of the hourly surpluses (PV above
    consumption). Without: PV energy left minus ``load_w`` (assumed constant)
    until PV production ends.
    """
    if forecast is None:
        return None
    if consumption_forecast is not None:
        pv = hourly(forecast)
        consumption = hourly(consumption_forecast)
        end_of_day = dt_util.start_of_local_day(dt_util.as_local(now)) + timedelta(days=1)
        total = 0.0
        for start in pv:
            end = start + PV_PERIOD
            if end <= now or start >= end_of_day:
                continue
            covered = (end - max(start, now)) / PV_PERIOD
            total += max(0.0, pv[start] - consumption.get(start, 0.0)) * covered
        return total
    if load_w is None:
        return None
    end = pv_end_today(forecast, now)
    if end is None:
        return 0.0
    hours = (end - now).total_seconds() / 3600
    return remaining_pv_wh(forecast, now) - max(0.0, load_w) * hours
