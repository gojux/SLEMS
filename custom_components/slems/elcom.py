"""Swiss tariffs from the open data of the Federal Electricity Commission ElCom.

ElCom publishes the tariffs of every grid operator and municipality per
consumption category (H1 to H8 for households), each year in September for
the next year, as linked open data (LINDAS, SPARQL). In Switzerland a
household cannot choose its supplier: energy, grid and levies come from the
operator of the municipality, so one year is one complete tariff (excluding
VAT, CHF):

* energy: mean working price (Rp./kWh) and fixed price (CHF/year),
* grid: grid use (Rp./kWh, the per kWh parts of the grid tariff) and the
  metering tariff (CHF/year),
* levies: charges to the municipality and the federal grid surcharge (Rp./kWh).

The prices are the means of the category; high and low tariff times are not
known. Each year becomes a price level of a template family (see
tariff_templates), so a published next year is offered as an update.
Queries only on the user's action or with the consent to fetch market prices.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
import re
from typing import Any

import aiohttp

from .tariff_templates import Template

LINDAS_URL = "https://ld.admin.ch/query"
REQUEST_TIMEOUT_S = 60
SOURCE = "ElCom, Strompreise (LINDAS, energy.ld.admin.ch), exkl. MWST"
VAT_PCT = 8.1
CATEGORIES = ("H1", "H2", "H3", "H4", "H5", "H6", "H7", "H8")
_BASE = "https://energy.ld.admin.ch/elcom/electricityprice"
_PREFIXES = f"""PREFIX cube: <https://cube.link/>
PREFIX schema: <http://schema.org/>
PREFIX s: <{_BASE}/dimension/>
"""


class ElcomError(Exception):
    """LINDAS could not be queried."""


@dataclass(frozen=True)
class Supply:
    """A municipality and the grid operator supplying it."""

    municipality: str
    municipality_name: str
    operator: str
    operator_name: str


async def async_query(session: aiohttp.ClientSession, query: str) -> list[dict[str, str]]:
    try:
        async with session.post(
            LINDAS_URL,
            data={"query": query},
            headers={"Accept": "text/csv"},
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_S),
        ) as response:
            if response.status != 200:
                raise ElcomError(f"HTTP {response.status}")
            text = await response.text()
    except (aiohttp.ClientError, TimeoutError) as err:
        raise ElcomError(str(err)) from err
    return list(csv.DictReader(io.StringIO(text)))


def _id(uri: str) -> str:
    return uri.rstrip("/").rsplit("/", 1)[-1]


def search_query(text: str, year: int) -> str:
    """Municipalities whose name contains ``text`` (letters only) and their operators."""
    cleaned = re.sub(r"[^\w\s.'-]", "", text, flags=re.UNICODE).strip().lower().replace("'", "\\'")
    return _PREFIXES + f"""SELECT DISTINCT ?municipality ?mname ?operator ?oname
FROM <https://lindas.admin.ch/elcom/electricityprice>
FROM <https://lindas.admin.ch/fso/register>
WHERE {{
  <{_BASE}> cube:observationSet/cube:observation ?obs.
  ?obs s:period "{year}"^^<http://www.w3.org/2001/XMLSchema#gYear>; s:municipality ?municipality;
       s:operator ?operator; s:product <{_BASE}/product/standard>; s:category <{_BASE}/category/H4>.
  ?municipality schema:name ?mname. ?operator schema:name ?oname.
  FILTER(CONTAINS(LCASE(STR(?mname)), '{cleaned}'))
}}
ORDER BY ?mname ?oname
LIMIT 50"""


async def async_search(session: aiohttp.ClientSession, text: str, year: int) -> list[Supply]:
    rows = await async_query(session, search_query(text, year))
    return [Supply(_id(r["municipality"]), r["mname"], _id(r["operator"]), r["oname"]) for r in rows]


async def async_categories(session: aiohttp.ClientSession) -> dict[str, str]:
    """Household categories -> description (German, as published)."""
    values = " ".join(f'"{name}"' for name in CATEGORIES)
    rows = await async_query(
        session,
        _PREFIXES
        + f"""SELECT ?name ?desc WHERE {{
  VALUES ?name {{ {values} }}
  BIND(IRI(CONCAT("{_BASE}/category/", ?name)) AS ?category)
  ?category schema:description ?desc . FILTER(lang(?desc) = "de") }}""",
    )
    # The published texts use "'" for thousands and, followed by a space, for commas.
    found = {row["name"]: row["desc"].replace("' ", ", ") for row in rows}
    return {name: found.get(name, name) for name in CATEGORIES}


def levels_query(supply: Supply, category: str, first_year: int) -> str:
    if category not in CATEGORIES:
        raise ElcomError(f"unknown category {category}")
    return _PREFIXES + f"""SELECT ?period ?gridusage ?energy ?energyworking ?energyfix ?meteringyear ?charge ?aidfee ?total
FROM <https://lindas.admin.ch/elcom/electricityprice>
WHERE {{
  <{_BASE}> cube:observationSet/cube:observation ?obs.
  ?obs s:period ?period; s:municipality <https://ld.admin.ch/municipality/{int(supply.municipality)}>;
       s:operator <{_BASE}/operator/{int(supply.operator)}>; s:category <{_BASE}/category/{category}>;
       s:product <{_BASE}/product/standard>;
       s:gridusage ?gridusage; s:energy ?energy; s:charge ?charge; s:aidfee ?aidfee; s:total ?total.
  OPTIONAL {{ ?obs s:energyworkingprice ?energyworking }} OPTIONAL {{ ?obs s:energyfixcost ?energyfix }}
  OPTIONAL {{ ?obs s:annualmeteringcost ?meteringyear }}
  FILTER(?period >= "{first_year}"^^<http://www.w3.org/2001/XMLSchema#gYear>)
}}
ORDER BY ?period"""


def family(supply: Supply, category: str) -> str:
    return f"ch/elcom/{supply.operator}/{supply.municipality}/{category.lower()}"


def template_of(supply: Supply, category: str, row: dict[str, str]) -> Template:
    """One tariff year of ElCom as a complete template (prices excluding VAT)."""
    year = int(row["period"])

    def number(key: str) -> float:
        try:
            return float(row.get(key) or 0.0)
        except ValueError:
            return 0.0

    energy = number("energyworking") or number("energy")
    items: list[dict[str, Any]] = [
        _item("Energie Arbeitspreis", "energy", "kwh", energy),
        _item("Netznutzung", "grid", "kwh", number("gridusage")),
        _item("Abgaben an das Gemeinwesen", "levies", "kwh", number("charge")),
        _item("Netzzuschlag", "levies", "kwh", number("aidfee")),
    ]
    if number("energyfix"):
        items.append(_item("Energie Grundpreis", "energy", "year", number("energyfix")))
    if number("meteringyear"):
        items.append(_item("Messtarif", "grid", "year", number("meteringyear")))
    vat = {f"{side}.{group}": VAT_PCT for side in ("import", "export") for group in ("energy", "grid", "levies")}
    vat["export.energy"] = 0.0
    meta = {
        "id": family(supply, category),
        "supplier": supply.operator_name,
        "grid_operator": supply.operator_name,
        "grid_area": f"{supply.municipality_name}, {category}",
        "country": "CH",
        "currency": "CHF",
        "year": year,
        "parts": ["energy", "grid", "levies"],
        "valid_from": f"{year}-01-01",
        "valid_to": f"{year}-12-31",
        "source": f"{SOURCE}, Kategorie {category}",
        "elcom": {"municipality": supply.municipality, "operator": supply.operator, "category": category,
                  "municipality_name": supply.municipality_name, "operator_name": supply.operator_name},
    }
    data = {"role": "current", "vat": vat, "items": items, "meta": meta}
    return Template(f"elcom/{family(supply, category)}/{year}", f"{supply.operator_name}, {supply.municipality_name}", data)


def _item(name: str, group: str, unit: str, price: float) -> dict[str, Any]:
    return {
        "name": name, "side": "import", "group": group, "unit": unit, "price": round(price, 4),
        "valid_from": None, "months": [], "weekdays": [], "time_from": None, "time_to": None,
        "factor_pct": 0.0, "month_prices": {}, "valid_to": None, "market": None,
    }


async def async_levels(
    session: aiohttp.ClientSession, supply: Supply, category: str, first_year: int
) -> list[Template]:
    """The published tariff years from ``first_year`` on, oldest first."""
    rows = await async_query(session, levels_query(supply, category, first_year))
    return [template_of(supply, category, row) for row in rows]


def supply_of(meta: dict[str, Any]) -> tuple[Supply, str] | None:
    """The supply and category a tariff from ElCom was made for (``meta.elcom``)."""
    origin = meta.get("elcom")
    if not isinstance(origin, dict) or not all(origin.get(k) for k in ("municipality", "operator", "category")):
        return None
    return (
        Supply(
            str(origin["municipality"]), str(origin.get("municipality_name") or ""),
            str(origin["operator"]), str(origin.get("operator_name") or ""),
        ),
        str(origin["category"]),
    )
