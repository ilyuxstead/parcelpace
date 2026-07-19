# Dropstats

A lightweight delivery-driver telematics tracker that uses Codeberg git repositories as its entire data store. No database, no backend server — just JSON files, a static web form, a handful of Python scripts, and a static dashboard.

> **Note:** This project moved from GitHub to Codeberg, under the `dropstats` organization. GitHub and GitHub Actions have been retired entirely. All workflows below reflect the Codeberg-native setup.

---

## How it works

1. A driver opens the **entry tool** (`index.html`, a single-page HTML app hosted on **Codeberg Pages**) on their phone.
2. They log a **start-of-day plan** and then an **hourly check-in** throughout their shift. Cumulative-to-delta conversion happens client-side (`recalcDeltas()` against a `lastreading` baseline in `localStorage`) — only per-hour deltas are ever written to JSON.
3. Each save is queued locally in the browser; at the end of a batch, the driver exports a combined `driver-XXXXXXX.json` payload and uploads it into the `inbox/` folder of the `dropstats` repo (via the Codeberg web UI or a git client on the phone). The uploaded filename itself is disposable — some mobile browsers substitute their own generated name (e.g. a UUID) on save regardless of what was requested, and `route_inbox.py` doesn't require any particular filename to process a submission.
4. **`route_inbox.py`** validates and files the submission into the date-organized data tree.
5. **`aggregate.py`** rolls daily and all-time stats per driver, from scratch, every run.
6. **`visualize.py`** and **`trends.py`** render SVG charts from those rollups.
7. **`run_pipeline.py`** chains all four of the above into a single command.
8. A separate static **dashboard** (`dashboard.html`, in the `pages` repo) lets anyone with a driver ID pull up that driver's charts, fetched cross-repo from Codeberg's raw content endpoint.

No driver ever hand-edits JSON. The web tool is the only thing that produces it.

---

## Repositories

The project is split across two Codeberg repos under the `dropstats` org:

| Repo | Purpose |
|---|---|
| `codeberg.org/dropstats/dropstats` | Data store + pipeline scripts: `inbox/`, the nested `yyyy/mm/dd/` data tree, `rollups/`, `overall/`, `charts/`, and all Python scripts. Also hosts `index.html` via Codeberg Pages. |
| `codeberg.org/Dropstats/pages` | The static `dashboard.html`, which fetches data and chart SVGs from the `dropstats` repo's raw content endpoint (via a `DATA_REPO_RAW_BASE` constant) rather than sharing a repo with the data itself. |

Keeping the dashboard in its own repo means the data repo's history stays purely about driver data and pipeline code, and the dashboard can be redeployed independently of it.

---

## Repository structure (`dropstats` repo)

```
dropstats/
├── index.html                      # the phone-native entry tool (Codeberg Pages)
├── inbox/                          # incoming submissions land here, pre-validation
│   ├── driver-XXXXXXX.json                 # one pending payload per driver
│   └── driver-XXXXXXX.log.json             # accumulating validation log per driver
├── 2026/                           # nested date path: yyyy/mm/dd, created once data lands
│   └── 06/
│       └── 26/
│           └── driver-XXXXXXX.json         # validated, organized driver-day record
├── rollups/
│   └── 2026/
│       └── 06/
│           └── 26/
│               └── driver-XXXXXXX.json     # per-driver, per-day computed stats
├── overall/
│   └── driver-XXXXXXX.json         # per-driver stats across all days
├── charts/
│   ├── 2026/
│   │   └── 06/
│   │       └── 26/
│   │           └── driver-XXXXXXX.svg      # per-day plan-vs-actual snapshot chart
│   └── trends/
│       └── driver-XXXXXXX/
│           └── ROUTE.svg                   # per-driver, per-route pace sparkline chart
└── scripts/
    ├── inbox_common.py      # shared filename/folder helpers
    ├── validate.py           # schema + sanity checks for one payload
    ├── route_inbox.py        # validates inbox/ and files data by date
    ├── aggregate.py           # rebuilds rollups/ and overall/ from scratch
    ├── visualize.py           # renders per-day plan-vs-actual SVG snapshots
    ├── trends.py              # renders per-driver/per-route pace sparkline SVGs
    └── run_pipeline.py        # orchestrates all of the above in sequence
```

The `pages` repo is much smaller — just `dashboard.html` plus whatever static assets it needs.

**Date lives in the JSON, not the filename.** Neither the date nor the driver ID has to be recoverable from a driver's raw upload filename — both are read out of the payload body during routing, then used to place the file in the right `yyyy/mm/dd/driver-XXXXXXX.json` location (the destination filename is always rebuilt from `driver_id`, regardless of what the file was called in `inbox/`). `route_inbox.py` accepts any non-log `*.json` file in `inbox/`, since some mobile browsers substitute their own generated filename on save.

**Data folders are nested by year/month/day, not flat.** A flat `YYYY-MM-DD/` folder per day would eventually leave hundreds of folders sitting at the repo root. Nesting keeps the root tidy — at most ~12 month folders per year, ~31 day folders per month — while `rollups/` and `charts/` mirror the same nesting for consistency. `overall/` isn't date-keyed at all, so it stays flat, and `charts/trends/` is keyed by driver+route instead of by date.

---

## Data flow, end to end

```
   ┌──────────────┐      ┌─────────────┐      ┌───────────────────────┐      ┌─────────────┐      ┌───────────────────┐
   │  index.html   │ ───▶ │  inbox/     │ ───▶ │  yyyy/mm/dd/driver-…  │ ───▶ │  rollups/   │ ───▶ │  charts/          │
   │ (Codeberg     │ JSON │  (pending)  │ move │  (organized)          │ agg  │  overall/   │ svg  │  charts/trends/   │
   │  Pages)       │      │             │      │                       │      │             │      │                   │
   └──────────────┘      └─────────────┘      └───────────────────────┘      └─────────────┘      └───────────────────┘
                          validate.py +                                                             visualize.py +
                          route_inbox.py                                                            trends.py
                                                                                                              │
                                                                                                              ▼
                                                                                                  dashboard.html (pages repo)
                                                                                                  fetches driver's rollup +
                                                                                                  chart SVGs via <img> tags
```

1. **Plan + hourly entries** are composed client-side in `index.html`. Cumulative device readings (stops, pieces, pieces picked up, miles) are converted to per-hour deltas *in the browser*, via `recalcDeltas()` against a `lastreading` baseline kept in `localStorage`. Only deltas are ever written to JSON — the raw cumulative totals never leave the phone.
2. **Export** bundles the queued plan + hourly entries into one consolidated `driver-XXXXXXX.json` and the driver uploads it into `inbox/`.
3. **`route_inbox.py`** scans `inbox/`, skips log files, and for each data file (any non-log `*.json`, regardless of its name):
   - Calls `validate.py` against the schema. A missing `plan` is only a warning, but if a plan *is* submitted, a missing or blank `route_id` is a hard error — every plan needs a route so same-route days can be grouped for trending.
   - Appends any errors/warnings to that driver's accumulating `driver-XXXXXXX.log.json` (silent if the submission is fully clean).
   - On success, splits the payload's `date` field into `yyyy/mm/dd` and moves the file into `yyyy/mm/dd/driver-XXXXXXX.json`, overwriting any prior file for that driver+date.
   - On a hard error, leaves the file in `inbox/` for a human to look at. Other drivers' files are still processed — one blocked file never stalls the rest of the batch.
4. **`aggregate.py`** walks every nested `yyyy/mm/dd/` folder and rebuilds, from scratch each run:
   - `rollups/yyyy/mm/dd/driver-XXXXXXX.json` — one day's totals, plan-vs-actual deltas, and pace metrics for a driver. Pace metrics are `stops_per_mile`, `pieces_per_mile`, `stops_per_active_hour`, and `pieces_per_active_hour`. The active-hour metrics are normalized against **elapsed wall-clock time** between consecutive `entry_time` timestamps (not a raw count of submitted hour buckets), so a driver logging in late doesn't silently inflate one hour's apparent activity. Irregular gaps are surfaced in `data_quality.irregular_hour_gaps` for a human to glance at.
   - `overall/driver-XXXXXXX.json` — totals, averages, consistency (stddev), and trend across all days tracked. Trend is computed **per route** (`trend.by_route["17F"]`, etc.) — recent vs. prior pace metrics, windowed by the last 7 days *that route was driven*, not 7 calendar days. Days with no plan (no route) are excluded from every route's series.
5. **`visualize.py`** reads `rollups/` and renders a hand-rolled, dependency-free SVG snapshot per driver-day — planned vs. actual stops/miles/pieces — to `charts/yyyy/mm/dd/driver-XXXXXXX.svg`. It never touches `inbox/` or the organized data tree directly, and always runs after `aggregate.py`.
6. **`trends.py`** reads both `rollups/` (for the real day-by-day series) and `overall/` (for the recent/prior average + stddev already computed by `aggregate.py`) and renders one sparkline panel per pace metric, per driver+route, to `charts/trends/driver-XXXXXXX/ROUTE.svg`. It's a pure rendering layer — it never recomputes trend math, so the chart and the JSON can't drift apart.
7. **`run_pipeline.py`** chains steps 3–6 in order (`route_inbox` → `aggregate` → `visualize` → `trends`). It exits with code `1` if any files were blocked, but still runs every downstream stage for the drivers that filed cleanly — a blocked driver never holds up everyone else's rollups and charts.
8. **`dashboard.html`** (in the `pages` repo) accepts a driver ID and fetches that driver's data from the `dropstats` repo via a `DATA_REPO_RAW_BASE` constant pointing at Codeberg's raw endpoint. Direct `fetch()` calls to that endpoint are CORS-blocked, so chart SVGs are pulled in via `<img>` tags instead, which aren't subject to the same restriction — this cross-origin quirk is what drove the "render SVGs server-side, display them client-side via `<img>`" split between the pipeline and the dashboard. Displaying a driver's chart history also currently requires the `overall/driver-XXXXXXX.json` rollup to include a `dates` array (a small addition to `build_overall_rollup()` in `aggregate.py`).

---

## Schema

A consolidated driver-day payload:

```json
{
  "driver_id": "2266642",
  "date": "2026-06-26",
  "plan": {
    "route_id": "17F",
    "planned_stops": 0,
    "planned_miles": 0,
    "planned_pieces": 0,
    "predicted_finish": "HH:MM | null",
    "entry_time": "ISO 8601"
  },
  "hours": {
    "13": {
      "hourly_stops": 0,
      "hourly_miles": 0,
      "hourly_pieces": 0,
      "hourly_pieces_picked_up": 0,
      "notes": "",
      "break_flag": false,
      "confirmed_zero": false,
      "entry_time": "ISO 8601"
    }
  }
}
```

`confirmed_zero` is optional and only meaningful when every hourly delta is 0 and `break_flag` is `false` -- the web tool prompts the driver to explicitly confirm a genuine zero-activity hour in that case, and sets this to `true` if they do. It's the difference between "confirmed zero" and "possibly forgotten/unedited" for an otherwise-identical all-zero hour (see `data_quality.unconfirmed_zero_hours` below). Older entries predating this field simply omit it, and are treated the same as an unconfirmed zero.

`plan` may be `null` if a driver hasn't submitted a day plan. Hour keys are zero-padded `"00"`–`"23"` strings; the `hour-HH.json` naming convention in the export process gives one slot per hour at the filesystem level, so collisions are structurally impossible rather than something validation has to catch.

The payload's `date` field stays `"YYYY-MM-DD"` — that format doesn't change. It's only the *on-disk folder* it gets routed into that's nested (`yyyy/mm/dd/` instead of a flat `YYYY-MM-DD/`).

---

## Running the scripts

These are plain Python, no dependencies beyond the standard library:

```bash
# Validate one file by hand
python scripts/validate.py inbox/driver-2266642.json

# Process everything currently sitting in inbox/
python scripts/route_inbox.py

# Rebuild all rollups from the organized date folders
python scripts/aggregate.py

# Render per-day plan-vs-actual snapshot charts from rollups/
python scripts/visualize.py

# Render per-driver/per-route pace trend charts
python scripts/trends.py

# Or run the whole pipeline (route_inbox -> aggregate -> visualize -> trends) in one go
python scripts/run_pipeline.py
```

### Automation

The previous GitHub Actions setup has been retired along with the GitHub repo. Automation is not yet wired up — `run_pipeline.py` (and its constituent scripts) are run manually or via a local cron job against a clone of the `dropstats` repo.

**Woodpecker CI** (`ci.codeberg.org`), which integrates natively with Codeberg, is the planned next step and is under active consideration but not yet implemented. The rough shape of that setup:

- Enable the `dropstats` repo on `ci.codeberg.org`.
- Add an SSH deploy key so CI can push the regenerated `rollups/`, `overall/`, and `charts/` back to the repo.
- A `.woodpecker.yml` that chains the pipeline stages with `depends_on`, mirroring `run_pipeline.py`'s ordering.
- Commits made by CI itself carry `[skip ci]` to avoid triggering an infinite rebuild loop.

This is a known gap on the roadmap, not a permanent design choice.

---

## Design principles

- **Zero external dependencies.** Every Python script uses only the standard library — no matplotlib, pillow, or similar. Charts are rendered as hand-rolled SVG rather than PNG, both to avoid a dependency and because SVG (being text) diffs cleanly in git the same way the JSON rollups do.
- **Collect raw stats first, score later.** No scoring weights are defined yet — there isn't enough real driver data to know what a fair weighting even looks like. The pipeline focuses entirely on capturing clean, well-shaped data. A future driver-scoring system (composite "effort" metrics, miles-normalized pace metrics) is deferred until real data distributions are available.
- **Fault-tolerant over blocking.** Odd values (e.g. a negative hourly delta) are flagged as warnings, logged, and let through rather than rejected outright. Hard errors (missing required fields, malformed dates) are the only thing that blocks a file from being filed, and only that one driver's file — everyone else's data still gets processed.
- **Cumulative-to-delta conversion happens in the app, never in storage.** The web tool is the only place that ever sees a raw odometer-style cumulative number; everything written to JSON is already a clean per-hour delta.
- **Elapsed-time denominator for active-hour pace metrics.** Raw hour-bucket counts are an inaccurate denominator for stops/pieces-per-active-hour; `aggregate.py` derives real elapsed minutes from consecutive `entry_time` timestamps instead, computed retroactively so it applies to old and new data alike with no backfill gap.
- **Rollups are always rebuilt, never patched.** `aggregate.py` (and `visualize.py`/`trends.py` downstream of it) has no incremental update logic — every run recomputes its outputs from scratch from the full set of organized date folders. Simple and safe at the current data volume.
- **Nest date folders to keep the repo root readable.** The organized data tree, `rollups/`, and `charts/` all use `yyyy/mm/dd/` nesting instead of one flat folder per day, so none of them accumulate hundreds of sibling date folders over time.
- **Pure rendering layers stay pure.** `visualize.py` and `trends.py` only ever read already-computed data from `rollups/` and `overall/` — they never recompute trend math or aggregate math themselves, so a chart can never silently disagree with the JSON it was drawn from.
- **Cross-origin constraints shape the dashboard's architecture.** Direct `fetch()` from the dashboard (in the `pages` repo) to the `dropstats` repo's raw content endpoint is CORS-blocked. SVGs delivered via `<img>` tags aren't subject to the same restriction, which is why chart rendering happens server-side (in the pipeline) rather than client-side in the dashboard.
- **Discuss design before implementation.** Cross-file ripple effects (a change in one script that implies a change in another) are surfaced and scoped before code is written, rather than patched in piecemeal.

---

## Known open questions

These are tracked as active design decisions, not bugs:

- Whether `planned_miles: 0` and `predicted_finish: null` represent legitimate states or should be tightened in `validate.py`.
- Whether `aggregate.py`'s handling of non-contiguous hour ranges (gaps in a shift) needs more nuance than the current `has_gaps` flag.
- What composite "effort" and miles-normalized pace metrics should eventually feed into driver scoring.
- `route_id` casing is only normalized client-side (the HTML tool uppercases on save). `validate.py`/`aggregate.py` don't normalize it, so a route entered inconsistently outside the web tool (e.g. `17f` vs `17F`) would silently split into two separate trend buckets rather than being caught.
- Chart access control: whether driver-facing and manager-only views need to be distinguished, and how — no static-site access control mechanism exists yet, so today anyone with a driver ID can view that driver's dashboard.

**Resolved:** two same-driver uploads for different dates used to be able to clobber each other in `inbox/` before `route_inbox.py` ran, because every upload was forced to the identical `driver-XXXXXXX.json` name. Now that `route_inbox.py` accepts any non-log `*.json` file (see `is_data_file()` in `inbox_common.py`), that collision no longer occurs as long as each upload has a distinct filename — which mobile-browser downloads already do in practice (they commonly assign their own generated filename, e.g. a UUID, rather than honoring the page's requested name).

**Resolved:** an all-zero, non-break hour used to be indistinguishable from a forgotten/unedited entry — both produced identical deltas, since untouched wheel fields naturally compute a zero delta and the miles field falls back to `0` when blank. `index.html` now prompts the driver to explicitly confirm a genuine all-zero hour before it can be saved, setting `hours[HH].confirmed_zero: true` on confirmation. `validate.py` only warns on an all-zero hour that's *unconfirmed*, and `aggregate.py` surfaces those specifically via `data_quality.unconfirmed_zero_hours` (daily rollups) and `data_quality.days_with_unconfirmed_zero_hours` (overall rollups) — the original unfiltered `all_zero_hours` list is unchanged. Older data predating this field has no way to have been confirmed, so it's treated as unconfirmed by default, same backfill-free pattern as the active-hour elapsed-time derivation.

---

## Uploading from a phone

Drivers don't need a full git setup. The Codeberg mobile-friendly web UI supports uploading a file directly into `inbox/` from a phone browser — open the repo, navigate to `inbox/`, and use the upload option to add the exported JSON. No app install required.
