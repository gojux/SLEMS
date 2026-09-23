"""Distribution of the available power between batteries and consumers.

``available_w`` is the power SLEMS can distribute: the (filtered) grid export
plus the power currently drawn by everything SLEMS controls (batteries and
unblocked controllable consumers). Positive = surplus, negative = deficit.

Consumers that must keep running (minimum runtime) keep their power first.
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

Night discharge (optional, see night_discharge): outside a surplus the
batteries discharge at least with the planned night power, ignoring the
discharge grid target but still respecting the maximum grid export.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from homeassistant.util import dt as dt_util

from .const import ControlMode
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

    @property
    def energy_to_full_wh(self) -> float:
        """Energy that must be charged to fill the batteries, losses included."""
        missing = max(0.0, 100.0 - self.soc_pct) / 100 * self.capacity_wh
        return missing / self.charge_efficiency

    @property
    def is_full(self) -> bool:
        return self.soc_pct >= FULL_SOC_PCT


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
    # Minimum pause not yet elapsed: must stay off.
    must_stay_off: bool = False

    @property
    def minimum_running_power_w(self) -> float:
        if self.control_mode is ControlMode.SWITCH:
            return self.nominal_power_w
        return self.min_power_w


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
) -> Allocation:
    """Distribute ``available_w`` between batteries and consumers."""
    ordered = sorted(consumers, key=lambda c: (c.priority, c.subentry_id))
    consumer_power = {c.subentry_id: 0.0 for c in ordered}

    # Consumers within their minimum runtime keep at least their running power.
    for consumer in ordered:
        if consumer.must_stay_on:
            consumer_power[consumer.subentry_id] = consumer.minimum_running_power_w
    remaining = available_w - sum(consumer_power.values())

    charge_secured = battery is not None and _charge_secured(
        battery, settings, expected_surplus_wh
    )

    discharge_target = min(
        settings.discharge_grid_target_w, settings.discharge_max_grid_export_w
    )
    if (
        night_discharge_w
        and battery is not None
        and remaining <= settings.charge_grid_target_w
        and not _peak_shaving_active(battery, settings)
    ):
        normal = max(0.0, discharge_target - remaining)
        discharge = min(
            max(normal, night_discharge_w),
            settings.discharge_max_grid_export_w - remaining,
            battery.max_discharge_w,
        )
        return Allocation(
            strategy=Strategy.NIGHT_DISCHARGE,
            battery_power_w=-max(0.0, discharge),
            consumer_power_w=consumer_power,
            charge_secured=charge_secured,
        )
    if remaining < discharge_target:
        allocation = _cover_deficit(remaining, discharge_target, battery, settings)
        allocation.consumer_power_w = consumer_power
        allocation.charge_secured = charge_secured
        return allocation
    if remaining <= settings.charge_grid_target_w:
        return Allocation(
            strategy=Strategy.IDLE,
            consumer_power_w=consumer_power,
            charge_secured=charge_secured,
        )
    remaining -= settings.charge_grid_target_w

    max_charge = 0.0 if battery is None or battery.is_full else battery.max_charge_w
    if charge_secured:
        strategy = Strategy.SHARED
        battery_budget = remaining * settings.battery_share_when_secured_pct / 100
    else:
        strategy = Strategy.BATTERY_PRIORITY
        battery_budget = remaining
    battery_power = min(battery_budget, max_charge)
    consumer_budget = remaining - battery_power

    unused = _distribute(consumer_budget, ordered, consumer_power)
    # What the consumers cannot take goes back to the batteries.
    battery_power = min(battery_power + unused, max_charge)

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
) -> Allocation:
    if battery is None:
        return Allocation(strategy=Strategy.SELF_CONSUMPTION)
    if _peak_shaving_active(battery, settings):
        grid_import = -remaining_w
        discharge = max(0.0, grid_import - settings.peak_shaving_grid_limit_w)
        strategy = Strategy.PEAK_SHAVING
    else:
        discharge = grid_target_w - remaining_w
        strategy = Strategy.SELF_CONSUMPTION
    return Allocation(
        strategy=strategy, battery_power_w=-min(discharge, battery.max_discharge_w)
    )


def _distribute(
    budget: float,
    consumers: Iterable[ConsumerRequest],
    consumer_power: dict[str, float],
) -> float:
    """Hand out ``budget`` in order of priority; return what is left."""
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
    end_of_day = dt_util.start_of_local_day(now) + timedelta(days=1)
    total = 0.0
    for start, wh in forecast.items():
        end = start + PV_PERIOD
        if end <= now or start >= end_of_day:
            continue
        # Pro rata share of the running period.
        covered = (min(end, end_of_day) - max(start, now)) / PV_PERIOD
        total += wh * covered
    return total


def pv_end_today(forecast: PvForecast, now: datetime) -> datetime | None:
    """End of the last period with PV production today, None if already over."""
    end_of_day = dt_util.start_of_local_day(now) + timedelta(days=1)
    ends = [
        start + PV_PERIOD
        for start, wh in forecast.items()
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
        end_of_day = dt_util.start_of_local_day(now) + timedelta(days=1)
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
