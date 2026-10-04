"""Tests for the day-ahead prices (made-up values in the format of the sources)."""

from datetime import date, datetime, time, timedelta

import pytest

from homeassistant.util import dt as dt_util

from custom_components.slems.market_prices import (
    PriceError,
    PriceSource,
    day_bounds,
    day_runs,
    default_source,
    estimate_prices,
    hourly_means,
    missing_days,
    parse_apg,
    parse_energy_charts,
    parse_smard,
    quarter_slots,
    smard_weeks,
)


@pytest.fixture(autouse=True)
def vienna() -> None:
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Vienna"))


def apg_rows(count: int) -> dict:
    return {
        "ResponseData": {
            "ValueRows": [
                {"DF": "x", "TF": "x", "V": [{"V": float(index)}, {"V": None}, {"V": float(index)}]}
                for index in range(count)
            ]
        }
    }


def test_apg_rows_follow_the_local_day_also_when_the_clock_changes() -> None:
    # The clock goes back: 25 hours, the rows "2A" / "2B" are only labels.
    day = date(2026, 10, 25)
    prices = parse_apg(day, apg_rows(100))
    start, end = day_bounds(day)
    assert end - start == 25 * 3600
    assert prices[start] == 0 and prices[end - 900] == 99
    # Spring: 23 hours.
    assert len(parse_apg(date(2026, 3, 29), apg_rows(92))) == 92
    with pytest.raises(PriceError):
        parse_apg(day, apg_rows(96))
    assert parse_apg(day, {"ResponseData": {"ValueRows": []}}) == {}
    with pytest.raises(PriceError):
        parse_apg(day, {"Message": "Invalid request"})


def test_hourly_prices_fill_their_quarter_hours() -> None:
    start = 1_767_225_600  # an hour start
    slots = quarter_slots([(start, 50.0), (start + 3600, 60.0)])
    assert [slots[start + i * 900] for i in range(8)] == [50.0] * 4 + [60.0] * 4
    # A gap (no price) stays a gap.
    slots = quarter_slots([(start, 50.0), (start + 900, None), (start + 1800, 40.0)])
    assert start + 900 not in slots and slots[start + 1800] == 40.0


def test_smard_and_energy_charts_payloads() -> None:
    start = 1_767_225_600
    smard = {"series": [[start * 1000, 12.5], [(start + 900) * 1000, 13.0], [(start + 1800) * 1000, None]]}
    assert parse_smard(smard) == {start: 12.5, start + 900: 13.0}
    charts = {"unix_seconds": [start, start + 3600], "price": [-5.0, 20.0], "unit": "EUR / MWh"}
    slots = parse_energy_charts(charts)
    assert slots[start + 2700] == -5.0 and slots[start + 3600 + 2700] == 20.0
    with pytest.raises(PriceError):
        parse_energy_charts({"price": []})


def test_smard_weeks_overlapping_the_period() -> None:
    week = 7 * 86400
    weeks = [w * 1000 for w in (0, week, 2 * week, 3 * week)]
    assert smard_weeks(weeks, week + 10, 2 * week + 10) == [week * 1000, 2 * week * 1000]
    # The last file covers a week.
    assert smard_weeks(weeks, 3 * week + 5, 3 * week + 100) == [3 * week * 1000]


def test_missing_days_and_runs() -> None:
    first = date(2026, 5, 1)
    prices: dict[int, float] = {}
    for offset in (0, 3):
        start, end = day_bounds(first + timedelta(days=offset))
        prices.update({slot: 1.0 for slot in range(start, end, 900)})
    missing = missing_days(prices, first, first + timedelta(days=4))
    assert missing == [first + timedelta(days=d) for d in (1, 2, 4)]
    assert day_runs(missing) == [
        (first + timedelta(days=1), first + timedelta(days=2)),
        (first + timedelta(days=4), first + timedelta(days=4)),
    ]


def test_hourly_means() -> None:
    hour = datetime.combine(date(2026, 5, 1), time(12), dt_util.get_default_time_zone())
    start = int(hour.timestamp())
    prices = {start: 10.0, start + 900: 20.0, start + 1800: 30.0, start + 2700: 40.0, start + 3600: 5.0}
    means = hourly_means(prices, hour, hour + timedelta(hours=1))
    assert means == {dt_util.as_utc(hour): 25.0}


def test_default_source_by_country() -> None:
    assert default_source("AT") is PriceSource.APG
    assert default_source("DE") is PriceSource.SMARD
    assert default_source(None) is PriceSource.SMARD


def test_estimates_after_the_last_known_price() -> None:
    zone = dt_util.get_default_time_zone()
    # Two weeks, Monday 2026-09-14 to Sunday 2026-09-27: working days 50 €/MWh
    # at night and 150 at 18:00, weekends 30 all day.
    prices = {}
    day = date(2026, 9, 14)
    while day <= date(2026, 9, 27):
        start, end = day_bounds(day)
        for slot in range(start, end, 900):
            hour = datetime.fromtimestamp(slot, zone).hour
            prices[slot] = 30.0 if day.weekday() >= 5 else (150.0 if hour == 18 else 50.0)
        day += timedelta(days=1)
    monday, _ = day_bounds(date(2026, 9, 28))
    estimates = estimate_prices(prices, monday - 3600, monday + 86400)
    # Only after the last known price.
    assert min(estimates) == monday
    evening = int(datetime.combine(date(2026, 9, 28), time(18), zone).timestamp())
    night = int(datetime.combine(date(2026, 9, 28), time(3), zone).timestamp())
    # Working day profile, its swing halved around the day mean (50 + 100 / 24).
    mean = 50 + 100 / 24
    assert estimates[evening] == pytest.approx(mean + 0.5 * (150 - mean))
    assert estimates[night] == pytest.approx(mean + 0.5 * (50 - mean))
    saturday, _ = day_bounds(date(2026, 10, 3))
    assert estimate_prices(prices, saturday, saturday + 900)[saturday] == pytest.approx(30.0)
    assert estimate_prices({}, monday, monday + 900) == {}


def test_public_holidays_count_like_weekends_in_the_estimate() -> None:
    zone = dt_util.get_default_time_zone()
    # Two weeks: working days 100 €/MWh, weekends 30.
    prices = {}
    day = date(2026, 9, 14)
    while day <= date(2026, 9, 27):
        start, end = day_bounds(day)
        for slot in range(start, end, 900):
            prices[slot] = 30.0 if day.weekday() >= 5 else 100.0
        day += timedelta(days=1)
    monday, _ = day_bounds(date(2026, 9, 28))
    noon = int(datetime.combine(date(2026, 9, 28), time(12), zone).timestamp())
    assert estimate_prices(prices, monday, monday + 86400, share=1.0)[noon] == pytest.approx(100.0)
    # Monday a public holiday: estimated like a Sunday.
    holiday = estimate_prices(prices, monday, monday + 86400, share=1.0, day_off=lambda d: d == date(2026, 9, 28))
    assert holiday[noon] == pytest.approx(30.0)
