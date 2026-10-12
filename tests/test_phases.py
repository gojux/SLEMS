"""Learning the phase of the devices and checking whether the meter's counters net the phases."""

import random

import pytest

from custom_components.slems.grid_quarters import GridQuarters
from custom_components.slems.netting import NettingCheck, per_phase_pattern
from custom_components.slems.phases import Phase, PhaseLearner, PhaseModel, classify, pattern_of, replay


def test_classify_one_or_three_phases() -> None:
    assert classify(800, [790, 20, -30]) is Phase.L1
    assert classify(-800, [-10, 15, -760]) is Phase.L3
    # A meter with the opposite sign per phase.
    assert classify(800, [0, -820, 10]) is Phase.L2
    assert classify(3000, [1000, 950, 1050]) is Phase.THREE_PHASE
    # Another load switched on another phase at the same time, or too small.
    assert classify(800, [790, 500, 0]) is None
    assert classify(200, [200, 0, 0]) is None


def simulate(model: PhaseModel, hours: float, *, elwa_phase: int = 0, start: float = 0.0, seed: int = 1) -> float:
    """A house with three batteries on L1, L2, L3 that the control moves alike against all loads in
    the day, a heating rod on one phase, a heat pump on three phases and loads SLEMS does not measure."""
    rng = random.Random(seed)
    elwa = heat_pump = 0.0
    other = [150.0, 200.0, 100.0]
    now = start
    while now < start + hours * 3600:
        if rng.random() < 5 / 1200:
            elwa = 0.0 if elwa else 3400.0
        if rng.random() < 5 / 2400:
            heat_pump = 0.0 if heat_pump else 1200.0
        if rng.random() < 5 / 600:
            other[rng.randrange(3)] = rng.choice((50.0, 300.0, 900.0, 1500.0))
        loads = [other[k] + heat_pump / 3 + (elwa if k == elwa_phase else 0.0) for k in range(3)]
        # Zero feed-in: the batteries discharge the whole import, each a third and a bit apart.
        day = 8 <= now / 3600 % 24 < 18
        batteries = [-sum(loads) / 3 + rng.uniform(-60, 60) if day else 0.0 for _ in range(3)]
        phases = [loads[k] + batteries[k] for k in range(3)]
        readings = {"bat1": batteries[0], "bat2": batteries[1], "bat3": batteries[2], "elwa": elwa, "wp": heat_pump}
        if model.add(now, readings, phases, 0.0):
            model.evaluate()
        now += 5
    return now


def make_model() -> PhaseModel:
    model = PhaseModel()
    model.configure(["bat1", "bat2", "bat3", "elwa", "wp"], ["bat1", "bat2", "bat3"])
    return model


def test_consumers_are_learned_although_the_batteries_compensate() -> None:
    model = make_model()
    simulate(model, 24)
    phases = {device: learner.phase for device, learner in model.learners.items()}
    assert phases == {"bat1": None, "bat2": None, "bat3": None, "elwa": Phase.L1, "wp": Phase.THREE_PHASE}
    shares = model.learner("elwa").shares
    assert shares is not None and shares[0] == pytest.approx(1.0, abs=0.1)


def test_diagnostics_show_the_statistics() -> None:
    model = make_model()
    simulate(model, 24)
    state = model.diagnostics()
    elwa = state["devices"]["elwa"]
    assert elwa["phase"] == "l1" and elwa["shares"][0] == pytest.approx(1.0, abs=0.1)
    assert max(elwa["errors"]) <= 0.15 and elwa["changes"] >= 6
    assert state["devices"]["bat1"]["battery"]


def test_a_few_steps_are_not_enough() -> None:
    model = make_model()
    simulate(model, 0.5)
    assert model.learner("elwa").phase is None


def test_a_moved_consumer_is_reported() -> None:
    model = make_model()
    now = simulate(model, 24)
    # Plugged into another socket: the old blocks fade.
    simulate(model, 24 * 5, elwa_phase=2, start=now, seed=2)
    learner = model.learner("elwa")
    assert (learner.phase, learner.changed_to) == (Phase.L1, Phase.L3)
    learner.choose(Phase.L3, manual=False)
    assert (learner.phase, learner.changed_to) == (Phase.L3, None)


def test_the_patterns() -> None:
    assert pattern_of((0.93, 0.05, 0.06)) is Phase.L1
    assert pattern_of((0.37, 0.38, 0.16)) is Phase.THREE_PHASE
    # Partly taken up by the batteries: about 60 % of the power on the meter.
    assert pattern_of((0.185, 0.235, 0.185)) is Phase.THREE_PHASE
    # Hardly on the meter at all: the direction is noise.
    assert pattern_of((0.05, 0.01, 0.0)) is None
    # Between one and three phases, or nothing.
    assert pattern_of((0.6, 0.5, 0.0)) is None
    assert pattern_of((0.0, 0.0, 0.0)) is None


def test_relearning_the_same_phase_asks_to_confirm() -> None:
    learner = PhaseLearner()
    assert learner.observe(Phase.L2)
    learner.relearn()
    assert learner.phase is None
    learner.observe(Phase.L2)
    assert learner.phase is Phase.L2 and learner.confirm is Phase.L2
    learner.choose(Phase.L1, manual=True)
    restored = PhaseLearner.from_dict(learner.as_dict())
    assert (restored.phase, restored.manual, restored.confirm) == (Phase.L1, True, None)
    # A measurement against the chosen phase is reported, the chosen one stays.
    restored.observe(Phase.L2)
    assert (restored.phase, restored.changed_to) == (Phase.L1, Phase.L2)


def test_stored_and_restored() -> None:
    model = make_model()
    simulate(model, 6)
    restored = PhaseModel.from_dict(model.as_dict())
    assert not restored.configure(["bat1", "bat2", "bat3", "elwa", "wp"], ["bat1", "bat2", "bat3"])
    assert restored.shares("elwa") == pytest.approx(model.shares("elwa"))
    # Another device: the statistics start anew, the phases stay.
    assert restored.configure(["bat1", "bat2", "bat3", "elwa", "wp", "wallbox"], ["bat1", "bat2", "bat3"])
    assert restored.empty and restored.learner("elwa").phase is model.learner("elwa").phase
    # Stored before the statistics: only the learners.
    old = PhaseModel.from_dict({"elwa": {"phase": "l1", "votes": ["l1"]}})
    assert old.learner("elwa").phase is Phase.L1 and old.empty


def test_the_recorded_history_is_replayed() -> None:
    model = PhaseModel()
    model.configure(["elwa", "wallbox"], [])
    elwa: list[tuple[float, float | None]] = []
    wallbox: list[tuple[float, float | None]] = []
    active: list[tuple[float, float | None]] = []
    phases: list[list[tuple[float, float | None]]] = [[], [], []]
    rng = random.Random(3)
    for minute in range(0, 24 * 60, 10):
        t = minute * 60.0
        on = rng.random() < 0.5
        three = rng.random() < 0.5
        charging = rng.choice((0.0, 4200.0))
        elwa.append((t + 4, 3000.0 if on else 0.0))
        wallbox.append((t, charging))
        active.append((t, 3.0 if three else 1.0))
        spread = charging / 3 if three else 0.0
        phases[0].append((t, spread))
        phases[1].append((t, (3000.0 if on else 0.0) + spread))
        phases[2].append((t, spread + (0.0 if three else charging)))
    replay(model, 0.0, 24 * 3600.0, phases, {"elwa": elwa, "wallbox": wallbox}, active_phases={"wallbox": active})
    model.evaluate()
    assert model.learner("elwa").phase is Phase.L2
    assert model.learner("wallbox").phase is Phase.L3


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
