"""Tariff templates for Germany: the federal levies and the EEG feed-in credit
of small PV plants, from the published values.

Sources:
* levies 2026: Stromsteuer (§ 3 StromStG, households), KWKG-Umlage,
  Offshore-Netzumlage and Aufschlag für besondere Netznutzung (formerly
  § 19 StromNEV-Umlage) for non-privileged consumers as published by the
  transmission system operators on netztransparenz.de (October 2025),
  Konzessionsabgabe: maximum for supplies from the low voltage grid (§ 2 KAV),
  by the size of the municipality; most municipalities charge the maximum,
* grid fees of a grid operator from its price sheet (``GRID``): low voltage
  without load profile metering, and the variants for controllable devices
  (§ 14a EnWG, connected since 2024): module 1 (a flat credit per year) and
  module 1 + 3 (time variable working price, needs a smart meter). Module 2
  (reduced price on a separate meter) is left out, as it prices only the
  device. Metering (Messstellenbetrieb) is billed by the metering operator,
  offered as options at its legal maximum (§ 30, § 32 MsbG),
* EEG feed-in credit: Bundesnetzagentur, "Fördersätze für Solaranlagen"
  (VergSaetzeAug26bisDez26.xlsx), building-mounted plants with partial
  feed-in, rounded to two decimals as published. The rate stays fixed for
  20 years after the year of commissioning; plants commissioned since
  25 February 2025 get nothing while the day-ahead price is negative
  (§ 51 EEG 2023, Solarspitzengesetz).

Run from the repository root: ``python3 tools/de_templates.py``. For a new
year or commissioning period add the values; earlier files stay.
"""

from __future__ import annotations

import calendar
from datetime import date
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "custom_components" / "slems" / "templates" / "tariffs" / "de"
VAT = "vat_pct:\n  import: {energy: 19, grid: 19, levies: 19}\n  export: {energy: 0, grid: 19, levies: 19}\n"

LEVIES_YEAR = 2026
SOURCE_LEVIES = (
    "Stromsteuer § 3 StromStG; KWKG-Umlage, Offshore-Netzumlage und Aufschlag für besondere Netznutzung 2026 "
    "(Übertragungsnetzbetreiber, netztransparenz.de, Oktober 2025); Konzessionsabgabe: Höchstbetrag § 2 KAV"
)
# ct/kWh, net.
LEVIES = {
    "Stromsteuer": 2.05,
    "KWKG-Umlage": 0.446,
    "Offshore-Netzumlage": 0.941,
    "Aufschlag für besondere Netznutzung": 1.559,
}
# Municipality size -> (slug, Konzessionsabgabe ct/kWh) for supplies from the low voltage grid (§ 2 Abs. 2, 7 KAV).
CONCESSION = {
    "bis 25.000 Einwohner": ("bis-25000", 1.32),
    "bis 100.000 Einwohner": ("bis-100000", 1.59),
    "bis 500.000 Einwohner": ("bis-500000", 1.99),
    "über 500.000 Einwohner": ("ueber-500000", 2.39),
}

GRID_YEAR = 2026
# Metering (Messstellenbetrieb) as options: the legal maximum per year (gross,
# § 30 and § 32 MsbG since 2025), billed by the metering operator.
METERING = [
    ("Messstellenbetrieb moderne Messeinrichtung (Höchstpreis)", 25.0),
    ("Messstellenbetrieb intelligentes Messsystem bis 6.000 kWh (Höchstpreis)", 30.0),
    ("Messstellenbetrieb intelligentes Messsystem 6.000 bis 10.000 kWh (Höchstpreis)", 40.0),
    ("Messstellenbetrieb intelligentes Messsystem 10.000 bis 20.000 kWh, PV ab 7 kW oder § 14a (Höchstpreis)", 50.0),
    ("Steuerbox (Höchstpreis)", 50.0),
]
# Operator slug -> price sheet data (ct/kWh and €/year net), low voltage. Module 3:
# windows (tariff level, ct/kWh, from, to, may cross midnight) in the months of
# ``module3_months`` (empty: all year); other hours have the standard working price.
GRID = {
    "netze-bw": {
        "operator": "Netze BW GmbH",
        "area": "Netze BW",
        "source": "Netze BW GmbH, Endgültige Preise für die Nutzung des Stromverteilnetzes, gültig ab 1. Januar 2026 "
        "(Version 1.2, 12.12.2025), Preisblatt 2 und 2a",
        "base_year": 84.00,
        "work": 7.57,
        "module1_year": 124.00,
        "module3": [("Hochtarif", 11.06, "17:00", "22:00"), ("Niedrigtarif", 3.03, "10:00", "14:00")],
        # Quarters with module 3 (months); empty: the whole year.
        "module3_months": [],
    },
    "westnetz": {
        "operator": "Westnetz GmbH",
        "area": "Westnetz",
        "source": "Westnetz GmbH, Entgelte für Netznutzung, Preisgültigkeit ab 01.01.2026, Preisblatt 2 und 6",
        "base_year": 80.30,
        "work": 9.53,
        "module1_year": 138.70,
        "module3": [("Niedriglast", 0.95, "00:00", "07:00"), ("Hochlast", 15.65, "15:00", "20:00")],
        "module3_months": [],
    },
    "bayernwerk": {
        "operator": "Bayernwerk Netz GmbH",
        "area": "Bayernwerk",
        "source": "Bayernwerk Netz GmbH, Preisblatt Netzentgelte Strom, gültig ab 01. Januar 2026, Preisblatt SLP und sVE",
        "base_year": 98.55,
        "work": 4.72,
        "module1_year": 102.63,
        "module3": [("Niedriglast", 0.47, "10:00", "15:00"), ("Hochlast", 9.03, "17:00", "22:00")],
        "module3_months": [4, 5, 6, 7, 8, 9],
    },
    "avacon": {
        "operator": "Avacon Netz GmbH",
        "area": "Avacon",
        "source": "Avacon Netz GmbH, Preisblätter Strom, gültig ab 01.01.2026 (Stand 10.12.2025), Preisblatt SLP und sVE",
        "base_year": 80.30,
        "work": 6.04,
        "module1_year": 112.53,
        "module3": [("Niedriglast", 0.60, "23:00", "05:00"), ("Hochlast", 8.41, "16:30", "21:00")],
        "module3_months": [1, 2, 3, 10, 11, 12],
    },
    "e-dis": {
        "operator": "E.DIS Netz GmbH",
        "area": "E.DIS",
        "source": "E.DIS Netz GmbH, Endgültige Netzentgelte 2026 (Stand 10. Dezember 2025), Preisblatt SLP und sVE",
        "base_year": 76.65,
        "work": 5.47,
        "module1_year": 108.25,
        "module3": [
            ("Niedriglast", 0.55, "23:30", "05:00"),
            ("Hochlast", 8.80, "10:15", "12:00"),
            ("Hochlast", 8.80, "16:45", "20:15"),
        ],
        "module3_months": [1, 2, 3, 10, 11, 12],
    },
    "sh-netz": {
        "operator": "Schleswig-Holstein Netz GmbH",
        "area": "Schleswig-Holstein Netz",
        "source": "Schleswig-Holstein Netz GmbH, Preisblatt Netzentgelte Strom, gültig ab 01. Januar 2026 "
        "(Stand 10. Dezember 2025), Preisblatt SLP und sVE",
        "base_year": 94.90,
        "work": 6.40,
        "module1_year": 115.23,
        "module3": [
            ("Niedriglast", 0.64, "22:00", "05:00"),
            ("Hochlast", 8.32, "09:00", "14:00"),
            ("Hochlast", 8.32, "17:00", "21:00"),
        ],
        "module3_months": [1, 2, 3, 10, 11, 12],
    },
    "stromnetz-berlin": {
        "operator": "Stromnetz Berlin GmbH",
        "area": "Stromnetz Berlin",
        "source": "Stromnetz Berlin GmbH, Preisblatt Netzentgelte ab 01.01.2026, vorläufig (Stand 15.10.2025)",
        "base_year": 33.36,
        "work": 7.46,
        "module1_year": 123.18,
        "module3": [
            ("Niedriglast", 2.61, "22:15", "06:30"),
            ("Hochlast", 13.94, "17:15", "20:15"),
        ],
        "module3_months": [],
    },
    "hamburger-energienetze": {
        "operator": "Hamburger Energienetze GmbH",
        "area": "Hamburg",
        "source": "Hamburger Energienetze GmbH, Netzentgelte Stromnetz ab 01.01.2026",
        "base_year": 75.00,
        "work": 7.29,
        "module1_year": 121.90,
        "module3": [
            ("Niedriglast", 2.51, "22:30", "07:30"),
            ("Hochlast", 8.75, "08:00", "21:00"),
        ],
        "module3_months": [1, 2, 3, 10, 11, 12],
    },
    "ewe-netz": {
        "operator": "EWE NETZ GmbH",
        "area": "EWE NETZ",
        "source": "EWE NETZ GmbH, Netzentgelte Strom für 2026 (Stand 15.12.2025)",
        "base_year": 102.00,
        "work": 3.20,
        "module1_year": 91.23,
        "module3": [
            ("Niedriglast", 0.32, "23:00", "05:00"),
            ("Hochlast", 5.62, "16:30", "20:30"),
        ],
        "module3_months": [],
    },
    "n-ergie-netz": {
        "operator": "N-ERGIE Netz GmbH",
        "area": "N-ERGIE Netz",
        "source": "N-ERGIE Netz GmbH, Preisblätter Netznutzung Strom ab 01.01.2026 (Stand 16.12.2025)",
        "base_year": 95.00,
        "work": 5.96,
        "module1_year": 111.93,
        "module3": [
            ("Niedriglast", 2.38, "23:00", "06:00"),
            ("Niedriglast", 2.38, "12:00", "14:15"),
            ("Hochlast", 11.77, "18:00", "21:00"),
        ],
        "module3_months": [],
    },
    "swm-infrastruktur": {
        "operator": "SWM Infrastruktur GmbH & Co. KG",
        "area": "München (SWM)",
        "source": "SWM Infrastruktur, Netzentgelte Strom 2026 (Stand 12/2025)",
        "base_year": 63.27,
        "work": 6.47,
        "module1_year": 115.75,
        "module3": [
            ("Niedriglast", 2.59, "00:45", "06:45"),
            ("Hochlast", 7.14, "08:30", "20:45"),
        ],
        "module3_months": [1, 2, 3, 10, 11, 12],
    },
    "rheinnetz": {
        "operator": "RheinNetz GmbH",
        "area": "RheinNetz (Köln)",
        "source": "RheinNetz GmbH, Netznutzungsentgelte Strom 2026, Preisblatt 1 bis 3",
        "base_year": 150.00,
        "work": 3.78,
        "module1_year": 95.58,
        "module3": [
            ("Niedriglast", 1.13, "23:45", "05:45"),
            ("Hochlast", 6.64, "17:00", "19:30"),
        ],
        "module3_months": [],
    },
    "enercity-netz": {
        "operator": "enercity Netz GmbH",
        "area": "enercity Netz (Hannover)",
        "source": "enercity Netz GmbH, Preisblatt Strom ab 1. Januar 2026, Preisblatt 2",
        "base_year": 53.00,
        "work": 8.54,
        "module1_year": 131.28,
        "module3": [
            ("Niedriglast", 0.86, "00:00", "06:00"),
            ("Hochlast", 13.35, "16:30", "20:15"),
        ],
        "module3_months": [],
    },
    "thueringer-energienetze": {
        "operator": "TEN Thüringer Energienetze GmbH & Co. KG",
        "area": "Thüringer Energienetze",
        "source": "TEN Thüringer Energienetze, Netzentgelte Strom gültig ab 01.01.2026, Preisblatt SLP und STV",
        "base_year": 87.60,
        "work": 5.56,
        "module1_year": 108.93,
        "module3": [
            ("Niedriglast", 1.67, "22:00", "06:00"),
            ("Hochlast", 8.62, "08:00", "09:00"),
            ("Hochlast", 8.62, "10:30", "12:00"),
            ("Hochlast", 8.62, "17:30", "19:00"),
        ],
        "module3_months": [1, 2, 3, 10, 11, 12],
    },
    "netze-odr": {
        "operator": "Netze ODR GmbH",
        "area": "Netze ODR",
        "source": "Netze ODR GmbH, Netzentgelte Strom 2026, endgültig (Stand 17.12.2025)",
        "base_year": 65.70,
        "work": 5.87,
        "module1_year": 111.25,
        "module3": [
            ("Niedriglast", 2.35, "11:00", "17:00"),
            ("Hochlast", 10.18, "22:00", "00:00"),
        ],
        "module3_months": [4, 5, 6, 7, 8, 9],
    },
    "lew-verteilnetz": {
        "operator": "LEW Verteilnetz GmbH",
        "area": "LEW Verteilnetz",
        "source": "LEW Verteilnetz GmbH, Netzentgelte Strom ab 01.01.2026 (Stand 06.10.2025), Preisblatt SLP und sVE",
        "base_year": 73.00,
        "work": 4.09,
        "module1_year": 97.90,
        "module3": [
            ("Niedriglast", 0.41, "10:00", "15:00"),
            ("Hochlast", 8.09, "17:00", "21:00"),
        ],
        "module3_months": [],
    },
    "westfalen-weser-netz": {
        "operator": "Westfalen Weser Netz GmbH",
        "area": "Westfalen Weser Netz",
        "source": "Westfalen Weser Netz GmbH, Preisblatt Netznutzung Strom 2026, vorläufig (Stand 18.11.2025)",
        "base_year": 120.45,
        "work": 6.46,
        "module1_year": 115.68,
        "module3": [
            ("Niedriglast", 2.58, "11:00", "16:00"),
            ("Hochlast", 10.90, "17:00", "21:00"),
        ],
        "module3_months": [4, 5, 6, 7, 8, 9],
    },
    "syna": {
        "operator": "Syna GmbH",
        "area": "Syna",
        "source": "Syna GmbH, Entgelte für den Zugang zum Stromverteilnetz 2026, Preisblatt 4 und 6a",
        "base_year": 91.25,
        "work": 8.67,
        "module1_year": 132.26,
        "module3": [
            ("Niedriglast", 0.87, "00:00", "06:00"),
            ("Hochlast", 13.46, "17:00", "21:00"),
        ],
        "module3_months": [1, 2, 3, 10, 11, 12],
    },
    "mainnetz": {
        "operator": "Mainnetz GmbH",
        "area": "Mainnetz",
        "source": "Mainnetz GmbH, Preisblätter Strom 2026, final (Stand 22.12.2025), Preisblatt 2",
        "base_year": 72.00,
        "work": 7.96,
        "module1_year": 126.93,
        "module3": [
            ("Niedriglast", 3.18, "02:15", "05:45"),
            ("Hochlast", 12.53, "17:45", "19:45"),
        ],
        "module3_months": [1, 2, 3, 10, 11, 12],
    },
}
SOURCE_EEG = (
    "Bundesnetzagentur, Fördersätze für Solaranlagen (EEG 2023), Gebäudeanlagen mit Teileinspeisung bis 10 kWp; "
    "die Vergütung gilt 20 Jahre ab dem Jahr der Inbetriebnahme"
)
# Commissioning (first day, last day) -> ct/kWh partial feed-in up to 10 kWp and
# for the power from 10 to 40 kWp (a larger plant gets the mean weighted by the
# power); whether nothing is paid at negative day-ahead prices (§ 51 EEG 2023).
EEG_PERIODS = [
    ("2023-01-01", "2024-01-31", 8.20, 7.10, False),
    ("2024-02-01", "2024-07-31", 8.11, 7.03, False),
    ("2024-08-01", "2025-01-31", 8.03, 6.95, False),
    ("2025-02-01", "2025-02-24", 7.94, 6.88, False),
    ("2025-02-25", "2025-07-31", 7.94, 6.88, True),
    ("2025-08-01", "2026-01-31", 7.86, 6.80, True),
    ("2026-02-01", "2026-07-31", 7.78, 6.73, True),
    ("2026-08-01", "2026-12-31", 7.70, 6.66, True),
]


def _comma(value: float) -> str:
    return f"{value:.2f}".replace(".", ",")


def _period(first: str, last: str) -> str:
    """'08/2026–12/2026' for whole months, else the days ('25.02.2025–31.07.2025')."""
    if first.endswith("-01") and date.fromisoformat(last).day == calendar.monthrange(int(last[:4]), int(last[5:7]))[1]:
        return f"{first[5:7]}/{first[:4]}–{last[5:7]}/{last[:4]}"
    return f"{first[8:10]}.{first[5:7]}.{first[:4]}–{last[8:10]}.{last[5:7]}.{last[:4]}"


def levies(size: str) -> tuple[Path, str]:
    slug, concession = CONCESSION[size]
    items = [
        f"  - {{name: {name}, side: import, group: levies, unit: kwh, price: {price}}}" for name, price in LEVIES.items()
    ]
    items.append(f"  - {{name: Konzessionsabgabe, side: import, group: levies, unit: kwh, price: {concession}}}")
    text = (
        "format: slems-tariff\nversion: 1\n"
        f"id: de/levies/{slug}\n"
        f"name: Abgaben Deutschland, Konzessionsabgabe {_comma(concession)} ct (Gemeinde {size})\n"
        "household: true\n"
        f"country: DE\ncurrency: EUR\nyear: {LEVIES_YEAR}\nparts: [levies]\n"
        f"valid_from: {LEVIES_YEAR}-01-01\nvalid_to: {LEVIES_YEAR}-12-31\n"
        f"source: \"{SOURCE_LEVIES}\"\n" + VAT + "items:\n" + "\n".join(items) + "\n"
    )
    return OUT / "levies" / f"{LEVIES_YEAR}-01-01_{slug}.yaml", text


def grid(slug: str, variant: str) -> tuple[Path, str]:
    sheet = GRID[slug]
    titles = {
        "ns": "Niederspannung",
        "ns-14a-modul-1": "Niederspannung, § 14a Modul 1",
        "ns-14a-modul-3": "Niederspannung, § 14a Modul 1 + 3 (zeitvariabel)",
    }
    title = f"Netzgebiet {sheet['area']}, {titles[variant]}"
    items = [
        f"  - {{name: Netzentgelt Grundpreis, side: import, group: grid, unit: year, price: {sheet['base_year']:.2f}}}",
        f"  - {{name: Netzentgelt Arbeitspreis, side: import, group: grid, unit: kwh, price: {sheet['work']:.2f}}}",
    ]
    if variant != "ns":
        items.append(
            "  - {name: Netzentgeltreduzierung § 14a Modul 1, side: import, group: grid, unit: year, "
            f"price: {-sheet['module1_year']:.2f}}}"
        )
    if variant == "ns-14a-modul-3":
        months = f", months: {sheet['module3_months']}" if sheet["module3_months"] else ""
        for _level, price, start, end in sheet["module3"]:
            items.append(
                f"  - {{name: Netzentgelt Arbeitspreis, side: import, group: grid, unit: kwh, price: {price:.2f}{months}, "
                f"time_from: '{start}', time_to: '{end}'}}"
            )
    for name, gross in METERING:
        items.append(
            f"  - {{name: '{name}', side: import, group: grid, unit: year, price: {gross / 1.19:.2f}, optional: true}}"
        )
    text = (
        "format: slems-tariff\nversion: 1\n"
        f"id: de/grid/{slug}/{variant}\n"
        f"name: {title}\n"
        f"grid_operator: {sheet['operator']}\n"
        f"grid_area: {title}\n"
        f"household: {'true' if variant == 'ns' else 'false'}\n"
        f"country: DE\ncurrency: EUR\nyear: {GRID_YEAR}\nparts: [grid]\n"
        f"valid_from: {GRID_YEAR}-01-01\nvalid_to: {GRID_YEAR}-12-31\n"
        f"source: \"{sheet['source']}; Messstellenbetrieb als Option mit dem Höchstpreis nach § 30, § 32 MsbG\"\n" + VAT + "items:\n" + "\n".join(items) + "\n"
    )
    return OUT / "grid" / slug / f"{GRID_YEAR}-01-01_{variant}.yaml", text


def eeg(first: str, last: str, price: float, price_40: float, zero_when_negative: bool) -> tuple[Path, str]:
    # Each commissioning period is a tariff of its own: a later period is no update of it.
    period = f"{first[:7]}" if first[8:] == "01" else first
    extra = ", zero_when_negative: true" if zero_when_negative else ""
    source = SOURCE_EEG + f"; Anlagen über 10 kWp: {_comma(price_40)} ct/kWh für die Leistung von 10 bis 40 kWp, anteilig gemittelt" + ("; keine Vergütung bei negativen Börsenpreisen (§ 51 EEG 2023)" if zero_when_negative else "")
    text = (
        "format: slems-tariff\nversion: 1\n"
        f"id: de/eeg/solar-teileinspeisung-bis-10-kwp/{period}\n"
        # The period first: it tells the templates apart in a narrow list.
        f"name: Inbetriebnahme {_period(first, last)}, PV bis 10 kWp\n"
        "supplier: EEG\n"
        f"country: DE\ncurrency: EUR\nyear: {first[:4]}\nparts: [energy]\n"
        f"valid_from: {first}\n"
        f"source: \"{source}\"\n" + VAT + "items:\n"
        f"  - {{name: Einspeisevergütung, side: export, group: energy, unit: kwh, price: {price:.2f}{extra}}}\n"
    )
    return OUT / "energy" / "eeg" / f"{first}_solar-teileinspeisung-bis-10-kwp.yaml", text


def main() -> None:
    files = [levies(size) for size in CONCESSION]
    files += [grid(slug, variant) for slug in GRID for variant in ("ns", "ns-14a-modul-1", "ns-14a-modul-3")]
    files += [eeg(*period) for period in EEG_PERIODS]
    for path, text in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    print(f"{len(files)} templates written to {OUT}")


if __name__ == "__main__":
    main()
