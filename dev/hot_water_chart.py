"""Chart for the heat pump hot water guide: a day in the transition season.

An illustrative example, not a simulation: the heat pump heats in short
cycles; on its own it charges the hot water in the cold morning with a start
of its own, with the release of SLEMS the automation appends the charge to a
heating cycle at noon, when there is PV surplus and the air is warmer.

    python3 dev/hot_water_chart.py

writes ``docs/images/hot_water_day_<language>.svg``.
"""

from __future__ import annotations

from pathlib import Path

COLORS = {"pv": "#eda100", "heating": "#4a3aa7", "hot_water": "#2a78d6", "release": "#1baf7a",
          "forced": "#d03b3b", "outdoor": "#898781", "axis": "#c3c2b7", "grid_line": "#e1e0d9",
          "text": "#3d3d3a", "muted": "#6f6e69"}
TEXT = {
    "en": {
        "title": "Hot water on a day in the transition season",
        "pv": "PV", "outdoor": "outdoor temperature", "heating": "heating", "hot_water": "hot water",
        "release": "hot water release", "forced": "forced from 14:55",
        "alone": "Heat pump on its own", "slems": "With the SLEMS release",
        "starts": "{} starts",
        "alone_note": "hot water at 3 °C after the morning showers, own start",
        "slems_note": "buffer full: hot water appended, no start of its own",
        "note": "Example; ▲ compressor start",
    },
    "de": {
        "title": "Warmwasser an einem Tag in der Übergangszeit",
        "pv": "PV", "outdoor": "Außentemperatur", "heating": "Heizen", "hot_water": "Warmwasser",
        "release": "Warmwasser-Freigabe", "forced": "erzwungen ab 14:55",
        "alone": "Wärmepumpe allein", "slems": "Mit der Freigabe von SLEMS",
        "starts": "{} Starts",
        "alone_note": "Warmwasser bei 3 °C nach dem Duschen, eigener Start",
        "slems_note": "Puffer voll: Warmwasser angehängt, kein eigener Start",
        "note": "Beispiel; ▲ Start des Kompressors",
    },
}
W, PAD_L, PAD_R = 760, 56, 72
START, END = 6.0, 19.0
# Mean PV power (kW) and outdoor temperature (°C) at the full hour.
PV = {6: 0.0, 7: 0.2, 8: 0.8, 9: 1.8, 10: 2.9, 11: 3.8, 12: 4.4, 13: 4.5, 14: 4.1, 15: 3.3,
      16: 2.2, 17: 1.1, 18: 0.3, 19: 0.0}
OUTDOOR = {6: 2, 7: 2, 8: 3, 9: 5, 10: 7, 11: 9, 12: 11, 13: 12, 14: 12, 15: 11, 16: 10, 17: 8,
           18: 7, 19: 6}
HEATING_ALONE = [(6.0, 7.5), (9.5, 10.5), (12.5, 13.17), (15.0, 16.0), (17.5, 19.0)]
HOT_WATER_ALONE = [(8.0, 8.83)]
HEATING_SLEMS = [(6.0, 7.5), (9.5, 10.5), (12.5, 13.17), (15.0, 16.0), (17.5, 19.0)]
HOT_WATER_SLEMS = [(13.17, 13.92)]
RELEASE = (11.75, 13.92)
FORCED = 14.92


def x_of(hour: float) -> float:
    return PAD_L + (hour - START) / (END - START) * (W - PAD_L - PAD_R)


def _text(x, y, text, color=COLORS["text"], anchor="start", size=None, weight=None):
    extra = f' font-size="{size}"' if size else ""
    extra += f' font-weight="{weight}"' if weight else ""
    return f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" fill="{color}"{extra}>{text}</text>'


def _bars(runs, color, y, height):
    return [f'<rect x="{x_of(a):.1f}" y="{y}" width="{x_of(b) - x_of(a):.1f}" height="{height}" '
            f'fill="{color}" rx="2"/>' for a, b in runs]


def _starts(runs, y):
    """A triangle at each start (a run that does not continue another one)."""
    ends = {round(b, 2) for _, b in runs}
    out = []
    for a, _ in runs:
        if round(a, 2) in ends:
            continue
        x = x_of(a)
        out.append(f'<polygon points="{x:.1f},{y - 8} {x - 5:.1f},{y} {x + 5:.1f},{y}" '
                   f'fill="{COLORS["text"]}"/>')
    return out, len(out)


def render_svg(lang: str) -> str:
    t = TEXT[lang]
    out = []
    # Legend.
    legend = [("pv", "line"), ("outdoor", "dash"), ("heating", "bar"), ("hot_water", "bar"),
              ("release", "band")]
    x = PAD_L
    for key, kind in legend:
        color = COLORS[key]
        if kind == "line":
            out.append(f'<line x1="{x}" x2="{x + 24}" y1="40" y2="40" stroke="{color}" stroke-width="2"/>')
        elif kind == "dash":
            out.append(f'<line x1="{x}" x2="{x + 24}" y1="40" y2="40" stroke="{color}" stroke-width="2" '
                       f'stroke-dasharray="6 4"/>')
        elif kind == "bar":
            out.append(f'<rect x="{x}" y="34" width="24" height="12" fill="{color}" rx="2"/>')
        else:
            out.append(f'<rect x="{x}" y="34" width="24" height="12" fill="{color}" fill-opacity="0.18" '
                       f'stroke="{color}"/>')
        out.append(_text(x + 30, 44, t[key]))
        x += 30 + 7.2 * len(t[key]) + 22

    # PV and outdoor temperature.
    top, bottom = 70, 170
    for kw in (0, 1, 2, 3, 4, 5):
        y = bottom - kw / 5 * (bottom - top)
        out.append(f'<line x1="{PAD_L}" x2="{W - PAD_R}" y1="{y:.1f}" y2="{y:.1f}" '
                   f'stroke="{COLORS["axis" if kw == 0 else "grid_line"]}"/>')
        out.append(_text(PAD_L - 6, y + 4, f"{kw} kW", COLORS["muted"], "end"))
    for celsius in (0, 5, 10, 15):
        y = bottom - celsius / 15 * (bottom - top)
        out.append(_text(W - PAD_R + 6, y + 4, f"{celsius} °C", COLORS["muted"]))
    pv = [(x_of(h), bottom - kw / 5 * (bottom - top)) for h, kw in PV.items()]
    area = " ".join(f"{px:.1f},{py:.1f}" for px, py in pv)
    out.append(f'<polygon points="{x_of(START):.1f},{bottom} {area} {x_of(END):.1f},{bottom}" '
               f'fill="{COLORS["pv"]}" fill-opacity="0.18"/>')
    out.append(f'<polyline points="{area}" fill="none" stroke="{COLORS["pv"]}" stroke-width="2" '
               f'stroke-linejoin="round"/>')
    temps = " ".join(f"{x_of(h):.1f},{bottom - c / 15 * (bottom - top):.1f}" for h, c in OUTDOOR.items())
    out.append(f'<polyline points="{temps}" fill="none" stroke="{COLORS["outdoor"]}" stroke-width="2" '
               f'stroke-dasharray="6 4" stroke-linejoin="round"/>')

    # The two rows of the heat pump.
    rows = [
        (t["alone"], 214, HEATING_ALONE, HOT_WATER_ALONE, None, t["alone_note"], HOT_WATER_ALONE[0][1]),
        (t["slems"], 310, HEATING_SLEMS, HOT_WATER_SLEMS, RELEASE, t["slems_note"], HOT_WATER_SLEMS[0][1]),
    ]
    for label, y, heating, hot_water, release, note, note_at in rows:
        out.append(_text(PAD_L, y - 18, label, weight="bold"))
        if release is not None:
            a, b = release
            out.append(f'<rect x="{x_of(a):.1f}" y="{y - 8}" width="{x_of(b) - x_of(a):.1f}" height="38" '
                       f'fill="{COLORS["release"]}" fill-opacity="0.18" stroke="{COLORS["release"]}"/>')
            fx = x_of(FORCED)
            out.append(f'<line x1="{fx:.1f}" x2="{fx:.1f}" y1="{y - 8}" y2="{y + 30}" '
                       f'stroke="{COLORS["forced"]}" stroke-width="2" stroke-dasharray="4 3"/>')
            out.append(_text(fx + 6, y - 2, t["forced"], COLORS["forced"]))
        out += _bars(heating, COLORS["heating"], y, 22)
        out += _bars(hot_water, COLORS["hot_water"], y, 22)
        triangles, count = _starts(sorted(heating + hot_water), y + 36)
        out += triangles
        out.append(_text(W - PAD_R + 6, y + 15, t["starts"].format(count), COLORS["muted"]))
        out.append(_text(x_of(note_at) + 6, y + 50, note, COLORS["muted"],
                         "end" if note_at > 14 else "start"))

    # Time axis.
    axis_y = 380
    out.append(f'<line x1="{PAD_L}" x2="{W - PAD_R}" y1="{axis_y}" y2="{axis_y}" stroke="{COLORS["axis"]}"/>')
    for hour in range(int(START), int(END) + 1):
        x = x_of(hour)
        out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{axis_y}" y2="{axis_y + 4}" stroke="{COLORS["axis"]}"/>')
        if hour % 2 == 0:
            out.append(_text(x, axis_y + 18, f"{hour}:00", COLORS["muted"], "middle"))
    out.append(_text(PAD_L, axis_y + 40, t["note"], COLORS["muted"]))

    height = axis_y + 52
    head = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {height}" width="{W}" '
            f'height="{height}" font-family="Helvetica, Arial, sans-serif" font-size="12">\n'
            f'<rect width="{W}" height="{height}" fill="#ffffff"/>\n'
            + _text(PAD_L, 22, t["title"], size=16, weight="bold"))
    return head + "\n" + "\n".join(out) + "\n</svg>\n"


if __name__ == "__main__":
    images = Path(__file__).resolve().parent.parent / "docs" / "images"
    for lang in TEXT:
        (images / f"hot_water_day_{lang}.svg").write_text(render_svg(lang))
