"""
visualize.py

Reads the daily rollups produced by aggregate.py (rollups/yyyy/mm/dd/
driver-XXXXXXX.json) and renders a plan-vs-actual snapshot chart for each
one, as a hand-rolled SVG -- no matplotlib/pillow, stdlib only, consistent
with the project's zero-dependency principle (see README). SVG is also
just text, so unlike a PNG it diffs cleanly in git the same way the JSON
rollups do.

Output mirrors the same yyyy/mm/dd nesting used everywhere else:

    charts/yyyy/mm/dd/driver-XXXXXXX.svg

Run manually or via cron, same as aggregate.py, and always AFTER it --
this script only reads from rollups/, it never touches inbox/ or the
organized yyyy/mm/dd/ data tree directly. Like aggregate.py, every run is
a full rebuild of charts/ from the current rollups/ -- no incremental/
merge logic.

Visual language (colors, mono font, panel styling) is intentionally
lifted from index.html / the plan-vs-actual React prototype, so a chart
generated here, the entry tool, and the dashboard mockup all read as the
same product rather than three different tools bolted together.

Scope note: this renders the per-day snapshot only (stops / miles /
pieces, planned vs actual). Overall/trend charts from aggregate.py's
`trend.by_route` are a natural follow-on but out of scope here -- flagged
for a later pass rather than folded in unasked.
"""

import json
import os
import sys
from xml.sax.saxutils import escape as _xml_escape

from inbox_common import (
    data_filename_for_driver,
    split_date,
    walk_driver_day_tree,
)

REPO_ROOT = "."
ROLLUPS_DIR = "rollups"
CHARTS_DIR = "charts"

# ---- design tokens, matching index.html's CSS variables and the React ----
# ---- plan-vs-actual prototype, so all three stay visually consistent. ---
COLORS = {
    "bg": "#15191B",
    "panel": "#1F2426",
    "line": "#343B3D",
    "text": "#F2EFE9",
    "text_dim": "#9AA3A5",
    "amber": "#E8A33D",  # planned
    "green": "#4FA876",  # actual, on/ahead of plan
    "red": "#D14B4B",  # actual, behind plan
}
MONO = "'JetBrains Mono','SF Mono',Menlo,Consolas,monospace"

# One row per metric panel. Centralized here (same pattern as
# aggregate.py's TREND_METRICS / index.html's HOURLY_METRICS) so adding a
# future panel is one line here rather than a change to _metric_rows(),
# _panel_svg(), and build_svg() separately.
METRIC_CONFIG = (
    {"key": "stops", "label": "STOPS", "planned_field": "planned_stops",
     "actual_field": "total_stops", "delta_field": "stops_delta", "decimals": 0},
    {"key": "miles", "label": "MILES", "planned_field": "planned_miles",
     "actual_field": "total_miles", "delta_field": "miles_delta", "decimals": 1},
    {"key": "pieces", "label": "PIECES", "planned_field": "planned_pieces",
     "actual_field": "total_pieces", "delta_field": "pieces_delta", "decimals": 0},
)

# ---- layout constants ----
MARGIN = 20
HEADER_H = 56
PANEL_W = 220
PANEL_H = 210
GAP = 16
BAR_W = 46
BAR_GAP = 20
CHART_H = 120


def _esc(s):
    return _xml_escape(str(s))


def _fmt(value, decimals):
    """Format a number for display, or an em dash if it's missing."""
    if value is None:
        return "\u2014"
    try:
        return "{:.{d}f}".format(value, d=decimals) if decimals else str(round(value))
    except (TypeError, ValueError):
        return "\u2014"


def _metric_rows(rollup):
    """
    Flatten a daily rollup into one row per METRIC_CONFIG entry, each with
    planned/actual/delta already picked out. A day with no plan (plan is
    null) yields planned=None/delta=None across every row -- the panel
    renderer treats that as "no plan submitted" rather than drawing empty
    bars.
    """
    plan = rollup.get("plan")
    actual = rollup.get("actual") or {}
    delta = rollup.get("plan_vs_actual") or {}

    rows = []
    for cfg in METRIC_CONFIG:
        rows.append({
            "label": cfg["label"],
            "planned": plan.get(cfg["planned_field"]) if plan else None,
            "actual": actual.get(cfg["actual_field"]),
            "delta": delta.get(cfg["delta_field"]) if plan else None,
            "decimals": cfg["decimals"],
        })
    return rows


def _panel_svg(px, py, row):
    """Render one metric panel (background + bars-or-fallback-text) as SVG markup."""
    parts = [
        '<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{fill}"/>'.format(
            x=px, y=py, w=PANEL_W, h=PANEL_H, fill=COLORS["panel"]
        ),
        '<text x="{x}" y="{y}" font-family="{f}" font-size="11" letter-spacing="0.08em" '
        'fill="{c}">{label}</text>'.format(
            x=px + 14, y=py + 20, f=MONO, c=COLORS["text_dim"], label=_esc(row["label"])
        ),
    ]

    chart_top = py + 34
    baseline = chart_top + CHART_H
    has_both = row["planned"] is not None and row["actual"] is not None

    if not has_both:
        parts.append(
            '<text x="{x}" y="{y}" font-family="{f}" font-size="12" fill="{c}" '
            'text-anchor="middle">no plan submitted</text>'.format(
                x=px + PANEL_W / 2, y=chart_top + CHART_H / 2, f=MONO, c=COLORS["text_dim"]
            )
        )
        return "\n".join(parts)

    max_val = max(row["planned"], row["actual"], 0.0001)
    pair_w = BAR_W * 2 + BAR_GAP
    bar1_x = px + 14 + ((PANEL_W - 28) - pair_w) / 2.0
    bar2_x = bar1_x + BAR_W + BAR_GAP

    def bar_h(v):
        return max(2, (v / max_val) * CHART_H * 0.88)

    h1, h2 = bar_h(row["planned"]), bar_h(row["actual"])

    for bx, h, val, color in (
        (bar1_x, h1, row["planned"], COLORS["amber"]),
        (bar2_x, h2, row["actual"], COLORS["green"]),
    ):
        parts.append(
            '<text x="{x}" y="{y}" font-family="{f}" font-size="11" fill="{c}" '
            'text-anchor="middle">{val}</text>'.format(
                x=bx + BAR_W / 2, y=baseline - h - 6, f=MONO, c=COLORS["text"],
                val=_esc(_fmt(val, row["decimals"])),
            )
        )
        parts.append(
            '<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="4" fill="{fill}"/>'.format(
                x=bx, y=baseline - h, w=BAR_W, h=h, fill=color
            )
        )

    axis_y = baseline + 14
    for bx, label in ((bar1_x, "PLANNED"), (bar2_x, "ACTUAL")):
        parts.append(
            '<text x="{x}" y="{y}" font-family="{f}" font-size="9" fill="{c}" '
            'text-anchor="middle">{label}</text>'.format(
                x=bx + BAR_W / 2, y=axis_y, f=MONO, c=COLORS["text_dim"], label=label
            )
        )

    dash_y = py + 178
    parts.append(
        '<line x1="{x1}" y1="{y}" x2="{x2}" y2="{y}" stroke="{c}" stroke-dasharray="3,3"/>'.format(
            x1=px + 14, x2=px + PANEL_W - 14, y=dash_y, c=COLORS["line"]
        )
    )

    footer_y = py + 194
    parts.append(
        '<text x="{x}" y="{y}" font-family="{f}" font-size="11" fill="{c}">'
        '{planned} plan / {actual} actual</text>'.format(
            x=px + 14, y=footer_y, f=MONO, c=COLORS["text_dim"],
            planned=_esc(_fmt(row["planned"], row["decimals"])),
            actual=_esc(_fmt(row["actual"], row["decimals"])),
        )
    )

    delta = row["delta"]
    delta_positive = (delta or 0) >= 0
    delta_color = COLORS["text_dim"] if delta is None else (COLORS["green"] if delta_positive else COLORS["red"])
    delta_text = "\u2014" if delta is None else "{}{}".format(
        "+" if delta_positive else "", _fmt(delta, row["decimals"])
    )
    parts.append(
        '<text x="{x}" y="{y}" font-family="{f}" font-size="11" font-weight="700" '
        'fill="{c}" text-anchor="end">{val}</text>'.format(
            x=px + PANEL_W - 14, y=footer_y, f=MONO, c=delta_color, val=_esc(delta_text)
        )
    )

    return "\n".join(parts)


def build_svg(rollup):
    """Render one daily rollup dict (as produced by aggregate.py) to a full SVG document string."""
    rows = _metric_rows(rollup)
    n = len(rows)
    total_w = MARGIN * 2 + PANEL_W * n + GAP * (n - 1)
    total_h = MARGIN * 2 + HEADER_H + PANEL_H

    plan = rollup.get("plan")
    route = (plan or {}).get("route_id") or "----"
    header_right = "{} \u00b7 {}".format(
        rollup.get("driver_id") or "----", rollup.get("date") or "----------"
    )

    svg = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">'.format(
            w=total_w, h=total_h
        ),
        '<rect width="{w}" height="{h}" fill="{c}"/>'.format(w=total_w, h=total_h, c=COLORS["bg"]),
        '<text x="{x}" y="{y}" font-family="-apple-system,sans-serif" font-size="15" '
        'font-weight="700" letter-spacing="0.02em" fill="{c}">PLAN VS ACTUAL</text>'.format(
            x=MARGIN, y=MARGIN + 16, c=COLORS["text"]
        ),
        '<text x="{x}" y="{y}" font-family="{f}" font-size="12" fill="{c}" '
        'text-anchor="end">{val}</text>'.format(
            x=total_w - MARGIN, y=MARGIN + 15, f=MONO, c=COLORS["text_dim"], val=_esc(header_right)
        ),
        '<text x="{x}" y="{y}" font-family="{f}" font-size="11" fill="{c}">ROUTE {val}</text>'.format(
            x=MARGIN, y=MARGIN + 34, f=MONO, c=COLORS["text_dim"], val=_esc(route)
        ),
    ]

    py = MARGIN + HEADER_H
    for i, row in enumerate(rows):
        px = MARGIN + i * (PANEL_W + GAP)
        svg.append(_panel_svg(px, py, row))

    svg.append("</svg>")
    return "\n".join(svg)


# ---------- orchestration ----------

def _load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def find_rollup_files(rollups_root):
    """
    Walk rollups_root for nested yyyy/mm/dd folders and return
    (date, driver_id, full_path) tuples -- same traversal shape as
    aggregate.py's find_driver_day_files(), just rooted at rollups/
    instead of the repo root.

    Thin wrapper over inbox_common.walk_driver_day_tree(), which both this
    and aggregate.py's find_driver_day_files() now call, so the two can't
    drift apart on what counts as a valid date folder or how driver_id
    gets parsed out of a filename.
    """
    return walk_driver_day_tree(rollups_root)


def run(repo_root=REPO_ROOT, rollups_dir=None, charts_dir=None):
    rollups_dir = rollups_dir or os.path.join(repo_root, ROLLUPS_DIR)
    charts_dir = charts_dir or os.path.join(repo_root, CHARTS_DIR)

    rollup_files = find_rollup_files(rollups_dir)
    print("Found {} daily rollup(s).".format(len(rollup_files)))

    written = 0
    for date, driver_id, path in rollup_files:
        rollup = _load_json(path)
        if rollup is None:
            print("  skipping unreadable rollup: {}".format(path))
            continue

        svg = build_svg(rollup)

        yyyy, mm, dd = split_date(date)
        out_dir = os.path.join(charts_dir, yyyy, mm, dd)
        os.makedirs(out_dir, exist_ok=True)
        out_filename = data_filename_for_driver(driver_id).replace(".json", ".svg")
        out_path = os.path.join(out_dir, out_filename)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(svg)
        written += 1

    print("Wrote {} chart(s) to '{}'.".format(written, charts_dir))


def _cli():
    repo_root = sys.argv[1] if len(sys.argv) > 1 else REPO_ROOT
    run(repo_root=repo_root)


if __name__ == "__main__":
    _cli()
