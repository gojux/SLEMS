"""Minimum power of a battery: no charging or discharging with so little power
that it does not pay off.

Below some tens of watts the fixed conversion loss of an inverter takes most
of the power (Venus: about 35-48 W): charging below it even discharges the
battery. ``LowLossTable`` learns the loss per ``LOW_BIN_W`` bin below
``LOW_MAX_POWER_W``, charging and discharging apart (the loss curve of the
distribution starts at 100 W in 250 W bins). The thresholds per mode:

* ``AUTO_ENERGY``: charging only where it stores anything (DC > 0, the
  physical floor); discharging always delivers, so no threshold.
* ``AUTO_ECONOMIC``: charging with P pays off when the stored energy is worth
  more than the feed-in now: eta_charge(P) * W_s > feed-in price, with W_s =
  import price * typical discharge efficiency, or, when the charge is secured
  (the battery gets full anyway), only at the typical charge efficiency
  (charging later with more power is better). Discharging with P pays off when
  import price * eta_discharge(P) > feed-in price / typical charge efficiency.
  Without prices or with a feed-in credit of 0 or less: the physical floor.
* ``MANUAL``: one value for both directions.
* ``OFF``: no threshold.

With "no import" (grid targets) there is no discharge threshold. Both
automatic modes need the table learned (``LOW_MIN_BINS`` bins with
``LOW_MIN_SAMPLES`` samples, one below ``LOW_LEARNED_BELOW_W``); until then no
threshold. Thresholds stay within ``MAX_THRESHOLD_W``.

``MinPowerGate`` applies a threshold to the unsmoothed battery target with a
hysteresis: a battery starts at the threshold, keeps running down to the
threshold minus ``BAND_W`` (never below the physical floor; the band lies
above the floor when the threshold is the floor) and runs at least the learned
start time. Replayed against debug logs of 2026-10-30 a band of 25 W saved
16 % of the starts, 50 W hardly more.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

LOW_BIN_W = 50.0
LOW_MIN_POWER_W = 40.0
LOW_MAX_POWER_W = 300.0
LOW_MIN_SAMPLES = 20
LOW_MIN_BINS = 3
LOW_LEARNED_BELOW_W = 100.0
LOW_EMA_ALPHA = 0.05
# Maximum relative change to the previous sample that still counts as steady.
LOW_STEADY_RATIO = 0.05
MAX_THRESHOLD_W = 300.0
SEARCH_STEP_W = 5.0
# Hysteresis: the battery runs down to the threshold minus this (the control's dead band).
BAND_W = 25.0


class MinPowerMode(StrEnum):
    AUTO_ENERGY = "auto_energy"
    AUTO_ECONOMIC = "auto_economic"
    MANUAL = "manual"
    OFF = "off"


@dataclass
class LowLossTable:
    """Conversion loss of small AC powers, charging and discharging apart.

    direction ("charge"/"discharge") -> bin index -> [mean loss W, samples, mean AC power W].
    """

    bins: dict[str, dict[int, list[float]]] = field(default_factory=dict)
    last_ac_power_w: float | None = None

    def add(self, ac_power_w: float | None, dc_power_w: float | None) -> None:
        """A steady reading of AC (+charge) and DC (+charge) power."""
        previous, self.last_ac_power_w = self.last_ac_power_w, ac_power_w
        if ac_power_w is None or dc_power_w is None:
            return
        power = abs(ac_power_w)
        if not LOW_MIN_POWER_W <= power < LOW_MAX_POWER_W:
            return
        if previous is None or abs(ac_power_w - previous) > LOW_STEADY_RATIO * power:
            return
        # Below the fixed loss charging discharges the battery (DC < 0): also learned.
        loss = ac_power_w - dc_power_w
        if loss < 0:
            return
        direction = "charge" if ac_power_w > 0 else "discharge"
        table = self.bins.setdefault(direction, {})
        index = int(power // LOW_BIN_W)
        mean, count, mean_power = table.get(index, [loss, 0.0, power])
        alpha = max(LOW_EMA_ALPHA, 1 / (count + 1))
        table[index] = [mean + alpha * (loss - mean), count + 1, mean_power + alpha * (power - mean_power)]

    def points(self, direction: str) -> list[tuple[float, float]]:
        """Learned (AC power, loss) of a direction, by power; empty until learned."""
        usable = sorted(
            (mean_power, mean)
            for mean, count, mean_power in self.bins.get(direction, {}).values()
            if count >= LOW_MIN_SAMPLES
        )
        if len(usable) < LOW_MIN_BINS or usable[0][0] >= LOW_LEARNED_BELOW_W:
            return []
        return usable

    def as_dict(self) -> dict[str, Any]:
        return {direction: {str(i): v for i, v in table.items()} for direction, table in self.bins.items()}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> LowLossTable:
        if not data:
            return cls()
        return cls({d: {int(i): list(v) for i, v in table.items()} for d, table in data.items()})


def _loss_w(points: list[tuple[float, float]], power_w: float) -> float:
    """Loss at ``power_w``, linear between the learned points, the nearest one outside."""
    if power_w <= points[0][0]:
        return points[0][1]
    for (p0, l0), (p1, l1) in zip(points, points[1:]):
        if power_w <= p1:
            return l0 + (l1 - l0) * (power_w - p0) / (p1 - p0)
    return points[-1][1]


def _first_power(points: list[tuple[float, float]], ok) -> float:
    """Smallest power (in SEARCH_STEP_W steps from LOW_MIN_POWER_W) meeting ``ok(power, loss)``."""
    power = LOW_MIN_POWER_W
    while power < MAX_THRESHOLD_W:
        if ok(power, _loss_w(points, power)):
            return power
        power += SEARCH_STEP_W
    return MAX_THRESHOLD_W


@dataclass(frozen=True)
class Prices:
    """Prices now (any currency unit per kWh)."""

    import_price: float
    export_price: float


def charge_floor_w(table: LowLossTable) -> float | None:
    """Least charge power that stores anything (DC > 0); None until learned."""
    points = table.points("charge")
    return _first_power(points, lambda p, loss: p > loss) if points else None


def charge_threshold_w(
    table: LowLossTable,
    mode: MinPowerMode,
    manual_w: float,
    *,
    secured: bool,
    prices: Prices | None,
    charge_efficiency: float,
    discharge_efficiency: float,
) -> float | None:
    """Least charge power of the battery, None: no threshold."""
    if mode is MinPowerMode.OFF:
        return None
    if mode is MinPowerMode.MANUAL:
        return manual_w or None
    points = table.points("charge")
    if not points:
        return None
    floor = _first_power(points, lambda p, loss: p > loss)
    if mode is MinPowerMode.AUTO_ENERGY or prices is None or prices.export_price <= 0:
        return floor
    if secured:
        needed = charge_efficiency
    elif prices.import_price <= 0 or discharge_efficiency <= 0:
        return floor
    else:
        needed = prices.export_price / (prices.import_price * discharge_efficiency)
    return max(floor, _first_power(points, lambda p, loss: (p - loss) / p > needed))


def discharge_threshold_w(
    table: LowLossTable,
    mode: MinPowerMode,
    manual_w: float,
    *,
    no_import: bool,
    prices: Prices | None,
    charge_efficiency: float,
) -> float | None:
    """Least discharge power of the battery, None: no threshold."""
    if mode in (MinPowerMode.OFF, MinPowerMode.AUTO_ENERGY) or no_import:
        return None
    if mode is MinPowerMode.MANUAL:
        return manual_w or None
    points = table.points("discharge")
    if not points or prices is None or prices.import_price <= 0 or charge_efficiency <= 0:
        return None
    if prices.export_price <= 0:
        return None
    needed = prices.export_price / (prices.import_price * charge_efficiency)
    return _first_power(points, lambda p, loss: p / (p + loss) > needed)


@dataclass(frozen=True)
class GateResult:
    """Target after the minimum power. ``stop``: 0 W at once (the control's
    smoothing would leave a small power below its dead band); ``start_w``: a
    start gets at least this power, also while smoothed."""

    target_w: float
    suppressed: bool = False
    stop: bool = False
    start_w: float | None = None


class MinPowerGate:
    """Hysteresis of the minimum power on the unsmoothed target of all batteries."""

    def __init__(self) -> None:
        # Monotonic time the batteries started in the direction (+1/-1) under the gate.
        self._started: dict[int, float] = {}

    def apply(
        self,
        target_w: float,
        previous_w: float,
        threshold_w: float | None,
        floor_w: float,
        min_run_s: float,
        now: float,
    ) -> GateResult:
        """``previous_w``: the commanded total (+charge); ``floor_w``: the
        physical floor of the direction of ``target_w`` (0 when discharging)."""
        direction = (target_w > 0) - (target_w < 0)
        running = (previous_w > 0) - (previous_w < 0)
        if direction == 0 or threshold_w is None:
            return GateResult(target_w)
        off_w = max(threshold_w - BAND_W, floor_w)
        on_w = max(threshold_w, off_w + BAND_W)
        power = abs(target_w)
        if running == direction:
            started = self._started.get(direction)
            if power >= off_w or (started is not None and now - started < min_run_s):
                return GateResult(target_w)
            self._started.pop(direction, None)
            return GateResult(0.0, suppressed=True, stop=True)
        if power >= on_w:
            self._started[direction] = now
            return GateResult(target_w, start_w=on_w)
        # Below the threshold: no start; a battery running the other way stops as usual.
        return GateResult(0.0, suppressed=True, stop=running == 0)
