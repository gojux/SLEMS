"""Tests for the minimum power of a battery (min_power)."""

import pytest

from custom_components.slems.min_power import (
    BAND_W,
    LOW_MIN_SAMPLES,
    LowLossTable,
    MinPowerGate,
    MinPowerMode,
    Prices,
    charge_floor_w,
    charge_threshold_w,
    discharge_threshold_w,
)

# Losses of a Venus E 3.0 (fit of 2026-10-27): about 40 W fixed.
VENUS_LOSS_W = {45: 38.0, 75: 40.0, 125: 42.0, 175: 44.0, 225: 46.0, 275: 48.0}


def learned_table(losses: dict[int, float] = VENUS_LOSS_W) -> LowLossTable:
    table = LowLossTable()
    for power, loss in losses.items():
        for _ in range(LOW_MIN_SAMPLES + 1):
            table.add(power, power - loss)
        for _ in range(LOW_MIN_SAMPLES + 1):
            table.add(-power, -(power + loss))
    return table


def test_low_loss_table_learns_both_directions_also_below_the_fixed_loss() -> None:
    table = LowLossTable()
    # Not steady yet (first reading), then steady charging at 30 W: DC -10 W.
    table.add(30.0, -10.0)
    table.add(30.0, -10.0)
    assert table.bins == {}  # below 40 W not learned
    table.add(45.0, 7.0)
    table.add(45.0, 7.0)
    assert table.bins["charge"][0][0] == pytest.approx(38.0)
    table.add(-75.0, -115.0)
    table.add(-75.0, -115.0)
    assert table.bins["discharge"][1][0] == pytest.approx(40.0)
    # Not learned before enough samples and a bin below 100 W.
    assert table.points("charge") == []
    assert LowLossTable.from_dict(table.as_dict()).bins == table.bins


def test_thresholds_by_mode() -> None:
    table = learned_table()
    prices = Prices(import_price=19.8, export_price=7.0)
    common = {"prices": prices, "charge_efficiency": 0.93}
    # Energetic: charging once it stores anything, discharging always.
    floor = charge_floor_w(table)
    assert 38 < floor <= 45
    assert charge_threshold_w(table, MinPowerMode.AUTO_ENERGY, 60, secured=False, discharge_efficiency=0.93, **common) == floor
    assert discharge_threshold_w(table, MinPowerMode.AUTO_ENERGY, 60, no_import=False, **common) is None
    # Economic: worth more stored than fed in (about 60-70 W for a Venus).
    economic = charge_threshold_w(table, MinPowerMode.AUTO_ECONOMIC, 60, secured=False, discharge_efficiency=0.93, **common)
    assert 55 <= economic <= 75
    # Charge secured: small powers never pay off (up to the typical efficiency).
    secured = charge_threshold_w(table, MinPowerMode.AUTO_ECONOMIC, 60, secured=True, discharge_efficiency=0.93, **common)
    assert secured > economic
    # Feed-in worth nothing: only the physical floor.
    free = Prices(import_price=19.8, export_price=0.0)
    assert charge_threshold_w(
        table, MinPowerMode.AUTO_ECONOMIC, 60, secured=False, prices=free, charge_efficiency=0.93, discharge_efficiency=0.93
    ) == floor
    # Discharging delivers P of P + loss: with about 40 W fixed loss it pays off
    # from about 25 W already, so the search starts at its lowest power.
    discharge = discharge_threshold_w(table, MinPowerMode.AUTO_ECONOMIC, 60, no_import=False, **common)
    assert discharge == 40
    # No import wanted: no discharge threshold.
    assert discharge_threshold_w(table, MinPowerMode.AUTO_ECONOMIC, 60, no_import=True, **common) is None
    assert discharge_threshold_w(table, MinPowerMode.MANUAL, 60, no_import=True, **common) is None
    # Manual and off.
    assert charge_threshold_w(table, MinPowerMode.MANUAL, 80, secured=False, discharge_efficiency=0.93, **common) == 80
    assert discharge_threshold_w(table, MinPowerMode.MANUAL, 80, no_import=False, **common) == 80
    assert charge_threshold_w(table, MinPowerMode.OFF, 80, secured=False, discharge_efficiency=0.93, **common) is None
    # Not learned: the automatic modes have no threshold.
    empty = LowLossTable()
    assert charge_threshold_w(empty, MinPowerMode.AUTO_ECONOMIC, 60, secured=False, discharge_efficiency=0.93, **common) is None
    assert discharge_threshold_w(empty, MinPowerMode.AUTO_ECONOMIC, 60, no_import=False, **common) is None


def test_gate_starts_at_the_threshold_and_stops_below_it_minus_the_band() -> None:
    gate = MinPowerGate()
    # 50 W surplus, standby: no start.
    result = gate.apply(50.0, 0.0, 60.0, 40.0, 2.7, 0.0)
    assert result.target_w == 0 and result.suppressed and result.stop
    # Off at max(60 - 25, floor 40) = 40 W, so on at 40 + 25 = 65 W.
    assert gate.apply(64.0, 0.0, 60.0, 40.0, 2.7, 0.5).suppressed
    result = gate.apply(70.0, 0.0, 60.0, 40.0, 2.7, 1.0)
    assert result.target_w == 70 and result.start_w == 65 and not result.suppressed
    # Running: 45 W keeps it running (above the floor of 40 W).
    assert gate.apply(45.0, 60.0, 60.0, 40.0, 2.7, 5.0).target_w == 45
    # 30 W: within the minimum run time it keeps running, afterwards it stops.
    assert gate.apply(30.0, 45.0, 60.0, 40.0, 2.7, 2.0).target_w == 30
    result = gate.apply(30.0, 45.0, 60.0, 40.0, 2.7, 6.0)
    assert result.target_w == 0 and result.stop and result.suppressed
    # Never below the physical floor: with the floor as threshold the band lies above it.
    gate = MinPowerGate()
    assert gate.apply(42.0 + BAND_W - 1, 0.0, 42.0, 42.0, 2.7, 0.0).suppressed
    assert not gate.apply(42.0 + BAND_W, 0.0, 42.0, 42.0, 2.7, 1.0).suppressed
    result = gate.apply(41.0, 60.0, 42.0, 42.0, 2.7, 10.0)
    assert result.stop
    # A battery running the other way stops as usual (no immediate stop).
    result = gate.apply(30.0, -200.0, 60.0, 40.0, 2.7, 20.0)
    assert result.suppressed and not result.stop
    # Without a threshold nothing changes.
    assert gate.apply(10.0, 0.0, None, 0.0, 2.7, 30.0).target_w == 10
