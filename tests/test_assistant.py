"""Tests for the suggestions of the settings assistant."""

from datetime import date

from custom_components.slems.assistant import (
    BatteryFacts,
    Context,
    Observations,
    TariffFacts,
    notes,
    observations_from_comparison,
    suggestions,
    tariff_facts,
)
from custom_components.slems.coordinator import ControlSettings
from custom_components.slems.full_charge import DAY_S
from custom_components.slems.tariff import Group, Role, Side, Tariff, TariffItem, Unit

NOW = 1_800_000_000.0


def context(**changes) -> Context:
    values = {
        "settings": ControlSettings(),
        "market_prices": False,
        "price_source": "apg",
        "country": "AT",
        "tariff": TariffFacts(),
        "batteries": [],
        "observations": Observations(),
        "now": NOW,
    }
    values.update(changes)
    return Context(**values)


def by_key(ctx: Context) -> dict:
    return {(s.key, s.device_id): s for s in suggestions(ctx)}


def battery(**changes) -> BatteryFacts:
    values = {
        "device_id": "dev1",
        "name": "Venus 1",
        "learn_capacity": False,
        "capacity_learned": False,
        "last_full": NOW - DAY_S,
    }
    values.update(changes)
    return BatteryFacts(**values)


def test_basic_rules_without_tariff() -> None:
    result = by_key(context())
    for key in ("auto_gain", "timing_auto", "night_reserve_auto", "feed_in_cap_auto_buffer"):
        assert result[(key, None)].value is True
        assert result[(key, None)].sources[0].kind == "rule"
    # The learned grid targets are under review.
    assert ("grid_targets_auto", None) not in result
    assert result[("regular_full_charge", None)].value is True
    # No tariff: nothing from the tariff.
    assert ("grid_charge", None) not in result
    assert ("market_prices", None) not in result


def test_full_charge_names_the_batteries_not_full() -> None:
    ctx = context(
        batteries=[
            battery(),
            battery(device_id="dev2", name="Venus 2", last_full=NOW - 10 * DAY_S),
            battery(device_id="dev3", name="Venus 3", last_full=None),
        ]
    )
    sources = by_key(ctx)[("regular_full_charge", None)].sources
    assert [s.params for s in sources if s.kind == "observation"] == [
        {"name": "Venus 2", "days": 10},
        {"name": "Venus 3", "days": None},
    ]


def test_learned_capacity_per_battery() -> None:
    result = by_key(context(batteries=[battery(capacity_learned=True), battery(device_id="dev2")]))
    assert result[("learn_capacity", "dev1")].value is True
    assert result[("learn_capacity", "dev1")].sources[0].code == "learned_capacity"
    # Not learned yet: suggested all the same (the configured capacity applies until then).
    assert result[("learn_capacity", "dev2")].value is True
    assert result[("learn_capacity", "dev2")].sources[0].code == "auto"
    # Battery settings are not simulated as a setting.
    assert result[("learn_capacity", "dev1")].field is None


def test_fixed_tariff() -> None:
    result = by_key(context(tariff=TariffFacts(has_tariff=True)))
    assert result[("grid_charge", None)].value is False
    assert result[("battery_export", None)].value is False
    assert result[("grid_charge", None)].field == "grid_charge"
    assert ("market_prices", None) not in result
    assert ("peak_shaving_mode", None) not in result


def test_dynamic_tariff_with_power_price() -> None:
    tariff = TariffFacts(
        has_tariff=True, needs_market=True, import_market=True, export_market=True,
        export_zero_negative=True, power_priced=True,
    )
    result = by_key(context(tariff=tariff, price_source="smard"))
    assert ("grid_charge", None) not in result
    assert ("battery_export", None) not in result
    assert result[("market_prices", None)].value is True
    # Austria: the source of an Austrian bidding zone.
    assert result[("price_source", None)].value == "apg"
    assert result[("peak_shaving_mode", None)].value == "economic"
    assert result[("peak_shaving_mode", None)].field == "peak_shaving_mode"
    assert result[("grid_friendly_charging", None)].value is True


def test_price_source_of_the_same_country_is_kept() -> None:
    tariff = TariffFacts(has_tariff=True, needs_market=True)
    assert ("price_source", None) not in by_key(context(tariff=tariff, price_source="energy_charts_at"))
    # Luxembourg shares the bidding zone with Germany.
    assert ("price_source", None) not in by_key(context(tariff=tariff, country="LU", price_source="smard"))
    # A country without a source: nothing.
    assert ("price_source", None) not in by_key(context(tariff=tariff, country="IT"))


def test_observations_above_the_threshold() -> None:
    tariff = TariffFacts(has_tariff=True, needs_market=True, import_market=True, power_priced=True)
    seen = Observations(price_saving=4.2, price_months=3, peak_saving=0.5, peak_limit_kw=3.8, peak_months=3)
    result = by_key(context(tariff=tariff, observations=seen))
    source = result[("price_control", None)].sources[0]
    assert (source.code, source.params) == ("price_backtest", {"saving": 4.2, "months": 3})
    # Below the threshold: no suggestion.
    assert ("peak_shaving", None) not in result
    seen = Observations(peak_saving=6.0, peak_limit_kw=3.8, peak_months=2)
    source = by_key(context(tariff=tariff, observations=seen))[("peak_shaving", None)].sources[0]
    assert source.params == {"saving": 6.0, "months": 2, "limit_kw": 3.8}


def test_a_later_layer_does_not_contradict_an_earlier_one(monkeypatch) -> None:
    # A fixed feed-in credit: battery export off, whatever an observation says.
    from custom_components.slems import assistant

    monkeypatch.setattr(
        assistant,
        "_observation_rules",
        lambda _ctx: [
            assistant.Suggestion("battery_export", True, [assistant.Source("observation", "x")]),
            assistant.Suggestion("grid_charge", False, [assistant.Source("observation", "y")]),
        ],
    )
    result = by_key(context(tariff=TariffFacts(has_tariff=True)))
    assert result[("battery_export", None)].value is False
    assert [s.code for s in result[("battery_export", None)].sources] == ["export_fixed"]
    # The same value: both sources.
    assert [s.code for s in result[("grid_charge", None)].sources] == ["import_fixed", "y"]


def test_observations_from_the_complete_months() -> None:
    result = {
        "months": [
            {"month": "2026-07", "costs": {"t": {"total": 1}}, "savings": {"t": 9.0}},
            {"month": "2026-08", "costs": {"t": {"total": 1, "peak_saving": 4.0, "peak_limit_kw": 3.5}}, "savings": {"t": 2.0}},
            {"month": "2026-09", "costs": {"t": {"total": 1}}, "savings": {"t": 3.0}},
            {"month": "2026-10", "costs": {"t": {"total": 1, "peak_saving": 8.0, "peak_limit_kw": 4.1}}, "savings": {"t": 4.0}},
            # The running month is left out.
            {"month": "2026-11", "costs": {"t": {"total": 1, "peak_saving": 50.0, "peak_limit_kw": 9.0}}, "savings": {"t": 99.0}},
        ]
    }
    seen = observations_from_comparison(result, "t", date(2026, 11, 22))
    assert (seen.price_saving, seen.price_months) == (3.0, 3)
    # A month without a peak saving counts as 0; the highest limit held in every month.
    assert (seen.peak_saving, seen.peak_limit_kw, seen.peak_months) == (4.0, 4.1, 3)


def test_observations_without_data() -> None:
    assert observations_from_comparison(None, "t", date(2026, 11, 22)) == Observations()
    only_running = {"months": [{"month": "2026-11", "costs": {"t": {}}, "savings": {"t": 5.0}}]}
    assert observations_from_comparison(only_running, "t", date(2026, 11, 22)) == Observations()
    # No backtest (no batteries): no price saving, no peak without a power price.
    plain = {"months": [{"month": "2026-10", "costs": {"t": {"total": 1}}}]}
    assert observations_from_comparison(plain, "t", date(2026, 11, 22)) == Observations()


def test_tariff_facts_of_a_tariff() -> None:
    assert tariff_facts(None) == TariffFacts()
    tariff = Tariff("T", Role.CURRENT, (
        TariffItem("Energy", Side.IMPORT, Group.ENERGY, Unit.SPOT, 1.5),
        TariffItem("Power", Side.IMPORT, Group.GRID, Unit.KW_MONTH, 5.0),
        TariffItem("Feed-in", Side.EXPORT, Group.ENERGY, Unit.KWH, 7.7, zero_when_negative=True),
    ), {})
    assert tariff_facts(tariff) == TariffFacts(
        has_tariff=True, needs_market=True, import_market=True, export_market=False,
        export_zero_negative=True, power_priced=True,
    )


def test_no_goal_no_goal_suggestions() -> None:
    assert not any(s.kind == "goal" for sug in suggestions(context()) for s in sug.sources)


def test_goal_self_consumption() -> None:
    result = by_key(context(goal="self_consumption"))
    assert result[("night_discharge", None)].value is False
    assert result[("battery_priority_soc", None)].value == 50.0
    assert result[("night_discharge", None)].sources[0].params == {"goal": "self_consumption"}
    # The simulation knows the night discharge, not the battery priority.
    assert result[("night_discharge", None)].field == "night_discharge"
    assert result[("battery_priority_soc", None)].field is None


def test_goal_costs_follows_the_tariff() -> None:
    fixed = by_key(context(goal="costs", tariff=TariffFacts(has_tariff=True)))
    # The tariff says no grid charging; the goal does not contradict it.
    assert fixed[("grid_charge", None)].value is False
    assert ("price_control", None) not in fixed
    dynamic = TariffFacts(
        has_tariff=True, needs_market=True, import_market=True, export_market=True, power_priced=True,
    )
    result = by_key(context(goal="costs", tariff=dynamic))
    assert result[("price_control", None)].value is True
    assert result[("grid_charge", None)].value is True
    assert result[("grid_charge_max_soc", None)].value == 90.0
    assert result[("grid_charge_max_soc", None)].field == "grid_charge_max_soc_pct"
    assert result[("battery_export", None)].value is True
    assert result[("peak_shaving", None)].value is True


def test_no_feed_in_from_the_batteries_without_export() -> None:
    dynamic = TariffFacts(has_tariff=True, needs_market=True, import_market=True, export_market=True)
    no_export = ControlSettings(discharge_max_grid_export_w=0)
    assert ("battery_export", None) not in by_key(context(goal="costs", tariff=dynamic, settings=no_export))
    result = by_key(context(goal="grid", settings=no_export))
    assert ("night_discharge", None) not in result
    assert result[("grid_friendly_charging", None)].value is True
    assert by_key(context(goal="grid"))[("night_discharge", None)].value is True


def test_note_where_the_goal_leaves_out_feed_in() -> None:
    dynamic = TariffFacts(has_tariff=True, needs_market=True, import_market=True, export_market=True)
    no_export = ControlSettings(discharge_max_grid_export_w=0)
    grid = notes(context(goal="grid", settings=no_export))
    assert [(n.code, n.key) for n in grid if n.code == "goal_no_export"] == [("goal_no_export", "night_discharge")]
    costs = notes(context(goal="costs", tariff=dynamic, settings=no_export))
    assert [n.key for n in costs if n.code == "goal_no_export"] == ["battery_export"]
    # Nothing left out: no note.
    assert not [n for n in notes(context(goal="grid")) if n.code == "goal_no_export"]
    assert not [n for n in notes(context(settings=no_export)) if n.code == "goal_no_export"]
    assert not [n for n in notes(context(goal="grid", settings=no_export, has_pv=False)) if n.code == "goal_no_export"]


def test_goal_battery_first_wins_over_a_tariff_preference() -> None:
    tariff = TariffFacts(has_tariff=True, needs_market=True, export_zero_negative=True)
    result = by_key(context(goal="battery_first", tariff=tariff, batteries=[battery()]))
    assert result[("grid_friendly_charging", None)].value is False
    assert result[("battery_share_when_secured", None)].value == 100.0
    assert result[("max_soc", "dev1")].value == 100.0
    # Without the goal the tariff suggests it.
    assert by_key(context(tariff=tariff))[("grid_friendly_charging", None)].value is True


def test_goal_battery_care() -> None:
    result = by_key(context(goal="battery_care", batteries=[battery(), battery(device_id="dev2")]))
    assert result[("temperature_limit", None)].value is True
    assert result[("price_min_gain", None)].value == 5.0
    assert result[("max_soc", "dev1")].value == 90.0
    assert result[("max_soc", "dev2")].value == 90.0
    # The regular full charge stays a basic rule.
    assert result[("regular_full_charge", None)].value is True


def test_minimum_soc_not_below_the_device() -> None:
    below = battery(device_min_soc_pct=12.0, min_soc_pct=5.0)
    result = by_key(context(batteries=[below]))
    source = result[("min_soc", "dev1")].sources[0]
    assert result[("min_soc", "dev1")].value == 12.0
    assert (source.kind, source.code, source.params) == ("plant", "device_min_soc", {"pct": 12.0})
    # At or above the device's minimum, or unknown: nothing.
    assert ("min_soc", "dev1") not in by_key(context(batteries=[battery(device_min_soc_pct=12.0, min_soc_pct=12.0)]))
    assert ("min_soc", "dev1") not in by_key(context(batteries=[battery(min_soc_pct=0.0)]))


def test_goal_battery_care_keeps_both_ends_of_the_soc() -> None:
    venus = battery(device_min_soc_pct=12.0, min_soc_pct=5.0)
    unknown = battery(device_id="dev2")
    result = by_key(context(goal="battery_care", batteries=[venus, unknown]))
    assert result[("min_soc", "dev1")].value == 20.0
    assert result[("min_soc", "dev1")].sources[0].kind == "goal"
    assert result[("min_soc", "dev2")].value == 20.0
    # A device that stops higher than the goal's value.
    high = battery(device_min_soc_pct=25.0, min_soc_pct=25.0)
    assert by_key(context(goal="battery_care", batteries=[high]))[("min_soc", "dev1")].value == 25.0


def test_pv_peak_sums_the_pv_entities() -> None:
    from custom_components.slems.assistant import pv_peak_w

    assert pv_peak_w({}) is None
    assert pv_peak_w({"a": {1: 3000.0, 2: 5000.0}, "b": {1: 4000.0, 2: 1000.0}}) == 7000.0


def test_export_cap_needs_many_days_at_the_same_level() -> None:
    from custom_components.slems.assistant import export_cap

    capped = [6000.0] * 6 + [5950.0, 4000.0, 2500.0] + [3000.0] * 10
    assert export_cap(capped, 9500.0) == (6000.0, 7)
    # Too few days with data, or no PV peak to compare with.
    assert export_cap(capped[:10], 9500.0) is None
    assert export_cap(capped, None) is None
    # Spread out: no cap.
    assert export_cap([float(v) for v in range(2000, 8000, 300)], 9500.0) is None
    # The full PV output on many sunny days is no cap.
    assert export_cap(capped, 6200.0) is None


def test_observed_feed_in_cap() -> None:
    seen = Observations(pv_peak_w=9000.0, export_cap_w=6000.0, export_cap_days=7, export_days=28)
    result = by_key(context(settings=ControlSettings(pv_peak_power_kwp=10.0), observations=seen))
    assert result[("feed_in_cap", None)].value is True
    assert result[("feed_in_cap", None)].sources[0].params == {"kw": 6.0, "days": 7, "of": 28}
    assert result[("feed_in_cap_limit", None)].value == 60.0
    assert result[("feed_in_cap_limit", None)].field == "feed_in_cap_limit_pct"
    assert ("pv_peak_power", None) not in result


def test_pv_peak_power_too_low() -> None:
    # 12 kW measured, 10 kWp configured: the limit in % would be wrong, the PV peak first.
    seen = Observations(pv_peak_w=12030.0, export_cap_w=6000.0, export_cap_days=7, export_days=28)
    result = by_key(context(settings=ControlSettings(pv_peak_power_kwp=10.0), observations=seen))
    assert result[("pv_peak_power", None)].value == 12.1
    assert ("feed_in_cap_limit", None) not in result
    # Without a feed-in cap the PV peak power does not matter.
    seen = Observations(pv_peak_w=12030.0)
    assert ("pv_peak_power", None) not in by_key(context(observations=seen))
    on = ControlSettings(feed_in_cap=True, pv_peak_power_kwp=10.0)
    assert by_key(context(settings=on, observations=seen))[("pv_peak_power", None)].value == 12.1


def test_hot_batteries() -> None:
    from custom_components.slems.assistant import hours_at_or_above

    assert hours_at_or_above({1: 39.9, 2: 40.0, 3: 43.0}, 40.0) == 2
    seen = Observations(hot_hours=(("Venus 1", 5), ("Venus 2", 1)))
    sources = by_key(context(observations=seen))[("temperature_limit", None)].sources
    assert [s.params for s in sources] == [{"name": "Venus 1", "hours": 5, "temp": 40.0}]
    assert ("temperature_limit", None) not in by_key(context(observations=Observations(hot_hours=(("V", 2),))))


def test_feed_in_peaks_at_the_own_limit_are_no_cap() -> None:
    from custom_components.slems.assistant import at_own_limit

    peaks = {
        date(2026, 11, 1): (4000.0, 100),  # at the limit of grid friendly charging
        date(2026, 11, 2): (6000.0, 200),  # above the own limit: the inverter
        date(2026, 11, 3): (5000.0, 300),  # no own limit in that hour
        date(2026, 11, 4): (3050.0, 400),  # within the tolerance of a limit changing in the hour
    }
    limits = {100: (4000.0, 4000.0), 200: (4500.0, 4500.0), 400: (3100.0, 3600.0)}
    assert at_own_limit(peaks, limits) == {date(2026, 11, 1), date(2026, 11, 4)}


def test_days_not_full_although_fed_in() -> None:
    from custom_components.slems.assistant import FriendlyDay, short_days

    days = [
        FriendlyDay(100.0, 100.0, 3000.0),  # full
        FriendlyDay(98.0, 100.0, 5000.0),  # within the margin: full
        FriendlyDay(80.0, 100.0, 3000.0),  # 2 kWh missing, 3 kWh fed in: short
        FriendlyDay(70.0, 100.0, 1000.0),  # 3 kWh missing, only 1 kWh fed in: a dull day
        FriendlyDay(60.0, 90.0, 4000.0),  # 3 kWh missing below a maximum of 90 %: short
    ]
    assert short_days(days, 10000.0) == (2, 2500.0)
    assert short_days([], 10000.0) == (0, 0.0)


def test_grid_friendly_buffer_from_the_days_not_full() -> None:
    seen = Observations(friendly_days=20, friendly_short_days=4, friendly_missing_wh=1300.0)
    # Learned buffer: a warning on the automatic switch, nothing else to set.
    result = by_key(context(observations=seen))
    sources = result[("grid_friendly_buffer_auto", None)].sources
    assert [s.code for s in sources] == ["auto", "friendly_short"]
    assert sources[1].params == {"days": 4, "of": 20, "kwh": 1.3, "warn": True}
    assert ("grid_friendly_buffer", None) not in result
    # Manual buffer of 1 kWh: 1 + 1.3, rounded up to 0.5 kWh.
    manual = ControlSettings(grid_friendly_buffer_auto=False, grid_friendly_buffer_kwh=1.0)
    result = by_key(context(settings=manual, observations=seen))
    assert result[("grid_friendly_buffer", None)].value == 2.5
    assert result[("grid_friendly_buffer", None)].field == "grid_friendly_buffer_kwh"
    # Too few days, or grid friendly charging off: nothing.
    few = Observations(friendly_days=20, friendly_short_days=2, friendly_missing_wh=1300.0)
    assert len(by_key(context(observations=few))[("grid_friendly_buffer_auto", None)].sources) == 1
    off = ControlSettings(grid_friendly_charging=False)
    assert len(by_key(context(settings=off, observations=seen))[("grid_friendly_buffer_auto", None)].sources) == 1


def test_no_pv_no_pv_suggestions() -> None:
    tariff = TariffFacts(has_tariff=True, needs_market=True, import_market=True, export_zero_negative=True)
    result = by_key(context(goal="grid", tariff=tariff, has_pv=False))
    for key in ("grid_friendly_charging", "grid_friendly_buffer_auto", "charge_secured_buffer_auto", "night_discharge"):
        assert (key, None) not in result
    # Price and battery settings stay.
    assert result[("peak_shaving", None)].value is True
    assert result[("market_prices", None)].value is True


def hour(day, soc_max, grid_w, battery_w=0.0, soc_min=None):
    from custom_components.slems.assistant import LimitHour

    return LimitHour(
        date(2026, 11, day), soc_max, soc_max if soc_min is None else soc_min, 100.0, 12.0, grid_w, battery_w
    )


def test_plant_limits_capacity() -> None:
    from custom_components.slems.assistant import plant_limits

    hours = [
        # Day 1: full and 3 kWh fed in that day.
        hour(1, 100.0, -1500.0), hour(1, 100.0, -1500.0),
        # Day 2: full, only 0.5 kWh fed in: no limit.
        hour(2, 99.0, -500.0),
        # Day 3: not full, 4 kWh fed in (grid friendly or a full PV day without the battery).
        hour(3, 60.0, -4000.0),
        # Day 4: empty for two hours importing 1.6 kWh.
        hour(4, 13.0, 800.0), hour(4, 12.0, 800.0), hour(4, 30.0, 900.0),
    ]
    limits = plant_limits(hours, 2500.0, 2500.0)
    assert limits.days == 4
    assert (limits.full_export_days, limits.full_export_wh) == (1, 3000.0)
    assert (limits.empty_import_days, limits.empty_import_wh) == (1, 1600.0)


def test_plant_limits_power() -> None:
    from custom_components.slems.assistant import plant_limits

    hours = [
        hour(1, 50.0, -1200.0, battery_w=2400.0),  # charging at 96 % and feeding in: limited
        hour(1, 50.0, -100.0, battery_w=2500.0),  # feeding in only 100 W: not counted
        hour(1, 50.0, -1500.0, battery_w=1500.0),  # charging at 60 %: not limited
        hour(2, 50.0, 1800.0, battery_w=-2450.0),  # discharging at full power and importing
    ]
    limits = plant_limits(hours, 2500.0, 2500.0)
    assert (limits.charge_limited_hours, limits.charge_needed_w) == (1, 3600.0)
    assert (limits.discharge_limited_hours, limits.discharge_needed_w) == (1, 4250.0)
    assert plant_limits(hours, 0.0, 0.0).charge_limited_hours == 0


def test_notes_of_the_plant_limits() -> None:
    from custom_components.slems.assistant import PlantLimits, notes

    limits = PlantLimits(
        days=30, full_export_days=12, full_export_wh=5200.0, empty_import_days=2, empty_import_wh=900.0,
        charge_limited_hours=8, charge_needed_w=3240.0, discharge_limited_hours=6, discharge_needed_w=2400.0,
    )
    result = {n.code: n for n in notes(context(limits=limits, max_charge_w=2500.0, max_discharge_w=2500.0))}
    assert result["capacity_pv"].params == {"days": 12, "of": 30, "kwh": 5.2}
    assert result["capacity_pv"].hardware == {"capacity_kwh": 6.0}
    assert result["capacity_pv"].key is None
    # Too few days.
    assert "capacity_night" not in result
    assert result["charge_power"].hardware == {"max_charge_w": 800.0}
    # Needed no more than the batteries have: no limit of the power.
    assert "discharge_power" not in result


def test_note_of_the_price_spread() -> None:
    from custom_components.slems.assistant import notes

    dynamic = TariffFacts(has_tariff=True, needs_market=True, import_market=True)
    seen = Observations(price_saving=0.3, price_months=2)
    result = {n.code: n for n in notes(context(tariff=dynamic, observations=seen))}
    assert result["price_spread"].key == "price_control"
    assert result["price_spread"].params == {"saving": 0.3, "months": 2}
    # A fixed tariff: price differences are not the question.
    assert notes(context(tariff=TariffFacts(has_tariff=True), observations=seen)) == []


def test_plant_limits_leave_out_the_batteries_own_feed_in_and_grid_charging() -> None:
    from custom_components.slems.assistant import plant_limits

    hours = [
        # Full, then the night discharge feeds in 2 kWh from the batteries: not PV left over.
        hour(1, 100.0, -300.0), hour(1, 100.0, -2000.0, battery_w=-2000.0),
        # Empty and importing while charging from the grid: on purpose.
        hour(2, 12.0, 3000.0, battery_w=2500.0), hour(2, 12.0, 1200.0, battery_w=2400.0),
    ]
    limits = plant_limits(hours, 2500.0, 2500.0)
    assert limits.full_export_days == 0
    assert limits.empty_import_days == 0


def test_days_charging_at_full_power_are_no_buffer_question() -> None:
    from custom_components.slems.assistant import FriendlyDay, short_days

    # Not full although fed in, but charging at full power meanwhile: a larger buffer would not help.
    assert short_days([FriendlyDay(80.0, 100.0, 3000.0, charge_limited=True)], 10000.0) == (0, 0.0)
    assert short_days([FriendlyDay(80.0, 100.0, 3000.0)], 10000.0) == (1, 2000.0)
