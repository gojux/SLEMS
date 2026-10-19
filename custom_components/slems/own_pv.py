"""Batteries with their own PV modules (see drivers/base): input and output,
curtailed PV and curtailing for the feed-in cap.

Ports: the battery power P (+charge / -discharge) becomes the exchange at
the AC connection, P − own PV. Positive it is the input from the grid (only
with an input), otherwise the output PV − P, within 0 and the highest output.

Curtailed PV: a battery that takes less than PV − output (full, at the top of
the charge, limited by temperature) makes the MPPT curtail the modules. The
PV read then follows the output, and output = PV − P would sink further with
every write until the PV is lost. While that can happen (``at_top``) and the
device does not pass its PV through, a probe raises the output by
``PROBE_STEP_W`` (at most the highest output) every ``PROBE_INTERVAL_S``:

* the PV followed: it was curtailed; the output is raised at once by a step
  twice as large (at most ``PROBE_MAX_STEP_W``), and what the battery took
  meanwhile is its acceptance;
* the PV did not follow within ``PROBE_SETTLE_S``: the battery discharged
  the step for a moment and
  nothing is curtailed. After a chain that found curtailed PV the
  acceptance stays; otherwise it rises by a step, so a battery that takes
  more again gets it (at most a step is curtailed until the next probe).

Between probes the output is PV − the smaller of P and the acceptance, so
the PV found is not curtailed again. A wanted curtailment (feed-in cap) and
discharging need no probe.

Feed-in cap: once neither the batteries nor the consumers can take the
surplus above the limit, the batteries with own PV curtail the rest
(``CurtailControl``). The curtailed PV is not measured, so the control
integrates the export above the limit instead of the planned excess: it
rises while the export stays above the limit and falls while it is below.
Curtailed, the PV read follows the output; the driver keeps the highest PV
read meanwhile as the PV of the modules (``own_pv_available_w``), otherwise
output = PV − curtailment would sink to 0 and what can be curtailed with it.
A battery that passes its PV through when full cannot curtail then.
"""

from __future__ import annotations

PROBE_STEP_W = 50.0
PROBE_MAX_STEP_W = 400.0
PROBE_INTERVAL_S = 60.0
# Time for the device and its integration to show the PV after a probe.
PROBE_SETTLE_S = 10.0
# Share of the export error per control cycle the curtailment moves by.
CURTAIL_GAIN = 0.2
# Curtailment below this ends.
CURTAIL_MIN_W = 10.0


def ports(
    power_w: float, pv_w: float, max_output_w: float | None, has_input: bool
) -> tuple[float, float]:
    """(input, output) that make the battery take ``power_w`` with ``pv_w``."""
    connection = power_w - pv_w
    if connection > 0 and has_input:
        return connection, 0.0
    output = max(0.0, -connection)
    if max_output_w is not None:
        output = min(output, max_output_w)
    return 0.0, output


def feasible_command_w(
    commanded_w: float, pv_w: float | None, *, ac_charge: bool, max_output_w: float | None
) -> float:
    """The part of a command the battery can follow with the PV read now.

    The PV may change between the command and the next reading; the battery
    then takes less (without an input at most its PV, discharging at most
    the highest output − PV). Only that part counts when judging whether it
    delivers (see delivery_monitor), not as a BMS stop or a full battery.
    """
    if pv_w is None:
        return commanded_w
    if commanded_w > 0 and not ac_charge:
        return min(commanded_w, pv_w)
    if commanded_w < 0 and max_output_w is not None:
        return max(commanded_w, -max(0.0, max_output_w - pv_w))
    return commanded_w


class OutputProbe:
    """Output of a battery with own PV that may curtail its modules (see above)."""

    def __init__(self) -> None:
        # Monotonic time of the probe written, its output, its step and the PV before it.
        self._probe_at: float | None = None
        self._probe_output = 0.0
        self._probe_step = PROBE_STEP_W
        self._probe_pv = 0.0
        # A probe of this chain found curtailed PV.
        self._followed = False
        self._next_probe: float | None = None
        # Power the battery took after the PV was found again (W).
        self.accepted_w: float | None = None

    @property
    def probing(self) -> bool:
        return self._probe_at is not None

    def diagnostics(self) -> dict:
        return {
            "probing": self.probing,
            "probe_output_w": self._probe_output if self.probing else None,
            "probe_step_w": self._probe_step if self.probing else None,
            "followed": self._followed,
            "accepted_w": self.accepted_w,
        }

    def reset(self) -> None:
        self._probe_at = None
        self._followed = False
        self._next_probe = None
        self.accepted_w = None

    def output(
        self,
        now: float,
        power_w: float,
        pv_w: float,
        output_read_w: float,
        max_output_w: float | None,
    ) -> float:
        """Output for the battery power ``power_w`` (>= 0) at the top of the charge."""
        if self._probe_at is not None:
            if pv_w >= self._probe_pv + self._probe_step / 2:
                self.accepted_w = max(0.0, pv_w - output_read_w)
                self._followed = True
                step = min(PROBE_MAX_STEP_W, 2 * self._probe_step)
                if (probe := self._probe(now, pv_w, output_read_w, max_output_w, step)) is not None:
                    return probe
            elif now - self._probe_at < PROBE_SETTLE_S:
                return self._probe_output
            elif not self._followed and self.accepted_w is not None:
                self.accepted_w += PROBE_STEP_W
                if self.accepted_w >= power_w:
                    self.accepted_w = None
            self._probe_at = None
            self._followed = False
            self._next_probe = now + PROBE_INTERVAL_S
        elif self._next_probe is None or now >= self._next_probe:
            if (probe := self._probe(now, pv_w, output_read_w, max_output_w)) is not None:
                return probe
            self._next_probe = now + PROBE_INTERVAL_S
        taken = power_w if self.accepted_w is None else min(power_w, self.accepted_w)
        _, output = ports(taken, pv_w, max_output_w, False)
        return output

    def _probe(
        self,
        now: float,
        pv_w: float,
        output_read_w: float,
        max_output_w: float | None,
        step_w: float = PROBE_STEP_W,
    ) -> float | None:
        output = output_read_w + step_w
        if max_output_w is not None:
            output = min(output, max_output_w)
        if output < output_read_w + PROBE_STEP_W / 2:
            return None  # at the highest output: nothing can be curtailed
        self._probe_at = now
        self._probe_output = output
        self._probe_step = output - output_read_w
        self._probe_pv = pv_w
        return output


class CurtailControl:
    """PV the batteries with own PV curtail for the feed-in cap (see above)."""

    def __init__(self) -> None:
        self.total_w = 0.0

    def update(
        self, export_w: float | None, max_export_w: float | None, excess_w: float, curtailable_w: float
    ) -> float:
        """One control cycle; ``excess_w`` is the surplus above the limit that
        neither batteries nor consumers take, ``curtailable_w`` what the
        batteries with own PV can curtail now."""
        if export_w is None or max_export_w is None or curtailable_w <= 0:
            self.total_w = 0.0
            return 0.0
        if self.total_w <= 0 and excess_w <= 0:
            return 0.0
        total = self.total_w + CURTAIL_GAIN * (export_w - max_export_w)
        self.total_w = min(curtailable_w, total) if total >= CURTAIL_MIN_W else 0.0
        return self.total_w
