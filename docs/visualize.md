# `visualize.py`

Stage 3 of the pipeline. Reads the daily rollups produced by
`aggregate.py` and renders one **plan-vs-actual snapshot chart** per
driver-day, as hand-rolled SVG.

## Entry point

`run(repo_root=".", rollups_dir=None, charts_dir=None)`. Run standalone
via `python visualize.py [repo_root]`, or as stage 3 of
`run_pipeline.py`. Always run **after** `aggregate.py` — this script
only reads `rollups/`, never the organized `yyyy/mm/dd/` data tree or
`inbox/` directly.

Output: `charts/yyyy/mm/dd/driver-XXXXXXX.svg` — one file per rollup
found, mirroring the same date nesting used everywhere else. Every run
is a full rebuild of `charts/` (the plain-snapshot part; `trends.py`
owns `charts/trends/`) from whatever's currently in `rollups/` — no
incremental logic.

## Why SVG, and why hand-rolled

No `matplotlib`/`pillow` — stdlib only, consistent with the project's
zero-dependency principle. SVG is also just text, so it diffs cleanly
in git the same way the JSON rollups do; a PNG would be an opaque
binary blob on every regeneration even if nothing visually changed.

## What gets rendered

One chart = one header (route, driver ID, date) + three side-by-side
metric panels, driven by `METRIC_CONFIG`:

| panel | planned field | actual field | delta field |
|---|---|---|---|
| STOPS | `planned_stops` | `total_stops` | `stops_delta` |
| MILES | `planned_miles` | `total_miles` | `miles_delta` |
| PIECES | `planned_pieces` | `total_pieces` | `pieces_delta` |

`METRIC_CONFIG` is the single place these three panels are defined —
adding a fourth metric panel in the future means adding one tuple entry
here, not touching `_metric_rows()`, `_panel_svg()`, and `build_svg()`
separately.

Each panel (`_panel_svg`) draws two bars (planned in amber, actual in
green/red-adjacent per the shared `COLORS` palette), their numeric
labels, a "planned / actual" footer line, and a delta value colored
green (≥0) or red (<0). **If a day has no plan** (`plan` was `null` in
the source rollup), the panel instead renders "no plan submitted" text
in place of bars — `_metric_rows()` produces `planned=None` /
`delta=None` for every row in that case, and `_panel_svg` treats
"either value is `None`" as the no-plan case.

## Design tokens

`COLORS` and `MONO` (the monospace font stack) are deliberately kept
in sync with `index.html`'s CSS variables and the plan-vs-actual React
prototype the project started from — the entry tool, this chart, and
the dashboard all read as the same visual product rather than three
things bolted together. `trends.py` imports `COLORS` and `MONO`
directly from this file for the same reason, rather than redefining
its own palette.

## Scope note

This is the **per-day snapshot only** (stops/miles/pieces, planned vs
actual, one day). Trend-over-time charts are `trends.py`'s job — see
that file's doc for the sparkline/variance-band rendering, which is a
different script by design (different data source: `trends.py` needs
both `rollups/` *and* `overall/`, this one only needs `rollups/`).

## Reused by `trends.py`

`find_rollup_files(rollups_root)` (the tree-walk that returns
`(date, driver_id, path)` tuples for every file under `rollups/`) is
imported directly by `trends.py` rather than being reimplemented there
— same traversal shape as `aggregate.py`'s `find_driver_day_files()`,
just rooted at `rollups/` instead of the repo root.
