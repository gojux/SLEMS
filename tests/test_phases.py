"""Learning the phase of a device and checking whether the meter's counters net the phases."""

from custom_components.slems.grid_quarters import GridQuarters
from custom_components.slems.netting import NettingCheck, per_phase_pattern
from custom_components.slems.phases import Phase, PhaseLearner, classify


def test_classify_one_or_three_phases() -> None:
    assert classify(800, [790, 20, -30]) is Phase.L1
    assert classify(-800, [-10, 15, -760]) is Phase.L3
    # A meter with the opposite sign per phase.
    assert classify(800, [0, -820, 10]) is Phase.L2
    assert classify(3000, [1000, 950, 1050]) is Phase.THREE_PHASE
    # Another load switched on another phase at the same time, or too small.
    assert classify(800, [790, 500, 0]) is None
    assert classify(200, [200, 0, 0]) is None


def steps(learner: PhaseLearner, start: float, levels: list[tuple[float, tuple[float, float, float]]]) -> list:
    """Each level held for 40 s, a reading every 5 s; returns the votes."""
    votes = []
    now = start
    for device, phases in levels:
        for _ in range(8):
            if (vote := learner.add(now, device, phases)) is not None:
                votes.append(vote)
            now += 5
    return votes


BASE = (300.0, 200.0, 100.0)


def on_l2(device: float) -> tuple[float, float, float]:
    return (BASE[0], BASE[1] + device, BASE[2])


def test_learns_after_agreeing_votes_and_reports_a_change() -> None:
    learner = PhaseLearner()
    cycle = [(0.0, on_l2(0)), (900.0, on_l2(900))]
    assert steps(learner, 0, cycle * 2) == [Phase.L2, Phase.L2, Phase.L2]
    assert learner.phase is Phase.L2
    # Plugged into another socket: three steps in a row on L3.
    learner_votes = steps(learner, 1000, [(0.0, BASE), (900.0, (BASE[0], BASE[1], BASE[2] + 900))] * 2)
    assert learner_votes[-3:] == [Phase.L3] * 3
    assert learner.phase is Phase.L2 and learner.changed_to is Phase.L3
    learner.choose(Phase.L3, manual=False)
    assert (learner.phase, learner.changed_to, list(learner.votes)) == (Phase.L3, None, [])


def test_a_step_with_another_load_does_not_vote() -> None:
    learner = PhaseLearner()
    other = (BASE[0] + 700, BASE[1] + 900, BASE[2])
    assert steps(learner, 0, [(0.0, BASE), (900.0, other)]) == []


def test_relearning_the_same_phase_asks_to_confirm() -> None:
    learner = PhaseLearner()
    cycle = [(0.0, on_l2(0)), (900.0, on_l2(900))]
    steps(learner, 0, cycle * 2)
    learner.relearn()
    assert learner.phase is None
    steps(learner, 1000, cycle * 2)
    assert learner.phase is Phase.L2 and learner.confirm is Phase.L2
    learner.choose(Phase.L1, manual=True)
    restored = PhaseLearner.from_dict(learner.as_dict())
    assert (restored.phase, restored.manual, restored.confirm) == (Phase.L1, True, None)


def test_counters_per_phase_are_found() -> None:
    # 200 Wh drawn on one phase while 150 Wh were fed in on another: both counters run.
    assert per_phase_pattern(imported_wh=250, exported_wh=200, grid_import_wh=50, grid_export_wh=0)
    # Netted counters, or a late reading that moves energy into the next quarter hour.
    assert not per_phase_pattern(imported_wh=50, exported_wh=0, grid_import_wh=50, grid_export_wh=0)
    assert not per_phase_pattern(imported_wh=90, exported_wh=0, grid_import_wh=50, grid_export_wh=0)


def test_netting_check_over_quarter_hours() -> None:
    quarters = GridQuarters()
    check = NettingCheck()
    counters = ("sensor.import", "sensor.export")
    imported = exported = 0.0
    start = 1_800_000_000 - 1_800_000_000 % 900
    for quarter in range(6):
        slot = start + quarter * 900
        # Net import of 200 W, so 50 Wh per quarter hour; the counters add 100 Wh each.
        for second in range(0, 900, 5):
            quarters.add(200.0, slot + second)
            if second <= 25:
                check.observe(slot + second, counters, imported, exported, quarters)
        imported += 0.150
        exported += 0.100
    check.observe(start + 6 * 900, counters, imported, exported, quarters)
    assert check.not_netted
    # Other counters start anew.
    check.observe(start + 6 * 900 + 1, ("sensor.a", "sensor.b"), 0, 0, quarters)
    assert not check.not_netted


def test_phase_dialog_preselects_the_measured_phase() -> None:
    import asyncio
    from types import SimpleNamespace

    from custom_components.slems.repairs import PhaseFlow

    learner = PhaseLearner()
    learner.phase, learner.changed_to = Phase.L1, Phase.L3
    chosen = []
    coordinator = SimpleNamespace(
        phase_learner=lambda _id: learner,
        phase_devices={"venus": "Venus"},
        choose_phase=lambda subentry_id, phase: chosen.append((subentry_id, phase)),
    )
    flow = PhaseFlow("venus")
    flow.hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_loaded_entries=lambda _domain: [SimpleNamespace(runtime_data=coordinator)])
    )
    flow.flow_id, flow.handler = "flow", "slems"
    form = asyncio.run(flow.async_step_init())
    assert form["description_placeholders"] == {"name": "Venus", "phase": "L1", "measured": "L3"}
    assert next(iter(form["data_schema"].schema)).default() == "l3"
    asyncio.run(flow.async_step_init({"phase": "l2"}))
    assert chosen == [("venus", Phase.L2)]
