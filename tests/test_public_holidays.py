"""Tests for the public holidays of a country or region."""

import asyncio
from datetime import date
from types import SimpleNamespace

import pytest

from custom_components.slems import public_holidays
from custom_components.slems.public_holidays import (
    NATIONAL,
    PublicHolidays,
    async_suggest_region,
    region_choices,
    region_from_lookup,
)


def test_swiss_holidays_kept_in_most_cantons() -> None:
    swiss = PublicHolidays("CH")
    # Not loaded yet (the executor loads them): no holidays.
    assert date(2026, 8, 1) not in swiss
    swiss.load([2026])
    assert swiss.years == {2026}
    # National, and cantonal ones kept in most cantons (Good Friday, Whit Monday).
    for day in (date(2026, 8, 1), date(2026, 4, 3), date(2026, 5, 25), date(2026, 12, 26)):
        assert day in swiss
    # Labour Day and Corpus Christi only in some cantons.
    assert date(2026, 5, 1) not in swiss
    assert date(2026, 6, 4) not in swiss
    german = PublicHolidays("DE")
    german.load([2026])
    assert date(2026, 10, 3) in german
    assert date(2026, 10, 31) not in german


def test_region_adds_its_own_holidays() -> None:
    bavaria = PublicHolidays("DE", "BY")
    bavaria.load([2026])
    # Corpus Christi and All Saints' Day are Bavarian, not national.
    assert date(2026, 6, 4) in bavaria
    assert date(2026, 11, 1) in bavaria
    assert date(2026, 10, 3) in bavaria
    zurich = PublicHolidays("CH", "ZH")
    zurich.load([2026])
    assert date(2026, 5, 1) in zurich


@pytest.mark.parametrize("region", [None, NATIONAL, "8"])
def test_national_holidays_without_a_region_of_the_country(region) -> None:
    # "8" (Vorarlberg) is no German region: a stored region of another country is ignored.
    german = PublicHolidays("DE", region)
    german.load([2026])
    assert date(2026, 10, 3) in german
    assert date(2026, 6, 4) not in german


def test_unknown_country_has_no_holidays() -> None:
    unknown = PublicHolidays("XX")
    unknown.load([2026])
    assert date(2026, 1, 1) not in unknown


def test_region_choices_with_names() -> None:
    austria = dict(region_choices("AT"))
    assert austria["8"] == "Vorarlberg"
    assert len(austria) == 9
    assert dict(region_choices("DE"))["BY"] == "Bayern"
    assert region_choices(None) == []
    assert region_choices("XX") == []


def test_region_from_lookup() -> None:
    assert region_from_lookup({"address": {"state": "Vorarlberg", "ISO3166-2-lvl4": "AT-8"}}, "AT") == "8"
    assert region_from_lookup({"address": {"ISO3166-2-lvl4": "DE-BY"}}, "de") == "BY"
    # Outside the country of Home Assistant (home near a border, wrong coordinates).
    assert region_from_lookup({"address": {"ISO3166-2-lvl4": "DE-BY"}}, "AT") is None
    assert region_from_lookup({"address": {"state": "Vorarlberg"}}, "AT") is None
    assert region_from_lookup({"error": "Unable to geocode"}, "AT") is None
    assert region_from_lookup([], "AT") is None


class _Response:
    def __init__(self, data) -> None:
        self._data = data

    def raise_for_status(self) -> None:
        pass

    async def json(self):
        return self._data


class _Session:
    def __init__(self, data) -> None:
        self.data = data
        self.calls: list[dict] = []

    async def get(self, url, params, headers):
        self.calls.append(params)
        return _Response(self.data)


def _hass(country: str | None):
    async def executor(func, *args):
        return func(*args)

    return SimpleNamespace(
        config=SimpleNamespace(country=country, latitude=47.503456, longitude=9.747123),
        async_add_executor_job=executor,
    )


def test_suggest_region_asks_nominatim_once_with_rounded_coordinates(monkeypatch) -> None:
    session = _Session({"address": {"ISO3166-2-lvl4": "AT-8"}})
    monkeypatch.setattr(public_holidays, "async_get_clientsession", lambda hass: session)
    assert asyncio.run(async_suggest_region(_hass("AT"))) == "8"
    assert session.calls == [{"lat": "47.503", "lon": "9.747", "zoom": "5", "format": "jsonv2"}]


@pytest.mark.parametrize("country", [None, "XX"])
def test_no_lookup_without_regions(monkeypatch, country) -> None:
    session = _Session({})
    monkeypatch.setattr(public_holidays, "async_get_clientsession", lambda hass: session)
    assert asyncio.run(async_suggest_region(_hass(country))) is None
    assert session.calls == []
