"""Cell balance monitoring and active cell balancing for LFP batteries.

Based on the cell balance monitor and the active-balance blueprint of the
Omnibattery project (https://github.com/ffunes/Omnibattery, GPL-3.0).

LFP cells have an almost flat voltage curve between about 10 and 97 % SoC;
cells with clearly different SoC show nearly the same voltage there. The cell
delta (highest minus lowest cell voltage) is therefore only meaningful at the
top of the charge. As in Omnibattery, the monitor records a *top measurement*
after the highest cell reached ``CHARGE_STOP_V`` or the BMS ended the charge
(SoC ``BMS_FULL_SOC_PCT``) and the battery then rested for
``MEASUREMENT_WAIT_S``; the live delta is shown but not judged. The curve is
steep there: Marstek cells typically show 170–180 mV from the factory, which
is normal, so the status limits are far above that.

Active balancing keeps the battery in the top window long enough for the BMS
to bleed the highest cells (it takes hours: roughly 5 mV per 24 h in the
window):

1. PRE_TOP_CHARGE: charge with the PV surplus (at least ``TOP_CHARGE_W``, at
   most the maximum charge power) until the highest cell reaches
   ``TOP_ZONE_V``.
2. CHARGE: charge with ``TOP_CHARGE_W`` until ``CHARGE_STOP_V``. If the BMS
   refuses charging (``REJECTION_SAMPLES`` samples of about 0 W after
   ``CHARGE_ENGAGE_GRACE_S``), the retry voltage is lowered by
   ``RESUME_STEP_V`` (not below ``MIN_RESUME_V``).
3. WAIT_MEASURE: idle for ``MEASUREMENT_WAIT_S``, then measure the delta.
4. Delta above ``TARGET_DELTA_V``: DISCHARGE with ``DISCHARGE_W`` down to the
   retry voltage and continue with CHARGE; otherwise FINAL_DISCHARGE with
   ``DISCHARGE_W`` down to ``FINAL_DISCHARGE_V``, then DONE.

A run ends with an error after ``MAX_RUN_S`` or on invalid telemetry. While
it is paused (SLEMS not in operating mode active, battery still ramping out)
the timers of the current leg start again on resume.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

TOP_ZONE_V = 3.49
CHARGE_STOP_V = 3.60
FINAL_DISCHARGE_V = 3.48
TARGET_DELTA_V = 0.03
MIN_RESUME_V = 3.40
RESUME_STEP_V = 0.01
TOP_CHARGE_W = 95
DISCHARGE_W = 200
MEASUREMENT_WAIT_S = 60.0
CHARGE_ENGAGE_GRACE_S = 10.0
REJECTION_SAMPLES = 3
IDLE_POWER_W = 10.0
MAX_RUN_S = 24 * 3600.0

# The BMS ended the charge (top measurement without reaching CHARGE_STOP_V).
BMS_FULL_SOC_PCT = 99.5

# Balance status of a top measurement (mV), as in Omnibattery (measured at
# 3.60 V or at the BMS cut-off; about 180 mV is normal for Marstek cells).
STATUS_LIMITS_MV = ((200, "green"), (230, "yellow"), (250, "orange"))
# From this top measurement on, active balancing is suggested.
SUGGEST_BALANCING_MV = 230


class BalancingPhase(StrEnum):
    """Phase of an active balancing run."""

    OFF = "off"
    # Started, but paused (SLEMS not in operating mode active) or the battery
    # still ramps out of its normal operation.
    WAITING = "waiting"
    PRE_TOP_CHARGE = "pre_top_charge"
    CHARGE = "charge"
    WAIT_MEASURE = "wait_measure"
    DISCHARGE = "discharge"
    FINAL_DISCHARGE = "final_discharge"
    DONE = "done"
    ERROR = "error"


def balance_status(delta_mv: float | None) -> str | None:
    """green / yellow / orange / red for a top measurement."""
    if delta_mv is None:
        return None
    for limit, status in STATUS_LIMITS_MV:
        if delta_mv < limit:
            return status
    return "red"


@dataclass
class TopMeasurement:
    """Settled cell delta near the top of the charge."""

    delta_mv: float
    timestamp: float
    source: str  # "rest" or "balancing"


class CellMonitor:
    """Records a top measurement after the top of the charge and a rest."""

    def __init__(self) -> None:
        self.last: TopMeasurement | None = None
        self._rest_since: float | None = None
        # The top of the charge was reached and not yet measured.
        self._top_reached = False

    def update(
        self,
        now: float,
        max_cell_v: float | None,
        min_cell_v: float | None,
        power_w: float | None,
        wall_timestamp: float,
        soc_pct: float | None = None,
    ) -> None:
        if max_cell_v is None or min_cell_v is None or power_w is None:
            self._rest_since = None
            return
        if max_cell_v >= CHARGE_STOP_V or (soc_pct is not None and soc_pct >= BMS_FULL_SOC_PCT):
            self._top_reached = True
        elif max_cell_v < TOP_ZONE_V:
            # Discharged out of the top window: no longer a top measurement.
            self._top_reached = False
            self._rest_since = None
            return
        if not self._top_reached or abs(power_w) > IDLE_POWER_W:
            self._rest_since = None
            return
        if self._rest_since is None:
            self._rest_since = now
        elif now - self._rest_since >= MEASUREMENT_WAIT_S:
            self.record((max_cell_v - min_cell_v) * 1000, wall_timestamp, "rest")
            # One measurement each time the top is reached.
            self._top_reached = False
            self._rest_since = None

    def record(self, delta_mv: float, wall_timestamp: float, source: str) -> None:
        self.last = TopMeasurement(round(delta_mv, 1), wall_timestamp, source)

    @property
    def suggest_balancing(self) -> bool:
        return self.last is not None and self.last.delta_mv >= SUGGEST_BALANCING_MV

    def as_dict(self) -> dict | None:
        if self.last is None:
            return None
        return {
            "delta_mv": self.last.delta_mv,
            "timestamp": self.last.timestamp,
            "source": self.last.source,
        }

    def restore(self, data: dict | None) -> None:
        if data:
            self.last = TopMeasurement(data["delta_mv"], data["timestamp"], data["source"])

    def pause(self) -> None:
        """No rest measurement (e.g. during a balancing run, which measures itself)."""
        self._rest_since = None
        self._top_reached = False


@dataclass
class BalancingStep:
    """What the balancer wants from the battery (+charge / -discharge W)."""

    power_w: float
    phase: BalancingPhase


class CellBalancer:
    """State machine of one active balancing run."""

    def __init__(self, max_charge_w: float, started_at: float) -> None:
        self._max_charge_w = max_charge_w
        # Wall clock time (UNIX timestamp) of the start, for MAX_RUN_S.
        self.started_at = started_at
        self.phase = BalancingPhase.PRE_TOP_CHARGE
        self.retry_voltage = TOP_ZONE_V
        self.last_delta_mv: float | None = None
        # Top measurement before the start (for the final report).
        self.initial_delta_mv: float | None = None
        self.error: str | None = None
        # Monotonic start of the current leg; None until the next step.
        self._leg_started: float | None = None
        self._rejections = 0
        self._measure_since: float | None = None

    @property
    def finished(self) -> bool:
        return self.phase in (BalancingPhase.DONE, BalancingPhase.ERROR)

    def as_dict(self) -> dict:
        return {
            "phase": self.phase.value,
            "retry_voltage": self.retry_voltage,
            "last_delta_mv": self.last_delta_mv,
            "initial_delta_mv": self.initial_delta_mv,
            "started_at": self.started_at,
        }

    @classmethod
    def from_dict(cls, data: dict, max_charge_w: float) -> CellBalancer:
        balancer = cls(max_charge_w, data["started_at"])
        balancer.phase = BalancingPhase(data["phase"])
        balancer.retry_voltage = data["retry_voltage"]
        balancer.last_delta_mv = data["last_delta_mv"]
        balancer.initial_delta_mv = data.get("initial_delta_mv")
        return balancer

    def pause(self) -> None:
        """Timers of the current leg start again with the next step."""
        self._leg_started = None
        self._rejections = 0
        self._measure_since = None

    def _enter(self, phase: BalancingPhase, now: float) -> None:
        self.phase = phase
        self._leg_started = now
        self._rejections = 0
        self._measure_since = None

    def _fail(self, error: str, now: float) -> BalancingStep:
        self.error = error
        self._enter(BalancingPhase.ERROR, now)
        return BalancingStep(0.0, self.phase)

    def step(
        self,
        now: float,
        wall_now: float,
        max_cell_v: float | None,
        min_cell_v: float | None,
        power_w: float | None,
        surplus_w: float = 0.0,
    ) -> BalancingStep:
        """Advance the run with the latest telemetry; return the wanted power.

        ``surplus_w`` is the PV surplus available to this battery (its own
        charge power included), used before the top window is reached.
        """
        if self.finished:
            return BalancingStep(0.0, self.phase)
        if wall_now - self.started_at >= MAX_RUN_S:
            return self._fail("timeout", now)
        if max_cell_v is None or min_cell_v is None or power_w is None:
            return self._fail("telemetry", now)
        if self._leg_started is None:
            self._leg_started = now

        if self.phase is BalancingPhase.PRE_TOP_CHARGE:
            refused = (
                abs(power_w) <= IDLE_POWER_W and now - self._leg_started >= CHARGE_ENGAGE_GRACE_S
            )
            if max_cell_v >= TOP_ZONE_V or refused:
                self._enter(BalancingPhase.CHARGE, now)
            else:
                power = min(self._max_charge_w, max(TOP_CHARGE_W, surplus_w))
                return BalancingStep(power, self.phase)
        if self.phase is BalancingPhase.CHARGE:
            if max_cell_v >= CHARGE_STOP_V:
                self.retry_voltage = TOP_ZONE_V
                self._enter(BalancingPhase.WAIT_MEASURE, now)
            else:
                if now - self._leg_started >= CHARGE_ENGAGE_GRACE_S:
                    # Consecutive samples of about 0 W while charge is commanded.
                    self._rejections = self._rejections + 1 if abs(power_w) <= IDLE_POWER_W else 0
                if self._rejections < REJECTION_SAMPLES:
                    return BalancingStep(TOP_CHARGE_W, self.phase)
                # The BMS refuses charging: retry from a lower voltage.
                self.retry_voltage = round(
                    max(
                        MIN_RESUME_V,
                        min(self.retry_voltage - RESUME_STEP_V, max_cell_v - RESUME_STEP_V),
                    ),
                    3,
                )
                self._enter(
                    BalancingPhase.WAIT_MEASURE if max_cell_v >= TOP_ZONE_V else BalancingPhase.DISCHARGE,
                    now,
                )

        if self.phase is BalancingPhase.WAIT_MEASURE:
            if self._measure_since is None:
                self._measure_since = now
            if now - self._measure_since < MEASUREMENT_WAIT_S:
                return BalancingStep(0.0, self.phase)
            delta_v = max_cell_v - min_cell_v
            self.last_delta_mv = round(delta_v * 1000, 1)
            self._enter(
                BalancingPhase.FINAL_DISCHARGE if delta_v <= TARGET_DELTA_V else BalancingPhase.DISCHARGE,
                now,
            )

        if self.phase is BalancingPhase.DISCHARGE:
            if max_cell_v > min(self.retry_voltage, TOP_ZONE_V):
                return BalancingStep(-DISCHARGE_W, self.phase)
            self._enter(BalancingPhase.CHARGE, now)
            return BalancingStep(TOP_CHARGE_W, self.phase)

        if self.phase is BalancingPhase.FINAL_DISCHARGE:
            if max_cell_v > FINAL_DISCHARGE_V:
                return BalancingStep(-DISCHARGE_W, self.phase)
            self._enter(BalancingPhase.DONE, now)

        return BalancingStep(0.0, self.phase)
