"""Tests for the official monthly market values (made-up workbook in the E-Control layout)."""

from datetime import date, datetime, time
import io
import zipfile

import pytest

from homeassistant.util import dt as dt_util

from custom_components.slems.reference_values import ReferenceError, parse_at_reference
from custom_components.slems.tariff import Group, Role, Side, Tariff, TariffItem, Unit, compute_bill, kwh_price

SHEET = """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
<row r="1"><c r="A1" t="s"><v>0</v></c></row>
<row r="3"><c r="A3" t="s"><v>1</v></c><c r="B3" t="s"><v>2</v></c><c r="C3" t="s"><v>3</v></c><c r="D3" t="s"><v>4</v></c></row>
<row r="5"><c r="A5"><v>46235</v></c><c r="B5"><v>8.1</v></c><c r="C5"><v>7.2</v></c><c r="D5"><v>9.42</v></c></row>
<row r="6"><c r="A6"><v>46266</v></c><c r="B6"><v>9.0</v></c><c r="C6"><v>8.0</v></c><c r="D6"><v>7.5</v></c></row>
<row r="9"><c r="A9" t="s"><v>5</v></c></row>
</sheetData></worksheet>"""
STRINGS = ["REFERENZMARKTWERT", "Monat", "Wasserkraftanlagen", "Windkraftanlagen", "Photovoltaikanlagen", "Quelle: E-Control"]


def workbook() -> bytes:
    shared = "".join(f"<si><t>{text}</t></si>" for text in STRINGS)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/worksheets/sheet1.xml", SHEET)
        archive.writestr(
            "xl/sharedStrings.xml",
            f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">{shared}</sst>',
        )
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def vienna() -> None:
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Vienna"))


def test_parse_the_e_control_workbook() -> None:
    values = parse_at_reference(workbook())
    # Excel day 46235 is 2026-08-01.
    assert values["at-pv"] == {"2026-08": 9.42, "2026-09": 7.5}
    assert values["at-wind"]["2026-08"] == 7.2 and values["at-hydro"]["2026-09"] == 9.0
    with pytest.raises(ReferenceError):
        parse_at_reference(b"no workbook")


def test_feed_in_follows_the_official_value_else_the_own_mean() -> None:
    item = TariffItem("Credit", Side.EXPORT, Group.ENERGY, Unit.MARKET_MONTH, -0.6, market="at-pv")
    tariff = Tariff("PV", Role.CURRENT, (item,), {})
    references = {"at-pv": {"2026-08": 9.42}}
    august = datetime.combine(date(2026, 8, 10), time(12), dt_util.get_default_time_zone())
    october = august.replace(month=10)
    assert kwh_price(tariff, Side.EXPORT, august, None, 5.0, references) == pytest.approx(8.82)
    # Not published yet: the own weighted mean.
    assert kwh_price(tariff, Side.EXPORT, october, None, 5.0, references) == pytest.approx(4.4)
    bill = compute_bill(tariff, date(2026, 8, 10), date(2026, 8, 10), {}, {august: 10000}, {august: 50.0}, references)
    assert bill.lines[(Side.EXPORT, "Credit")] == pytest.approx(-10 * 8.82 / 100)
