"""Round trip efficiency of a battery.

Round trip efficiency (RTE) = energy out / energy in. It is split
symmetrically into a charge and a discharge efficiency of sqrt(RTE) each.

An energy balance over a period gives
    RTE ≈ (discharged + stored_end - stored_start) / charged
which is only reliable after enough throughput. Until ``MIN_CYCLES`` full
cycles have been charged, the configured value is used.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math

import numpy as np

from .battery_distribution import LossModel
from .const import EfficiencyMode

MIN_CYCLES = 3
MIN_EFFICIENCY = 0.5
MAX_EFFICIENCY = 1.0


def estimate_efficiency(
    charged_kwh: float,
    discharged_kwh: float,
    stored_delta_kwh: float,
    capacity_kwh: float,
) -> float | None:
    """Estimate RTE from an energy balance; None if not enough throughput."""
    if capacity_kwh <= 0 or charged_kwh < MIN_CYCLES * capacity_kwh:
        return None
    efficiency = (discharged_kwh + stored_delta_kwh) / charged_kwh
    return min(MAX_EFFICIENCY, max(MIN_EFFICIENCY, efficiency))


@dataclass
class EnergyIntegrator:
    """Charged / discharged energy integrated from power samples."""

    charged_kwh: float = 0.0
    discharged_kwh: float = 0.0
    start_soc_pct: float | None = None
    last_timestamp: float | None = None
    last_power_w: float | None = None

    def add(self, timestamp: float, power_w: float, soc_pct: float | None) -> None:
        """Add a power sample (+charge / -discharge); integrates step wise."""
        if self.start_soc_pct is None and soc_pct is not None:
            self.start_soc_pct = soc_pct
        if self.last_timestamp is not None and self.last_power_w is not None:
            hours = (timestamp - self.last_timestamp) / 3600
            # Gaps (restart, outage) are not bridged.
            if 0 < hours < 0.1:
                energy = self.last_power_w * hours / 1000
                if energy > 0:
                    self.charged_kwh += energy
                else:
                    self.discharged_kwh -= energy
        self.last_timestamp = timestamp
        self.last_power_w = power_w

    def as_dict(self) -> dict:
        return {
            "charged_kwh": self.charged_kwh,
            "discharged_kwh": self.discharged_kwh,
            "start_soc_pct": self.start_soc_pct,
        }

    @classmethod
    def from_dict(cls, data: dict) -> EnergyIntegrator:
        return cls(
            charged_kwh=data.get("charged_kwh", 0.0),
            discharged_kwh=data.get("discharged_kwh", 0.0),
            start_soc_pct=data.get("start_soc_pct"),
        )


class EfficiencyTracker:
    """Provides the current round trip efficiency of one battery."""

    def __init__(
        self,
        mode: EfficiencyMode,
        configured_pct: float,
        capacity_kwh: float,
        integrator: EnergyIntegrator | None = None,
    ) -> None:
        self.mode = mode
        # Manual value, or start value until a measured value is available.
        self._configured = configured_pct / 100
        self._capacity_kwh = capacity_kwh
        self.integrator = integrator or EnergyIntegrator()
        self._estimate: float | None = None

    def update(
        self,
        timestamp: float,
        power_w: float | None,
        soc_pct: float | None,
        total_charged_kwh: float | None,
        total_discharged_kwh: float | None,
    ) -> None:
        """Feed the latest telemetry."""
        if self.mode is EfficiencyMode.BATTERY_COUNTERS:
            if total_charged_kwh is None or total_discharged_kwh is None or soc_pct is None:
                return
            # Lifetime counters: assume an empty battery at the start.
            self._estimate = estimate_efficiency(
                total_charged_kwh,
                total_discharged_kwh,
                soc_pct / 100 * self._capacity_kwh,
                self._capacity_kwh,
            )
        elif self.mode is EfficiencyMode.LEARNED:
            if power_w is None:
                return
            self.integrator.add(timestamp, power_w, soc_pct)
            start = self.integrator.start_soc_pct
            if soc_pct is None or start is None:
                return
            self._estimate = estimate_efficiency(
                self.integrator.charged_kwh,
                self.integrator.discharged_kwh,
                (soc_pct - start) / 100 * self._capacity_kwh,
                self._capacity_kwh,
            )

    @property
    def is_learned(self) -> bool:
        """True if the value comes from measurements, not from a default."""
        return self.mode is not EfficiencyMode.MANUAL and self._estimate is not None

    @property
    def round_trip(self) -> float:
        """Current round trip efficiency (0..1)."""
        if self.mode is not EfficiencyMode.MANUAL and self._estimate is not None:
            return self._estimate
        return self._configured

    @property
    def one_way(self) -> float:
        """Charge or discharge efficiency (0..1)."""
        return math.sqrt(self.round_trip)


LOSS_BIN_W = 250
LOSS_MIN_POWER_W = 100
LOSS_EMA_ALPHA = 0.05
LOSS_MIN_SAMPLES = 20
LOSS_MIN_BINS = 3
# Maximum relative change to the previous sample that still counts as steady.
LOSS_STEADY_RATIO = 0.05


@dataclass
class LossCurveLearner:
    """Learns the conversion loss per AC power range from AC and DC power.

    Discharging: loss = |DC| - |AC|; charging: loss = |AC| - |DC|. Each 250 W
    bin keeps an exponential moving average; the loss model is a quadratic fit
    through the bins with enough samples. Only steady operation is learned:
    while the power changes, AC and DC values are read at slightly different
    moments and do not belong together.
    """

    # bin index -> [mean loss in W, sample count, mean AC power in W]
    bins: dict[int, list[float]] = field(default_factory=dict)
    last_ac_power_w: float | None = None

    def add(self, ac_power_w: float | None, dc_power_w: float | None) -> None:
        previous, self.last_ac_power_w = self.last_ac_power_w, ac_power_w
        if ac_power_w is None or dc_power_w is None or abs(ac_power_w) < LOSS_MIN_POWER_W:
            return
        if previous is None or abs(ac_power_w - previous) > LOSS_STEADY_RATIO * abs(ac_power_w):
            return
        # Opposite directions of AC and DC side are transients; skip them.
        if (ac_power_w > 0) != (dc_power_w > 0):
            return
        if ac_power_w > 0:
            loss = abs(ac_power_w) - abs(dc_power_w)
        else:
            loss = abs(dc_power_w) - abs(ac_power_w)
        power = abs(ac_power_w)
        index = int(power // LOSS_BIN_W)
        mean, count, mean_power = self.bins.get(index, [loss, 0.0, power])
        alpha = max(LOSS_EMA_ALPHA, 1 / (count + 1))
        self.bins[index] = [
            mean + alpha * (loss - mean),
            count + 1,
            mean_power + alpha * (power - mean_power),
        ]

    def model(self, default: LossModel) -> LossModel:
        """Fitted loss model, or ``default`` while there is not enough data."""
        usable = [
            (mean_power, mean)
            for mean, count, mean_power in self.bins.values()
            if count >= LOSS_MIN_SAMPLES
        ]
        if len(usable) < LOSS_MIN_BINS:
            return default
        power = np.array([p for p, _ in usable])
        loss = np.array([value for _, value in usable])
        design = np.column_stack([np.ones_like(power), power, power**2])
        coefficients, *_ = np.linalg.lstsq(design, loss, rcond=None)
        fixed, linear, quadratic = (max(0.0, float(c)) for c in coefficients)
        return LossModel(fixed_w=fixed, linear=linear, quadratic_per_w=quadratic)

    def as_dict(self) -> dict:
        return {str(index): values for index, values in self.bins.items()}

    @classmethod
    def from_dict(cls, data: dict) -> LossCurveLearner:
        return cls({int(index): list(values) for index, values in data.items()})
