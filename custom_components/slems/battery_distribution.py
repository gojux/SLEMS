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
(selected) or 0 (not selected) within the ramp time; the total power is split
in proportion to weight × maximum power, so the sum always matches. If the
weighted batteries cannot deliver the total, the remaining ones help at once.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

KEEP_TOLERANCE = 1.05
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
    ramp_s: float


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

    def distribute(
        self,
        total_w: float,
        units: Sequence[BatteryUnit],
        settings: RotationSettings,
        now: float,
    ) -> Distribution:
        """Split ``total_w`` (+charge / -discharge) between ``units``."""
        elapsed = 0.0 if self._last_call is None else max(0.0, now - self._last_call)
        self._last_call = now
        if total_w == 0 or not units:
            self._ramp(elapsed, settings, set(self._selected))
            return Distribution({u.battery_id: 0.0 for u in units}, self._selected)

        charging = total_w > 0
        power = abs(total_w)
        candidates = [u for u in units if u.can(charging)]
        if not candidates:
            return Distribution({u.battery_id: 0.0 for u in units}, ())

        count = self._choose_count(power, candidates, charging)
        self._select(candidates, count, charging, settings, now)
        self._ramp(elapsed, settings, set(self._selected))
        split = self._split(power, candidates, charging)
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
        best = min(losses, key=losses.get)
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

    def _ramp(self, elapsed: float, settings: RotationSettings, targets: set[str]) -> None:
        step = 1.0 if settings.ramp_s <= 0 else elapsed / settings.ramp_s
        for battery_id in set(self._weights) | targets:
            target = 1.0 if battery_id in targets else 0.0
            weight = self._weights.get(battery_id, 0.0)
            if weight < target:
                weight = min(target, weight + step)
            else:
                weight = max(target, weight - step)
            self._weights[battery_id] = weight

    def _split(
        self, power: float, candidates: Sequence[BatteryUnit], charging: bool
    ) -> dict[str, float]:
        result = {u.battery_id: 0.0 for u in candidates}
        remaining = power
        # First the weighted (selected or ramping) batteries, in proportion.
        weighted = [
            (u, self._weights.get(u.battery_id, 0.0) * u.max_power_w(charging))
            for u in candidates
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


def _rank(unit: BatteryUnit, charging: bool) -> float:
    """Lower is preferred: lowest SoC when charging, highest when discharging."""
    return unit.soc_pct if charging else -unit.soc_pct


def _ordered(units: Sequence[BatteryUnit], charging: bool) -> list[BatteryUnit]:
    return sorted(units, key=lambda u: (_rank(u, charging), u.battery_id))

