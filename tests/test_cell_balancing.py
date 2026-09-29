"""Tests for cell monitoring and active cell balancing."""

from custom_components.slems.cell_balancing import (
    MAX_RUN_S,
    TOP_CHARGE_W,
    BalancingPhase,
    CellBalancer,
    CellMonitor,
    balance_status,
)

# Wall clock start of the runs in these tests (UNIX time).
START = 1_800_000_000.0


def test_balance_status_thresholds() -> None:
    assert balance_status(None) is None
    assert balance_status(180) == "green"  # usual factory spread of Marstek cells
    assert balance_status(200) == "yellow"
    assert balance_status(240) == "orange"
    assert balance_status(250) == "red"


def test_last_full_charge() -> None:
    monitor = CellMonitor()
    # Full at a start with nothing stored: that moment counts.
    monitor.observe_full(3.60, 100, 1000)
    assert monitor.last_full == 1000
    monitor.observe_full(3.58, 100, 1100)  # still at the top
    assert monitor.last_full == 1000
    monitor.observe_full(3.35, 80, 2000)  # discharged out of the top
    monitor.observe_full(3.61, 99, 3000)  # full again, discharged right away (no rest)
    assert monitor.last_full == 3000
    assert monitor.last is None
    # Stored and restored; standing full after a restart keeps the stored time.
    restored = CellMonitor()
    restored.restore(monitor.as_dict())
    restored.observe_full(3.60, 100, 5000)
    assert restored.last_full == 3000
    # Without cell voltages the SoC decides.
    soc_only = CellMonitor()
    soc_only.observe_full(None, 99.6, 100)
    soc_only.observe_full(None, 98.0, 200)  # not out of the top yet
    soc_only.observe_full(None, 99.8, 300)
    assert soc_only.last_full == 100
    soc_only.observe_full(None, 90.0, 400)
    soc_only.observe_full(None, 99.6, 500)
    assert soc_only.last_full == 500


def test_full_when_the_bms_ends_the_charge() -> None:
    monitor = CellMonitor()
    monitor.update(0, 3.40, 3.38, 800, 1000, soc_pct=90)  # below the top: armed
    monitor.observe_full(3.40, 90, 1000)
    # Commanded 200 W, the BMS stops at 99 % and 3.46 V.
    for t in range(0, 120, 5):
        monitor.observe_full(3.46, 99, 2000 + t, now=float(t), commanded_w=200, power_w=-13)
    assert monitor.last_full is None
    monitor.observe_full(3.46, 99, 2125, now=125.0, commanded_w=200, power_w=-13)
    assert monitor.last_full == 2125
    # Charging again later without leaving the top: no new full charge.
    for t in range(200, 400, 5):
        monitor.observe_full(3.46, 99, 2000 + t, now=float(t), commanded_w=200, power_w=-13)
    assert monitor.last_full == 2125
    # Still charging: not ended.
    fresh = CellMonitor()
    for t in range(0, 300, 5):
        fresh.observe_full(3.46, 99, 1000 + t, now=float(t), commanded_w=200, power_w=200)
    assert fresh.last_full is None


def test_monitor_records_after_the_top_and_a_rest() -> None:
    monitor = CellMonitor()
    monitor.update(-60, 3.45, 3.40, 800, 940)  # charging below the top window
    monitor.update(0, 3.605, 3.40, 95, 1000)  # charging reaches the top
    monitor.update(10, 3.58, 3.40, 0, 1010)  # rests, the voltage relaxes
    monitor.update(40, 3.56, 3.40, 5, 1040)
    assert monitor.last is None
    monitor.update(71, 3.55, 3.36, 0, 1071)
    assert monitor.last.delta_mv == 190.0
    assert not monitor.suggest_balancing
    monitor.update(200, 3.55, 3.30, 0, 1200)
    assert monitor.last.timestamp == 1071  # one measurement per top


def test_monitor_records_after_the_bms_ended_the_charge() -> None:
    monitor = CellMonitor()
    monitor.update(-60, 3.45, 3.30, 500, 940, soc_pct=97)
    monitor.update(0, 3.52, 3.30, 0, 1000, soc_pct=100)
    monitor.update(61, 3.52, 3.28, 0, 1061, soc_pct=100)
    assert monitor.last.delta_mv == 240.0
    assert monitor.suggest_balancing


def test_monitor_measures_once_while_the_battery_stays_full() -> None:
    monitor = CellMonitor()
    monitor.update(-60, 3.45, 3.40, 900, 940, soc_pct=97)
    monitor.update(0, 3.55, 3.46, -13, 1000, soc_pct=100)  # standby draw counts as rest
    monitor.update(61, 3.55, 3.46, -13, 1061, soc_pct=100)
    assert monitor.last.delta_mv == 90.0
    # Standing full, the cells relax: no further measurement.
    for t in range(120, 4000, 60):
        monitor.update(t, 3.549, 3.475, -13, 1000 + t, soc_pct=100)
    assert monitor.last.timestamp == 1061
    # Discharged out of the top window and charged again: measured again.
    monitor.update(5000, 3.40, 3.38, -800, 6000, soc_pct=60)
    monitor.update(9000, 3.55, 3.47, -13, 10000, soc_pct=100)
    monitor.update(9061, 3.55, 3.47, -13, 10061, soc_pct=100)
    assert monitor.last.timestamp == 10061
    assert monitor.last.delta_mv == 80.0


def test_no_measurement_after_a_start_while_full() -> None:
    monitor = CellMonitor()
    for t in range(0, 600, 60):
        monitor.update(t, 3.549, 3.475, -13, 1000 + t, soc_pct=100)
    assert monitor.last is None


def test_monitor_ignores_plateau_knee_and_load() -> None:
    monitor = CellMonitor()
    for t in range(0, 200, 10):
        monitor.update(t, 3.32, 3.30, 0, t)  # mid SoC: not meaningful
    for t in range(200, 400, 10):
        monitor.update(t, 3.50, 3.38, 0, t, soc_pct=95)  # knee, not the top
    for t in range(400, 600, 10):
        monitor.update(t, 3.61, 3.40, 800, t)  # at the top, but charging
    assert monitor.last is None


class Battery:
    """Very small battery: max cell voltage follows the power."""

    def __init__(self, voltage: float, delta: float) -> None:
        self.voltage = voltage
        self.delta = delta

    def apply(self, power: float, seconds: float) -> None:
        self.voltage += power * seconds * 2e-6
        # The BMS bleeds the high cell while in the top window.
        if self.voltage >= 3.49:
            self.delta = max(0.0, self.delta - seconds * 5e-6)


def run(balancer: CellBalancer, battery: Battery, limit: int = 20000) -> list[BalancingPhase]:
    phases = []
    power = 0.0
    t = 0.0
    while not balancer.finished and t < limit * 5:
        step = balancer.step(t, START + t, battery.voltage, battery.voltage - battery.delta, power)
        power = step.power_w
        battery.apply(power, 5)
        if not phases or phases[-1] is not step.phase:
            phases.append(step.phase)
        t += 5
    return phases


def test_full_run_until_balanced() -> None:
    balancer = CellBalancer(max_charge_w=2500, started_at=START)
    battery = Battery(3.35, 0.06)
    phases = run(balancer, battery)
    assert phases[0] is BalancingPhase.PRE_TOP_CHARGE
    assert BalancingPhase.DISCHARGE in phases  # at least one retry
    assert phases[-1] is BalancingPhase.DONE
    assert balancer.last_delta_mv <= 30
    assert battery.voltage <= 3.48


def test_rejected_charge_lowers_retry_voltage() -> None:
    balancer = CellBalancer(max_charge_w=2500, started_at=START)
    balancer.step(0, START, 3.50, 3.40, 0)  # enters CHARGE directly (above top zone)
    assert balancer.phase is BalancingPhase.CHARGE
    for t in range(15, 40, 5):  # BMS refuses: power stays 0
        balancer.step(t, START + t, 3.55, 3.45, 0)
    assert balancer.phase is BalancingPhase.WAIT_MEASURE
    assert balancer.retry_voltage == 3.48


def test_refusal_with_standby_draw() -> None:
    # A Venus at standby shows about -13 W DC while the BMS refuses charging.
    balancer = CellBalancer(max_charge_w=2500, started_at=START)
    balancer.step(0, START, 3.55, 3.47, -13)
    assert balancer.phase is BalancingPhase.CHARGE
    for t in range(15, 40, 5):
        balancer.step(t, START + t, 3.549, 3.47, -13)
    assert balancer.phase is BalancingPhase.WAIT_MEASURE


def test_invalid_telemetry_stops_the_run() -> None:
    balancer = CellBalancer(max_charge_w=2500, started_at=START)
    step = balancer.step(0, START, None, 3.3, 0)
    assert step.phase is BalancingPhase.ERROR and step.power_w == 0
    assert balancer.error == "telemetry"


def test_initial_climb_uses_surplus_with_minimum() -> None:
    balancer = CellBalancer(max_charge_w=2500, started_at=START)
    assert balancer.step(0, START, 3.35, 3.33, 0, surplus_w=-400).power_w == TOP_CHARGE_W
    assert balancer.step(5, START + 5, 3.35, 3.33, 95, surplus_w=1200).power_w == 1200
    assert balancer.step(10, START + 10, 3.36, 3.33, 1200, surplus_w=4000).power_w == 2500


def test_run_stops_after_max_duration() -> None:
    balancer = CellBalancer(max_charge_w=2500, started_at=START)
    balancer.step(0, START, 3.35, 3.33, 0)
    step = balancer.step(10, START + MAX_RUN_S, 3.40, 3.36, 95)
    assert step.phase is BalancingPhase.ERROR and step.power_w == 0
    assert balancer.error == "timeout"


def test_restored_run_measures_again_after_restart() -> None:
    balancer = CellBalancer(max_charge_w=2500, started_at=START)
    balancer.step(0, START, 3.61, 3.50, 95)  # PRE_TOP -> CHARGE -> WAIT_MEASURE
    assert balancer.phase is BalancingPhase.WAIT_MEASURE
    balancer.step(50, START + 50, 3.58, 3.52, 0)
    restored = CellBalancer.from_dict(balancer.as_dict(), 2500)
    assert restored.phase is BalancingPhase.WAIT_MEASURE
    assert restored.started_at == START
    # New monotonic clock after the restart: the 60 s rest starts again.
    assert restored.step(1, START + 60, 3.58, 3.52, 0).phase is BalancingPhase.WAIT_MEASURE
    step = restored.step(62, START + 121, 3.58, 3.52, 0)
    assert step.phase is BalancingPhase.DISCHARGE and restored.last_delta_mv == 60.0


def test_pause_restarts_the_measurement_rest() -> None:
    balancer = CellBalancer(max_charge_w=2500, started_at=START)
    balancer.step(0, START, 3.61, 3.50, 95)
    balancer.step(50, START + 50, 3.58, 3.57, 0)
    balancer.pause()
    assert balancer.step(70, START + 70, 3.58, 3.57, 0).phase is BalancingPhase.WAIT_MEASURE
    assert balancer.step(131, START + 131, 3.58, 3.57, 0).phase is BalancingPhase.FINAL_DISCHARGE
