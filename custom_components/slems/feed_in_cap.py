"""Feed-in cap: keep the export at the grid connection point below a limit.

The PV system may only feed in a share of its peak power (``limit_w``); the
inverter curtails everything above it. Instead of losing that energy, the
batteries store it. For this they need enough free space when the PV surplus
exceeds the limit (a *peak block*), and they must be able to charge fast
enough.

The plan is calculated in steps of ``STEP`` from now until the end of
tomorrow, with the native periods of the PV forecast (15/30/60 min, so short
peaks are not averaged away) and the hourly consumption forecast:

* Per step the surplus above the limit (``excess``) is taken by the consumers
  counted for the cap first, then by the batteries up to their charge power;
  what is left is curtailed unless consumers set to take it instead of curtailing do.
* The free space the batteries need at each moment is calculated backwards
  over all steps, so several peaks per day and peaks on both days are covered:
  ``need(t) = max(0, need(t + 1) + absorbed(t) · (1 + buffer) − drained(t))``.
  ``drained`` is the energy the batteries deliver for the house in a step with
  a deficit (cloud dip, evening, night); a surplus below the limit drains
  nothing. The minimum buffer is added once per peak block.
* Charging with surplus below the limit is only allowed while the free space
  stays above the need (``hold_charging`` otherwise).
* If the free space cannot be reached by the house consumption alone, battery
  energy is fed in before the next peak block, as late as possible: it is to
  be finished ``EXPORT_MARGIN`` before the block, planned with
  ``EXPORT_USABLE_SHARE`` of the possible export power, and never above the
  limit.

Energies of the batteries (space, need) are stored energy; the charge and
discharge losses are applied with the one-way efficiency. Consumers, excess
and export are AC energy.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import math

from homeassistant.util import dt as dt_util

from .allocation import BatteryGroup
from .pv_forecast import power_lookup

STEP = timedelta(minutes=15)
STEP_H = STEP / timedelta(hours=1)
HOUR = timedelta(hours=1)
# Blocks with a shorter gap in between count as one peak.
BLOCK_MERGE_GAP = timedelta(hours=1)
# The export before a peak is to be finished this long before it ...
EXPORT_MARGIN = timedelta(hours=1)
# ... and is planned with this share of the possible export power.
EXPORT_USABLE_SHARE = 0.7
# The export is kept this far below the limit (meter noise, control delay).
CAP_MARGIN_W = 100.0
# Energies below this are not reported as a problem.
PROBLEM_TOLERANCE_WH = 100.0
# Charging below the limit is held once the spare space is below this.
HOLD_MARGIN_WH = 50.0
# Automatic buffer: recorded days needed and quantile of the underestimation.
AUTO_BUFFER_MIN_DAYS = 14
AUTO_BUFFER_QUANTILE = 0.8


@dataclass(frozen=True)
class CapSettings:
    limit_w: float
    # Buffer on the energy to absorb, in % (may be negative).
    buffer_pct: float
    # Added once per peak block (stored energy).
    min_buffer_wh: float
    # Factor on the PV forecast instead of the buffer (automatic buffer).
    pv_factor: float = 1.0


@dataclass(frozen=True)
class PeakBlock:
    """Steps with a surplus above the limit (short gaps included)."""

    start: datetime
    end: datetime
    # Surplus above the limit (AC), before consumers.
    excess_wh: float
    # Part the batteries take (AC).
    absorbed_wh: float
    # Part neither the batteries nor any consumer can take (AC).
    curtailed_wh: float


@dataclass(frozen=True)
class HourCap:
    excess_wh: float = 0.0
    absorbed_wh: float = 0.0
    curtailed_wh: float = 0.0


@dataclass
class CapPlan:
    limit_w: float
    blocks: list[PeakBlock] = field(default_factory=list)
    # Step start -> free space the batteries need at that moment (stored Wh).
    need: list[tuple[datetime, float]] = field(default_factory=list)
    free_now_wh: float = 0.0
    # Free space needed at the start of the next peak block (stored Wh).
    required_space_wh: float = 0.0
    # Battery energy to feed in before the next block (AC).
    export_needed_wh: float = 0.0
    # Export possible until the next block at full power (AC).
    export_possible_wh: float = 0.0
    # Latest moment to start the export and the moment it should be done.
    export_start: datetime | None = None
    export_until: datetime | None = None
    # Planned export power right now (AC); 0 before ``export_start``.
    export_power_w: float = 0.0
    # Charging with surplus below the limit would take space needed later.
    hold_charging: bool = False
    # More space needed than the batteries have (above the minimum SoC).
    battery_too_small: bool = False
    # The forecast fits, the full buffer does not (a note, not a problem).
    buffer_short: bool = False
    # Power of the consumers that take surplus above the limit (counted and
    # instead of curtailing).
    consumers_w: float = 0.0
    # Local hour start -> Wh from now on (day chart, SoC projection).
    hourly: dict[datetime, HourCap] = field(default_factory=dict)
    # Same per half hour (day chart).
    half_hourly: dict[datetime, HourCap] = field(default_factory=dict)

    @property
    def next_block(self) -> PeakBlock | None:
        return self.blocks[0] if self.blocks else None

    @property
    def curtailed_wh(self) -> float:
        return sum(block.curtailed_wh for block in self.blocks)

    @property
    def too_late(self) -> bool:
        """Not enough time or power left to feed in before the next block."""
        return self.export_needed_wh - self.export_possible_wh > PROBLEM_TOLERANCE_WH

    @property
    def problems(self) -> list[str]:
        result = []
        if self.battery_too_small:
            result.append("battery_too_small")
        if self.too_late:
            result.append("too_late")
        if self.curtailed_wh > PROBLEM_TOLERANCE_WH:
            result.append("charge_power_too_low")
        return result

    def day_blocks(self) -> list[PeakBlock]:
        """Blocks on the local day of the next block."""
        if not self.blocks:
            return []
        day = dt_util.as_local(self.blocks[0].start).date()
        return [b for b in self.blocks if dt_util.as_local(b.start).date() == day]

    def space_needed_at(self, moment: datetime) -> float:
        """Free space needed at ``moment`` (the need of the step it falls in)."""
        starts = [start for start, _ in self.need]
        index = bisect_right(starts, moment) - 1
        if index < 0:
            return self.need[0][1] if self.need else 0.0
        return self.need[index][1]


def auto_buffer(days: Mapping[str, Mapping[str, float]]) -> tuple[float | None, int]:
    """Buffer from the recorded PV forecasts: (factor − 1 or None, days used).

    Only days on which more was produced than forecast count; the buffer is
    the ``AUTO_BUFFER_QUANTILE`` quantile of their underestimation. None until
    ``AUTO_BUFFER_MIN_DAYS`` complete days are recorded.
    """
    complete = [
        (entry["forecast_wh"], entry["actual_wh"])
        for entry in days.values()
        if entry.get("actual_wh") is not None and entry.get("forecast_wh")
    ]
    if len(complete) < AUTO_BUFFER_MIN_DAYS:
        return None, len(complete)
    under = sorted(actual / forecast - 1 for forecast, actual in complete if actual > forecast)
    if not under:
        return 0.0, len(complete)
    index = max(0, math.ceil(AUTO_BUFFER_QUANTILE * len(under)) - 1)
    return under[index], len(complete)


@dataclass
class _Step:
    start: datetime
    hours: float
    surplus_w: float
    excess_w: float = 0.0
    absorbed_w: float = 0.0
    curtailed_w: float = 0.0
    drained_w: float = 0.0


def plan_cap(
    now: datetime,
    battery: BatteryGroup,
    max_charge_w: float,
    max_discharge_w: float,
    pv: Mapping[datetime, float],
    consumption: Mapping[datetime, float],
    settings: CapSettings,
    counted_consumers_w: float = 0.0,
    emergency_consumers_w: float = 0.0,
) -> CapPlan:
    """Plan the feed-in cap from ``now`` until the end of tomorrow.

    ``pv`` are the native forecast periods (Wh), ``consumption`` hourly local
    buckets (Wh). ``max_charge_w`` / ``max_discharge_w`` are the powers of
    the batteries apart from the SoC window.
    """
    plan = CapPlan(limit_w=settings.limit_w, consumers_w=counted_consumers_w + emergency_consumers_w)
    capacity = battery.capacity_wh
    if capacity <= 0:
        return plan
    efficiency = battery.charge_efficiency or 1.0
    local_now = dt_util.as_local(now)
    end = dt_util.start_of_local_day(local_now) + timedelta(days=2)
    pv_power = power_lookup(pv)
    limit = settings.limit_w

    steps: list[_Step] = []
    start = local_now.replace(minute=local_now.minute // 15 * 15, second=0, microsecond=0)
    while start < end:
        begin = max(start, local_now)
        hours = (start + STEP - begin) / HOUR
        hour = start.replace(minute=0)
        middle = start + STEP / 2
        surplus = pv_power(middle) * settings.pv_factor - consumption.get(hour, 0.0)
        step = _Step(start, hours, surplus)
        if surplus > limit:
            step.excess_w = surplus - limit
            over = max(0.0, step.excess_w - counted_consumers_w)
            step.absorbed_w = min(over, max_charge_w)
            step.curtailed_w = max(0.0, over - step.absorbed_w - emergency_consumers_w)
        elif surplus < 0:
            step.drained_w = min(-surplus, max_discharge_w)
        steps.append(step)
        start += STEP

    for step in steps:
        hour = step.start.replace(minute=0)
        half = step.start.replace(minute=step.start.minute // 30 * 30)
        for buckets, key in ((plan.hourly, hour), (plan.half_hourly, half)):
            previous = buckets.get(key, HourCap())
            buckets[key] = HourCap(
                previous.excess_wh + step.excess_w * step.hours,
                previous.absorbed_wh + step.absorbed_w * step.hours,
                previous.curtailed_wh + step.curtailed_w * step.hours,
            )
    plan.blocks = _blocks(steps)
    block_ends = {block.end for block in plan.blocks}

    usable = max(0.0, battery.full_soc_pct - battery.min_soc_pct) / 100 * capacity
    buffer = 1 + settings.buffer_pct / 100
    need = bare = 0.0
    needs: list[float] = []
    for step in reversed(steps):
        drained = step.drained_w * step.hours / efficiency
        stored = step.absorbed_w * step.hours * efficiency
        absorbed = stored * buffer
        if step.start + STEP in block_ends and step.absorbed_w > 0:
            absorbed += settings.min_buffer_wh
        need = max(0.0, need + absorbed - drained)
        # The same without buffers: does the forecast itself fit?
        bare = max(0.0, bare + stored - drained)
        if bare > usable + PROBLEM_TOLERANCE_WH:
            plan.battery_too_small = True
        elif need > usable + PROBLEM_TOLERANCE_WH:
            plan.buffer_short = True
        need = min(need, usable)
        bare = min(bare, usable)
        needs.append(need)
    if plan.battery_too_small:
        plan.buffer_short = False
    needs.reverse()
    plan.need = [(step.start, value) for step, value in zip(steps, needs, strict=True)]
    if not steps:
        return plan

    plan.free_now_wh = max(0.0, battery.full_soc_pct - battery.soc_pct) / 100 * capacity
    plan.hold_charging = plan.free_now_wh - needs[0] <= HOLD_MARGIN_WH and needs[0] > 0
    block = plan.next_block
    if block is None:
        return plan
    plan.required_space_wh = plan.space_needed_at(block.start)
    missing = max(0.0, needs[0] - plan.free_now_wh)
    plan.export_needed_wh = missing * efficiency
    if plan.export_needed_wh <= 0:
        return plan
    _plan_export(plan, steps, block.start, local_now, max_discharge_w, limit)
    return plan


def _blocks(steps: Sequence[_Step]) -> list[PeakBlock]:
    blocks: list[PeakBlock] = []
    current: list[_Step] = []

    def close() -> None:
        if current:
            blocks.append(
                PeakBlock(
                    start=current[0].start,
                    end=current[-1].start + STEP,
                    excess_wh=sum(s.excess_w * s.hours for s in current),
                    absorbed_wh=sum(s.absorbed_w * s.hours for s in current),
                    curtailed_wh=sum(s.curtailed_w * s.hours for s in current),
                )
            )

    last_end: datetime | None = None
    for step in steps:
        if step.excess_w <= 0:
            continue
        if last_end is not None and step.start - last_end > BLOCK_MERGE_GAP:
            close()
            current = []
        current.append(step)
        last_end = step.start + STEP
    close()
    return blocks


def _plan_export(
    plan: CapPlan,
    steps: Sequence[_Step],
    block_start: datetime,
    now: datetime,
    max_discharge_w: float,
    limit_w: float,
) -> None:
    """Export power, latest start and the export possible before the block."""
    before = [step for step in steps if step.start < block_start]

    def export_w(step: _Step) -> float:
        deficit = max(0.0, -step.surplus_w)
        surplus = max(0.0, step.surplus_w)
        return max(0.0, min(max_discharge_w - deficit, limit_w - CAP_MARGIN_W - surplus))

    plan.export_possible_wh = sum(export_w(step) * step.hours for step in before)
    until = max(now, block_start - EXPORT_MARGIN)
    plan.export_until = until
    needed = plan.export_needed_wh
    collected = 0.0
    start = now
    for step in reversed([step for step in before if step.start < until]):
        collected += EXPORT_USABLE_SHARE * export_w(step) * step.hours
        if collected >= needed:
            start = max(now, step.start)
            break
    plan.export_start = start
    if now < start or not before:
        return
    hours = (until - now) / HOUR
    current = export_w(before[0])
    plan.export_power_w = current if hours < STEP_H else min(current, needed / hours)
