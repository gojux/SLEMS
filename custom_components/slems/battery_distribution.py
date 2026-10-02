"""Distribution of the total battery power between several batteries.

Number of active batteries: each battery has a loss model
``loss(P) = fixed + linear * P + quadratic * P²`` (AC power P). A fixed loss
per running inverter favours few batteries at low power, the quadratic part
favours sharing at high power. The number with the lowest total loss is used;
the current number is kept while it is within ``KEEP_TOLERANCE`` of the best,
to avoid toggling around the break-even point.

Which batteries: when discharging the ones with the highest state of charge,
when charging the ones with the lowest. An active battery is replaced when it
is more than the rotation threshold below (discharging) or above (charging) the
best inactive one, but not more often than the minimum rotation interval.

Transitions are smooth: every battery has a weight that ramps towards 1
(selected) or 0 (not selected); the total power is split in proportion to
weight × maximum power, so the sum always matches. The ramp moves the power
with the configured rate (W/s) but takes at most the maximum ramp time, so
small powers switch quickly and large ones never take longer than the maximum.
If the weighted batteries cannot deliver the total, the others help at once.

A battery that is being disabled while discharging is *leaving*: it is no
longer selected, its weight is limited to the remaining fraction of
``LEAVE_RAMP_S`` (independent of how often the distribution runs) and it
never helps out.

Fast batteries first: if the batteries react at clearly different speeds
(learned response times, the slowest at least ``SPEED_RATIO`` times and
``MIN_SPEED_GAP_S`` slower than the fastest), the distribution above is made
for a *settled* total that follows the total with the slow batteries'
response time, and the fastest battery takes the difference at once. Changes
are first absorbed by the battery that can follow them, then move over to
the efficient split. No battery works against the direction of the total;
what the fast battery cannot take, the others take at once.

A battery due for its regular full charge (see full_charge) gets the charge
power first; the others share the rest as above. When discharging it is
spared while all others have more than ``SPARE_OTHERS_MIN_SOC_PCT`` and can
deliver the power.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from .full_charge import SPARE_OTHERS_MIN_SOC_PCT

KEEP_TOLERANCE = 1.05
# Fast batteries first only with clearly different response times.
SPEED_RATIO = 2.0
# Learned times of similar batteries differ by a second or two (noise).
MIN_SPEED_GAP_S = 3.0
# Below this total loss the loss model gives no reason to run more batteries.
MIN_RELEVANT_LOSS_W = 1.0
# Ramp-out of a disabled battery; with the command transfer (about 1 s for two
# Venus) the handover completes within 5 s.
LEAVE_RAMP_S = 3.5
FULL_SOC_PCT = 99.5
EMPTY_SOC_PCT = 0.5


@dataclass(frozen=True)
class LossModel:
    """Conversion losses of one battery as a function of the AC power (W)."""

    fixed_w: float = 15.0
    linear: float = 0.04
    quadratic_per_w: float = 1e-5

    def loss(self, power_w: float) -> float:
        power = abs(power_w)
        if power == 0:
            return 0.0
        return self.fixed_w + self.linear * power + self.quadratic_per_w * power**2


@dataclass(frozen=True)
class BatteryUnit:
    """A battery that can take part in the distribution."""

    battery_id: str
    soc_pct: float
    max_charge_w: float
    max_discharge_w: float
    loss_model: LossModel = field(default_factory=LossModel)
    # Being disabled: remaining share of the ramp-out (1 -> 0), None otherwise.
    leaving_fraction: float | None = None
    # Learned time until the grid meter shows a command (None: not learned).
    response_s: float | None = None

    @property
    def leaving(self) -> bool:
        return self.leaving_fraction is not None

    def max_power_w(self, charging: bool) -> float:
        return self.max_charge_w if charging else self.max_discharge_w

    def can(self, charging: bool) -> bool:
        if charging:
            return self.soc_pct < FULL_SOC_PCT and self.max_charge_w > 0
        return self.soc_pct > EMPTY_SOC_PCT and self.max_discharge_w > 0


@dataclass(frozen=True)
class RotationSettings:
    soc_threshold_pct: float
    min_interval_s: float
    # Ramp speed in W/s and upper limit of the ramp duration.
    ramp_rate_w_per_s: float
    ramp_max_s: float


@dataclass
class Distribution:
    """Power per battery (+charge / -discharge) and the selected batteries."""

    power_w: dict[str, float]
    selected: tuple[str, ...]


class BatteryDistributor:
    """Stateful distributor (selection, rotation time, ramp weights)."""

    def __init__(self) -> None:
        self._selected: tuple[str, ...] = ()
        self._charging: bool | None = None
        self._last_rotation: float | None = None
        self._weights: dict[str, float] = {}
        self._last_call: float | None = None
        # Total the slow batteries follow (fast batteries first), None: not in use.
        self._settled: float | None = None

    def distribute(
        self,
        total_w: float,
        units: Sequence[BatteryUnit],
        settings: RotationSettings,
        now: float,
        full_charge: str | None = None,
    ) -> Distribution:
        """Split ``total_w`` (+charge / -discharge) between ``units``.

        ``full_charge`` is the battery due for its full charge: charged first,
        spared when discharging while the others can do it.
        """
        elapsed = 0.0 if self._last_call is None else max(0.0, now - self._last_call)
        self._last_call = now
        fast, slow_s = _fast_battery(units, total_w)
        if fast is None:
            self._settled = None
            return self._distribute_all(total_w, units, settings, now, elapsed, full_charge)
        settled = total_w if self._settled is None else self._settled
        if settled * total_w < 0:
            # The direction changed: the slow batteries leave it at once.
            settled = 0.0
        settled += (total_w - settled) * min(1.0, elapsed / slow_s)
        self._settled = settled
        result = self._distribute_all(settled, units, settings, now, elapsed, full_charge)
        _shift_to_fast(result, total_w - settled, fast, units, total_w)
        return result

    def _distribute_all(
        self,
        total_w: float,
        units: Sequence[BatteryUnit],
        settings: RotationSettings,
        now: float,
        elapsed: float,
        full_charge: str | None,
    ) -> Distribution:
        unit = next((u for u in units if u.battery_id == full_charge), None)
        if unit is not None and total_w > 0 and unit.can(True):
            first = min(total_w, unit.max_charge_w)
            others = [u for u in units if u is not unit]
            result = self._distribute(total_w - first, others, settings, now, elapsed)
            result.power_w[unit.battery_id] = first
            return result
        if unit is not None and total_w < 0:
            others = [u for u in units if u is not unit and u.can(False) and not u.leaving]
            if (
                others
                and all(u.soc_pct > SPARE_OTHERS_MIN_SOC_PCT for u in others)
                and sum(u.max_discharge_w for u in others) >= -total_w
            ):
                result = self._distribute(
                    total_w, [u for u in units if u is not unit], settings, now, elapsed
                )
                result.power_w[unit.battery_id] = 0.0
                return result
        return self._distribute(total_w, units, settings, now, elapsed)

    def _distribute(
        self,
        total_w: float,
        units: Sequence[BatteryUnit],
        settings: RotationSettings,
        now: float,
        elapsed: float,
    ) -> Distribution:
        power = abs(total_w)
        leaving = {u.battery_id: u.leaving_fraction for u in units if u.leaving}
        if total_w == 0 or not units:
            self._ramp(elapsed, settings, set(self._selected), power, leaving)
            return Distribution({u.battery_id: 0.0 for u in units}, self._selected)

        charging = total_w > 0
        candidates = [u for u in units if u.can(charging) and not u.leaving]
        if not candidates:
            return Distribution({u.battery_id: 0.0 for u in units}, ())

        count = self._choose_count(power, candidates, charging)
        self._select(candidates, count, charging, settings, now)
        self._ramp(elapsed, settings, set(self._selected), power, leaving)
        ramping_out = [u for u in units if u.leaving and u.can(charging)]
        split = self._split(power, candidates, ramping_out, charging)
        sign = 1 if charging else -1
        return Distribution(
            {
                u.battery_id: sign * power if (power := split.get(u.battery_id, 0.0)) else 0.0
                for u in units
            },
            self._selected,
        )

    # --- number of batteries ------------------------------------------------

    def _choose_count(
        self, power: float, candidates: Sequence[BatteryUnit], charging: bool
    ) -> int:
        preferred = _ordered(candidates, charging)
        losses: dict[int, float] = {}
        for count in range(1, len(preferred) + 1):
            chosen = preferred[:count]
            capacity = sum(u.max_power_w(charging) for u in chosen)
            if capacity < power and count < len(preferred):
                continue
            losses[count] = sum(
                u.loss_model.loss(power * u.max_power_w(charging) / capacity)
                for u in chosen
            )
        # Equal losses (e.g. a battery that reports no losses): fewer batteries.
        best = min(losses, key=lambda count: (round(losses[count]), count))
        if losses[best] < MIN_RELEVANT_LOSS_W:
            return best
        candidate_ids = {u.battery_id for u in candidates}
        current = sum(1 for b in self._selected if b in candidate_ids)
        if (
            self._charging == charging
            and current in losses
            and losses[current] <= losses[best] * KEEP_TOLERANCE
        ):
            return current
        return best

    # --- which batteries ------------------------------------------------------

    def _select(
        self,
        candidates: Sequence[BatteryUnit],
        count: int,
        charging: bool,
        settings: RotationSettings,
        now: float,
    ) -> None:
        by_id = {u.battery_id: u for u in candidates}
        preferred = _ordered(candidates, charging)
        if self._charging != charging:
            # New direction: start with the preferred batteries.
            self._charging = charging
            self._selected = tuple(u.battery_id for u in preferred[:count])
            return

        selected = [b for b in self._selected if b in by_id]
        # Grow or shrink with the preferred order.
        for unit in preferred:
            if len(selected) >= count:
                break
            if unit.battery_id not in selected:
                selected.append(unit.battery_id)
        while len(selected) > count:
            worst = max(selected, key=lambda b: _rank(by_id[b], charging))
            selected.remove(worst)

        rotation_due = (
            self._last_rotation is None
            or now - self._last_rotation >= settings.min_interval_s
        )
        inactive = [u for u in preferred if u.battery_id not in selected]
        if rotation_due and selected and inactive:
            worst = max(selected, key=lambda b: _rank(by_id[b], charging))
            best_inactive = inactive[0]
            gap = abs(by_id[worst].soc_pct - best_inactive.soc_pct)
            better = _rank(best_inactive, charging) < _rank(by_id[worst], charging)
            if better and gap > settings.soc_threshold_pct:
                selected[selected.index(worst)] = best_inactive.battery_id
                self._last_rotation = now
        self._selected = tuple(selected)

    # --- smooth transition ------------------------------------------------------

    def _ramp(
        self,
        elapsed: float,
        settings: RotationSettings,
        targets: set[str],
        power: float,
        leaving: dict[str, float],
    ) -> None:
        # Weight change per call: moving the whole power takes
        # power / rate seconds, but never longer than the maximum ramp time.
        by_rate = (
            elapsed * settings.ramp_rate_w_per_s / power
            if settings.ramp_rate_w_per_s > 0 and power > 0
            else 1.0
        )
        by_duration = elapsed / settings.ramp_max_s if settings.ramp_max_s > 0 else 1.0
        step = max(by_rate, by_duration)
        for battery_id in set(self._weights) | targets:
            target = 1.0 if battery_id in targets else 0.0
            weight = self._weights.get(battery_id, 0.0)
            if weight < target:
                weight = min(target, weight + step)
            else:
                weight = max(target, weight - step)
            if battery_id in leaving:
                weight = min(weight, leaving[battery_id])
            if abs(weight - target) < 1e-9:
                weight = target
            self._weights[battery_id] = weight

    def _split(
        self,
        power: float,
        candidates: Sequence[BatteryUnit],
        ramping_out: Sequence[BatteryUnit],
        charging: bool,
    ) -> dict[str, float]:
        result = {u.battery_id: 0.0 for u in [*candidates, *ramping_out]}
        remaining = power
        # First the weighted (selected or ramping) batteries, in proportion.
        weighted = [
            (u, self._weights.get(u.battery_id, 0.0) * u.max_power_w(charging))
            for u in [*candidates, *ramping_out]
        ]
        weighted = [(u, w) for u, w in weighted if w > 0]
        total_weight = sum(w for _, w in weighted)
        if total_weight > 0:
            # Water filling: batteries at their limit pass the rest on.
            active = list(weighted)
            while active and remaining > 1e-6:
                weight_sum = sum(w for _, w in active)
                capped = []
                for unit, weight in active:
                    share = remaining * weight / weight_sum
                    room = unit.max_power_w(charging) - result[unit.battery_id]
                    if share >= room:
                        capped.append((unit, room))
                if not capped:
                    for unit, weight in active:
                        result[unit.battery_id] += remaining * weight / weight_sum
                    remaining = 0.0
                    break
                for unit, room in capped:
                    result[unit.battery_id] += room
                    remaining -= room
                active = [(u, w) for u, w in active if all(u is not c for c, _ in capped)]
        # Not enough: the other batteries help immediately, preferred first.
        for unit in _ordered(candidates, charging):
            if remaining <= 1e-6:
                break
            room = unit.max_power_w(charging) - result[unit.battery_id]
            take = min(room, remaining)
            result[unit.battery_id] += take
            remaining -= take
        return result


def _fast_battery(units: Sequence[BatteryUnit], total_w: float) -> tuple[BatteryUnit | None, float]:
    """The battery to take changes first and the slow batteries' response time.

    None if the batteries do not differ enough in speed (or a time is not learned).
    """
    charging = total_w > 0
    active = [u for u in units if not u.leaving and (total_w == 0 or u.can(charging))]
    if len(active) < 2 or any(u.response_s is None for u in active):
        return None, 0.0
    fast = min(active, key=lambda u: (u.response_s, u.battery_id))
    slow_s = max(u.response_s for u in active)
    if slow_s < fast.response_s * SPEED_RATIO or slow_s - fast.response_s < MIN_SPEED_GAP_S:
        return None, 0.0
    return fast, slow_s


def _shift_to_fast(
    result: Distribution,
    delta_w: float,
    fast: BatteryUnit,
    units: Sequence[BatteryUnit],
    total_w: float,
) -> None:
    """Give the fast battery ``delta_w`` on top; never against the direction of the total."""
    if abs(delta_w) < 1e-6:
        return

    def bounds(unit: BatteryUnit) -> tuple[float, float]:
        if total_w > 0:
            return 0.0, unit.max_charge_w if unit.can(True) else 0.0
        if total_w < 0:
            return -(unit.max_discharge_w if unit.can(False) else 0.0), 0.0
        return 0.0, 0.0

    power = result.power_w
    rest = delta_w
    order = [fast, *[u for u in units if u is not fast and not u.leaving]]
    for unit in order:
        low, high = bounds(unit)
        current = power.get(unit.battery_id, 0.0)
        target = min(high, max(low, current + rest))
        power[unit.battery_id] = target
        rest -= target - current
        if abs(rest) < 1e-6:
            break


def _rank(unit: BatteryUnit, charging: bool) -> float:
    """Lower is preferred: lowest SoC when charging, highest when discharging."""
    return unit.soc_pct if charging else -unit.soc_pct


def _ordered(units: Sequence[BatteryUnit], charging: bool) -> list[BatteryUnit]:
    return sorted(units, key=lambda u: (_rank(u, charging), u.battery_id))

