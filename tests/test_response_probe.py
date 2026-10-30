"""Tests for the probe step of a battery (response_probe)."""

import pytest

from custom_components.slems.phases import Phase as GridPhase
from custom_components.slems.response_probe import (
    ATTEMPTS,
    MAX_AGE_S,
    PAUSE_S,
    RETRY_S,
    ProbeRecord,
    QuietWatch,
    Report,
    ResponseProbe,
    Stage,
    measure,
    probe_offset_w,
)

METER_S = 0.48


def test_offset_follows_the_house_and_respects_the_allowed_power() -> None:
    # Charging 800 W with a surplus: 500 W more; with a deficit: 500 W less.
    assert probe_offset_w(800, -1500, 2500, 2500) == 500
    assert probe_offset_w(800, 300, 2500, 2500) == -500
    # Discharging with a deficit: more discharging.
    assert probe_offset_w(-800, 300, 2500, 2500) == -500
    # Near full (charging allowed only up to 1000 W): the rest above 300 W, else a reduction.
    assert probe_offset_w(800, -1500, 1200, 2500) == 400
    assert probe_offset_w(800, -1500, 1000, 2500) == -500
    # A small power leaves at least 100 W: 350 W less at most, never a reversal.
    assert probe_offset_w(450, 300, 450, 2500) == -350
    # Neither direction possible, or not running.
    assert probe_offset_w(300, 300, 300, 2500) is None
    assert probe_offset_w(0, 300, 2500, 2500) is None


def test_measure_takes_the_last_report_before_the_movement() -> None:
    # Step +500 W written at 10.0; dead time 0.6 s, then 0.4 s ramp.
    reports = [Report(10.0 + k * METER_S, 100 + _ramp(k * METER_S, 0.6, 0.4) * 500) for k in range(1, 8)]
    result = measure(10.0, 100.0, 500.0, reports)
    # 0.48 s still, 0.96 s moved by 72 %: dead time at least 0.48 s, arrived after 0.96 s.
    assert result == {"change_w": 500, "dead_s": 0.48, "arrived_s": 0.96}


@pytest.mark.parametrize(
    ("values", "reason"),
    [
        ([100, -200, -300], "against"),  # the house moved the other way
        ([100, 400, 900], "jump"),  # more than 1.5 times the step
        ([100, 150, 200, 250], "none"),  # never 60 %
    ],
)
def test_measure_unclear(values: list[float], reason: str) -> None:
    reports = [Report(10.0 + (k + 1) * METER_S, v) for k, v in enumerate(values)]
    assert measure(10.0, 100.0, 500.0, reports)["reason"] == reason


def _ramp(elapsed: float, dead_s: float, ramp_s: float) -> float:
    return min(1.0, max(0.0, (elapsed - dead_s) / ramp_s))


class _Battery:
    """A battery behind the meter: dead time, then a linear ramp; the house at ``house_w``."""

    def __init__(self, probe: ResponseProbe, dead_s: float = 0.6, ramp_s: float = 0.4, phases: bool = False) -> None:
        self.probe = probe
        self.dead_s = dead_s
        self.ramp_s = ramp_s
        self.phases = phases
        # (write time, power before, power after)
        self.writes: list[tuple[float, float, float]] = []
        self.house_w = 100.0
        self.power = 0.0

    def grid(self, t: float) -> float:
        power = self.power_at(t)
        return self.house_w + power

    def power_at(self, t: float) -> float:
        power = self.writes[0][1] if self.writes else self.power
        for written, before, after in self.writes:
            power = before + (after - before) * _ramp(t - written, self.dead_s, self.ramp_s)
        return power

    def run(self, start: float, base_w: float, offset_w: float, until: float) -> float:
        """Meter reports and the controller's writes until ``until``; returns the time reached."""
        self.power = base_w
        t = start
        while t < until and self.probe.stage is not Stage.DONE:
            if self.probe.ready(t):
                self.probe.begin(t, base_w, offset_w)
            target = self.probe.target_w
            if target is not None and round(target) != round(self.power_at(t + 100)):
                # The controller writes in the next cycle after the probe asks.
                self.writes.append((t + 0.05, self.power_at(t + 100), target))
                self.probe.written(t + 0.05, target)
            t += METER_S
            grid = self.grid(t)
            phases = (grid - self.house_w + 50.0, 25.0, 25.0) if self.phases else None
            self.probe.report(Report(t, grid, phases))
        return t


def test_probe_measures_the_dead_time_and_the_phase() -> None:
    probe = ResponseProbe("manual", 0.0)
    battery = _Battery(probe, phases=True)
    battery.run(0.0, 800.0, 500.0, 200.0)
    assert probe.stage is Stage.DONE and probe.result == "ok"
    assert probe.attempts == ATTEMPTS
    # Three attempts, each the step and the step back.
    assert len(probe.measurements) == 2 * ATTEMPTS
    assert all("dead_s" in m for m in probe.measurements)
    # A lower bound: below the real 0.6 s by less than one meter interval.
    assert 0.6 - METER_S <= probe.dead_time_s <= 0.6
    assert all(0.6 < s <= 0.6 + 0.4 + METER_S for s in probe.arrived_s)
    assert probe.phase is GridPhase.L1
    assert not probe.holds_others and probe.target_w is None


def test_probe_pauses_between_attempts_and_holds_only_while_measuring() -> None:
    probe = ResponseProbe("age", 0.0)
    assert probe.ready(0.0) and not probe.holds_others
    probe.begin(0.0, -600.0, -500.0)
    assert probe.holds_others and probe.target_w == -600.0
    for k in range(1, 4):
        probe.report(Report(k * METER_S, 200.0))
    assert probe.stage is Stage.STEP and probe.target_w == -1100.0
    probe.written(1.5, -1100.0)
    probe.advance(4.6)
    assert probe.stage is Stage.BACK and probe.target_w == -600.0
    probe.written(4.6, -600.0)
    probe.advance(7.7)
    # Nothing moved: both measurements unclear, the next attempt after the pause.
    assert probe.stage is Stage.PAUSE and not probe.holds_others
    assert [m["reason"] for m in probe.measurements] == ["none", "none"]
    assert not probe.ready(7.7 + PAUSE_S - 1) and probe.ready(7.7 + PAUSE_S)


def test_restless_grid_or_no_write_makes_an_attempt_unclear() -> None:
    probe = ResponseProbe("manual", 0.0)
    probe.begin(0.0, 800.0, 500.0)
    probe.report(Report(0.5, 100.0))
    probe.report(Report(1.0, 180.0))
    assert probe.stage is Stage.PAUSE
    assert probe.measurements == [{"change_w": 500, "reason": "noisy"}]
    probe.begin(20.0, 800.0, 500.0)
    probe.report(Report(20.5, 100.0))
    probe.report(Report(21.0, 110.0))
    probe.advance(26.5)
    assert probe.measurements[-1] == {"change_w": 500, "reason": "not_written"}
    probe.begin(40.0, 800.0, 500.0)
    probe.report(Report(40.5, 100.0))
    probe.report(Report(41.0, 100.0))
    probe.advance(47.0)
    assert probe.stage is Stage.DONE and probe.result == "unclear" and probe.dead_time_s is None


def test_house_load_jumping_in_the_step_is_not_measured() -> None:
    probe = ResponseProbe("manual", 0.0)
    battery = _Battery(probe)
    probe.begin(0.0, 800.0, 500.0)
    battery.power = 800.0
    for k in range(1, 4):
        probe.report(Report(k * METER_S, battery.grid(k * METER_S)))
    probe.written(1.5, 1300.0)
    battery.writes.append((1.5, 800.0, 1300.0))
    # A kettle switches on with the step.
    battery.house_w = 2100.0
    probe.report(Report(1.9, battery.grid(1.9)))
    assert probe.measurements == []
    probe.advance(4.6)
    probe.written(4.6, 800.0)
    probe.advance(7.7)
    assert probe.measurements[0]["reason"] == "jump"


def test_quiet_watch() -> None:
    watch = QuietWatch()
    for k in range(25):
        watch.add(k * 0.5, 300 + (20 if k % 2 else -20))
    assert watch.quiet(12.0)
    watch.add(12.5, 700)
    assert not watch.quiet(12.5)
    # Too short a history is not quiet.
    fresh = QuietWatch()
    fresh.add(0.0, 300)
    fresh.add(0.5, 300)
    assert not fresh.quiet(0.5)


def _finished(result: str, reason: str = "new", dead: float = 0.5) -> ResponseProbe:
    probe = ResponseProbe(reason, 0.0)
    probe.result = result
    if result == "ok":
        probe.measurements = [{"change_w": 500, "dead_s": dead, "arrived_s": 0.9}]
    probe.stage = Stage.DONE
    return probe


def test_record_is_due_for_a_new_battery_and_after_90_days() -> None:
    record = ProbeRecord()
    assert record.due(0.0) is None
    assert record.check({"config": {"key": "a"}}) == []
    assert record.due(0.0) == "new"
    record.finished(_finished("ok"), 100.0)
    assert record.due(101.0) is None and record.dead_time_s == 0.5
    assert record.due(100.0 + MAX_AGE_S) == "age"


def test_record_changes_drop_the_dead_time_and_ask_for_a_probe() -> None:
    record = ProbeRecord()
    record.check({"config": {"key": "a"}, "device": {"firmware": "1"}, "meter": {"config": "m"}})
    record.finished(_finished("ok"), 100.0)
    # A value missing on one side (not read once) changes nothing.
    assert record.check({"config": {"key": "a"}, "device": {}, "meter": {"config": "m", "sw_version": "2"}}) == []
    assert record.due(200.0) is None
    assert record.check({"config": {"key": "a"}, "device": {"firmware": "2"}, "meter": {"config": "m"}}) == ["device"]
    assert record.due(200.0) == "firmware" and record.dead_time_s is None
    record.finished(_finished("ok"), 300.0)
    assert record.check({"meter": {"config": "m", "sw_version": "3"}}) == ["meter"]
    assert record.due(300.0) == "meter"


def test_record_retries_an_unclear_probe_after_an_hour() -> None:
    record = ProbeRecord()
    record.check({"config": {"key": "a"}})
    record.finished(_finished("unclear"), 100.0)
    assert record.failed == "unclear"
    assert record.due(100.0 + RETRY_S - 1) is None
    assert record.due(100.0 + RETRY_S) == "new"
    # By hand at once; the reason asked for before stays until a probe succeeds.
    record.request("manual")
    assert record.due(101.0) == "new"
    record.finished(_finished("not_quiet", "new"), 102.0)
    assert record.failed == "not_quiet" and record.due(102.0 + RETRY_S) == "new"
    record.finished(_finished("ok", "new", 0.7), 200.0)
    assert record.failed is None and record.dead_time_s == 0.7 and record.due(201.0) is None
    # Asked for by hand alone: tried once.
    record.request("manual")
    assert record.due(201.0) == "manual"
    record.finished(_finished("unclear", "manual"), 202.0)
    assert record.due(202.0 + RETRY_S) is None and record.dead_time_s == 0.7


def test_record_survives_a_restart() -> None:
    record = ProbeRecord()
    record.check({"config": {"key": "a"}, "device": {"firmware": "1"}})
    record.finished(_finished("ok"), 100.0)
    restored = ProbeRecord.from_dict(record.as_dict())
    assert restored == record
    assert ProbeRecord.from_dict(None) == ProbeRecord()


def test_hints_that_the_probe_result_may_be_outdated() -> None:
    from custom_components.slems.response_probe import HINT_INTERVAL_S, ProbeHints, ProbeRecord

    record = ProbeRecord(arrived_s=0.9, meter_interval_s=0.5)
    hints = ProbeHints()
    steady = [0.85, 0.9, 0.95, 0.9, 0.88]
    assert hints.check(0.0, record, 0.5, steady) == []
    # The meter reports every second: a hint only after a day of it.
    assert hints.check(100.0, record, 1.0, steady) == []
    assert hints.check(100.0 + 86400.0, record, 1.0, steady) == ["cadence"]
    # Then at most once a day.
    assert hints.check(200.0 + 86400.0, record, 1.0, steady) == []
    # Learned step time far from the probe's, and widely scattered values.
    hints = ProbeHints()
    assert hints.check(0.0, record, 0.5, [1.3, 1.4, 1.35, 1.3, 1.4]) == ["drift"]
    assert hints.check(10.0, record, 0.5, [0.4, 0.9, 1.5, 0.5, 1.4, 0.9]) == ["scatter"]
    # Not delivered twice within a day.
    assert not hints.not_delivered(0.0)
    assert hints.not_delivered(3600.0)
    assert not hints.not_delivered(7200.0)
    assert not hints.not_delivered(3600.0 + HINT_INTERVAL_S + 90000.0)


def test_probe_record_keeps_the_reference_for_the_hints() -> None:
    from custom_components.slems.response_probe import ProbeRecord

    record = ProbeRecord.from_dict({"arrived_s": 0.9, "meter_interval_s": 0.48})
    assert ProbeRecord.from_dict(record.as_dict()).arrived_s == 0.9
    assert ProbeRecord.from_dict(record.as_dict()).meter_interval_s == 0.48
