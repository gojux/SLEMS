"""Round trip efficiency of a battery.

Round trip efficiency (RTE) = energy out / energy in. It is split
symmetrically into a charge and a discharge efficiency of sqrt(RTE) each.

An energy balance over a period gives
    RTE ≈ (discharged + stored_end - stored_start) / charged
which is only reliable after enough throughput. Until ``MIN_CYCLES`` full
cycles have been charged, the configured value is used.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

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
