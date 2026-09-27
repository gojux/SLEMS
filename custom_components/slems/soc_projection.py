"""Projected total state of charge and planned charging for today and tomorrow.

Hour by hour from now until the end of tomorrow, with the corrected PV
forecast and the consumption forecast, following the allocation in a
simplified way:

* Hours with PV surplus: planned charging. With grid friendly charging only
  the surplus above the feed-in limit, until the batteries are full plus the
  safety buffer (see ``grid_friendly``). Like the controller, the charging is
  planned again every hour from the projected state of charge, so charging
  held back (feed-in cap) is made up later; today's feed-in limit is the one
  the controller uses.
* Hours with a deficit: the batteries cover it. With import peak shaving at
  low state of charge only the import above the limit; with night discharge
  at least the planned night discharge, the extra part not below its target
  and not above the maximum grid export while discharging.
* Charge and discharge losses with the one-way efficiency; maximum charge and
  discharge power; the SoC window of the batteries (minimum and maximum SoC).

* Feed-in cap (see feed_in_cap): the surplus above the limit is charged in
  any case; charging below it only as long as the space the cap needs later
  stays free. From the planned export start the batteries feed in until they
  have that space; the night discharge target leaves it free.

Controllable consumers (they run on surplus), grid targets and batteries in
active cell balancing are left out.
"""

from __future__ import annotations

from collections.abc import Mapping
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from homeassistant.util import dt as dt_util

from .allocation import BatteryGroup
from .feed_in_cap import CapPlan
from .grid_friendly import feed_in_limit, planned_charging, remaining_surplus_by_hour
from .night_discharge import plan_night_discharge

PERIOD = timedelta(hours=1)


@dataclass(frozen=True)
class ProjectionSettings:
    grid_friendly_charging: bool
    # Reserve of the charge planning (grid friendly charging).
    charge_buffer_wh: float
    # Safety buffer of the night discharge target.
    night_buffer_wh: float
    peak_shaving: bool
    peak_shaving_grid_limit_w: float
    peak_shaving_soc_threshold_pct: float
    night_discharge: bool
    night_reserve_pct: float
    # Grid export caused by discharging (night discharge) at most.
    discharge_max_grid_export_w: float = math.inf


@dataclass
class SocProjection:
    """Per local hour start: SoC at the end of the hour, planned charge power and
    the expected grid power (+ import / − export, mean W over the projected part
    of the hour)."""

    soc_pct: dict[datetime, float] = field(default_factory=dict)
    planned_charge_w: dict[datetime, float] = field(default_factory=dict)
    grid_w: dict[datetime, float] = field(default_factory=dict)


def project_soc(
    now: datetime,
    battery: BatteryGroup,
    pv: Mapping[datetime, float],
    consumption: Mapping[datetime, float] | None,
    load_w: float | None,
    settings: ProjectionSettings,
    today_limit_w: float | None,
    today_extra_wh: float = 0.0,
    cap: CapPlan | None = None,
) -> SocProjection:
    """Project until the end of tomorrow.

    ``pv`` and ``consumption`` are hourly local buckets (Wh). Without a
    consumption forecast ``load_w`` is assumed to stay. ``today_extra_wh`` is
    charged today by batteries outside the group (it lowers the surplus left
    for the group). ``cap`` is the plan of the feed-in cap, None when off.
    """
    result = SocProjection()
    capacity = battery.capacity_wh
    efficiency = battery.charge_efficiency or 1.0
    if capacity <= 0:
        return result
    local_now = dt_util.as_local(now)
    today = local_now.date()
    end = dt_util.start_of_local_day(local_now) + timedelta(days=2)
    stored = battery.soc_pct / 100 * capacity
    full = battery.full_soc_pct / 100 * capacity

    def charge_plan(hour: datetime, start: datetime) -> dict[datetime, float]:
        day_end = dt_util.start_of_local_day(hour) + timedelta(days=1)
        by_hour = remaining_surplus_by_hour(pv, consumption, load_w, start, day_end)
        needed = max(0.0, full - stored) / efficiency + settings.charge_buffer_wh
        surplus = [(power, hours) for _, power, hours in by_hour]
        if hour.date() == today:
            needed += today_extra_wh
            limit = today_limit_w
        elif settings.grid_friendly_charging:
            limit = feed_in_limit(surplus, needed, battery.max_charge_w)
        else:
            limit = None
        charges = planned_charging(surplus, limit, battery.max_charge_w, needed)
        return {start: charge for (start, _, _), charge in zip(by_hour, charges, strict=True)}

    hour = local_now.replace(minute=0, second=0, microsecond=0)
    while hour < end:
        start = max(hour, local_now)
        share = (hour + PERIOD - start) / PERIOD
        pv_w = pv.get(hour, 0.0)
        load = consumption.get(hour, load_w or 0.0) if consumption is not None else load_w or 0.0
        hour_end = hour + PERIOD
        stored_before = stored
        # Highest stored energy that leaves the space the feed-in cap needs.
        allowed = full - cap.space_needed_at(hour_end) if cap is not None else full
        if pv_w > load:
            plan = charge_plan(hour, start)
            # The plan includes the buffer (it lowers the feed-in limit); only
            # what still fits into the batteries is charged.
            room = max(0.0, min(full, allowed) - stored)
            charge = min(plan.get(hour, 0.0), room / (share * efficiency))
            if cap is not None and hour in cap.hourly:
                charge = max(charge, cap.hourly[hour].absorbed_wh / share)
                charge = min(charge, max(0.0, full - stored) / (share * efficiency))
            result.planned_charge_w[hour] = charge
            stored = min(max(stored, full), stored + charge * share * efficiency)
        else:
            stored = _discharge(
                stored, load - pv_w, share, start, battery, pv, consumption, settings, cap
            )
        if (
            cap is not None
            and cap.export_start is not None
            and cap.next_block is not None
            and hour_end > cap.export_start
            and hour < cap.next_block.start
            and stored > allowed
        ):
            export_w = max(0.0, min(battery.max_discharge_w, cap.limit_w - max(0.0, pv_w - load)))
            floor = battery.min_soc_pct / 100 * capacity
            stored = max(floor, allowed, stored - export_w * share / efficiency)
        result.soc_pct[hour] = stored / capacity * 100
        # Battery AC power from the change of the stored energy (losses on the AC side).
        change = (stored - stored_before) / share
        battery_ac = change / efficiency if change > 0 else change * efficiency
        result.grid_w[hour] = load - pv_w + battery_ac
        hour += PERIOD
    return result


def _discharge(
    stored: float,
    deficit_w: float,
    share: float,
    start: datetime,
    battery: BatteryGroup,
    pv: Mapping[datetime, float],
    consumption: Mapping[datetime, float] | None,
    settings: ProjectionSettings,
    cap: CapPlan | None = None,
) -> float:
    """Stored energy after covering ``deficit_w`` for ``share`` of an hour."""
    capacity = battery.capacity_wh
    efficiency = battery.charge_efficiency or 1.0
    floor = battery.min_soc_pct / 100 * capacity
    soc = stored / capacity * 100
    if settings.peak_shaving and soc <= settings.peak_shaving_soc_threshold_pct:
        power = min(max(0.0, deficit_w - settings.peak_shaving_grid_limit_w), battery.max_discharge_w)
        return min(stored, max(floor, stored - power * share / efficiency))
    power = min(deficit_w, battery.max_discharge_w)
    after = min(stored, max(floor, stored - power * share / efficiency))
    if not settings.night_discharge or consumption is None:
        return after
    plan = plan_night_discharge(
        start,
        soc,
        capacity,
        efficiency,
        efficiency,
        pv,
        consumption,
        settings.night_reserve_pct,
        settings.night_buffer_wh,
        floor,
        (lambda moment: battery.full_soc_pct / 100 * capacity - cap.space_needed_at(moment))
        if cap is not None
        else None,
    )
    if plan is None:
        return after
    # As in the allocation: at least the night discharge; the part beyond the
    # deficit stops at the target.
    extra = min(
        max(0.0, plan.power_w - power),
        battery.max_discharge_w - power,
        settings.discharge_max_grid_export_w,
    )
    return max(min(after, plan.target_wh), after - extra * share / efficiency)
