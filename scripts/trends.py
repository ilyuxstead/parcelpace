"""
trends.py

Renders one sparkline-panel SVG per driver+route, showing each pace
metric's real day-by-day series -- drawn straight from rollups/ -- with
the recent/prior boundary and variance bands pulled from the already-
computed overall/driver-XXXXXXX.json stddevs. This script never
recomputes trend math itself; it's a pure rendering layer over
aggregate.py's numbers, so the chart and the JSON can never silently
disagree with each other.

Output mirrors the per-driver/per-route grouping:

    charts/trends/driver-XXXXXXX/ROUTE.svg

Run manually or via cron, same as visualize.py, and always AFTER
aggregate.py -- this script reads both rollups/ (for the real per-day
series) and overall/ (for the recent/prior avg + stddev), and needs both
to exist. Like aggregate.py and visualize.py, every run is a full rebuild
of charts/trends/ from current data -- no incremental/merge logic.

Scope note: only the four TREND_METRICS pace metrics get a panel (one
each, stacked top to bottom). Plan-vs-actual daily snapshots are
visualize.py's job; this script is trend-over-time only. Days with no
plan (no route_id) are excluded from every route's series -- same
exclusion aggregate.py's _build_trend_by_route already applies, since
there's nothing to group them by.
"""

import json
import os
import sys
from xml.sax.saxutils import escape as _esc

from aggregate import TREND_METRICS, TREND_WINDOW_DAYS
from inbox_common import data_filename_for_driver
from visualize import COLORS, MONO, find_rollup_files

REPO_ROOT = "."
ROLLUPS_DIR = "rollups"
OVERALL_DIR = "overall"
CHARTS_DIR = "charts/trends"

# Display labels for the imported TREND_METRICS tuple -- imported rather
# than re-listed so a future metric added to aggregate.py's TREND_METRICS
# automatically gets a panel here too; only its label needs adding below.
METRIC_LABELS = {
    "stops_per_mile": "STOPS / MILE",
    "pieces_per_mile": "PIECES / MILE",
    "stops_per_active_hour": "STOPS / ACTIVE HR",
    "pieces_per_active_hour": "PIECES / ACTIVE HR",
}

PRIOR_COLOR = COLORS["amber"]
RECENT_COLOR = COLORS["green"]

# ---- layout constants ----
MARGIN = 20
HEADER_H = 50
PANEL_W = 640
PANEL_H = 130
PANEL_GAP = 22
CHART_PAD_X = 40
CHART_PAD_TOP = 24
CHART_PAD_BOTTOM = 20
POINT_R = 3


def _fmt(value, decimals=2):
    if value is None:
        return "\u2014"
    try:
        return "{:.{d}f}".format(value, d=decimals)
    except (TypeError, ValueError):
        return "\u2014"


def _load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


_overall_cache = {}


def _load_overall(overall_dir, driver_id):
    if driver_id in _overall_cache:
        return _overall_cache[driver_id]
    path = os.path.join(overall_dir, data_filename_for_driver(driver_id))
    data = _load_json(path)
    _overall_cache[driver_id] = data
    return data


# ---------- gathering per-driver/per-route series ----------

def gather_series(rollups_dir):
    """
    Walk rollups_dir and group daily rollups by (driver_id, route_id),
    sorted by date -- the same grouping aggregate.py's
    _build_trend_by_route applies, just kept as the real per-day series
    here instead of being collapsed into recent/prior averages.

    Returns { driver_id: { route_id: [rollup, rollup, ...] } }.
    """
    by_driver_route = {}
    for date, driver_id, path in find_rollup_files(rollups_dir):
        rollup = _load_json(path)
        if rollup is None:
            print("  skipping unreadable rollup: {}".format(path))
            continue
        plan = rollup.get("plan")
        route_id = plan.get("route_id") if plan else None
        if not route_id:
            continue
        by_driver_route.setdefault(driver_id, {}).setdefault(route_id, []).append(rollup)

    for driver_id, routes in by_driver_route.items():
        for route_id, rollups in routes.items():
            routes[route_id] = sorted(rollups, key=lambda r: r["date"])

    return by_driver_route


# ---------- SVG rendering ----------

def _panel_svg(px, py, metric, series, route_trend, window_days):
    """
    Render one metric's sparkline panel: shaded prior/recent variance
    bands (from route_trend, if available), a boundary marker, and the
    real day-by-day line with gaps left open wherever the metric was
    None that day.
    """
    label = METRIC_LABELS.get(metric, metric.upper())
    parts = [
        '<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{fill}"/>'.format(
            x=px, y=py, w=PANEL_W, h=PANEL_H, fill=COLORS["panel"]
        ),
        '<text x="{x}" y="{y}" font-family="{f}" font-size="11" letter-spacing="0.08em" '
        'fill="{c}">{label}</text>'.format(
            x=px + 14, y=py + 20, f=MONO, c=COLORS["text_dim"], label=_esc(label)
        ),
    ]

    n = len(series)
    chart_left = px + CHART_PAD_X
    chart_right = px + PANEL_W - CHART_PAD_X
    chart_top = py + CHART_PAD_TOP
    chart_bottom = py + PANEL_H - CHART_PAD_BOTTOM

    values = [r["pace"].get(metric) for r in series]
    non_null = [v for v in values if v is not None]

    if not non_null:
        parts.append(
            '<text x="{x}" y="{y}" font-family="{f}" font-size="12" fill="{c}" '
            'text-anchor="middle">no data for this route yet</text>'.format(
                x=(chart_left + chart_right) / 2.0, y=(chart_top + chart_bottom) / 2.0,
                f=MONO, c=COLORS["text_dim"],
            )
        )
        return "\n".join(parts)

    # Boundary between prior/recent segments, count-based (matching
    # aggregate.py's route_rollups[-TREND_WINDOW_DAYS:] slicing) rather
    # than a calendar-date cutoff.
    boundary_idx = max(0, n - window_days)

    # y-range: include the real values plus whatever the recent/prior
    # avg+-stddev bands span, so a band never gets clipped by the axes.
    y_candidates = list(non_null)
    if route_trend:
        for seg in ("recent", "prior"):
            avg = route_trend.get("{}_{}_avg".format(metric, seg))
            std = route_trend.get("{}_{}_stddev".format(metric, seg))
            if avg is not None:
                y_candidates.append(avg)
                if std is not None:
                    y_candidates.append(avg + std)
                    y_candidates.append(avg - std)

    y_min, y_max = min(y_candidates), max(y_candidates)
    if y_min == y_max:
        y_min -= 1
        y_max += 1
    pad = (y_max - y_min) * 0.1
    y_min -= pad
    y_max += pad

    def x_at(i):
        if n == 1:
            return (chart_left + chart_right) / 2.0
        return chart_left + (chart_right - chart_left) * (i / float(n - 1))

    def y_at(v):
        return chart_bottom - (v - y_min) / (y_max - y_min) * (chart_bottom - chart_top)

    # ---- variance bands (drawn first, behind everything else) ----
    def draw_band(seg_name, color, x0, x1):
        if not route_trend or x1 <= x0:
            return
        avg = route_trend.get("{}_{}_avg".format(metric, seg_name))
        std = route_trend.get("{}_{}_stddev".format(metric, seg_name))
        if avg is None or std is None:
            return
        top = y_at(avg + std)
        bottom = y_at(avg - std)
        parts.append(
            '<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{c}" fill-opacity="0.15"/>'.format(
                x=x0, y=top, w=(x1 - x0), h=(bottom - top), c=color
            )
        )
        parts.append(
            '<line x1="{x0}" y1="{y}" x2="{x1}" y2="{y}" stroke="{c}" stroke-dasharray="2,3"/>'.format(
                x0=x0, x1=x1, y=y_at(avg), c=color
            )
        )

    if boundary_idx > 0:
        draw_band("prior", PRIOR_COLOR, chart_left, x_at(boundary_idx - 1) if boundary_idx <= n - 1 else x_at(boundary_idx))
    if boundary_idx < n:
        draw_band("recent", RECENT_COLOR, x_at(boundary_idx), chart_right)

    # boundary marker, only meaningful when there's history on both sides
    if 0 < boundary_idx < n:
        bx = (x_at(boundary_idx - 1) + x_at(boundary_idx)) / 2.0
        parts.append(
            '<line x1="{x}" y1="{y0}" x2="{x}" y2="{y1}" stroke="{c}" stroke-dasharray="3,3"/>'.format(
                x=bx, y0=chart_top, y1=chart_bottom, c=COLORS["line"]
            )
        )

    # ---- the real line, broken at None gaps, colored by segment ----
    i = 0
    while i < n:
        if values[i] is None:
            i += 1
            continue
        run_start = i
        while i + 1 < n and values[i + 1] is not None:
            i += 1
        run_end = i  # inclusive

        if run_end > run_start:
            pts = " ".join(
                "{:.1f},{:.1f}".format(x_at(j), y_at(values[j]))
                for j in range(run_start, run_end + 1)
            )
            color = RECENT_COLOR if run_end >= boundary_idx else PRIOR_COLOR
            parts.append(
                '<polyline points="{pts}" fill="none" stroke="{c}" stroke-width="2"/>'.format(
                    pts=pts, c=color
                )
            )
        for j in range(run_start, run_end + 1):
            color = RECENT_COLOR if j >= boundary_idx else PRIOR_COLOR
            parts.append(
                '<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{c}"/>'.format(
                    x=x_at(j), y=y_at(values[j]), r=POINT_R, c=color
                )
            )
        i += 1

    # end-value labels for readability
    first_i = next((j for j, v in enumerate(values) if v is not None), None)
    last_i = next((j for j in range(n - 1, -1, -1) if values[j] is not None), None)
    for j, anchor in ((first_i, "start"), (last_i, "end")):
        if j is None:
            continue
        parts.append(
            '<text x="{x:.1f}" y="{y:.1f}" font-family="{f}" font-size="10" fill="{c}" '
            'text-anchor="{a}">{val}</text>'.format(
                x=x_at(j) + (4 if anchor == "start" else -4),
                y=y_at(values[j]) - 8,
                f=MONO, c=COLORS["text"], a=anchor, val=_esc(_fmt(values[j])),
            )
        )

    return "\n".join(parts)


def build_svg(driver_id, route_id, series, route_trend, window_days):
    n_panels = len(TREND_METRICS)
    total_w = MARGIN * 2 + PANEL_W
    total_h = MARGIN * 2 + HEADER_H + PANEL_H * n_panels + PANEL_GAP * (n_panels - 1)

    dates = [r["date"] for r in series]
    date_range = "{} \u2192 {}".format(dates[0], dates[-1]) if dates else "----"

    svg = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">'.format(
            w=total_w, h=total_h
        ),
        '<rect width="{w}" height="{h}" fill="{c}"/>'.format(w=total_w, h=total_h, c=COLORS["bg"]),
        '<text x="{x}" y="{y}" font-family="-apple-system,sans-serif" font-size="15" '
        'font-weight="700" letter-spacing="0.02em" fill="{c}">PACE TREND</text>'.format(
            x=MARGIN, y=MARGIN + 16, c=COLORS["text"]
        ),
        '<text x="{x}" y="{y}" font-family="{f}" font-size="12" fill="{c}" '
        'text-anchor="end">{val}</text>'.format(
            x=total_w - MARGIN, y=MARGIN + 15, f=MONO, c=COLORS["text_dim"],
            val=_esc("{} \u00b7 route {}".format(driver_id, route_id)),
        ),
        '<text x="{x}" y="{y}" font-family="{f}" font-size="11" fill="{c}">{val}</text>'.format(
            x=MARGIN, y=MARGIN + 34, f=MONO, c=COLORS["text_dim"],
            val=_esc("{} days tracked \u00b7 {}".format(len(series), date_range)),
        ),
    ]

    py = MARGIN + HEADER_H
    for metric in TREND_METRICS:
        svg.append(_panel_svg(MARGIN, py, metric, series, route_trend, window_days))
        py += PANEL_H + PANEL_GAP

    svg.append("</svg>")
    return "\n".join(svg)


# ---------- orchestration ----------

def run(repo_root=REPO_ROOT, rollups_dir=None, overall_dir=None, charts_dir=None):
    rollups_dir = rollups_dir or os.path.join(repo_root, ROLLUPS_DIR)
    overall_dir = overall_dir or os.path.join(repo_root, OVERALL_DIR)
    charts_dir = charts_dir or os.path.join(repo_root, CHARTS_DIR)

    _overall_cache.clear()

    by_driver_route = gather_series(rollups_dir)
    n_routes = sum(len(routes) for routes in by_driver_route.values())
    print("Found {} driver-route series across {} driver(s).".format(
        n_routes, len(by_driver_route)
    ))

    written = 0
    for driver_id, routes in by_driver_route.items():
        overall = _load_overall(overall_dir, driver_id)
        window_days = (overall or {}).get("trend", {}).get("window_days", TREND_WINDOW_DAYS)
        by_route_trend = (overall or {}).get("trend", {}).get("by_route", {})

        if overall is None:
            print("  no overall/ data for driver {} yet -- rendering without bands".format(driver_id))

        out_driver_dir = os.path.join(charts_dir, "driver-{}".format(driver_id))
        os.makedirs(out_driver_dir, exist_ok=True)

        for route_id, series in routes.items():
            route_trend = by_route_trend.get(route_id)
            svg = build_svg(driver_id, route_id, series, route_trend, window_days)
            out_path = os.path.join(out_driver_dir, "{}.svg".format(route_id))
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(svg)
            written += 1

    print("Wrote {} trend chart(s) to '{}'.".format(written, charts_dir))


def _cli():
    repo_root = sys.argv[1] if len(sys.argv) > 1 else REPO_ROOT
    run(repo_root=repo_root)


if __name__ == "__main__":
    _cli()
