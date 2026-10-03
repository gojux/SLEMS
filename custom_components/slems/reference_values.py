"""Official monthly market values (ct/kWh) for tariff items that follow them.

A ``market_month`` item may name such a value (``TariffItem.market``); until
the value of a month is published, the mean day-ahead price weighted by the
own feed-in stands in (see tariff). Fetched together with the market prices,
only with the user's consent, and only the values a configured tariff uses.

* ``at-pv``: Referenzmarktwert Photovoltaik according to § 13 EAG, published
  monthly by E-Control (Excel file, all months since 2022).
"""

from __future__ import annotations

from datetime import date, timedelta
import io
import xml.etree.ElementTree as ET
import zipfile

import aiohttp

AT_REFERENCE_URL = "https://www.e-control.at/documents/1785851/10823410/Referenzmarktwert_Entwicklung.xlsx"
# Market value -> column heading in the E-Control file.
AT_COLUMNS = {"at-pv": "Photovoltaik", "at-wind": "Wind", "at-hydro": "Wasserkraft"}
MARKETS = tuple(AT_COLUMNS)
ATTRIBUTION = {market: "E-Control (Referenzmarktwert gem. § 13 EAG)" for market in AT_COLUMNS}
REQUEST_TIMEOUT_S = 30
_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


class ReferenceError(Exception):
    """The file could not be fetched or read."""


def parse_at_reference(content: bytes) -> dict[str, dict[str, float]]:
    """Market value -> month ("2026-08") -> ct/kWh from the E-Control file."""
    try:
        workbook = zipfile.ZipFile(io.BytesIO(content))
        shared = []
        if "xl/sharedStrings.xml" in workbook.namelist():
            for item in ET.fromstring(workbook.read("xl/sharedStrings.xml")).findall("m:si", _NS):
                shared.append("".join(text.text or "" for text in item.iter(f"{{{_NS['m']}}}t")))
        sheet = ET.fromstring(workbook.read("xl/worksheets/sheet1.xml")).find("m:sheetData", _NS)
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as err:
        raise ReferenceError(f"not a readable workbook: {err}") from err

    def cells(row) -> dict[str, str]:
        values = {}
        for cell in row:
            column = "".join(char for char in cell.get("r", "") if char.isalpha())
            value = cell.find("m:v", _NS)
            text = value.text if value is not None else ""
            if cell.get("t") == "s" and text:
                text = shared[int(text)]
            values[column] = text or ""
        return values

    columns: dict[str, str] = {}
    result: dict[str, dict[str, float]] = {market: {} for market in AT_COLUMNS}
    for row in sheet if sheet is not None else ():
        values = cells(row)
        if not columns:
            # The heading row names the plant types.
            for column, text in values.items():
                for market, heading in AT_COLUMNS.items():
                    if heading.lower() in text.lower():
                        columns[market] = column
            continue
        first = values.get("A", "")
        try:
            month = date(1899, 12, 30) + timedelta(days=int(float(first)))
        except ValueError:
            continue
        for market, column in columns.items():
            try:
                result[market][f"{month.year:04d}-{month.month:02d}"] = round(float(values.get(column, "")), 4)
            except ValueError:
                continue
    if not columns or not any(result.values()):
        raise ReferenceError("no reference market values found")
    return result


async def async_fetch_at_reference(session: aiohttp.ClientSession) -> dict[str, dict[str, float]]:
    try:
        async with session.get(AT_REFERENCE_URL, timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_S)) as response:
            if response.status != 200:
                raise ReferenceError(f"HTTP {response.status}")
            content = await response.read()
    except (aiohttp.ClientError, TimeoutError) as err:
        raise ReferenceError(str(err)) from err
    return parse_at_reference(content)
