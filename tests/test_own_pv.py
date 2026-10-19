"""Tests for batteries with own PV modules: ports, curtailed PV, curtailing."""

import pytest

from custom_components.slems.own_pv import (
    PROBE_INTERVAL_S,
    PROBE_SETTLE_S,
    PROBE_STEP_W,
    CurtailControl,
    OutputProbe,
    feasible_command_w,
    ports,
)


def test_ports_turn_the_battery_power_into_input_or_output() -> None:
    assert ports(400, 600, 800, False) == (0.0, 200.0)
    assert ports(-500, 600, 800, False) == (0.0, 800.0)
    # More than the PV: without an input no output, with one the rest from the grid.
    assert ports(1000, 600, 800, False) == (0.0, 0.0)
    assert ports(1000, 600, 800, True) == (400.0, 0.0)
    assert ports(-300, 0, None, True) == (0.0, 300.0)


def test_feasible_command_follows_the_pv_read() -> None:
    # The sun went behind a cloud: only the PV can be charged.
    assert feasible_command_w(500, 120, ac_charge=False, max_output_w=800) == 120
    # With an input the grid charges the rest.
    assert feasible_command_w(500, 120, ac_charge=True, max_output_w=800) == 500
    # More sun: less room in the output for discharging.
    assert feasible_command_w(-400, 700, ac_charge=False, max_output_w=800) == -100
    assert feasible_command_w(-400, 900, ac_charge=False, max_output_w=800) == 0
    assert feasible_command_w(-400, None, ac_charge=False, max_output_w=800) == -400


class CurtailingDevice:
    """Battery that takes at most ``accepts_w``; the MPPT curtails the rest."""

    def __init__(self, potential_w: float, accepts_w: float, output_w: float) -> None:
        self.potential_w = potential_w
        self.accepts_w = accepts_w
        self.output_w = output_w

    @property
    def pv_w(self) -> float:
        if self.output_w >= self.potential_w:
            return self.potential_w  # the battery covers the rest
        return min(self.potential_w, self.output_w + self.accepts_w)


def run(probe: OutputProbe, device: CurtailingDevice, power_w: float, seconds: float, start: float = 0.0) -> float:
    now = start
    while now < start + seconds:
        device.output_w = probe.output(now, power_w, device.pv_w, device.output_w, 800)
        now += PROBE_SETTLE_S + 1
    return now


def test_probe_finds_curtailed_pv_again() -> None:
    # 600 W of sun, the battery at the top takes only 100 W of the 500 W
    # commanded: output = PV − 500 has curtailed the modules to 200 W.
    device = CurtailingDevice(600, 100, 100)
    probe = OutputProbe()
    now = run(probe, device, 500, 6 * (PROBE_SETTLE_S + 1))
    assert device.pv_w == 600 and device.output_w == 500
    assert probe.accepted_w == 100
    # Kept between the probes; a later probe finds at most a step curtailed.
    run(probe, device, 500, 4 * PROBE_INTERVAL_S, now)
    assert device.pv_w >= 600 - PROBE_STEP_W


def test_probe_without_curtailed_pv_goes_back() -> None:
    device = CurtailingDevice(600, 2000, 100)
    probe = OutputProbe()
    device.output_w = probe.output(0, 500, device.pv_w, device.output_w, 800)
    assert device.output_w == 100 + PROBE_STEP_W and probe.probing
    # The PV did not follow: back to PV − power until the next probe.
    assert probe.output(PROBE_SETTLE_S + 1, 500, 600, device.output_w, 800) == 100
    assert not probe.probing and probe.accepted_w is None
    assert probe.output(PROBE_SETTLE_S + 2, 500, 600, 100, 800) == 100
    # No probe at the highest output.
    probe = OutputProbe()
    assert probe.output(0, 0, 900, 800, 800) == 800 and not probe.probing


def test_curtailment_integrates_the_export_above_the_limit() -> None:
    control = CurtailControl()
    # Nothing to curtail while batteries and consumers take the surplus.
    assert control.update(-50, 600, 0, 500) == 0
    assert control.update(900, 600, 300, 500) == pytest.approx(60)
    assert control.update(750, 600, 0, 500) == pytest.approx(90)
    # Not more than the batteries can curtail.
    assert control.update(3000, 600, 0, 300) == 300
    # Export below the limit: less until it ends.
    assert control.update(0, 600, 0, 300) == pytest.approx(180)
    assert control.update(0, 600, 0, 300) == pytest.approx(60)
    assert control.update(0, 600, 0, 300) == 0
    control.update(900, 600, 300, 500)
    assert control.update(None, 600, 0, 500) == 0
