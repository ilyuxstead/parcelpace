# Dropstats

A lightweight delivery-driver telematics tracker that uses a Codeberg git repository as its entire data store. No database, no backend server — just JSON files, a static web form, and a couple of Python scripts.

> **Note:** This project recently moved from GitHub to Codeberg, under the `dropstats` organization. All workflows below reflect the new home.

---

## How it works

1. A driver opens the **entry tool** (a single-page HTML app hosted on **Codeberg Pages**) on their phone.
2. They log a **start-of-day plan** and then an **hourly check-in** throughout their shift.
3. Each save is queued locally in the browser; at the end of a batch, the driver exports a combined JSON payload and uploads it into the `inbox/` folder of the repo (via the Codeberg web UI or a git client on the phone).
4. A processing script validates and files the submission into the date-organized data tree.
5. An aggregation script rolls daily and all-time stats per driver.

No driver ever hand-edits JSON. The web tool is the only thing that produces it.

---

## Repository structure

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
└── scripts/
    ├── inbox_common.py      # shared filename/folder helpers
    ├── validate.py          # schema + sanity checks for one payload
    ├── route_inbox.py       # validates inbox/ and files data by date
    └── aggregate.py         # rebuilds rollups/ and overall/ from scratch
```

**Date lives in the JSON, not the filename.** A driver's raw upload is always `driver-XXXXXXX.json` — the date is read out of the payload body during routing, then used to place the file in the right `yyyy/mm/dd/` folder.

**Data folders are nested by year/month/day, not flat.** A flat `YYYY-MM-DD/` folder per day would eventually leave hundreds of folders sitting at the repo root. Nesting keeps the root tidy — at most ~12 month folders per year, ~31 day folders per month — while `rollups/` mirrors the same nesting for consistency. `overall/` isn't date-keyed at all, so it stays flat.

---

## Data flow, end to end

```
   ┌──────────────┐      ┌─────────────┐      ┌───────────────────────┐      ┌─────────────┐
   │  index.html   │ ───▶ │  inbox/     │ ───▶ │  yyyy/mm/dd/driver-…  │ ───▶ │  rollups/   │
   │ (Codeberg     │ JSON │  (pending)  │ move │  (organized)          │ agg  │  overall/   │
   │  Pages)       │      │             │      │                       │      │             │
   └──────────────┘      └─────────────┘      └───────────────────────┘      └─────────────┘
                          validate.py +
                          route_inbox.py
```

1. **Plan + hourly entries** are composed client-side in `index.html`. Cumulative device readings (stops, pieces, pieces picked up) are converted to per-hour deltas *in the browser*, via `recalcDeltas()` against a `lastreading` baseline kept in `localStorage`. Only deltas are ever written to JSON — the raw cumulative totals never leave the phone.
2. **Export** bundles the queued plan + hourly entries into one consolidated `driver-XXXXXXX.json` and the driver uploads it into `inbox/`.
3. **`route_inbox.py`** scans `inbox/`, skips log files, and for each data file:
   - Calls `validate.py` against the schema. A missing `plan` is only a warning, but if a plan *is* submitted, a missing or blank `route_id` is a hard error — every plan needs a route so same-route days can be grouped for trending.
   - Appends any errors/warnings to that driver's accumulating `driver-XXXXXXX.log.json` (silent if the submission is fully clean).
   - On success, splits the payload's `date` field into `yyyy/mm/dd` and moves the file into `yyyy/mm/dd/driver-XXXXXXX.json`, overwriting any prior file for that driver+date.
   - On a hard error, leaves the file in `inbox/` for a human to look at.
4. **`aggregate.py`** walks every nested `yyyy/mm/dd/` folder and rebuilds, from scratch each run:
   - `rollups/yyyy/mm/dd/driver-XXXXXXX.json` — one day's totals, plan-vs-actual deltas, and pace metrics for a driver.
   - `overall/driver-XXXXXXX.json` — totals, averages, consistency (stddev), and trend across all days tracked. Trend is computed **per route** (`trend.by_route["17F"]`, etc.) — recent vs. prior `stops_per_mile`, windowed by the last 7 days *that route was driven*, not 7 calendar days. Days with no plan (no route) are excluded from every route's series.

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
      "entry_time": "ISO 8601"
    }
  }
}
```

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
```

### Automation

The previous GitHub Actions setup has been retired along with the GitHub repo. Until a Codeberg-native automation layer (e.g. **Woodpecker CI**, which integrates with Codeberg) is wired up, `route_inbox.py` and `aggregate.py` are run manually or via a local cron job against a clone of the repo. This is a known gap on the roadmap, not a permanent design choice.

---

## Design principles

- **Collect raw stats first, score later.** No scoring weights are defined yet — there isn't enough real driver data to know what a fair weighting even looks like. The pipeline focuses entirely on capturing clean, well-shaped data.
- **Fault-tolerant over blocking.** Odd values (e.g. a negative hourly delta) are flagged as warnings, logged, and let through rather than rejected outright. Hard errors (missing required fields, malformed dates) are the only thing that blocks a file from being filed.
- **Cumulative-to-delta conversion happens in the app, never in storage.** The web tool is the only place that ever sees a raw odometer-style cumulative number; everything written to JSON is already a clean per-hour delta.
- **Rollups are always rebuilt, never patched.** `aggregate.py` has no incremental update logic — every run recomputes `rollups/` and `overall/` from the full set of organized date folders. Simple and safe at the current data volume.
- **Nest date folders to keep the repo root readable.** Both the organized data tree and `rollups/` use `yyyy/mm/dd/` nesting instead of one flat folder per day, so the root and `rollups/` don't accumulate hundreds of sibling date folders over time.

---

## Known open questions

These are tracked as active design decisions, not bugs:

- Whether `planned_miles: 0` and `predicted_finish: null` represent legitimate states or should be tightened in `validate.py`.
- Whether `aggregate.py`'s handling of non-contiguous hour ranges (gaps in a shift) needs more nuance than the current `has_gaps` flag.
- Whether an all-zero, non-break hour should be distinguishable from an unedited/forgotten entry — currently both look identical to the pipeline.
- What composite "effort" and miles-normalized pace metrics should eventually feed into driver scoring.
- `route_id` casing is only normalized client-side (the HTML tool uppercases on save). `validate.py`/`aggregate.py` don't normalize it, so a route entered inconsistently outside the web tool (e.g. `17f` vs `17F`) would silently split into two separate trend buckets rather than being caught.

---

## Uploading from a phone

Drivers don't need a full git setup. The Codeberg mobile-friendly web UI supports uploading a file directly into `inbox/` from a phone browser — open the repo, navigate to `inbox/`, and use the upload option to add the exported JSON. No app install required.
