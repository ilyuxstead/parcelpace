# `trends.py`

Stage 4 of the pipeline. Renders one **pace-trend sparkline chart** per
driver+route, showing each of the four pace metrics' real day-by-day
values over time, with a recent/prior boundary marker and variance
bands.

## Entry point

`run(repo_root=".", rollups_dir=None, overall_dir=None, charts_dir=None)`.
Run standalone via `python trends.py [repo_root]`, or as stage 4 of
`run_pipeline.py`. Always run **after** `aggregate.py` — it needs both
`rollups/` (for the real per-day series) and `overall/` (for the
already-computed recent/prior averages and stddevs).

Output: `charts/trends/driver-XXXXXXX/ROUTE.svg` — one file per
driver+route combination found in `rollups/`. Full rebuild every run,
same as `visualize.py` and `aggregate.py`.

## Pure rendering layer — no math of its own

This is the important design property of this file: it **never
recomputes trend math**. Every average, stddev, and recent/prior split
comes straight from `overall/driver-XXXXXXX.json`'s
`trend.by_route[route_id]` block, which `aggregate.py` already built.
`trends.py` only:

1. re-derives the *real* per-day series from `rollups/` (aggregate.py
   only kept the collapsed recent/prior averages, not the individual
   days, in `overall/`), and
2. draws it.

The point of this separation: the chart and the JSON it's drawn from
can never silently disagree with each other, because there's only one
place (`aggregate.py`) where the trend math is actually computed.

## Data gathering

`gather_series(rollups_dir)` walks every rollup (via
`visualize.py`'s `find_rollup_files()`), groups them into
`{driver_id: {route_id: [rollup, ...]}}`, and sorts each route's list
by date. Days with no plan (no `route_id`) are excluded — same
exclusion `aggregate.py`'s `_build_trend_by_route` already applies,
since there's nothing to group a routeless day under.

## What each panel shows

One SVG = one header (driver, route, days tracked, date range) + four
stacked panels, one per `TREND_METRICS` entry imported directly from
`aggregate.py` (`stops_per_mile`, `pieces_per_mile`,
`stops_per_active_hour`, `pieces_per_active_hour`) — importing rather
than re-listing means a future metric added to `aggregate.py`'s
`TREND_METRICS` automatically gets a panel here too; only its display
label needs adding to `METRIC_LABELS`.

Per panel (`_panel_svg`):
- **Variance bands** — shaded rectangles for the prior segment (amber)
  and recent segment (green), each spanning `avg ± stddev` pulled from
  `overall/`'s `by_route` block. Skipped if that segment doesn't have
  both an avg and a stddev available (e.g. too few days for a stddev).
- **Boundary marker** — a dashed vertical line where the recent window
  (last `window_days`, matching `aggregate.py`'s
  `TREND_WINDOW_DAYS = 7` by default) begins, only drawn if there's
  history on both sides of it.
- **The real line** — drawn from the actual day-by-day series, broken
  (gapped) wherever that day's value is `None` rather than
  interpolated across the gap. Points before the boundary render amber,
  points at/after it render green.
- **Start/end value labels** — the first and last non-`None` values in
  the series get their numeric value printed next to the point.
- If a route has **no** non-`None` values at all yet, the panel just
  prints "no data for this route yet" instead of an empty chart.

## Orchestration notes

- `_overall_cache` memoizes each driver's `overall/` file for the
  duration of one `run()` call (cleared at the start of every run) —
  a driver with several routes only triggers one `overall/` read, not
  one per route.
- If a driver has no `overall/` file yet at all (e.g. `aggregate.py`
  hasn't been run, or this is a brand new driver with a rollup but no
  overall computed), charts still render — just without variance bands
  or a boundary marker, since there's nothing to draw them from. A
  message is printed noting this per-driver.
