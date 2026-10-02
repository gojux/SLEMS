"""Tariffs as YAML: export, import and the templates shipped with SLEMS.

The format carries its name and version (``format: slems-tariff``,
``version: 1``). A file of an older version is migrated on import
(``_MIGRATIONS``), a newer one is refused, so a file is never read wrongly.
Prices are net (without VAT), ``kwh`` items in ct/kWh, ``year`` items in
€/year, dynamic items (``spot``, ``market_month``) the markup in ct/kWh,
``percent`` items % of the other items of their side and group.

A template may cover only some parts of a bill (``parts``): the energy of a
supplier, the grid fees of a grid operator in a grid area (``grid_area``,
e.g. the network level; ``household: true`` for the one households usually
have), the levies of a country. An energy template may name the grid
operator it is usually combined with (``suggest: {grid_operator: …}``).

Example (made-up values)::

    format: slems-tariff
    version: 1
    name: Example Fix 2026
    supplier: Example Energy Ltd
    country: AT
    year: 2026
    parts: [energy]
    valid_from: 2026-01-01
    valid_to: 2026-12-31
    source: price sheet 01/2026
    vat_pct:
      import: {energy: 20, grid: 20, levies: 20}
      export: {energy: 0, grid: 20, levies: 20}
    items:
      - {name: Base price, side: import, group: energy, unit: year, price: 30.0}
      - {name: Energy price, side: import, group: energy, unit: kwh, price: 15.9}
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, time
from typing import Any

import yaml

from .tariff import Group, Role, Side, TariffItem, Unit

FORMAT = "slems-tariff"
VERSION = 1
# Information about the tariff kept with it (not used for the prices).
META_KEYS = (
    "supplier", "grid_operator", "grid_area", "household", "country", "year", "parts",
    "valid_from", "valid_to", "source", "suggest",
)
# Hints of a template for the other parts (``suggest``).
SUGGEST_KEYS = ("grid_operator",)
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DEFAULT_VAT = {
    "import": {"energy": 20.0, "grid": 20.0, "levies": 20.0},
    "export": {"energy": 0.0, "grid": 20.0, "levies": 20.0},
}
_TOP_KEYS = {"format", "version", "name", "vat_pct", "items", *META_KEYS}
_ITEM_KEYS = {
    "name", "side", "group", "unit", "price", "factor_pct", "valid_from",
    "months", "weekdays", "time_from", "time_to", "month_prices",
}
# Version -> function that turns a file of that version into the next one.
_MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {}


class TariffYamlError(ValueError):
    """A file that is not a valid tariff: ``key`` names the problem, ``detail`` where."""

    def __init__(self, key: str, detail: str = "") -> None:
        super().__init__(f"{key}: {detail}" if detail else key)
        self.key = key
        self.detail = detail


def export_yaml(title: str, data: Mapping[str, Any]) -> str:
    """The tariff of a config subentry (``data``) as YAML of the current version."""
    meta = data.get("meta") or {}
    document: dict[str, Any] = {"format": FORMAT, "version": VERSION, "name": title}
    for key in META_KEYS:
        if meta.get(key) not in (None, "", []):
            document[key] = meta[key]
    vat: dict[str, dict[str, float]] = {}
    for key, value in (data.get("vat") or {}).items():
        side, _, group = key.partition(".")
        vat.setdefault(side, {})[group] = value
    document["vat_pct"] = vat or DEFAULT_VAT
    document["items"] = [_item_document(TariffItem.from_dict(item)) for item in data.get("items") or ()]
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True, default_flow_style=None, width=100)


def _item_document(item: TariffItem) -> dict[str, Any]:
    document: dict[str, Any] = {
        "name": item.name,
        "side": item.side.value,
        "group": item.group.value,
        "unit": item.unit.value,
        "price": item.price,
    }
    if item.factor_pct:
        document["factor_pct"] = item.factor_pct
    if item.valid_from:
        document["valid_from"] = item.valid_from.isoformat()
    if item.months:
        document["months"] = sorted(item.months)
    if item.weekdays:
        document["weekdays"] = [WEEKDAYS[day] for day in sorted(item.weekdays)]
    if item.time_from and item.time_to:
        document["time_from"] = item.time_from.strftime("%H:%M")
        document["time_to"] = item.time_to.strftime("%H:%M")
    if item.month_prices:
        document["month_prices"] = dict(item.month_prices)
    return document


def parse_yaml(text: str) -> tuple[str, dict[str, Any]]:
    """Name and subentry data (role current, ``meta`` with the information) of
    a tariff file; raises ``TariffYamlError``."""
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as err:
        mark = getattr(err, "problem_mark", None)
        raise TariffYamlError("yaml_syntax", f"{mark.line + 1}" if mark else "") from err
    if not isinstance(document, dict) or document.get("format") != FORMAT:
        raise TariffYamlError("yaml_not_tariff")
    version = document.get("version")
    if not isinstance(version, int) or version < 1:
        raise TariffYamlError("yaml_version_missing")
    if version > VERSION:
        raise TariffYamlError("yaml_version_newer", str(version))
    while version < VERSION:
        document = _MIGRATIONS[version](document)
        version += 1
    unknown = sorted(set(document) - _TOP_KEYS)
    if unknown:
        raise TariffYamlError("yaml_unknown_field", ", ".join(unknown))
    name = document.get("name")
    if not isinstance(name, str) or not name.strip():
        raise TariffYamlError("yaml_field_invalid", "name")
    items = document.get("items")
    if not isinstance(items, list) or not items:
        raise TariffYamlError("yaml_no_items")
    parsed = [_parse_item(item, index) for index, item in enumerate(items, start=1)]
    meta = {key: _meta_value(key, document[key]) for key in META_KEYS if document.get(key) is not None}
    if meta.get("valid_from") and meta.get("valid_to") and meta["valid_to"] < meta["valid_from"]:
        raise TariffYamlError("yaml_field_invalid", "valid_to")
    return name.strip(), {
        "role": Role.CURRENT.value,
        "vat": _parse_vat(document.get("vat_pct")),
        "items": [item.as_dict() for item in parsed],
        "meta": meta,
    }


def _meta_value(key: str, value: Any) -> Any:
    if key in ("valid_from", "valid_to"):
        return _date(value, key).isoformat()
    if key == "year":
        if not isinstance(value, int):
            raise TariffYamlError("yaml_field_invalid", key)
        return value
    if key == "household":
        if not isinstance(value, bool):
            raise TariffYamlError("yaml_field_invalid", key)
        return value
    if key == "suggest":
        if not isinstance(value, dict) or any(k not in SUGGEST_KEYS or not isinstance(v, str) for k, v in value.items()):
            raise TariffYamlError("yaml_field_invalid", key)
        return dict(value)
    if key == "parts":
        parts = value if isinstance(value, list) else [value]
        if any(part not in {group.value for group in Group} for part in parts):
            raise TariffYamlError("yaml_field_invalid", key)
        return parts
    return str(value)


def _parse_vat(value: Any) -> dict[str, float]:
    vat = {side: dict(groups) for side, groups in DEFAULT_VAT.items()}
    if value is not None:
        if not isinstance(value, dict):
            raise TariffYamlError("yaml_field_invalid", "vat_pct")
        for side, groups in value.items():
            if side not in vat or not isinstance(groups, dict):
                raise TariffYamlError("yaml_field_invalid", f"vat_pct.{side}")
            for group, pct in groups.items():
                if group not in vat[side] or not isinstance(pct, int | float) or not 0 <= pct <= 100:
                    raise TariffYamlError("yaml_field_invalid", f"vat_pct.{side}.{group}")
                vat[side][group] = float(pct)
    return {f"{side}.{group}": pct for side, groups in vat.items() for group, pct in groups.items()}


def _parse_item(item: Any, index: int) -> TariffItem:
    def invalid(field: str) -> TariffYamlError:
        return TariffYamlError("yaml_item_invalid", f"{index}: {field}")

    if not isinstance(item, dict):
        raise invalid("–")
    unknown = sorted(set(item) - _ITEM_KEYS)
    if unknown:
        raise TariffYamlError("yaml_item_unknown_field", f"{index}: {', '.join(unknown)}")

    def choice(field: str, kind):
        try:
            return kind(item.get(field))
        except ValueError:
            raise invalid(field) from None

    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        raise invalid("name")
    price = item.get("price")
    if not isinstance(price, int | float) or isinstance(price, bool):
        raise invalid("price")
    factor = item.get("factor_pct", 0.0)
    if not isinstance(factor, int | float):
        raise invalid("factor_pct")
    months = item.get("months") or []
    if not isinstance(months, list) or any(not isinstance(m, int) or not 1 <= m <= 12 for m in months):
        raise invalid("months")
    weekdays = item.get("weekdays") or []
    if not isinstance(weekdays, list) or any(day not in WEEKDAYS for day in weekdays):
        raise invalid("weekdays")
    clocks = [_clock(item.get(field), index, field) for field in ("time_from", "time_to")]
    if (clocks[0] is None) != (clocks[1] is None):
        raise invalid("time_to")
    month_prices = item.get("month_prices") or {}
    if not isinstance(month_prices, dict):
        raise invalid("month_prices")
    try:
        prices = tuple(sorted((str(month), float(value)) for month, value in month_prices.items()))
        if any(len(month) != 7 or month[4] != "-" or not 1 <= int(month[5:]) <= 12 for month, _ in prices):
            raise ValueError
    except (TypeError, ValueError):
        raise invalid("month_prices") from None
    return TariffItem(
        name=name.strip(),
        side=choice("side", Side),
        group=choice("group", Group),
        unit=choice("unit", Unit),
        price=float(price),
        valid_from=_date(item["valid_from"], f"{index}: valid_from") if item.get("valid_from") else None,
        months=frozenset(months),
        weekdays=frozenset(WEEKDAYS.index(day) for day in weekdays),
        time_from=clocks[0],
        time_to=clocks[1],
        factor_pct=float(factor),
        month_prices=prices,
    )


def _date(value: Any, field: str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise TariffYamlError("yaml_field_invalid", field) from None


def _clock(value: Any, index: int, field: str) -> time | None:
    if value is None:
        return None
    if isinstance(value, int):
        # YAML 1.1 reads an unquoted 10:00 as minutes in base 60.
        if not 0 <= value < 24 * 60:
            raise TariffYamlError("yaml_item_invalid", f"{index}: {field}")
        return time(value // 60, value % 60)
    try:
        return time.fromisoformat(str(value))
    except ValueError:
        raise TariffYamlError("yaml_item_invalid", f"{index}: {field}") from None
