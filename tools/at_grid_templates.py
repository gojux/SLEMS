"""Tariff templates for Austria: grid fees of every grid area (network level 7)
and the federal levies, from the published regulations.

Sources (2026):
* grid fees: SNE-V 2018 – Novelle 2026, BGBl. II Nr. 305/2025, § 5 Abs. 1 Z 6
  (network level 7) and § 6 lit. b (grid loss fee); metering: SNE-V 2018 § 10
  (maximum price, operators may charge less),
* levies of a whole grid area its operator lists (``AREA_LEVIES``),
* levies: Erneuerbaren-Förderpauschale and -Förderbeitrag 2026 and the
  Elektrizitätsabgabe for households 2026, as published by the grid operators
  (e.g. Netz Niederösterreich, Salzburg Netz price sheets 01/2026).

Run from the repository root: ``python3 tools/at_grid_templates.py``. For a new
year add the new values with their ``VALID_FROM``; earlier files stay.
"""

from __future__ import annotations

from pathlib import Path

VALID_FROM = "2026-01-01"
VALID_TO = "2026-12-31"
YEAR = 2026
SOURCE_GRID = "SNE-V 2018 – Novelle 2026, BGBl. II Nr. 305/2025 (§ 5, § 6); Messentgelt: Höchstpreis § 10 SNE-V 2018"
SOURCE_LEVIES = (
    "Erneuerbaren-Förderpauschale und -Förderbeitrag 2026, Elektrizitätsabgabe (0,1 ct/kWh für Haushalte 2026); "
    "Preisblätter der Netzbetreiber 01/2026"
)
OUT = Path(__file__).resolve().parent.parent / "custom_components" / "slems" / "templates" / "tariffs" / "at"

# Grid area -> (slug, main grid operator, flat fee €/year, AP, SNAP, AP interruptible, SNAP interruptible, loss fee),
# network level 7; ct/kWh unless noted. SNAP: 1 April to 30 September, 10 to 16 o'clock (smart meter read).
AREAS = {
    "Burgenland": ("burgenland", "Netz Burgenland GmbH", 54.00, 8.46, 6.77, 5.30, 4.24, 0.000),
    "Kärnten": ("kaernten", "KNG-Kärnten Netz GmbH", 54.00, 9.67, 7.74, 5.47, 4.38, 0.368),
    "Klagenfurt": ("klagenfurt", "Energie Klagenfurt GmbH", 54.00, 6.90, 5.52, 4.36, 3.49, 0.578),
    "Niederösterreich": ("niederoesterreich", "Netz Niederösterreich GmbH", 54.00, 8.79, 7.03, 6.65, 5.32, 0.384),
    "Oberösterreich": ("oberoesterreich", "Netz Oberösterreich GmbH", 54.00, 6.29, 5.03, 4.09, 3.27, 0.528),
    "Linz": ("linz", "LINZ NETZ GmbH", 54.00, 5.57, 4.46, 4.85, 3.88, 0.487),
    "Salzburg": ("salzburg", "Salzburg Netz GmbH", 54.00, 6.59, 5.27, 3.91, 3.13, 0.357),
    "Steiermark": ("steiermark", "Energienetze Steiermark GmbH", 54.00, 8.82, 7.06, 5.60, 4.48, 0.336),
    "Graz": ("graz", "Stromnetz Graz GmbH & Co KG", 54.00, 5.17, 4.14, 3.16, 2.53, 0.658),
    "Tirol": ("tirol", "TINETZ-Tiroler Netze GmbH", 54.00, 6.81, 5.45, 5.50, 4.40, 0.293),
    "Innsbruck": ("innsbruck", "Innsbrucker Kommunalbetriebe AG", 54.00, 8.03, 6.42, 4.61, 3.69, 0.453),
    "Vorarlberg": ("vorarlberg", "Vorarlberger Energienetze GmbH", 54.00, 4.96, 3.97, 3.60, 2.88, 0.393),
    "Wien": ("wien", "Wiener Netze GmbH", 54.00, 6.98, 5.58, 4.21, 3.37, 0.700),
    "Kleinwalsertal": ("kleinwalsertal", "Energieversorgung Kleinwalsertal GesmbH", 54.00, 17.73, 14.18, 8.70, 6.96, 0.401),
}
# Levies of a grid area on network level 7 that its grid operator lists for all
# its customers (ct/kWh, net): name -> price.
AREA_LEVIES = {
    # Salzburger Gebrauchsabgabe (Salzburg Netz price sheet "Zuschläge zum Systemnutzungsentgelt" 01/2026).
    "Salzburg": {"Gebrauchsabgabe": 0.3789},
}
# Maximum metering price (three-phase meter): 2.40 €/month and metering direction.
METERING_YEAR = 28.80
VAT = "vat_pct:\n  import: {energy: 20, grid: 20, levies: 20}\n  export: {energy: 0, grid: 20, levies: 20}\n"
SUMMER = "months: [4, 5, 6, 7, 8, 9], time_from: '10:00', time_to: '16:00'"


def _number(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".") if value % 1 else f"{value:.1f}"


def grid(area: str, interruptible: bool) -> tuple[Path, str]:
    slug, operator, flat, ap, snap, ap_int, snap_int, loss = AREAS[area]
    kind = "ne7-unterbrechbar" if interruptible else "ne7"
    title = f"Netzbereich {area}, Netzebene 7" + (" unterbrechbar" if interruptible else "")
    items = []
    if not interruptible:
        items.append(f"  - {{name: Netznutzungsentgelt Pauschale, side: import, group: grid, unit: year, price: {_number(flat)}}}")
    work, summer = (ap_int, snap_int) if interruptible else (ap, snap)
    items += [
        f"  - {{name: Netznutzungsentgelt, side: import, group: grid, unit: kwh, price: {_number(work)}}}",
        f"  - {{name: Netznutzungsentgelt, side: import, group: grid, unit: kwh, price: {_number(summer)}, {SUMMER}}}",
        f"  - {{name: Netzverlustentgelt, side: import, group: grid, unit: kwh, price: {_number(loss)}}}",
    ]
    if not interruptible:
        items += [
            f"  - {{name: Messentgelt (Höchstpreis), side: import, group: grid, unit: year, price: {_number(METERING_YEAR)}}}",
            # The metering of the feed-in direction (PV): chosen as an option.
            f"  - {{name: Messentgelt Einspeisung (Höchstpreis), side: export, group: grid, unit: year, "
            f"price: {_number(METERING_YEAR)}, optional: true}}",
        ]
    for name, price in AREA_LEVIES.get(area, {}).items():
        items.append(f"  - {{name: {name}, side: import, group: levies, unit: kwh, price: {_number(price)}}}")
    text = (
        "format: slems-tariff\nversion: 1\n"
        f"id: at/grid/{slug}/{kind}\n"
        f"name: {title}\n"
        f"grid_operator: {operator}\n"
        f"grid_area: {title}\n"
        f"household: {'false' if interruptible else 'true'}\n"
        f"country: AT\nyear: {YEAR}\nparts: [grid]\n"
        f"valid_from: {VALID_FROM}\nvalid_to: {VALID_TO}\n"
        f"source: \"{SOURCE_GRID}\"\n" + VAT + "items:\n" + "\n".join(items) + "\n"
    )
    return OUT / "grid" / slug / f"{VALID_FROM}_{kind}.yaml", text


def levies(interruptible: bool) -> tuple[Path, str]:
    kind = "ne7-unterbrechbar" if interruptible else "ne7"
    title = "Abgaben Österreich, Netzebene 7" + (" unterbrechbar" if interruptible else " (Haushalt)")
    items = ["  - {name: Erneuerbaren-Förderpauschale, side: import, group: levies, unit: year, price: 19.02}"]
    if interruptible:
        items.append("  - {name: Erneuerbaren-Förderbeitrag Netznutzung, side: import, group: levies, unit: kwh, price: 0.347}")
    else:
        items += [
            "  - {name: Erneuerbaren-Förderbeitrag Netznutzung Pauschale, side: import, group: levies, unit: year, price: 3.796}",
            "  - {name: Erneuerbaren-Förderbeitrag Netznutzung, side: import, group: levies, unit: kwh, price: 0.583}",
        ]
    items += [
        "  - {name: Erneuerbaren-Förderbeitrag Netzverlust, side: import, group: levies, unit: kwh, price: 0.037}",
        "  - {name: Elektrizitätsabgabe, side: import, group: levies, unit: kwh, price: 0.1}",
    ]
    text = (
        "format: slems-tariff\nversion: 1\n"
        f"id: at/levies/{kind}\n"
        f"name: {title}\n"
        f"household: {'false' if interruptible else 'true'}\n"
        f"country: AT\nyear: {YEAR}\nparts: [levies]\n"
        f"valid_from: {VALID_FROM}\nvalid_to: {VALID_TO}\n"
        f"source: \"{SOURCE_LEVIES}\"\n" + VAT + "items:\n" + "\n".join(items) + "\n"
    )
    return OUT / "levies" / f"{VALID_FROM}_{kind}.yaml", text


def main() -> None:
    files = [grid(area, interruptible) for area in AREAS for interruptible in (False, True)]
    files += [levies(False), levies(True)]
    for path, text in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    print(f"{len(files)} templates written to {OUT}")


if __name__ == "__main__":
    main()
