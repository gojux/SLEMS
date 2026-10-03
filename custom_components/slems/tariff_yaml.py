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
``id`` names a template over all its price levels and stays when it is
renamed; a successor names the templates it replaces (``replaces``).
An item of a template with ``optional: true`` is chosen when the tariff is
made from it (e.g. an upgrade, a bonus with conditions); its ``suffix`` is
then added to the name of the tariff. ``currency`` (ISO code, e.g. EUR or CHF) is the currency of the prices; a
file without it is taken to be in the currency of Home Assistant.
``offer: true`` marks an offer for new contracts: its price is fixed from the
start of a contract, so a newer offer is no update of an existing one.

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
import re
from typing import Any

import yaml

from .reference_values import MARKETS
from .tariff import Group, Role, Side, TariffItem, Unit

FORMAT = "slems-tariff"
VERSION = 1
# Information about the tariff kept with it (not used for the prices).
META_KEYS = (
    "supplier", "grid_operator", "grid_area", "household", "country", "year", "parts",
    "valid_from", "valid_to", "source", "suggest", "templates", "id", "replaces", "declined", "offer",
    "currency", "elcom",
)
# Identifier of a template over all its price levels, e.g. "at/example-energy/fix".
ID_PATTERN = re.compile(r"[a-z0-9-]+(/[a-z0-9-]+)*")
# Hints of a template for the other parts (``suggest``).
SUGGEST_KEYS = ("grid_operator",)
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DEFAULT_VAT = {
    "import": {"energy": 20.0, "grid": 20.0, "levies": 20.0},
    "export": {"energy": 0.0, "grid": 20.0, "levies": 20.0},
}
_TOP_KEYS = {"format", "version", "name", "vat_pct", "items", *META_KEYS}
_ITEM_KEYS = {
    "name", "side", "group", "unit", "price", "factor_pct", "valid_from", "valid_to",
    "months", "weekdays", "time_from", "time_to", "month_prices", "optional", "market", "suffix",
    "zero_when_negative",
}
# Version -> function that turns a file of that version into the next one.
_MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {}


class TariffYamlError(ValueError):
    """A file that is not a valid tariff: ``key`` names the problem, ``detail`` where."""

    def __init__(self, key: str, detail: str = "") -> None:
        super().__init__(f"{key}: {detail}" if detail else key)
        self.key = key
        self.detail = detail


def export_yaml(title: str, data: Mapping[str, Any], currency: str | None = None) -> str:
    """The tariff of a config subentry (``data``) as YAML of the current version,
    in ``currency`` (the one of Home Assistant) unless the tariff names its own."""
    meta = dict(data.get("meta") or {})
    if currency and not meta.get("currency"):
        meta["currency"] = currency
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
    if item.valid_to:
        document["valid_to"] = item.valid_to.isoformat()
    if item.months:
        document["months"] = sorted(item.months)
    if item.weekdays:
        document["weekdays"] = [WEEKDAYS[day] for day in sorted(item.weekdays)]
    if item.time_from and item.time_to:
        document["time_from"] = item.time_from.strftime("%H:%M")
        document["time_to"] = item.time_to.strftime("%H:%M")
    if item.month_prices:
        document["month_prices"] = dict(item.month_prices)
    if item.market:
        document["market"] = item.market
    if item.zero_when_negative:
        document["zero_when_negative"] = True
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
    taken = []
    for raw, item in zip(items, parsed, strict=True):
        data = item.as_dict()
        if raw.get("optional") is not None:
            if not isinstance(raw["optional"], bool):
                raise TariffYamlError("yaml_item_invalid", f"{len(taken) + 1}: optional")
            if raw["optional"]:
                # A template item the user chooses (e.g. an upgrade or a bonus).
                data["optional"] = True
        if raw.get("suffix") is not None:
            if not isinstance(raw["suffix"], str) or not raw.get("optional"):
                raise TariffYamlError("yaml_item_invalid", f"{len(taken) + 1}: suffix")
            # Added to the name of the tariff when the option is chosen (e.g. "Öko+").
            data["suffix"] = raw["suffix"].strip()
        taken.append(data)
    meta = {key: _meta_value(key, document[key]) for key in META_KEYS if document.get(key) is not None}
    if meta.get("valid_from") and meta.get("valid_to") and meta["valid_to"] < meta["valid_from"]:
        raise TariffYamlError("yaml_field_invalid", "valid_to")
    return name.strip(), {
        "role": Role.CURRENT.value,
        "vat": _parse_vat(document.get("vat_pct")),
        "items": taken,
        "meta": meta,
    }


def _meta_value(key: str, value: Any) -> Any:
    if key in ("valid_from", "valid_to"):
        return _date(value, key).isoformat()
    if key == "year":
        if not isinstance(value, int):
            raise TariffYamlError("yaml_field_invalid", key)
        return value
    if key == "elcom":
        # Where a Swiss tariff came from (see elcom): municipality, operator, category.
        if not isinstance(value, dict) or not all(isinstance(value.get(k), str | int) for k in ("municipality", "operator", "category")):
            raise TariffYamlError("yaml_field_invalid", key)
        return {str(k): str(v) for k, v in value.items()}
    if key == "currency":
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z]{3}", value):
            raise TariffYamlError("yaml_field_invalid", key)
        return value.upper()
    if key in ("household", "offer"):
        if not isinstance(value, bool):
            raise TariffYamlError("yaml_field_invalid", key)
        return value
    if key == "suggest":
        if not isinstance(value, dict) or any(k not in SUGGEST_KEYS or not isinstance(v, str) for k, v in value.items()):
            raise TariffYamlError("yaml_field_invalid", key)
        return dict(value)
    if key == "id":
        if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
            raise TariffYamlError("yaml_field_invalid", key)
        return value
    if key == "replaces":
        values = value if isinstance(value, list) else [value]
        if any(not isinstance(v, str) or not ID_PATTERN.fullmatch(v) for v in values):
            raise TariffYamlError("yaml_field_invalid", key)
        return values
    if key == "declined":
        # Family -> date of the newest level declined, or a correction -> its fingerprint.
        if not isinstance(value, dict):
            raise TariffYamlError("yaml_field_invalid", key)
        return {str(k): v.isoformat() if isinstance(v, date) else str(v) for k, v in value.items()}
    if key == "templates":
        if not isinstance(value, list) or any(
            not isinstance(origin, dict) or not isinstance(origin.get("family"), str) for origin in value
        ):
            raise TariffYamlError("yaml_field_invalid", key)
        try:
            return [
                {
                    "family": origin["family"],
                    "valid_from": _date(origin["valid_from"], key).isoformat() if origin.get("valid_from") else None,
                    "valid_to": _date(origin["valid_to"], key).isoformat() if origin.get("valid_to") else None,
                    "from_start": bool(origin.get("from_start", False)),
                    "options": [str(name) for name in origin.get("options") or []],
                    "items": [TariffItem.from_dict(item).as_dict() for item in origin.get("items") or []],
                }
                for origin in value
            ]
        except (KeyError, TypeError, ValueError):
            raise TariffYamlError("yaml_field_invalid", key) from None
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
    market = item.get("market")
    if market is not None and (market not in MARKETS or choice("unit", Unit) is not Unit.MARKET_MONTH):
        raise invalid("market")
    zero_when_negative = item.get("zero_when_negative", False)
    if not isinstance(zero_when_negative, bool):
        raise invalid("zero_when_negative")
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
        valid_to=_date(item["valid_to"], f"{index}: valid_to") if item.get("valid_to") else None,
        market=market,
        zero_when_negative=zero_when_negative,
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
