"""Charts for the README: how the options change the state of charge and the grid.

The curves come from the SLEMS state of charge projection (``soc_projection``)
with example data: 8 kWp PV on a sunny day, a household with morning and
evening peaks and a 10 kWh battery. Two steps, because the projection needs
Home Assistant and the tests container writes files as root:

    docker compose --profile tests run --rm -T --entrypoint python3 tests \\
        dev/readme_charts.py simulate > /tmp/charts.json
    python3 dev/readme_charts.py render /tmp/charts.json

``render`` writes ``docs/images/<scenario>_<language>.svg``.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

HOURS = 30  # 18:00 until midnight of the next day
START_HOUR = 18
CAPACITY_WH = 10000.0
EFFICIENCY = 0.95
PV_PEAK_KWP = 8.0
CAP_LIMIT_W = PV_PEAK_KWP * 1000 * 0.6

# Mean power per hour of the day (W).
PV_SUNNY = {6: 200, 7: 800, 8: 1800, 9: 3000, 10: 4200, 11: 5200, 12: 5800, 13: 5900,
            14: 5500, 15: 4600, 16: 3500, 17: 2300, 18: 1200, 19: 400, 20: 50}
LOAD = {h: 250 for h in range(24)} | {6: 600, 7: 700, 8: 400, 12: 800, 18: 900, 19: 1000,
                                       20: 900, 21: 700, 22: 400}
# Cloudy day with an evening and a morning peak (peak shaving example).
LOAD_PEAKS = LOAD | {18: 2600, 19: 1900, 7: 2300}


def simulate() -> dict:
    from datetime import datetime, timedelta

    from homeassistant.util import dt as dt_util

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from custom_components.slems.allocation import BatteryGroup
    from custom_components.slems.feed_in_cap import CapSettings, plan_cap
    from custom_components.slems.soc_projection import ProjectionSettings, project_soc

    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Vienna"))
    day0 = datetime(2026, 6, 1, tzinfo=dt_util.get_default_time_zone())
    now = day0 + timedelta(hours=START_HOUR)

    def series(profile: dict[int, float], factor: float = 1.0) -> dict:
        return {
            day0 + timedelta(days=d, hours=h): profile.get(h, 0) * factor
            for d in (0, 1)
            for h in range(24)
        }

    def settings(**changes) -> ProjectionSettings:
        values = {
            "grid_friendly_charging": False,
            "charge_buffer_wh": 0.0,
            "night_buffer_wh": 1000.0,
            "peak_shaving": False,
            "peak_shaving_grid_limit_w": 1500.0,
            "peak_shaving_soc_threshold_pct": 35.0,
            "night_discharge": False,
            "night_reserve_pct": 25.0,
        }
        values.update(changes)
        return ProjectionSettings(**values)

    def run(soc, pv, load, projection_settings, cap_limit=None, with_cap=False):
        battery = BatteryGroup(
            soc_pct=soc, capacity_wh=CAPACITY_WH, max_charge_w=5000, max_discharge_w=5000,
            charge_efficiency=EFFICIENCY, min_soc_pct=10.0,
        )
        cap = None
        if with_cap:
            cap = plan_cap(
                now, battery, 5000, 5000, pv, load,
                CapSettings(limit_w=cap_limit, buffer_pct=20, min_buffer_wh=400),
            )
        projection = project_soc(
            now, battery, pv, load, None, projection_settings, None, 0.0, cap
        )
        stored = soc / 100 * CAPACITY_WH
        result = {"soc": [soc], "grid": [], "curtailed": []}
        for i in range(HOURS):
            hour = now + timedelta(hours=i)
            end = projection.soc_pct[hour] / 100 * CAPACITY_WH
            delta = end - stored
            battery_ac = delta / EFFICIENCY if delta > 0 else delta * EFFICIENCY
            grid = load[hour] - pv[hour] + battery_ac
            curtailed = 0.0
            if cap_limit is not None and -grid > cap_limit:
                curtailed = -grid - cap_limit
                grid = -cap_limit
            result["soc"].append(round(projection.soc_pct[hour], 1))
            result["grid"].append(round(grid))
            result["curtailed"].append(round(curtailed))
            stored = end
        return result

    sunny, load, peaks = series(PV_SUNNY), series(LOAD), series(LOAD_PEAKS)
    cloudy = series(PV_SUNNY, 0.2)

    def profile(pv, consumption) -> dict:
        hours = [now + timedelta(hours=i) for i in range(HOURS)]
        return {"pv": [pv[h] for h in hours], "load": [consumption[h] for h in hours]}

    result = {
        "grid_friendly": {
            "without": run(100, sunny, load, settings()),
            "with": run(100, sunny, load, settings(grid_friendly_charging=True, charge_buffer_wh=1000)),
        },
        "night_discharge": {
            "without": run(100, sunny, load, settings()),
            "with": run(100, sunny, load, settings(night_discharge=True, night_reserve_pct=10.0)),
        },
        "peak_shaving": {
            "without": run(50, cloudy, peaks, settings()),
            "with": run(50, cloudy, peaks, settings(peak_shaving=True)),
        },
        "feed_in_cap": {
            "without": run(100, sunny, load, settings(), cap_limit=CAP_LIMIT_W),
            "with": run(
                100, sunny, load, settings(night_discharge=True),
                cap_limit=CAP_LIMIT_W, with_cap=True,
            ),
        },
    }
    for scenario, scenario_data in result.items():
        scenario_data |= profile(cloudy if scenario == "peak_shaving" else sunny,
                                 peaks if scenario == "peak_shaving" else load)
    return result


# --- rendering (standard library only) -----------------------------------------

COLORS = {"pv": "#eda100", "load": "#4a3aa7", "soc": "#1baf7a", "grid": "#2a78d6", "without": "#898781", "curtailed": "#d03b3b",
          "axis": "#c3c2b7", "grid_line": "#e1e0d9", "text": "#3d3d3a", "muted": "#6f6e69",
          "limit": "#eda100"}
TEXT = {
    "en": {
        "power": "PV and consumption", "pv": "PV", "load": "consumption",
        "soc": "State of charge", "grid": "Grid (+ import / − export)",
        "without": "without", "with": "with", "curtailed": "curtailed", "limit": "feed-in limit",
        "titles": {
            "grid_friendly": "Grid friendly charging",
            "night_discharge": "Night discharge",
            "peak_shaving": "Import peak shaving (cloudy day)",
            "feed_in_cap": "Feed-in cap 60 % (with night discharge)",
        },
        "note": "Example: 8 kWp, 10 kWh battery; SLEMS state of charge projection, hourly means",
    },
    "de": {
        "power": "PV und Verbrauch", "pv": "PV", "load": "Verbrauch",
        "soc": "Ladezustand", "grid": "Netz (+ Bezug / − Einspeisung)",
        "without": "ohne", "with": "mit", "curtailed": "abgeregelt", "limit": "Einspeisegrenze",
        "titles": {
            "grid_friendly": "Netzdienliches Laden",
            "night_discharge": "Nachtentladung",
            "peak_shaving": "Bezugsspitzen abfangen (trüber Tag)",
            "feed_in_cap": "Einspeisebegrenzung 60 % (mit Nachtentladung)",
        },
        "note": "Beispiel: 8 kWp, 10-kWh-Batterie; SoC-Projektion von SLEMS, Stundenmittel",
    },
}
W, PANEL_H, POWER_H, PAD_L, PAD_R, TOP = 760, 150, 110, 56, 16, 80


def _polyline(points, color, dash=False, width=2):
    coords = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    extra = ' stroke-dasharray="6 4"' if dash else ""
    return (f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width="{width}" '
            f'stroke-linejoin="round"{extra}/>')


def render_svg(scenario: str, data: dict, lang: str) -> str:
    t = TEXT[lang]
    plot_w = W - PAD_L - PAD_R
    top0 = TOP
    top1 = top0 + POWER_H + 40
    top2 = top1 + PANEL_H + 40
    height = top2 + PANEL_H + 70
    x_hour = lambda i: PAD_L + i / HOURS * plot_w  # noqa: E731
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {height}" width="{W}" '
        f'height="{height}" font-family="Helvetica, Arial, sans-serif" font-size="12">',
        f'<rect width="{W}" height="{height}" fill="#ffffff"/>',
        f'<text x="{PAD_L}" y="22" font-size="16" font-weight="bold" fill="{COLORS["text"]}">'
        f'{t["titles"][scenario]}</text>',
    ]
    # Legend.
    lx = PAD_L
    parts.append(_polyline([(lx, 40), (lx + 24, 40)], COLORS["without"], True))
    parts.append(f'<text x="{lx + 30}" y="44" fill="{COLORS["text"]}">{t["without"]}</text>')
    lx += 90
    # "With" in the colour of each panel.
    parts.append(_polyline([(lx, 37), (lx + 24, 37)], COLORS["soc"]))
    parts.append(_polyline([(lx, 43), (lx + 24, 43)], COLORS["grid"]))
    parts.append(f'<text x="{lx + 30}" y="44" fill="{COLORS["text"]}">{t["with"]}</text>')
    lx += 70
    for key in ("pv", "load"):
        parts.append(_polyline([(lx, 40), (lx + 24, 40)], COLORS[key]))
        parts.append(f'<text x="{lx + 30}" y="44" fill="{COLORS["text"]}">{t[key]}</text>')
        lx += 70 if key == "pv" else 110
    if scenario == "feed_in_cap":
        parts.append(f'<rect x="{lx}" y="34" width="14" height="12" fill="{COLORS["curtailed"]}" opacity="0.5"/>')
        parts.append(f'<text x="{lx + 20}" y="44" fill="{COLORS["text"]}">{t["curtailed"]}</text>')
        lx += 110
        parts.append(_polyline([(lx, 40), (lx + 24, 40)], COLORS["limit"], True, 1.5))
        parts.append(f'<text x="{lx + 30}" y="44" fill="{COLORS["text"]}">{t["limit"]}</text>')

    # Panel 0: PV and consumption (the same with and without the option).
    power_top = max(max(data["pv"]), max(data["load"])) / 1000
    power_top = 2 * -(-power_top // 2)
    y_power = lambda w: top0 + POWER_H - w / 1000 / power_top * POWER_H  # noqa: E731
    parts.append(f'<text x="{PAD_L}" y="{top0 - 4}" fill="{COLORS["muted"]}">{t["power"]}</text>')
    value = 0
    while value <= power_top + 1e-9:
        parts.append(f'<line x1="{PAD_L}" x2="{W - PAD_R}" y1="{y_power(value * 1000)}" '
                     f'y2="{y_power(value * 1000)}" stroke="{COLORS["axis"] if value == 0 else COLORS["grid_line"]}"/>')
        parts.append(f'<text x="{PAD_L - 6}" y="{y_power(value * 1000) + 4}" text-anchor="end" '
                     f'fill="{COLORS["muted"]}">{value:g} kW</text>')
        value += 2 if power_top > 4 else 1
    for key in ("pv", "load"):
        points = []
        for i, w in enumerate(data[key]):
            points += [(x_hour(i), y_power(w)), (x_hour(i + 1), y_power(w))]
        parts.append(_polyline(points, COLORS[key]))

    # Panel 1: state of charge.
    y_soc = lambda v: top1 + PANEL_H - v / 100 * PANEL_H  # noqa: E731
    parts.append(f'<text x="{PAD_L}" y="{top1 - 4}" fill="{COLORS["muted"]}">{t["soc"]}</text>')
    for v in (0, 50, 100):
        parts.append(f'<line x1="{PAD_L}" x2="{W - PAD_R}" y1="{y_soc(v)}" y2="{y_soc(v)}" '
                     f'stroke="{COLORS["grid_line"] if v else COLORS["axis"]}"/>')
        parts.append(f'<text x="{PAD_L - 6}" y="{y_soc(v) + 4}" text-anchor="end" '
                     f'fill="{COLORS["muted"]}">{v} %</text>')
    for key, color, dash in (("without", COLORS["without"], True), ("with", COLORS["soc"], False)):
        soc = data[key]["soc"]
        parts.append(_polyline([(x_hour(i), y_soc(v)) for i, v in enumerate(soc)], color, dash))

    # Panel 2: grid power.
    grids = data["without"]["grid"] + data["with"]["grid"]
    peaks = [g - c for g, c in zip(data["without"]["grid"], data["without"]["curtailed"], strict=True)]
    high = max(1000, max(grids)) / 1000
    low = min(-1000, min(grids + peaks)) / 1000
    step = 2 if high - low > 6 else 1
    high, low = step * -(-high // step), step * (low // step)
    y_grid = lambda w: top2 + (high - w / 1000) / (high - low) * PANEL_H  # noqa: E731
    parts.append(f'<text x="{PAD_L}" y="{top2 - 4}" fill="{COLORS["muted"]}">{t["grid"]}</text>')
    value = low
    while value <= high + 1e-9:
        parts.append(f'<line x1="{PAD_L}" x2="{W - PAD_R}" y1="{y_grid(value * 1000)}" '
                     f'y2="{y_grid(value * 1000)}" stroke="{COLORS["axis"] if value == 0 else COLORS["grid_line"]}"/>')
        parts.append(f'<text x="{PAD_L - 6}" y="{y_grid(value * 1000) + 4}" text-anchor="end" '
                     f'fill="{COLORS["muted"]}">{value:g} kW</text>')
        value += step
    slot = plot_w / HOURS
    if scenario == "feed_in_cap":
        parts.append(_polyline([(PAD_L, y_grid(-CAP_LIMIT_W)), (W - PAD_R, y_grid(-CAP_LIMIT_W))],
                               COLORS["limit"], True, 1.5))
        for key in ("without", "with"):
            for i, c in enumerate(data[key]["curtailed"]):
                if c > 0:
                    # Below the limit line: the export the inverter cuts off.
                    y0, y1 = y_grid(-CAP_LIMIT_W), y_grid(-CAP_LIMIT_W - c)
                    x = x_hour(i) + (1 if key == "without" else slot / 2)
                    parts.append(f'<rect x="{x:.1f}" y="{y0:.1f}" width="{slot / 2 - 1:.1f}" '
                                 f'height="{y1 - y0:.1f}" fill="{COLORS["curtailed"]}" '
                                 f'opacity="{0.5 if key == "without" else 0.9}"/>')
    for key, color, dash in (("without", COLORS["without"], True), ("with", COLORS["grid"], False)):
        grid = data[key]["grid"]
        # Hourly mean drawn as a step line.
        points = []
        for i, w in enumerate(grid):
            points += [(x_hour(i), y_grid(w)), (x_hour(i + 1), y_grid(w))]
        parts.append(_polyline(points, color, dash))

    # Time axis.
    base = top2 + PANEL_H + 18
    for i in range(0, HOURS + 1, 6):
        hour = (START_HOUR + i) % 24
        parts.append(f'<text x="{x_hour(i)}" y="{base}" text-anchor="middle" '
                     f'fill="{COLORS["muted"]}">{hour:02d}:00</text>')
    parts.append(f'<text x="{PAD_L}" y="{base + 26}" font-size="11" fill="{COLORS["muted"]}">'
                 f'{t["note"]}</text>')
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def render(path: str) -> None:
    data = json.loads(Path(path).read_text())
    out = Path(__file__).resolve().parent.parent / "docs" / "images"
    out.mkdir(parents=True, exist_ok=True)
    for scenario, scenario_data in data.items():
        for lang in TEXT:
            (out / f"{scenario}_{lang}.svg").write_text(render_svg(scenario, scenario_data, lang))


if __name__ == "__main__":
    if sys.argv[1] == "simulate":
        json.dump(simulate(), sys.stdout)
    else:
        render(sys.argv[2])
